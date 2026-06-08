# GCP Resource-Region Domination Telemetry

## Goal

Build a telemetry layer that helps the agent route not only between models, but
also between Google Cloud regions. The routing target is a model-plus-region
choice, for example:

```text
gemini-flash-latest in us-central1
gemini-pro-latest in us-east4
gemini-flash-latest in europe-west3
```

Google Cloud does not expose the actual electricity price paid by a specific
Google data center, and customers generally select regions or zones rather than
individual data centers. This design estimates region-level criteria by joining
all-resource Google Cloud billing telemetry, compute/runtime telemetry, and
regional wholesale power-market proxies.

Compute-electricity spread is one criterion in the routing game. It is not the
whole objective and should not be collapsed into a fixed weighted sum before
the domination step. Treat the output as a routing and research feature, not
settlement-grade proof of Google's internal power cost.

## Two Joined Telemetry Layers

| Layer | Purpose | Main outputs |
| --- | --- | --- |
| Dynamic Google Cloud resource telemetry | Track resource changes, runtime intensity where available, reservations, accelerator usage, and customer billing for all billable resources. | all-resource cost, resource classes, vCPU-hours, reservation changes, accelerator proxy usage, compute-adjacent cost by project/region/hour. |
| Regional electricity proxy telemetry | Track wholesale/grid electricity price near the selected Google Cloud region. | price USD/MWh, source market, mapping confidence, carbon/CFE context. |

The joined view estimates:

```text
candidate = (model, google_region)

criteria(candidate) = {
  policy_allowed,
  model_quality_risk,
  latency_ms,
  all_resource_cost,
  compute_electricity_spread_stress,
  carbon_context_penalty,
  proxy_confidence_penalty
}
```

Candidate `A` dominates candidate `B` when `A` satisfies the hard constraints,
is no worse than `B` on every soft criterion within configured epsilon
tolerances, and is strictly better than `B` on at least one soft criterion. The
router should keep the nondominated frontier, then apply a narrow tie-break rule
only if the frontier still has multiple candidates.

All billable resources feed `all_resource_cost`. Compute, serverless
compute, AI platform, GPU, and TPU usage also feed
`compute_electricity_spread_stress` because they have the strongest available
usage-to-kWh proxy.

## What Google Cloud Can Expose Dynamically

### Cloud Asset Inventory Feeds

Cloud Asset Inventory feeds can publish real-time notifications for supported
resource and policy changes at project, folder, or organization scope. For
all-resource mode, configure coverage across the supported asset types you care
about at the chosen scope, then narrow with explicit asset-type allowlists only
if event volume is too high.

Compute-heavy assets are the first high-value allowlist because they drive the
electricity proxy:

```text
compute.googleapis.com/Instance
compute.googleapis.com/Disk
compute.googleapis.com/Reservation
```

Example compute-heavy starter setup:

```bash
export PROJECT_ID="your-project-id"
export TOPIC="gcp-asset-changes"

gcloud services enable \
  cloudasset.googleapis.com \
  pubsub.googleapis.com \
  monitoring.googleapis.com \
  cloudbilling.googleapis.com \
  bigquery.googleapis.com

gcloud pubsub topics create "$TOPIC"

gcloud asset feeds create compute-feed \
  --project="$PROJECT_ID" \
  --pubsub-topic="projects/$PROJECT_ID/topics/$TOPIC" \
  --asset-types="compute.googleapis.com/Instance,compute.googleapis.com/Disk,compute.googleapis.com/Reservation" \
  --content-type=resource
```

This detects topology changes such as instance creation/deletion, machine type
changes, disk changes, reservation creation/use, and accelerator-attached
resource changes. The all-resource criteria layer still uses Cloud Billing
export for complete billable-resource coverage, including services that do not
have useful runtime intensity metrics.

### Cloud Monitoring Metrics

Use Cloud Monitoring for runtime compute intensity. Useful metrics include:

```text
compute.googleapis.com/instance/cpu/reserved_cores
compute.googleapis.com/instance/cpu/usage_time
compute.googleapis.com/instance/cpu/utilization
```

The CPU usage metric is vCPU-seconds. If only utilization is available:

```text
vcpu_hours = SUM(reserved_cores * cpu_utilization * interval_seconds) / 3600
```

