"""BigQuery helpers for telemetry collectors and views."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.telemetry.config import TelemetryConfig, config_from_env
from app.telemetry.schemas import table_schemas, to_bigquery_schema


def build_client(config: TelemetryConfig | None = None) -> Any:
    config = config or config_from_env()
    if not config.project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    from google.cloud import bigquery

    return bigquery.Client(project=config.project_id)


def ensure_dataset_and_tables(config: TelemetryConfig | None = None) -> list[str]:
    config = config or config_from_env()
    if not config.project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    from google.cloud import bigquery

    client = build_client(config)
    dataset_id = f"{config.project_id}.{config.dataset_id}"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = config.location
    client.create_dataset(dataset, exists_ok=True)

    table_ids = {
        "gcp_asset_events": config.asset_table_id,
        "gcp_compute_metrics": config.compute_metrics_table_id,
        "region_power_prices": config.power_prices_table_id,
    }
    created: list[str] = []
    for schema_key, table_id in table_ids.items():
        table_ref = f"{dataset_id}.{table_id}"
        table = bigquery.Table(
            table_ref,
            schema=to_bigquery_schema(table_schemas()[schema_key]),
        )
        client.create_table(table, exists_ok=True)
        created.append(table_ref)
    return created


def insert_rows(
    table_id: str,
    rows: list[dict[str, Any]],
    *,
    config: TelemetryConfig | None = None,
) -> int:
    if not rows:
        return 0
    config = config or config_from_env()
    client = build_client(config)
    table_fqn = config.table_fqn(table_id)
    errors = client.insert_rows_json(table_fqn, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_fqn}: {errors}")
    return len(rows)


def install_views(
    *,
    config: TelemetryConfig | None = None,
    billing_table_pattern: str | None = None,
) -> list[str]:
    config = config or config_from_env()
    client = build_client(config)
    pattern = billing_table_pattern or config.billing_export_table_pattern
    pattern = pattern or discover_billing_export_table_pattern(config=config, client=client)
    sql_files = [
        Path("sql/gcp_resource_billing_hourly.example.sql"),
        Path("sql/resource_region_criteria_view.example.sql"),
    ]
    installed: list[str] = []
    for sql_file in sql_files:
        sql = render_sql_template(sql_file.read_text(), config, pattern)
        client.query(sql).result()
        installed.append(sql_file.as_posix())
    return installed


def discover_billing_export_table_pattern(
    *,
    config: TelemetryConfig | None = None,
    client: Any | None = None,
) -> str:
    config = config or config_from_env()
    client = client or build_client(config)
    dataset_ref = f"{config.project_id}.{config.dataset_id}"
    table_ids = sorted(table.table_id for table in client.list_tables(dataset_ref))
    detailed = [
        table_id
        for table_id in table_ids
        if table_id.startswith("gcp_billing_export_resource_v1_")
    ]
    if detailed:
        return f"{dataset_ref}.gcp_billing_export_resource_v1_*"
    standard = [
        table_id for table_id in table_ids if table_id.startswith("gcp_billing_export_v1_")
    ]
    if standard:
        return f"{dataset_ref}.gcp_billing_export_v1_*"
    raise RuntimeError(
        "No Cloud Billing export table found. Enable Billing export to BigQuery "
        "or set GCP_BILLING_EXPORT_TABLE_PATTERN."
    )


def render_sql_template(
    sql: str,
    config: TelemetryConfig,
    billing_table_pattern: str,
) -> str:
    if not config.project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    dataset_prefix = f"{config.project_id}.{config.dataset_id}"
    replacements = {
        "YOUR_PROJECT.telemetry": dataset_prefix,
        "YOUR_PROJECT": config.project_id,
        "YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_resource_v1_*": billing_table_pattern,
        "YOUR_BILLING_PROJECT.YOUR_BILLING_DATASET.gcp_billing_export_v1_*": billing_table_pattern,
    }
    rendered = sql
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    return rendered


def query_rows(
    sql: str,
    *,
    config: TelemetryConfig | None = None,
) -> list[dict[str, Any]]:
    client = build_client(config)
    return [dict(row.items()) for row in client.query(sql).result()]

