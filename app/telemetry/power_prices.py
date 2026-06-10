"""Regional electricity proxy rows for resource-region routing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.telemetry.bigquery_io import insert_rows
from app.telemetry.config import TelemetryConfig, config_from_env


def seed_static_power_prices(
    *,
    config: TelemetryConfig | None = None,
    hours: int = 1,
    price_usd_per_mwh: float | None = None,
    write_bigquery: bool = True,
) -> list[dict[str, Any]]:
    """Seed region_power_prices from the YAML region map.

    This is a proxy bootstrap, not a market-data feed. Replace it with
    GridStatus/EIA/ENTSO-E/Nord Pool providers as credentials become available.
    """

    config = config or config_from_env()
    price = price_usd_per_mwh or config.power_price_usd_per_mwh
    region_map = load_region_power_map(config.region_power_map_path)
    rows = build_static_power_price_rows(
        region_map,
        hours=hours,
        price_usd_per_mwh=price,
    )
    if rows and write_bigquery:
        insert_rows(config.power_prices_table_id, rows, config=config)
    return rows


def load_region_power_map(path: str | Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(Path(path).read_text()) or {}


def build_static_power_price_rows(
    region_map: dict[str, Any],
    *,
    hours: int,
    price_usd_per_mwh: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    regions = region_map.get("regions") or {}
    rows: list[dict[str, Any]] = []
    for offset in range(max(1, hours)):
        row_hour = hour - timedelta(hours=offset)
        for google_region, mapping in regions.items():
            rows.append(
                {
                    "hour": row_hour.isoformat(),
                    "google_region": google_region,
                    "power_market": mapping.get("power_proxy"),
                    "node_or_zone": mapping.get("node_or_zone")
                    or mapping.get("power_proxy"),
                    "price_usd_per_mwh": float(price_usd_per_mwh),
                    "source": "static_region_power_map",
                    "confidence": mapping.get("confidence")
                    or (region_map.get("defaults") or {}).get("confidence")
                    or "low",
                    "raw_source_json": json.dumps(mapping, sort_keys=True),
                    "updated_at": now.isoformat(),
                }
            )
    return rows

