"""CLI for live resource-region telemetry collectors."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from app.telemetry.asset_consumer import pull_asset_events
from app.telemetry.bigquery_io import ensure_dataset_and_tables, install_views
from app.telemetry.config import TelemetryConfig, config_from_env
from app.telemetry.monitoring_poller import poll_compute_metrics
from app.telemetry.power_prices import seed_static_power_prices


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage resource-region telemetry collectors."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show telemetry environment status.")
    subparsers.add_parser("init-tables", help="Create BigQuery telemetry tables.")

    pull_parser = subparsers.add_parser(
        "pull-assets",
        help="Pull Cloud Asset Inventory Pub/Sub events into BigQuery.",
    )
    pull_parser.add_argument("--limit", type=int, default=10)
    pull_parser.add_argument("--no-ack", action="store_true")
    pull_parser.add_argument("--no-insert", action="store_true")

    monitoring_parser = subparsers.add_parser(
        "poll-monitoring",
        help="Poll Cloud Monitoring compute metrics into BigQuery.",
    )
    monitoring_parser.add_argument("--minutes", type=int, default=None)
    monitoring_parser.add_argument("--no-insert", action="store_true")

    power_parser = subparsers.add_parser(
        "seed-power-prices",
        help="Seed static regional power proxy rows into BigQuery.",
    )
    power_parser.add_argument("--hours", type=int, default=1)
    power_parser.add_argument("--price-usd-per-mwh", type=float, default=None)
    power_parser.add_argument("--no-insert", action="store_true")

    views_parser = subparsers.add_parser(
        "install-views",
        help="Install billing and resource-region criteria BigQuery views.",
    )
    views_parser.add_argument("--billing-table-pattern", default=None)

    args = parser.parse_args(argv)
    config = config_from_env()

    if args.command == "status":
        print(render_status(config))
        return 0
    if args.command == "init-tables":
        created = ensure_dataset_and_tables(config)
        print(json.dumps({"created_or_existing": created}, indent=2))
        return 0
    if args.command == "pull-assets":
        rows = pull_asset_events(
            config=config,
            limit=args.limit,
            ack=not args.no_ack,
            write_bigquery=not args.no_insert,
        )
        print(json.dumps({"rows": len(rows), "sample": rows[:1]}, indent=2))
        return 0
    if args.command == "poll-monitoring":
        rows = poll_compute_metrics(
            config=config,
            minutes=args.minutes,
            write_bigquery=not args.no_insert,
        )
        print(json.dumps({"rows": len(rows), "sample": rows[:1]}, indent=2))
        return 0
    if args.command == "seed-power-prices":
        rows = seed_static_power_prices(
            config=config,
            hours=args.hours,
            price_usd_per_mwh=args.price_usd_per_mwh,
            write_bigquery=not args.no_insert,
        )
        print(json.dumps({"rows": len(rows), "sample": rows[:1]}, indent=2))
        return 0
    if args.command == "install-views":
        installed = install_views(
            config=config,
            billing_table_pattern=args.billing_table_pattern,
        )
        print(json.dumps({"installed": installed}, indent=2))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


def render_status(config: TelemetryConfig) -> str:
    items = {
        "project_id": config.project_id,
        "dataset_id": config.dataset_id,
        "location": config.location,
        "collectors_enabled": config.collectors_enabled,
        "asset_topic_id": config.asset_topic_id,
        "asset_subscription_path": config.asset_subscription_path,
        "asset_table": config.asset_table_id,
        "compute_metrics_table": config.compute_metrics_table_id,
        "power_prices_table": config.power_prices_table_id,
        "billing_export_table_pattern": config.billing_export_table_pattern,
        "region_power_map_path": config.region_power_map_path.as_posix(),
    }
    return "\n".join(f"{key}: {value}" for key, value in items.items())


if __name__ == "__main__":
    raise SystemExit(main())

