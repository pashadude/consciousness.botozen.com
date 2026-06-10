"""Cloud Asset Inventory Pub/Sub consumer."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.telemetry.bigquery_io import insert_rows
from app.telemetry.config import TelemetryConfig, config_from_env

PROJECT_RE = re.compile(r"/projects/([^/]+)")
ZONE_RE = re.compile(r"/zones/([^/]+)")
REGION_RE = re.compile(r"/regions/([^/]+)")


def pull_asset_events(
    *,
    config: TelemetryConfig | None = None,
    limit: int = 10,
    ack: bool = True,
    write_bigquery: bool = True,
) -> list[dict[str, Any]]:
    """Pull asset events from Pub/Sub and optionally write them to BigQuery."""

    config = config or config_from_env()
    if not config.asset_subscription_path:
        raise ValueError("GOOGLE_CLOUD_PROJECT and GCP_ASSET_CHANGES_SUBSCRIPTION are required.")

    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    response = subscriber.pull(
        request={
            "subscription": config.asset_subscription_path,
            "max_messages": limit,
        }
    )
    rows: list[dict[str, Any]] = []
    ack_ids: list[str] = []
    for received in response.received_messages:
        payload = json.loads(received.message.data.decode("utf-8"))
        rows.append(normalize_asset_event(payload))
        ack_ids.append(received.ack_id)

    if rows and write_bigquery:
        insert_rows(config.asset_table_id, rows, config=config)
    if ack and ack_ids:
        subscriber.acknowledge(
            request={
                "subscription": config.asset_subscription_path,
                "ack_ids": ack_ids,
            }
        )
    return rows


def normalize_asset_event(
    payload: dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    received_at = received_at or datetime.now(UTC)
    asset = payload.get("asset") or {}
    prior_asset = payload.get("priorAsset") or {}
    primary_asset = asset or prior_asset
    resource = primary_asset.get("resource") or {}
    data = resource.get("data") or {}
    asset_name = primary_asset.get("name")
    asset_type = primary_asset.get("assetType")
    zone = extract_zone(primary_asset, data)
    region = extract_region(primary_asset, data, zone)
    ts = (
        (payload.get("window") or {}).get("startTime")
        or primary_asset.get("updateTime")
        or received_at.isoformat()
    )
    return {
        "ts": ts,
        "received_at": received_at.isoformat(),
        "project_id": extract_project_id(primary_asset),
        "asset_name": asset_name,
        "asset_type": asset_type,
        "region": region,
        "zone": zone,
        "action": infer_action(payload),
        "prior_asset_state": payload.get("priorAssetState"),
        "resource_parent": resource.get("parent"),
        "asset_location": resource.get("location") or data.get("location"),
        "raw_asset_json": json.dumps(payload, sort_keys=True),
    }


def infer_action(payload: dict[str, Any]) -> str:
    asset = payload.get("asset")
    prior_asset = payload.get("priorAsset")
    prior_state = payload.get("priorAssetState")
    if asset and prior_state in {"DOES_NOT_EXIST", "DOES_NOT_EXIST_STATE"}:
        return "created"
    if asset and prior_asset:
        return "updated"
    if prior_asset and not asset:
        return "deleted"
    if asset:
        return "observed"
    return "unknown"


def extract_project_id(asset: dict[str, Any]) -> str | None:
    resource_data = ((asset.get("resource") or {}).get("data") or {})
    for candidate in (
        resource_data.get("projectId"),
        resource_data.get("project_id"),
        resource_data.get("project"),
    ):
        if candidate:
            return str(candidate)
    name = asset.get("name") or ""
    match = PROJECT_RE.search(name)
    return match.group(1) if match else None


def extract_zone(asset: dict[str, Any], data: dict[str, Any]) -> str | None:
    for candidate in (data.get("zone"), data.get("location")):
        if isinstance(candidate, str) and looks_like_zone(candidate):
            return candidate.rsplit("/", 1)[-1]
    match = ZONE_RE.search(asset.get("name") or "")
    return match.group(1) if match else None


def extract_region(
    asset: dict[str, Any],
    data: dict[str, Any],
    zone: str | None,
) -> str | None:
    for candidate in (data.get("region"), data.get("location")):
        if isinstance(candidate, str):
            cleaned = candidate.rsplit("/", 1)[-1]
            if cleaned and not looks_like_zone(cleaned):
                return cleaned
    match = REGION_RE.search(asset.get("name") or "")
    if match:
        return match.group(1)
    if zone and looks_like_zone(zone):
        return "-".join(zone.split("-")[:-1])
    return None


def looks_like_zone(value: str) -> bool:
    parts = value.rsplit("/", 1)[-1].split("-")
    return len(parts) >= 3 and len(parts[-1]) == 1 and parts[-1].isalpha()

