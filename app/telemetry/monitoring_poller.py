"""Cloud Monitoring poller for compute intensity metrics."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.telemetry.bigquery_io import insert_rows
from app.telemetry.config import TelemetryConfig, config_from_env

DEFAULT_COMPUTE_METRICS = (
    "compute.googleapis.com/instance/cpu/usage_time",
    "compute.googleapis.com/instance/cpu/reserved_cores",
    "compute.googleapis.com/instance/cpu/utilization",
)


def poll_compute_metrics(
    *,
    config: TelemetryConfig | None = None,
    minutes: int | None = None,
    metric_types: Iterable[str] | None = None,
    write_bigquery: bool = True,
) -> list[dict[str, Any]]:
    """Poll Compute Engine metrics and optionally write normalized rows."""

    config = config or config_from_env()
    if not config.project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required.")
    minutes = minutes or config.monitoring_window_minutes
    metric_types = tuple(metric_types or DEFAULT_COMPUTE_METRICS)

    from google.cloud import monitoring_v3

    client = monitoring_v3.MetricServiceClient()
    now = time.time()
    seconds = int(now)
    nanos = int((now - seconds) * 1_000_000_000)
    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": seconds, "nanos": nanos},
            "start_time": {"seconds": seconds - minutes * 60, "nanos": nanos},
        }
    )
    project_name = f"projects/{config.project_id}"
    rows: list[dict[str, Any]] = []
    for metric_type in metric_types:
        series_iter = client.list_time_series(
            request={
                "name": project_name,
                "filter": build_metric_filter(metric_type),
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            }
        )
        for series in series_iter:
            rows.extend(normalize_time_series(series, metric_type, config.project_id))

    if rows and write_bigquery:
        insert_rows(config.compute_metrics_table_id, rows, config=config)
    return rows


def build_metric_filter(metric_type: str) -> str:
    return f'metric.type="{metric_type}" AND resource.type="gce_instance"'


def normalize_time_series(
    series: Any,
    metric_type: str,
    fallback_project_id: str | None,
) -> list[dict[str, Any]]:
    resource_labels = dict(getattr(getattr(series, "resource", None), "labels", {}) or {})
    metric_labels = dict(getattr(getattr(series, "metric", None), "labels", {}) or {})
    zone = resource_labels.get("zone")
    region = region_from_zone(zone)
    rows: list[dict[str, Any]] = []
    for point in getattr(series, "points", []) or []:
        value = point_value(point)
        row = {
            "ts": point_end_time(point),
            "project_id": resource_labels.get("project_id") or fallback_project_id,
            "region": region,
            "zone": zone,
            "instance_id": resource_labels.get("instance_id"),
            "machine_type": metric_labels.get("machine_type"),
            "metric_type": metric_type,
            "reserved_cores": None,
            "cpu_usage_vcpu_seconds": None,
            "cpu_utilization": None,
            "accelerator_type": metric_labels.get("accelerator_type"),
            "accelerator_count": parse_float(metric_labels.get("accelerator_count")),
            "accelerator_utilization": None,
            "sample_interval_seconds": sample_interval_seconds(point),
            "raw_metric_json": json.dumps(
                {
                    "resource_labels": resource_labels,
                    "metric_labels": metric_labels,
                    "value": value,
                },
                sort_keys=True,
            ),
        }
        if metric_type.endswith("/cpu/usage_time"):
            row["cpu_usage_vcpu_seconds"] = value
        elif metric_type.endswith("/cpu/reserved_cores"):
            row["reserved_cores"] = value
        elif metric_type.endswith("/cpu/utilization"):
            row["cpu_utilization"] = value
        elif "accelerator" in metric_type:
            row["accelerator_utilization"] = value
        rows.append(row)
    return rows


def point_value(point: Any) -> float | None:
    value = getattr(point, "value", None)
    if value is None:
        return None
    for attr in ("double_value", "int64_value"):
        candidate = getattr(value, attr, None)
        if candidate is not None:
            return float(candidate)
    return None


def point_end_time(point: Any) -> str | None:
    interval = getattr(point, "interval", None)
    end_time = getattr(interval, "end_time", None)
    return timestamp_to_iso(end_time)


def sample_interval_seconds(point: Any) -> float | None:
    interval = getattr(point, "interval", None)
    end_time = getattr(interval, "end_time", None)
    start_time = getattr(interval, "start_time", None)
    end = timestamp_seconds(end_time)
    start = timestamp_seconds(start_time)
    if end is None or start is None:
        return None
    return max(0.0, end - start)


def timestamp_seconds(value: Any) -> float | None:
    seconds = getattr(value, "seconds", None)
    nanos = getattr(value, "nanos", 0)
    if seconds is None:
        return None
    return float(seconds) + float(nanos or 0) / 1_000_000_000.0


def timestamp_to_iso(value: Any) -> str | None:
    seconds = timestamp_seconds(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def region_from_zone(zone: str | None) -> str | None:
    if not zone:
        return None
    parts = zone.rsplit("/", 1)[-1].split("-")
    if len(parts) < 3:
        return zone
    return "-".join(parts[:-1])


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
