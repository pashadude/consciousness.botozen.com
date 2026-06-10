"""Configuration for live resource-region telemetry collectors."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TelemetryConfig:
    project_id: str | None
    dataset_id: str
    location: str
    asset_topic_id: str
    asset_subscription_id: str
    asset_table_id: str
    compute_metrics_table_id: str
    power_prices_table_id: str
    billing_view_id: str
    criteria_view_id: str
    billing_export_table_pattern: str | None
    monitoring_window_minutes: int
    power_price_usd_per_mwh: float
    region_power_map_path: Path
    collectors_enabled: bool

    @property
    def can_call_google(self) -> bool:
        return bool(self.project_id and self.dataset_id)

    @property
    def asset_subscription_path(self) -> str | None:
        if not self.project_id:
            return None
        if self.asset_subscription_id.startswith("projects/"):
            return self.asset_subscription_id
        return f"projects/{self.project_id}/subscriptions/{self.asset_subscription_id}"

    def table_fqn(self, table_id: str) -> str:
        if not self.project_id:
            raise ValueError("GOOGLE_CLOUD_PROJECT is required for BigQuery table names.")
        return f"{self.project_id}.{self.dataset_id}.{table_id}"


def config_from_env(env: dict[str, str] | None = None) -> TelemetryConfig:
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass
        env = os.environ
    project_id = env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCLOUD_PROJECT")
    return TelemetryConfig(
        project_id=configured_value(project_id),
        dataset_id=env.get("TELEMETRY_DATASET_ID", "telemetry"),
        location=env.get("TELEMETRY_LOCATION", env.get("BQ_LOCATION", "US")),
        asset_topic_id=env.get("GCP_ASSET_CHANGES_TOPIC", "gcp-all-resource-changes"),
        asset_subscription_id=env.get(
            "GCP_ASSET_CHANGES_SUBSCRIPTION",
            "gcp-all-resource-changes-sub",
        ),
        asset_table_id=env.get("GCP_ASSET_EVENTS_TABLE", "gcp_asset_events"),
        compute_metrics_table_id=env.get(
            "GCP_COMPUTE_METRICS_TABLE",
            "gcp_compute_metrics",
        ),
        power_prices_table_id=env.get("POWER_PRICES_TABLE", "region_power_prices"),
        billing_view_id=env.get(
            "GCP_RESOURCE_BILLING_VIEW",
            "gcp_resource_billing_hourly",
        ),
        criteria_view_id=env.get(
            "RESOURCE_REGION_CRITERIA_VIEW",
            "resource_region_criteria_by_hour",
        ),
        billing_export_table_pattern=configured_value(
            env.get("GCP_BILLING_EXPORT_TABLE_PATTERN")
        ),
        monitoring_window_minutes=parse_int(
            env.get("GCP_MONITORING_WINDOW_MINUTES"),
            15,
        ),
        power_price_usd_per_mwh=parse_float(
            env.get("POWER_PRICE_USD_PER_MWH"),
            80.0,
        ),
        region_power_map_path=Path(
            env.get("REGION_POWER_MAP_PATH", "config/region_power_map.example.yaml")
        ),
        collectors_enabled=parse_bool(
            env.get("RESOURCE_TELEMETRY_COLLECTORS_ENABLED"),
            False,
        ),
    )


def configured_value(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    lowered = stripped.lower()
    if not stripped or lowered in {"none", "null"} or lowered.startswith("your-"):
        return None
    return stripped


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
