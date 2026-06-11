import json

from app.spec_state import SpecLedger, update_ledger_from_user_text
from app.trader_rag import (
    apply_rag_to_ledger,
    load_trader_source_config,
    run_trader_rag,
)

SULFUR_OFFER = (
    "Look i have an offer of 50000 tonns of sulfur in Iraq, "
    "Umm Qasr, fob 550, should i go for it?"
)


def test_disabled_trader_rag_keeps_search_plan_without_evidence() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={"TRADER_RAG_PROVIDER": "disabled"},
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, result)

    assert result.status == "planned"
    assert result.queries
    assert any("Umm Qasr" in query for query in result.queries)
    assert not result.evidence
    assert any("large context" in warning for warning in result.warnings)
    assert any(source.source_type == "rag" and source.status == "planned" for source in ledger.evidence_sources)


def test_fixture_trader_rag_adds_cited_evidence_to_ledger(tmp_path) -> None:
    fixture = tmp_path / "sulfur_results.json"
    fixture.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "title": "Sulfur market benchmark",
                        "url": "https://example.test/sulfur-benchmark",
                        "snippet": "Regional sulfur pricing context for FOB offers.",
                    }
                ]
            }
        )
    )
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "fixture",
            "TRADER_RAG_FIXTURE_PATH": str(fixture),
        },
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, result)

    assert result.status == "retrieved"
    assert ledger.evidence
    assert any(item.source_type == "rag" for item in ledger.evidence)
    assert any("Sulfur market benchmark" == item.title for item in ledger.evidence)
    assert any(item.status == "satisfied" for item in ledger.search_plan)


def test_google_agent_search_provider_records_workflow_handoff() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "google_agent_search",
            "GOOGLE_AGENT_SEARCH_ENABLED": "true",
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, result)

    assert result.provider == "google_agent_search"
    assert result.status == "configured"
    assert any(source.source_type == "google_agent_search" for source in ledger.evidence_sources)


def test_spanner_rag_provider_records_private_corpus_target() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "spanner_rag",
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "SPANNER_RAG_INSTANCE_ID": "commodity-rag",
            "SPANNER_RAG_DATABASE_ID": "trader_rag",
        },
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, result)

    assert result.provider == "spanner_rag"
    assert result.status == "configured"
    assert any(source.source_type == "spanner_rag" for source in ledger.evidence_sources)
    assert any("Spanner" in warning for warning in result.warnings)


def test_yaml_source_layer_defaults_can_enable_google_agent_search(tmp_path) -> None:
    config_path = tmp_path / "source.yaml"
    config_path.write_text(
        "\n".join(
            [
                "default_provider: google_agent_search",
                "google_agent_search:",
                "  enabled: true",
                "limits:",
                "  max_queries: 2",
                "  max_results: 4",
                "  timeout_seconds: 7",
            ]
        )
    )

    config = load_trader_source_config(
        {
            "TRADER_SOURCE_LAYER_CONFIG": str(config_path),
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
    )

    assert config.provider == "google_agent_search"
    assert config.google_agent_search_enabled
    assert config.google_auth_configured
    assert config.max_queries == 2
    assert config.max_results == 4
    assert config.timeout_seconds == 7
