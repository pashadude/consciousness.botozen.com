import asyncio
import json

from app.spec_state import SpecLedger, update_ledger_from_user_text
from app.trader_rag import (
    SearchEvidence,
    TraderSourceConfig,
    apply_rag_to_ledger,
    dedupe_evidence,
    default_opoint_mcp_command,
    evidence_relevance,
    exception_summary,
    execute_spanner_search,
    filter_evidence_by_query,
    filter_evidence_for_queries,
    load_trader_source_config,
    mcp_search_arguments,
    mcp_stdio_env,
    records_from_mcp_payload,
    run_async_mcp,
    run_trader_rag,
    spanner_row_is_self_memory,
    spanner_rows_to_evidence,
    spanner_scan_terms,
)

SULFUR_OFFER = (
    "Look i have an offer of 50000 tonns of sulfur in Iraq, "
    "Umm Qasr, fob 550, should i go for it?"
)


class FakeParamTypes:
    STRING = "STRING"
    INT64 = "INT64"


class FakeSnapshot:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute_sql(self, sql, params, param_types):
        self.database.sql = sql
        self.database.params = params
        self.database.param_types = param_types
        return []


class FakeDatabase:
    def __init__(self):
        self.sql = ""
        self.params = {}
        self.param_types = {}

    def snapshot(self):
        return FakeSnapshot(self)


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


def test_spanner_rag_search_mode_can_be_configured_from_env() -> None:
    config = load_trader_source_config(
        {
            "TRADER_RAG_PROVIDER": "spanner_rag",
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "SPANNER_RAG_INSTANCE_ID": "commodity-rag",
            "SPANNER_RAG_DATABASE_ID": "trader_rag",
            "SPANNER_RAG_SEARCH_MODE": "vector",
        }
    )

    assert config.spanner_search_mode == "vector"


def test_spanner_semantic_mode_uses_full_text_search_sql() -> None:
    database = FakeDatabase()

    execute_spanner_search(
        database=database,
        table="RagChunks",
        model="RagEmbeddingModel",
        query="sulfur Umm Qasr",
        limit=5,
        param_types=FakeParamTypes,
        mode="semantic",
    )

    assert "SEARCH(ChunkTokens, @query)" in database.sql
    assert "ML.PREDICT" not in database.sql
    assert "APPROX_COSINE_DISTANCE" not in database.sql


def test_spanner_vector_mode_uses_embedding_distance_sql() -> None:
    database = FakeDatabase()

    execute_spanner_search(
        database=database,
        table="RagChunks",
        model="RagEmbeddingModel",
        query="sulfur Umm Qasr",
        limit=5,
        param_types=FakeParamTypes,
        mode="vector",
    )

    assert "ML.PREDICT" in database.sql
    assert "RETRIEVAL_QUERY" in database.sql
    assert "APPROX_COSINE_DISTANCE" in database.sql
    assert "SEARCH(ChunkTokens, @query)" not in database.sql


def test_spanner_hybrid_mode_uses_text_and_vector_rrf_sql() -> None:
    database = FakeDatabase()

    execute_spanner_search(
        database=database,
        table="RagChunks",
        model="RagEmbeddingModel",
        query="sulfur Umm Qasr",
        limit=5,
        param_types=FakeParamTypes,
        mode="hybrid",
    )

    assert "ML.PREDICT" in database.sql
    assert "APPROX_COSINE_DISTANCE" in database.sql
    assert "SEARCH(ChunkTokens, @query)" in database.sql
    assert "rrf_score" in database.sql


def test_spanner_rag_excludes_console_user_talk_memory_from_evidence() -> None:
    config = TraderSourceConfig(
        provider="spanner_rag",
        google_agent_search_enabled=False,
        max_queries=3,
        max_results=5,
        timeout_seconds=6,
        google_cloud_project="zenpulsar",
        spanner_instance_id="commodity-rag",
        spanner_database_id="trader_rag",
        spanner_chunks_table="RagChunks",
    )
    self_memory_row = (
        "self-chunk",
        "self-doc",
        "mutual_spec_user_talks",
        "Title: Look i have an offer of 50000 tonns of sulfur in Iraq...",
        "2026-06-11T12:46:23Z",
        "console_user_talk_log",
        ["human_ai_coordination", "sulfur", "trader_workflow"],
        ["conversation_trace", "mutual_specification_game", "trader_agent"],
    )
    market_row = (
        "market-chunk",
        "market-doc",
        "commodity_articles",
        "Title: Middle East sulfur price benchmark\nSulfur market context near Umm Qasr.",
        "2026-06-10T00:00:00Z",
        "commodity_news",
        ["sulfur"],
        ["market_price", "benchmark"],
    )

    evidence = spanner_rows_to_evidence(
        [self_memory_row, market_row],
        query='"sulfur" "Umm Qasr" FOB price',
        config=config,
        limit=5,
    )

    assert spanner_row_is_self_memory(self_memory_row)
    assert not spanner_row_is_self_memory(market_row)
    assert [item.title for item in evidence] == ["Middle East sulfur price benchmark"]
    assert "console_user_talk_log" not in evidence[0].summary


