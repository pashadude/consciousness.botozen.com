from datetime import UTC, datetime

from app.telemetry.asset_consumer import infer_action, normalize_asset_event
from app.telemetry.config import config_from_env
from app.telemetry.domination import (
    DominationEpsilons,
    RouteCandidate,
    dominates,
    nondominated_candidates,
    route_loss,
)
from app.telemetry.power_prices import build_static_power_price_rows


def test_config_from_env_matches_all_resource_feed() -> None:
    config = config_from_env(
        {
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "TELEMETRY_DATASET_ID": "telemetry",
            "RESOURCE_TELEMETRY_COLLECTORS_ENABLED": "true",
            "GCP_ASSET_CHANGES_SUBSCRIPTION": "gcp-all-resource-changes-sub",
        }
    )

    assert config.project_id == "zenpulsar"
    assert config.collectors_enabled
    assert (
        config.asset_subscription_path
        == "projects/zenpulsar/subscriptions/gcp-all-resource-changes-sub"
    )


def test_normalize_asset_event_for_billing_export_table() -> None:
    payload = {
        "asset": {
            "assetType": "bigquery.googleapis.com/Table",
            "name": "//bigquery.googleapis.com/projects/zenpulsar/datasets/telemetry/tables/gcp_billing_export_v1_ABC",
            "resource": {
                "location": "US",
                "parent": "//bigquery.googleapis.com/projects/zenpulsar/datasets/telemetry",
                "data": {
                    "location": "US",
                    "tableReference": {
                        "projectId": "zenpulsar",
                        "datasetId": "telemetry",
                        "tableId": "gcp_billing_export_v1_ABC",
                    },
                },
            },
        },
        "priorAsset": {"assetType": "bigquery.googleapis.com/Table"},
        "priorAssetState": "PRESENT",
        "window": {"startTime": "2026-06-10T11:10:43.436601Z"},
    }

    row = normalize_asset_event(
        payload,
        received_at=datetime(2026, 6, 10, 11, 11, tzinfo=UTC),
    )

    assert row["action"] == "updated"
    assert row["project_id"] == "zenpulsar"
    assert row["asset_type"] == "bigquery.googleapis.com/Table"
    assert row["asset_location"] == "US"
    assert "gcp_billing_export_v1_ABC" in row["raw_asset_json"]


def test_infer_action_created_deleted_updated() -> None:
    assert infer_action({"asset": {}, "priorAssetState": "DOES_NOT_EXIST"}) == "unknown"
    assert (
        infer_action({"asset": {"name": "x"}, "priorAssetState": "DOES_NOT_EXIST"})
        == "created"
    )
    assert infer_action({"asset": {"name": "x"}, "priorAsset": {"name": "x"}}) == "updated"
    assert infer_action({"priorAsset": {"name": "x"}}) == "deleted"


def test_static_power_price_rows_from_region_map() -> None:
    rows = build_static_power_price_rows(
        {
            "defaults": {"confidence": "low"},
            "regions": {
                "us-south1": {
                    "power_proxy": "ERCOT North Hub",
                    "confidence": "medium",
                }
            },
        },
        hours=2,
        price_usd_per_mwh=42.0,
        now=datetime(2026, 6, 10, 11, 35, tzinfo=UTC),
    )

    assert len(rows) == 2
    assert rows[0]["hour"] == "2026-06-10T11:00:00+00:00"
    assert rows[0]["google_region"] == "us-south1"
    assert rows[0]["price_usd_per_mwh"] == 42.0
    assert rows[0]["confidence"] == "medium"


def test_pareto_domination_keeps_nondominated_frontier() -> None:
    cheap_iowa = RouteCandidate(
        model="gemini-3.5-flash",
        region="us-central1",
        policy_allowed=True,
        model_quality_loss=0.2,
        latency_loss=100,
        all_resource_cost_loss=0.05,
        compute_electricity_spread_loss=0.05,
        carbon_context_loss=0.2,
    )
    better_dallas = RouteCandidate(
        model="gemini-3.5-flash",
        region="us-south1",
        policy_allowed=True,
        model_quality_loss=0.2,
        latency_loss=90,
        all_resource_cost_loss=0.04,
        compute_electricity_spread_loss=0.02,
        carbon_context_loss=0.1,
    )
    expensive_fast = RouteCandidate(
        model="gemini-3.5-flash",
        region="us-east4",
        policy_allowed=True,
        model_quality_loss=0.2,
        latency_loss=0,
        all_resource_cost_loss=200.0,
        compute_electricity_spread_loss=0.10,
        carbon_context_loss=0.1,
    )

    epsilons = DominationEpsilons(latency_loss=0)

    assert dominates(better_dallas, cheap_iowa, epsilons=epsilons)
    frontier = nondominated_candidates(
        [cheap_iowa, better_dallas, expensive_fast],
        epsilons=epsilons,
    )

    assert [candidate.region for candidate in frontier] == ["us-south1", "us-east4"]
    assert route_loss(better_dallas) < route_loss(expensive_fast)
