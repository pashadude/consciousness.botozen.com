import json

from app.spec_state import SpecLedger, update_ledger_from_user_text
from app.trader_rag import (
    SearchEvidence,
    apply_rag_to_ledger,
    load_trader_source_config,
    run_trader_rag,
    spanner_scan_terms,
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


def test_mutual_spec_goal_builds_trader_source_plan() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "/goal Mutual Specification Game for traders should minimize latent intent difference.",
    )

    assert ledger.search_plan
    assert any("Mutual Specification Game" in item.query for item in ledger.search_plan)
    assert any("user-talk ledgers" in item for item in ledger.verification_conditions)


def test_spanner_scan_terms_preserve_phrases_and_drop_generic_terms() -> None:
    terms = spanner_scan_terms('"Mutual Specification Game" trader latent task')

    assert "mutual specification game" in terms
    assert "trader" in terms
    assert "task" not in terms


def test_spanner_rag_provider_retrieves_private_corpus(monkeypatch) -> None:
    def fake_query_spanner_chunks(query, *, config, limit):
        return [
            SearchEvidence(
                evidence_id="spanner:test",
                title="Umm Qasr sulfur offer trace",
                url="spanner://zenpulsar/commodity-rag/trader_rag/RagChunks/doc/chunk",
                summary="Private corpus note on sulfur, FOB, Iraq, and trader verification.",
                query=query,
                source="spanner_rag",
            )
        ]

    monkeypatch.setattr("app.trader_rag.query_spanner_chunks", fake_query_spanner_chunks)
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
    assert result.status == "retrieved"
    assert result.evidence
    assert any(source.source_type == "spanner_rag" for source in ledger.evidence_sources)
    assert any("Umm Qasr sulfur offer trace" == item.title for item in ledger.evidence)


def test_mcp_opoint_provider_retrieves_live_source_shape(monkeypatch) -> None:
    async def fake_fetch_mcp_search_records(query, *, config, limit):
        return [
            {
                "title": "Iraq sulfur export fixture",
                "url": "https://example.test/iraq-sulfur",
                "summary": "Opoint article body about sulfur cargoes and Umm Qasr.",
                "source_name": "Opoint Test Wire",
                "published_date": "2026-06-10",
            }
        ]

    monkeypatch.setattr(
        "app.trader_rag.fetch_mcp_search_records",
        fake_fetch_mcp_search_records,
    )
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "mcp",
            "OPOINT_API_KEY": "test-key",
            "TRADER_RAG_MAX_RESULTS": "2",
        },
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, result)

    assert result.provider == "opoint_mcp"
    assert result.status == "retrieved"
    assert result.evidence[0].source == "Opoint Test Wire"
    assert any(item.source_type == "rag" for item in ledger.evidence)
    assert any("Iraq sulfur export fixture" == item.title for item in ledger.evidence)


def test_mcp_opoint_provider_requires_key_for_vendored_server() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "mcp",
            "MCP_RESEARCH_COMMAND": "python -m opoint_mcp.server",
        },
        search_plan=ledger.search_plan,
    )

    assert result.provider == "opoint_mcp"
    assert result.status == "missing_config"
    assert any("OPOINT_API_KEY" in warning for warning in result.warnings)


def test_joined_spanner_and_mcp_provider_merges_evidence(monkeypatch) -> None:
    def fake_query_spanner_chunks(query, *, config, limit):
        return [
            SearchEvidence(
                evidence_id="spanner:test",
                title="Private sulfur note",
                url="spanner://doc/chunk",
                summary="Internal commodity corpus sulfur note.",
                query=query,
                source="spanner_rag",
            )
        ]

    async def fake_fetch_mcp_search_records(query, *, config, limit):
        return [
            {
                "title": "External sulfur article",
                "url": "https://example.test/external-sulfur",
                "summary": "External news context from Opoint.",
                "provider": "opoint",
            }
        ]

    monkeypatch.setattr("app.trader_rag.query_spanner_chunks", fake_query_spanner_chunks)
    monkeypatch.setattr(
        "app.trader_rag.fetch_mcp_search_records",
        fake_fetch_mcp_search_records,
    )
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, SULFUR_OFFER)

    result = run_trader_rag(
        SULFUR_OFFER,
        env={
            "TRADER_RAG_PROVIDER": "spanner_rag,mcp",
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "SPANNER_RAG_INSTANCE_ID": "commodity-rag",
            "SPANNER_RAG_DATABASE_ID": "trader_rag",
            "OPOINT_API_KEY": "test-key",
            "TRADER_RAG_MAX_RESULTS": "5",
        },
        search_plan=ledger.search_plan,
    )

    assert result.provider == "spanner_rag+opoint_mcp"
    assert result.status == "retrieved"
    assert {item.source for item in result.evidence} == {"spanner_rag", "opoint_mcp"}


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
