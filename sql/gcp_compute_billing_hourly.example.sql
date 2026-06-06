-- Example hourly compute billing normalization view.
--
-- Replace:
--   YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_resource_v1_*
-- with your actual detailed Cloud Billing export table pattern.
--
-- Field names can vary by export type and schema version. Inspect your billing
-- export before promoting this example to a production view.

CREATE OR REPLACE VIEW `YOUR_PROJECT.telemetry.gcp_compute_billing_hourly` AS
SELECT
  TIMESTAMP_TRUNC(usage_start_time, HOUR) AS hour,
  project.id AS project_id,
  COALESCE(location.location, location.region, 'unknown') AS region,
  service.description AS service,
  sku.description AS sku,
  SUM(usage.amount) AS usage_amount,
  ANY_VALUE(usage.unit) AS usage_unit,
  SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0)) AS credits,
  SUM(cost) + SUM(IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0)) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM `YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_resource_v1_*`
WHERE service.description IN (
  'Compute Engine',
  'Kubernetes Engine',
  'Cloud Run',
  'Cloud Run functions'
)
GROUP BY
  hour,
  project_id,
  region,
  service,
  sku;