For accelerator workloads, add TPU/GPU metrics where supported, such as duty
cycle, tensorcore utilization, memory used, and memory bandwidth utilization.

### Cloud Billing Export To BigQuery

Cloud Billing export provides usage, cost estimates, pricing data, projects,
locations, services, SKUs, labels, credits, and adjustments. The detailed export
adds resource-level cost data for supported products such as Compute Engine,
GKE, Cloud Run functions, and Cloud Run.

Use a BigQuery view to normalize the export into hourly regional cost for every
billable resource. See `sql/gcp_resource_billing_hourly.example.sql`.

### Cloud Billing Pricing API Or Pricing Export

Use the Cloud Billing Pricing API or pricing export to compare regional
electricity proxies against Google Cloud SKU prices. This reveals the price paid
by the customer to Google, not Google's internal power cost.

## What Google Cloud Does Not Expose

| Missing field | Implication |
| --- | --- |
| Actual power price paid by a specific Google data center | Use regional wholesale/grid proxies with confidence scores. |
| Exact workload-level kWh | Estimate from vCPU-hours, accelerator-hours, utilization, power coefficients, and PUE assumptions. |
| Customer-level physical data center assignment | Route by Google Cloud region/location, not an individual facility. |
| Live Cloud Carbon Footprint by workload | Use monthly lagged carbon export for reconciliation, not live routing. |

Google's regional carbon and CFE data is useful context, but it is not a live
electricity price. Cloud Carbon Footprint export is monthly and lagged.

## Regional Power Proxy Mapping

Start with a versioned region map:

```text
config/region_power_map.example.yaml
```

Each mapping includes:

| Field | Meaning |
| --- | --- |
| `google_region` | Google Cloud region key. |
| `google_location` | Human-readable Google region location. |
| `power_proxy` | ISO/RTO, hub, load zone, or country day-ahead proxy. |
| `source` | Data source such as GridStatus, EIA, ERCOT, PJM, ENTSO-E, Nord Pool, or EPEX. |
| `confidence` | `high`, `medium`, or `low` confidence in the proxy. |
| `notes` | Caveats for basis risk and data availability. |

For v0, prioritize:

| Google region | Proxy | Confidence |
| --- | --- | --- |
| `us-south1` | Dallas / ERCOT North Hub or load-zone proxy | medium |
| `us-east4` | Northern Virginia / PJM Dominion proxy | medium |
| `europe-west3` | Frankfurt / Germany-Luxembourg day-ahead power | medium |

## Estimation Formulas

Compute usage:

```text
estimated_vcpu_hours = SUM(cpu_usage_time_vcpu_seconds) / 3600
```

If only utilization is available:

```text
estimated_vcpu_hours
  = SUM(reserved_cores * cpu_utilization * interval_seconds) / 3600
```

Estimated power:

```text
estimated_kwh
  = (vcpu_hours * watts_per_vcpu / 1000 * pue)
  + (gpu_hours * watts_per_gpu / 1000 * pue)
  + (tpu_hours * watts_per_tpu / 1000 * pue)
```

Electricity proxy cost:

```text
electricity_cost_proxy_usd
  = estimated_kwh * price_usd_per_mwh / 1000
```

Spread proxy:

```text
compute_electricity_spread_proxy
  = compute_adjacent_cost_usd - electricity_cost_proxy_usd
```

Regional domination criteria:

```text
compute_electricity_spread_stress
  = ABS(compute_electricity_spread_proxy) / confidence_weight

criteria(candidate)
  = [
      policy_allowed,
      model_quality_risk,
      latency_ms,
      all_resource_cost,
      compute_electricity_spread_stress,
      carbon_context_penalty,
      proxy_confidence_penalty
    ]
```

The watt and PUE constants are calibratable coefficients. Do not present them as
ground truth.

## BigQuery Schemas

### `gcp_resource_events`

