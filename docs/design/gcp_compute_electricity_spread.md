# GCP Compute-Electricity Spread Telemetry

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
individual data centers. This design estimates a regional compute-electricity
spread by joining Google Cloud compute/billing telemetry with regional wholesale
power-market proxies.

Treat the output as a signal and research feature, not settlement-grade proof of
Google's internal power cost.

## Two Joined Telemetry Layers

| Layer | Purpose | Main outputs |
| --- | --- | --- |
| Dynamic Google Cloud compute telemetry | Track resource changes, runtime intensity, reservations, accelerator usage, and customer billing. | vCPU-hours, reservation changes, accelerator proxy usage, compute cost by project/region/hour. |
| Regional electricity proxy telemetry | Track wholesale/grid electricity price near the selected Google Cloud region. | price USD/MWh, source market, mapping confidence, carbon/CFE context. |

The joined view estimates:

```text
compute_electricity_spread_proxy
  = gcp_compute_cost_or_revenue_usd - electricity_cost_proxy_usd
```

## What Google Cloud Can Expose Dynamically

### Cloud Asset Inventory Feeds

Cloud Asset Inventory feeds can publish real-time notifications for supported
resource and policy changes at project, folder, or organization scope.

Track these assets first:

```text
compute.googleapis.com/Instance
compute.googleapis.com/Disk
compute.googleapis.com/Reservation
```

Example setup:

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
resource changes.

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

Use a BigQuery view to normalize the export into hourly regional compute cost.
See `sql/gcp_compute_billing_hourly.example.sql`.

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
  = gcp_compute_cost_or_revenue_usd - electricity_cost_proxy_usd
```

The watt and PUE constants are calibratable coefficients. Do not present them as
ground truth.

## BigQuery Schemas

### `gcp_compute_events`

| Column | Type | Notes |
| --- | --- | --- |
| `ts` | TIMESTAMP | Event timestamp from Cloud Asset Inventory. |
| `project_id` | STRING | Google Cloud project. |
| `asset_name` | STRING | Full asset name. |
| `asset_type` | STRING | Example: `compute.googleapis.com/Instance`. |
| `region` | STRING | Derived from zone/location. |
| `zone` | STRING | Compute zone when available. |
| `machine_type` | STRING | Instance machine type when available. |
| `action` | STRING | Create, delete, update, resize, reservation change. |
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

### `gcp_billing_usage`

| Column | Type | Notes |
| --- | --- | --- |
| `hour` | TIMESTAMP | Hour bucket. |
| `project_id` | STRING | Billing project. |
| `region` | STRING | Billing location. |
| `service` | STRING | Compute Engine, GKE, Cloud Run, etc. |
| `sku` | STRING | Google Cloud SKU description. |
| `usage_amount` | FLOAT64 | Usage quantity. |
| `usage_unit` | STRING | Usage unit. |
| `cost` | NUMERIC | Cost before or after credits depending on view policy. |
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

### `compute_electricity_spread`

| Column | Type | Notes |
| --- | --- | --- |
| `hour` | TIMESTAMP | Hour bucket. |
| `google_region` | STRING | Google Cloud region. |
| `vcpu_hours` | FLOAT64 | Estimated active vCPU-hours. |
| `gpu_hours` | FLOAT64 | Estimated GPU-hours, optional. |
| `tpu_hours` | FLOAT64 | Estimated TPU-hours, optional. |
| `estimated_kwh` | FLOAT64 | Estimated energy from coefficients. |
| `gcp_compute_cost` | NUMERIC | Billing export cost. |
| `electricity_cost_proxy` | FLOAT64 | Estimated energy proxy cost. |
| `spread_proxy` | FLOAT64 | Compute cost minus electricity proxy cost. |
| `confidence` | STRING | Combined mapping/data confidence. |

## Architecture

```text
Cloud Asset Inventory Feed
  -> Pub/Sub
  -> Cloud Run or Dataflow consumer
  -> BigQuery: gcp_compute_events

Cloud Monitoring API poller
  -> Cloud Scheduler every 1-5 minutes
  -> Cloud Run job
  -> BigQuery: gcp_compute_metrics

Cloud Billing export
  -> BigQuery: gcp_billing_usage

Cloud Billing Pricing API or pricing export
  -> BigQuery: gcp_sku_prices

Power-market fetcher
  -> EIA / GridStatus / ISO / ENTSO-E / Nord Pool
  -> BigQuery: region_power_prices

Google CFE / carbon table
  -> BigQuery: google_region_carbon_context

BigQuery view
  -> compute_electricity_spread_by_region_hour

Agent routing/scoring feature
  -> choose model plus Google Cloud region
```

## Routing Use

This repo currently records model routing decisions in `app/router.py`. The next
routing step should add a region dimension:

```text
route = {
  model: "gemini-flash-latest",
  google_region: "us-central1",
  reason: "low ambiguity, lower regional spread stress, acceptable latency",
}
```

The compute-electricity spread should be an evidence feature for routing and
judging. It must not bypass verifier checks or automatically trigger settlement,
deployment, or trading actions.

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Google does not expose true data-center power price. | Spread can be misread as internal Google margin. | Label all outputs as proxy estimates. |
| Region-to-grid mapping basis risk. | Wrong power market can distort signal. | Store confidence and notes per region. |
| Billing export delay/schema drift. | Cost joins can lag or break. | Put billing logic behind views and inspect schema after export setup. |
| Monitoring metric delay. | Minute-level routing can use stale usage. | Use lag-aware windows and quality flags. |
| Carbon data is monthly/lagged or not assured. | Unsuitable for live routing. | Use CFE/carbon only as context or reconciliation. |
| Coefficients for watts/PUE are assumptions. | kWh estimate can be materially wrong. | Calibrate and version coefficients. |
| Model availability varies by region. | Region route may not support desired model. | Maintain a model-region availability map. |
| Latency and data residency constraints. | Cheapest region may be unacceptable. | Add latency, compliance, and user-location constraints. |

## v0 / v1 Deferrals

| Stage | Include | Defer |
| --- | --- | --- |
| v0 | Region map, billing hourly view, spread view, design doc, one power-market source. | Live API consumers, automatic routing, settlement/execution. |
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
