-- Example hourly all-resource billing normalization view.
--
-- Replace:
--   YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_resource_v1_*
-- with your actual Cloud Billing export table pattern. Prefer the detailed
-- resource export (`gcp_billing_export_resource_v1_*`) when available. The
-- standard export (`gcp_billing_export_v1_*`) is still useful for all-resource
-- service/region cost, and `mutual-spec-telemetry install-views` can discover
-- either pattern.
--
-- This is intentionally not compute-only. Every billable service can feed the
-- routing criteria vector. Compute resources get the strongest kWh proxy;
-- non-compute resources still contribute cost, region, service class, and
-- confidence.
--
-- Field names can vary by export type and schema version. Inspect your billing
-- export before promoting this example to a production view.

CREATE OR REPLACE VIEW `YOUR_PROJECT.telemetry.gcp_resource_billing_hourly` AS
SELECT
  TIMESTAMP_TRUNC(usage_start_time, HOUR) AS hour,
  project.id AS project_id,
  COALESCE(location.location, location.region, 'unknown') AS region,
  service.description AS service,
  sku.description AS sku,
  CASE
    WHEN service.description IN ('Compute Engine', 'Kubernetes Engine')
      THEN 'compute'
    WHEN service.description IN ('Cloud Run', 'Cloud Run functions')
      THEN 'serverless_compute'
    WHEN LOWER(service.description) LIKE '%storage%'
      THEN 'storage'
    WHEN LOWER(service.description) LIKE '%network%'
      OR LOWER(sku.description) LIKE '%egress%'
      THEN 'network'
    WHEN LOWER(service.description) LIKE '%bigquery%'
      THEN 'analytics'
    WHEN LOWER(service.description) LIKE '%vertex ai%'
      THEN 'ai_platform'
    ELSE 'managed_service'
  END AS resource_class,
  SUM(usage.amount) AS usage_amount,
  ANY_VALUE(usage.unit) AS usage_unit,
  SUM(cost) AS gross_cost,
  SUM(IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0)) AS credits,
  SUM(cost) + SUM(IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) AS credit), 0)) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM `YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_resource_v1_*`
GROUP BY
  hour,
  project_id,
  region,
  service,
  sku,
  resource_class;