| Column | Type | Notes |
| --- | --- | --- |
| `ts` | TIMESTAMP | Event timestamp from Cloud Asset Inventory. |
| `project_id` | STRING | Google Cloud project. |
| `asset_name` | STRING | Full asset name. |
| `asset_type` | STRING | Example: `compute.googleapis.com/Instance` or another supported asset type. |
| `region` | STRING | Derived from zone/location. |
| `zone` | STRING | Compute zone when available. |
| `machine_type` | STRING | Instance machine type when available. |
| `action` | STRING | Create, delete, update, resize, reservation change, policy change, or service-specific change. |
| `raw_asset_json` | JSON | Raw event payload. |

### `gcp_compute_metrics`

| Column | Type | Notes |
| --- | --- | --- |
| `ts` | TIMESTAMP | Sample timestamp. |
| `project_id` | STRING | Google Cloud project. |
| `region` | STRING | Region derived from zone. |
| `zone` | STRING | Compute zone. |
| `instance_id` | STRING | GCE instance ID. |
| `machine_type` | STRING | Instance machine type if available. |
| `reserved_cores` | FLOAT64 | Reserved vCPUs. |
| `cpu_usage_vcpu_seconds` | FLOAT64 | vCPU-seconds usage. |
| `cpu_utilization` | FLOAT64 | Utilization ratio. |
| `sample_interval_seconds` | FLOAT64 | Metric sample interval used for utilization-derived estimates. |
| `accelerator_type` | STRING | GPU/TPU type when available. |
| `accelerator_count` | FLOAT64 | Attached accelerator count when available. |
| `accelerator_utilization` | FLOAT64 | Accelerator utilization proxy. |

### `gcp_resource_billing_hourly`

| Column | Type | Notes |
| --- | --- | --- |
| `hour` | TIMESTAMP | Hour bucket. |
| `project_id` | STRING | Billing project. |
| `region` | STRING | Billing location. |
| `service` | STRING | Compute Engine, GKE, Cloud Run, Cloud Storage, BigQuery, Vertex AI, etc. |
| `sku` | STRING | Google Cloud SKU description. |
| `resource_class` | STRING | Normalized class such as `compute`, `serverless_compute`, `storage`, `network`, `analytics`, `ai_platform`, or `managed_service`. |
| `usage_amount` | FLOAT64 | Usage quantity. |
| `usage_unit` | STRING | Usage unit. |
| `gross_cost` | NUMERIC | Cost before credits. |
| `credits` | NUMERIC | Credits from the billing export. |
| `net_cost` | NUMERIC | Cost plus credits. |
| `currency` | STRING | Billing currency. |

### `region_power_prices`

| Column | Type | Notes |
| --- | --- | --- |
| `hour` | TIMESTAMP | Hour bucket. |
| `google_region` | STRING | Google Cloud region. |
| `power_market` | STRING | ISO/RTO/country market. |
| `node_or_zone` | STRING | Hub, node, load zone, or country zone. |
| `price_usd_per_mwh` | FLOAT64 | Normalized price. |
| `source` | STRING | EIA, GridStatus, ISO, ENTSO-E, Nord Pool, etc. |
| `confidence` | STRING | Mapping confidence. |

### `resource_region_criteria`

| Column | Type | Notes |
| --- | --- | --- |
| `hour` | TIMESTAMP | Hour bucket. |
| `google_region` | STRING | Google Cloud region. |
| `vcpu_hours` | FLOAT64 | Estimated active vCPU-hours. |
| `gpu_hours` | FLOAT64 | Estimated GPU-hours, optional. |
| `tpu_hours` | FLOAT64 | Estimated TPU-hours, optional. |
| `estimated_compute_kwh` | FLOAT64 | Estimated compute/accelerator energy from coefficients. |
| `all_resource_cost` | NUMERIC | Billing export cost across all resource classes. |
| `compute_adjacent_cost` | NUMERIC | Compute, serverless compute, and AI platform cost. |
| `electricity_cost_proxy` | FLOAT64 | Estimated energy proxy cost. |
| `compute_electricity_spread_proxy` | FLOAT64 | Compute-adjacent cost minus electricity proxy cost. |
| `compute_electricity_spread_stress` | FLOAT64 | Absolute spread proxy adjusted by mapping confidence. |
| `all_resource_cost_criterion` | FLOAT64 | Numeric criterion used for domination. |
| `confidence_penalty_criterion` | FLOAT64 | Penalty criterion for low/unknown proxy confidence. |
| `optional_tiebreak_score` | FLOAT64 | Secondary score only after nondominated filtering. |
| `confidence` | STRING | Combined mapping/data confidence. |

