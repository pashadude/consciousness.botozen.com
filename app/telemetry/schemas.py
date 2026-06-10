"""BigQuery schemas for telemetry collector tables."""

from __future__ import annotations

from collections.abc import Sequence

SchemaSpec = Sequence[tuple[str, str, str]]

ASSET_EVENTS_SCHEMA: SchemaSpec = (
    ("ts", "TIMESTAMP", "NULLABLE"),
    ("received_at", "TIMESTAMP", "NULLABLE"),
    ("project_id", "STRING", "NULLABLE"),
    ("asset_name", "STRING", "NULLABLE"),
    ("asset_type", "STRING", "NULLABLE"),
    ("region", "STRING", "NULLABLE"),
    ("zone", "STRING", "NULLABLE"),
    ("action", "STRING", "NULLABLE"),
    ("prior_asset_state", "STRING", "NULLABLE"),
    ("resource_parent", "STRING", "NULLABLE"),
    ("asset_location", "STRING", "NULLABLE"),
    ("raw_asset_json", "STRING", "NULLABLE"),
)

COMPUTE_METRICS_SCHEMA: SchemaSpec = (
    ("ts", "TIMESTAMP", "NULLABLE"),
    ("project_id", "STRING", "NULLABLE"),
    ("region", "STRING", "NULLABLE"),
    ("zone", "STRING", "NULLABLE"),
    ("instance_id", "STRING", "NULLABLE"),
    ("machine_type", "STRING", "NULLABLE"),
    ("metric_type", "STRING", "NULLABLE"),
    ("reserved_cores", "FLOAT", "NULLABLE"),
    ("cpu_usage_vcpu_seconds", "FLOAT", "NULLABLE"),
    ("cpu_utilization", "FLOAT", "NULLABLE"),
    ("accelerator_type", "STRING", "NULLABLE"),
    ("accelerator_count", "FLOAT", "NULLABLE"),
    ("accelerator_utilization", "FLOAT", "NULLABLE"),
    ("sample_interval_seconds", "FLOAT", "NULLABLE"),
    ("raw_metric_json", "STRING", "NULLABLE"),
)

POWER_PRICES_SCHEMA: SchemaSpec = (
    ("hour", "TIMESTAMP", "REQUIRED"),
    ("google_region", "STRING", "REQUIRED"),
    ("power_market", "STRING", "NULLABLE"),
    ("node_or_zone", "STRING", "NULLABLE"),
    ("price_usd_per_mwh", "FLOAT", "NULLABLE"),
    ("source", "STRING", "NULLABLE"),
    ("confidence", "STRING", "NULLABLE"),
    ("raw_source_json", "STRING", "NULLABLE"),
    ("updated_at", "TIMESTAMP", "NULLABLE"),
)


def table_schemas() -> dict[str, SchemaSpec]:
    return {
        "gcp_asset_events": ASSET_EVENTS_SCHEMA,
        "gcp_compute_metrics": COMPUTE_METRICS_SCHEMA,
        "region_power_prices": POWER_PRICES_SCHEMA,
    }


def to_bigquery_schema(schema: SchemaSpec) -> list[object]:
    from google.cloud import bigquery

    return [bigquery.SchemaField(name, field_type, mode=mode) for name, field_type, mode in schema]