def test_spanner_rag_filters_irrelevant_private_chunks() -> None:
    config = TraderSourceConfig(
        provider="spanner_rag",
        google_agent_search_enabled=False,
        max_queries=3,
        max_results=5,
        timeout_seconds=6,
        google_cloud_project="zenpulsar",
        spanner_instance_id="commodity-rag",
        spanner_database_id="trader_rag",
        spanner_chunks_table="RagChunks",
    )
    irrelevant_row = (
        "sugar-chunk",
        "sugar-doc",
        "commodity_articles",
        "Title: Thailand dry spell advisory\nSugarcane farmers advised to mulch fields.",
        "2026-06-10T00:00:00Z",
        "commodity_news",
        ["sugar"],
        ["weather", "agriculture"],
    )
    relevant_row = (
        "sulfur-chunk",
        "sulfur-doc",
        "commodity_articles",
        "Title: Iraq sulfur export checks\nSulfur cargoes and Umm Qasr loading inspections remain active.",
        "2026-06-10T00:00:00Z",
        "commodity_news",
        ["sulfur"],
        ["logistics", "inspection"],
    )

    evidence = spanner_rows_to_evidence(
        [irrelevant_row, relevant_row],
        query='"sulfur" "Umm Qasr" FOB price',
        config=config,
        limit=5,
    )

    assert [item.title for item in evidence] == ["Iraq sulfur export checks"]
    assert evidence[0].relevance_score >= 2.0
    assert "sulfur" in evidence[0].relevance_reasons


def test_spanner_rag_filters_irrelevant_chunks_for_ho_rb_spread() -> None:
    config = TraderSourceConfig(
        provider="spanner_rag",
        google_agent_search_enabled=False,
        max_queries=3,
        max_results=5,
        timeout_seconds=6,
        google_cloud_project="zenpulsar",
        spanner_instance_id="commodity-rag",
        spanner_database_id="trader_rag",
        spanner_chunks_table="RagChunks",
    )
    self_memory_row = (
        "talk-chunk",
        "talk-doc",
        "mutual_spec_user_talks",
        "Title: Look at HO/RB arb and give me risk on this spread.",
        "2026-06-11T00:00:00Z",
        "console_user_talk_log",
        ["human_ai_coordination", "sulfur", "trader_workflow"],
        ["conversation_trace", "mutual_specification_game", "trader_agent"],
    )
    sulfur_row = (
        "sulfur-chunk",
        "sulfur-doc",
        "commodity_articles",
        "Title: Iraq sulfur export checks\nSulfur cargoes and Umm Qasr loading inspections remain active.",
        "2026-06-10T00:00:00Z",
        "commodity_news",
        ["sulfur"],
        ["logistics", "inspection"],
    )
    spread_row = (
        "spread-chunk",
        "spread-doc",
        "commodity_articles",
        "Title: Heating oil RBOB spread risk\nRBOB gasoline and heating oil cracks moved on inventory data.",
        "2026-06-10T00:00:00Z",
        "commodity_news",
        ["heating_oil", "rbob", "gasoline"],
        ["spread", "inventory"],
    )

    evidence = spanner_rows_to_evidence(
        [self_memory_row, sulfur_row, spread_row],
        query='"HO/RB" "Heating Oil" RBOB spread price',
        config=config,
        limit=5,
    )

    assert [item.title for item in evidence] == ["Heating oil RBOB spread risk"]
    assert "console_user_talk_log" not in evidence[0].summary
    assert {"heating oil", "rbob"}.issubset(set(evidence[0].relevance_reasons))


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


def test_external_evidence_filter_drops_irrelevant_opoint_articles() -> None:
    query = '"sulfur" "Umm Qasr" FOB price'
    irrelevant = SearchEvidence(
        evidence_id="mcp:war",
        title="US-Israel-Iran War News LIVE",
        url="https://example.test/war",
        summary="Missile strikes and evacuation routes in West Asia.",
        query=query,
        source="opoint_mcp",
    )
    relevant = SearchEvidence(
        evidence_id="mcp:sulfur",
        title="Iraq seizes +100K tons of illegally stocked sulfur in Basra raid",
        url="https://example.test/sulfur",
        summary="Security forces seized sulfur stockpiles in Basra.",
        query=query,
        source="opoint_mcp",
    )

    filtered = filter_evidence_by_query([irrelevant, relevant], query)

    assert [item.evidence_id for item in filtered] == [relevant.evidence_id]
    assert filtered[0].relevance_score >= 2.0


