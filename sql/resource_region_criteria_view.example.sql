-- Example resource-region routing criteria view.
--
-- Expected supporting tables/views:
--   `YOUR_PROJECT.telemetry.gcp_compute_metrics`
--   `YOUR_PROJECT.telemetry.gcp_resource_billing_hourly`
--   `YOUR_PROJECT.telemetry.region_power_prices`
--   optional: `YOUR_PROJECT.telemetry.google_region_carbon_context`
--
-- This view emits criteria for multicriteria domination. It does not collapse
-- routing into one additive utility function. The agent should first filter
-- hard constraints, then keep nondominated model-region candidates. The optional
-- tiebreak score is only for unresolved Pareto ties.

CREATE OR REPLACE VIEW `YOUR_PROJECT.telemetry.resource_region_criteria_by_hour` AS
WITH coefficients AS (
  SELECT
    8.0 AS watts_per_vcpu,
    300.0 AS watts_per_gpu,
    300.0 AS watts_per_tpu,
    1.10 AS pue,
    1.00 AS cost_tiebreak_weight,
    0.35 AS spread_tiebreak_weight,
    0.50 AS low_confidence_penalty
),
compute_metrics AS (
  SELECT
    TIMESTAMP_TRUNC(ts, HOUR) AS hour,
    project_id,
    region AS google_region,
    SUM(IFNULL(cpu_usage_vcpu_seconds, 0)) / 3600.0 AS vcpu_hours,
    SUM(
      CASE
        WHEN LOWER(IFNULL(accelerator_type, '')) LIKE '%gpu%'
          THEN IFNULL(accelerator_count, 0)
            * IFNULL(accelerator_utilization, 0)
            * IFNULL(sample_interval_seconds, 60)
            / 3600.0
        ELSE 0
      END
    ) AS gpu_hours,
    SUM(
      CASE
        WHEN LOWER(IFNULL(accelerator_type, '')) LIKE '%tpu%'
          THEN IFNULL(accelerator_count, 0)
            * IFNULL(accelerator_utilization, 0)
            * IFNULL(sample_interval_seconds, 60)
            / 3600.0
        ELSE 0
      END
    ) AS tpu_hours
  FROM `YOUR_PROJECT.telemetry.gcp_compute_metrics`
  GROUP BY
    hour,
    project_id,
    google_region
),
resource_billing AS (
  SELECT
    hour,
    project_id,
    region AS google_region,
    resource_class,
    SUM(net_cost) AS resource_cost,
    ANY_VALUE(currency) AS currency
  FROM `YOUR_PROJECT.telemetry.gcp_resource_billing_hourly`
  GROUP BY
    hour,
    project_id,
    google_region,
    resource_class
),
regional_billing AS (
  SELECT
    hour,
    project_id,
    google_region,
    SUM(resource_cost) AS all_resource_cost,
    SUM(IF(resource_class IN ('compute', 'serverless_compute', 'ai_platform'), resource_cost, 0)) AS compute_adjacent_cost,
    ANY_VALUE(currency) AS currency
  FROM resource_billing
  GROUP BY
    hour,
    project_id,
    google_region
),
power AS (
  SELECT
    hour,
    google_region,
    ANY_VALUE(power_market) AS power_market,
    ANY_VALUE(node_or_zone) AS node_or_zone,
    AVG(price_usd_per_mwh) AS price_usd_per_mwh,
    ANY_VALUE(source) AS source,
    ANY_VALUE(confidence) AS confidence,
    CASE ANY_VALUE(confidence)
      WHEN 'high' THEN 1.0
      WHEN 'medium' THEN 0.7
      WHEN 'low' THEN 0.4
      ELSE 0.25
    END AS confidence_weight
  FROM `YOUR_PROJECT.telemetry.region_power_prices`
  GROUP BY
    hour,
    google_region
),
joined AS (
  SELECT
    b.hour,
    b.project_id,
    b.google_region,
    IFNULL(m.vcpu_hours, 0) AS vcpu_hours,
    IFNULL(m.gpu_hours, 0) AS gpu_hours,
    IFNULL(m.tpu_hours, 0) AS tpu_hours,
    b.all_resource_cost,
    b.compute_adjacent_cost,
    b.currency,
    p.power_market,
    p.node_or_zone,
    p.price_usd_per_mwh,
    p.source AS power_price_source,
    IFNULL(p.confidence, 'unknown') AS confidence,
    IFNULL(p.confidence_weight, 0.25) AS confidence_weight
  FROM regional_billing AS b
  LEFT JOIN compute_metrics AS m
    USING (hour, project_id, google_region)
  LEFT JOIN power AS p
    USING (hour, google_region)
)
SELECT
  j.hour,
  j.project_id,
  j.google_region,
  j.vcpu_hours,
  j.gpu_hours,
  j.tpu_hours,
  (
    (j.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
    + (j.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
    + (j.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
  ) AS estimated_compute_kwh,
  j.all_resource_cost,
  j.compute_adjacent_cost,
  j.currency,
  j.power_market,
  j.node_or_zone,
  j.price_usd_per_mwh,
  j.power_price_source,
  j.confidence,
  (
    (
      (j.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
      + (j.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
      + (j.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
    )
    * IFNULL(j.price_usd_per_mwh, 0) / 1000.0
  ) AS electricity_cost_proxy,
  CAST(j.compute_adjacent_cost AS FLOAT64)
    - (
      (
        (j.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
        + (j.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
        + (j.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
      )
      * IFNULL(j.price_usd_per_mwh, 0) / 1000.0
    ) AS compute_electricity_spread_proxy,
  ABS(
    CAST(j.compute_adjacent_cost AS FLOAT64)
      - (
        (
          (j.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
          + (j.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
          + (j.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
        )
        * IFNULL(j.price_usd_per_mwh, 0) / 1000.0
      )
  ) / GREATEST(j.confidence_weight, 0.01) AS compute_electricity_spread_stress,
  CAST(j.all_resource_cost AS FLOAT64) AS all_resource_cost_criterion,
  IF(j.confidence IN ('low', 'unknown'), c.low_confidence_penalty, 0) AS confidence_penalty_criterion,
  (
    c.cost_tiebreak_weight * CAST(j.all_resource_cost AS FLOAT64)
    + c.spread_tiebreak_weight * (
      ABS(
        CAST(j.compute_adjacent_cost AS FLOAT64)
          - (
            (
              (j.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
              + (j.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
              + (j.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
            )
            * IFNULL(j.price_usd_per_mwh, 0) / 1000.0
          )
      )
      / GREATEST(j.confidence_weight, 0.01)
    )
    + IF(j.confidence IN ('low', 'unknown'), c.low_confidence_penalty, 0)
  ) AS optional_tiebreak_score
FROM joined AS j
CROSS JOIN coefficients AS c;