## Architecture

```text
Cloud Asset Inventory Feed
  -> Pub/Sub
  -> Cloud Run or Dataflow consumer
  -> BigQuery: gcp_resource_events

Cloud Monitoring API poller
  -> Cloud Scheduler every 1-5 minutes
  -> Cloud Run job
  -> BigQuery: gcp_compute_metrics

Cloud Billing export
  -> BigQuery: gcp_resource_billing_hourly

Cloud Billing Pricing API or pricing export
  -> BigQuery: gcp_sku_prices

Power-market fetcher
  -> EIA / GridStatus / ISO / ENTSO-E / Nord Pool
  -> BigQuery: region_power_prices

Google CFE / carbon table
  -> BigQuery: google_region_carbon_context

BigQuery view
  -> resource_region_criteria_by_hour

Agent routing/scoring feature
  -> choose nondominated model plus Google Cloud region candidate
```

## Routing Use

This repo currently records model routing decisions in `app/router.py`. The next
routing step should add a region dimension:

```text
route = {
  model: "gemini-flash-latest",
  google_region: "us-central1",
  reason: "nondominated candidate: acceptable quality, lower cost, lower compute-electricity stress",
}
```

The all-resource criteria vector should be an evidence feature for routing and
judging. Compute-electricity spread is one criterion inside that vector. It must
not bypass verifier checks or automatically trigger settlement, deployment, or
trading actions.

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Google does not expose true data-center power price. | Spread can be misread as internal Google margin. | Label all outputs as proxy estimates. |
| Region-to-grid mapping basis risk. | Wrong power market can distort signal. | Store confidence and notes per region. |
| Billing export delay/schema drift. | Cost joins can lag or break. | Put billing logic behind views and inspect schema after export setup. |
| Monitoring metric delay. | Minute-level routing can use stale usage. | Use lag-aware windows and quality flags. |
| Weighted sums hide strategic tradeoffs. | A cheap but unsafe or low-quality route can win by arithmetic. | Use hard constraints and Pareto/nondominated filtering before tie-break scores. |
| Carbon data is monthly/lagged or not assured. | Unsuitable for live routing. | Use CFE/carbon only as context or reconciliation. |
| Coefficients for watts/PUE are assumptions. | kWh estimate can be materially wrong. | Calibrate and version coefficients. |
| Model availability varies by region. | Region route may not support desired model. | Maintain a model-region availability map. |
| Latency and data residency constraints. | Cheapest region may be unacceptable. | Add latency, compliance, and user-location constraints. |

## v0 / v1 Deferrals

| Stage | Include | Defer |
| --- | --- | --- |
| v0 | Region map, all-resource billing hourly view, resource-region criteria view, design doc, one power-market source. | Live API consumers, automatic routing, settlement/execution. |
| v1 | Cloud Asset feed consumer, Monitoring poller, billing export ingestion, dashboard. | Per-workload kWh truth claims, individual data-center routing. |
| v2 | Model-plus-region routing policy with latency/compliance guardrails. | Autonomous execution without verifier/judge approval. |

## References

- Cloud Asset Inventory feeds: https://cloud.google.com/asset-inventory/docs/monitoring-asset-changes
- Cloud Monitoring metrics: https://cloud.google.com/monitoring/api/metrics_gcp
- Compute metrics: https://docs.cloud.google.com/monitoring/api/metrics_gcp_c
- Monitoring `projects.timeSeries.list`: https://cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list
- Cloud Billing export to BigQuery: https://cloud.google.com/billing/docs/how-to/export-data-bigquery
- Cloud Billing Pricing API: https://cloud.google.com/billing/docs/reference/pricing-api/rest
- Google Cloud regional CFE/carbon context: https://cloud.google.com/sustainability/region-carbon
- Carbon Footprint export: https://cloud.google.com/carbon-footprint/docs/export
- EIA Open Data API: https://www.eia.gov/opendata/documentation.php
- GridStatus docs: https://docs.gridstatus.io/
- Cloud Carbon Footprint methodology: https://www.cloudcarbonfootprint.org/docs/methodology/