def test_rag_relevance_eval_blocks_generic_and_unrelated_noise() -> None:
    query = '"sulfur" "Umm Qasr" FOB price'
    candidates = [
        SearchEvidence(
            evidence_id="mcp:generic",
            title="Commodity market price benchmark",
            url="https://example.test/generic",
            summary="Generic market commentary on risk, payment, and documents.",
            query=query,
            source="opoint_mcp",
        ),
        SearchEvidence(
            evidence_id="mcp:sugar",
            title="Thailand sugarcane drought advisory",
            url="https://example.test/sugar",
            summary="Dry spell conditions affect sugarcane crops.",
            query=query,
            source="opoint_mcp",
        ),
        SearchEvidence(
            evidence_id="mcp:sulfur",
            title="Umm Qasr sulfur loading inspection update",
            url="https://example.test/sulfur",
            summary="Iraq sulfur FOB cargoes require inspection documents.",
            query=query,
            source="opoint_mcp",
        ),
    ]

    relevant = filter_evidence_for_queries(candidates)

    assert [item.evidence_id for item in relevant] == ["mcp:sulfur"]
    score, reasons = evidence_relevance(relevant[0], query)
    assert score >= 6.0
    assert {"sulfur", "umm qasr", "fob"}.issubset(set(reasons))


def test_mcp_provider_returns_empty_when_only_irrelevant_results(monkeypatch) -> None:
    async def fake_fetch_mcp_search_records(query, *, config, limit):
        return [
            {
                "title": "Thailand sugarcane drought advisory",
                "url": "https://example.test/sugar",
                "summary": "Dry spell conditions affect sugarcane crops.",
                "source_name": "Opoint Test Wire",
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

    assert result.provider == "opoint_mcp"
    assert result.status == "empty"
    assert result.evidence == []
    assert any("none matched material query terms" in warning for warning in result.warnings)


def test_evidence_dedupe_collapses_mirror_urls_by_title() -> None:
    first = SearchEvidence(
        evidence_id="mcp:1",
        title="Iraq seizes +100K tons of illegally stocked sulfur in Basra raid",
        url="https://www.shafaq.com/en/Security/Iraq-seizes-100K-tons",
        summary="First mirror.",
        query="sulfur",
        source="opoint_mcp",
    )
    second = SearchEvidence(
        evidence_id="mcp:2",
        title="Iraq seizes +100K tons of illegally stocked sulfur in Basra raid",
        url="https://shafaq.com/en/Security/Iraq-seizes-100K-tons",
        summary="Second mirror.",
        query="sulfur",
        source="opoint_mcp",
    )

    assert dedupe_evidence([first, second]) == [first]


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


def test_run_async_mcp_inside_active_event_loop() -> None:
    async def value():
        return "ok"

    async def inside_loop():
        return run_async_mcp(value())

    assert asyncio.run(inside_loop()) == "ok"


def test_mcp_empty_result_wrapper_is_not_evidence() -> None:
    assert records_from_mcp_payload({"result": []}) == []
    assert records_from_mcp_payload({"results": []}) == []


def test_opoint_mcp_search_arguments_strip_boolean_quotes() -> None:
    args = mcp_search_arguments(
        "search_site_and_articles",
        query='"sulfur" "Umm Qasr" FOB price',
        limit=5,
    )

    assert args == {"search_text": "sulfur Umm Qasr FOB price", "num_articles": 5}


def test_default_opoint_mcp_command_uses_current_interpreter() -> None:
    command = default_opoint_mcp_command()

    assert command.endswith(" -m opoint_mcp.server")
    assert "python" in command


def test_exception_summary_expands_exception_group() -> None:
    summary = exception_summary(
        ExceptionGroup("mcp failed", [RuntimeError("child import failed")])
    )

    assert summary == "RuntimeError: child import failed"


def test_mcp_stdio_env_passes_opoint_key_to_child_process(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    config = TraderSourceConfig(
        provider="mcp",
        google_agent_search_enabled=False,
        max_queries=3,
        max_results=5,
        timeout_seconds=6,
        google_cloud_project="zenpulsar",
        google_cloud_location="us-central1",
        opoint_api_key="secret-key",
    )

    env = mcp_stdio_env(config)

    assert env["OPOINT_API_KEY"] == "secret-key"
    assert env["GOOGLE_CLOUD_PROJECT"] == "zenpulsar"
    assert env["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert env["PATH"] == "/usr/bin"


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
