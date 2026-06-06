-- Example compute-electricity spread proxy view.
--
-- Expected supporting tables/views:
--   `YOUR_PROJECT.telemetry.gcp_compute_metrics`
--   `YOUR_PROJECT.telemetry.gcp_compute_billing_hourly`
--   `YOUR_PROJECT.telemetry.region_power_prices`
--
-- This is a signal feature, not settlement-grade truth. The watts and PUE
-- constants are assumptions and should be calibrated/versioned.

CREATE OR REPLACE VIEW `YOUR_PROJECT.telemetry.compute_electricity_spread_by_region_hour` AS
WITH coefficients AS (
  SELECT
    8.0 AS watts_per_vcpu,
    300.0 AS watts_per_gpu,
    300.0 AS watts_per_tpu,
    1.10 AS pue
),
metrics AS (
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
billing AS (
  SELECT
    hour,
    project_id,
    region AS google_region,
    SUM(net_cost) AS gcp_compute_cost,
    ANY_VALUE(currency) AS currency
  FROM `YOUR_PROJECT.telemetry.gcp_compute_billing_hourly`
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
    ANY_VALUE(confidence) AS confidence
  FROM `YOUR_PROJECT.telemetry.region_power_prices`
  GROUP BY
    hour,
    google_region
)
SELECT
  m.hour,
  m.project_id,
  m.google_region,
  m.vcpu_hours,
  m.gpu_hours,
  m.tpu_hours,
  (
    (m.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
    + (m.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
    + (m.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
  ) AS estimated_kwh,
  b.gcp_compute_cost,
  b.currency,
  p.power_market,
  p.node_or_zone,
  p.price_usd_per_mwh,
  p.source AS power_price_source,
  p.confidence,
  (
    (
      (m.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
      + (m.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
      + (m.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
    )
    * p.price_usd_per_mwh / 1000.0
  ) AS electricity_cost_proxy,
  CAST(b.gcp_compute_cost AS FLOAT64)
    - (
      (
        (m.vcpu_hours * c.watts_per_vcpu / 1000.0 * c.pue)
        + (m.gpu_hours * c.watts_per_gpu / 1000.0 * c.pue)
        + (m.tpu_hours * c.watts_per_tpu / 1000.0 * c.pue)
      )
      * p.price_usd_per_mwh / 1000.0
    ) AS spread_proxy
FROM metrics AS m
CROSS JOIN coefficients AS c
LEFT JOIN billing AS b
  USING (hour, project_id, google_region)
LEFT JOIN power AS p
  USING (hour, google_region);
