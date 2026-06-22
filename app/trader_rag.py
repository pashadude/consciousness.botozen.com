"""Evidence-first RAG for trader and physical commodity decision frames."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from app.spec_state import (
    EvidenceSourceStatus,
    SearchPlanItem,
    SpecLedger,
    add_external_evidence,
    add_unique,
    looks_like_trader_query,
    record_evidence_source,
    trader_required_evidence,
    trader_search_queries,
)

DEFAULT_SOURCE_LAYER_CONFIG = Path("config/trader_source_layer.yaml")
DEFAULT_MIN_RELEVANCE_SCORE = 2.0
EXCLUDED_SPANNER_EVIDENCE_SOURCES = {
    "console_human_review_log",
    "console_user_talk_log",
}
EXCLUDED_SPANNER_SOURCE_DATASETS = {"mutual_spec_user_talks"}
EXCLUDED_SPANNER_TAGS = {
    "conversation_trace",
    "human_review",
    "mutual_specification_game",
    "proof_carrying_response",
    "specification_ledger",
    "trader_agent",
    "user_talk",
}
GENERIC_RELEVANCE_STOP_TERMS = {
    "a",
    "about",
    "an",
    "and",
    "benchmark",
    "cargo",
    "commodity",
    "context",
    "counterparty",
    "documents",
    "export",
    "for",
    "freight",
    "from",
    "give",
    "inspection",
    "into",
    "logistics",
    "look",
    "market",
    "offer",
    "payment",
    "please",
    "price",
    "risk",
    "show",
    "shipping",
    "should",
    "terms",
    "that",
    "this",
    "tonnes",
    "tons",
    "trade",
    "trader",
    "verify",
    "verification",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "why",
}
@dataclass(frozen=True)
class SearchEvidence:
    evidence_id: str
    title: str
    url: str
    summary: str
    query: str
    source: str
    confidence: str = "medium"
    relevance_score: float = 0.0
    relevance_reasons: tuple[str, ...] = ()
    provider_score: float | None = None


@dataclass(frozen=True)
class TraderRagResult:
    provider: str
    status: str
    queries: list[str]
    required_evidence: list[str]
    evidence: list[SearchEvidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_live_evidence(self) -> bool:
        return bool(self.evidence)


@dataclass(frozen=True)
class QueryRelevanceProfile:
    """Query-derived lexical guard used after Spanner/provider ranking.

    The source provider should do the heavy retrieval work. This profile only
    blocks obvious mismatches by extracting material anchors from the query
    itself, instead of relying on a fixed commodity vocabulary.
    """

    query: str
    terms: tuple[str, ...]
    material_terms: tuple[str, ...]


@dataclass(frozen=True)
class TraderSourceConfig:
    provider: str
    google_agent_search_enabled: bool
    max_queries: int
    max_results: int
    timeout_seconds: float
    min_relevance_score: float = DEFAULT_MIN_RELEVANCE_SCORE
    google_search_api_key: str | None = None
    google_search_cx: str | None = None
    spanner_instance_id: str | None = None
    spanner_database_id: str | None = None
    spanner_documents_table: str | None = None
    spanner_chunks_table: str | None = None
    spanner_embedding_model: str | None = None
    spanner_search_mode: str = "hybrid"
    vertex_ai_search_data_store_id: str | None = None
    vertex_ai_search_engine_id: str | None = None
    mcp_research_url: str | None = None
    mcp_research_command: str | None = None
    mcp_research_cwd: str | None = None
    opoint_api_key: str | None = None
    fixture_path: str | None = None
    source_layer_config_path: str | None = None
    google_genai_use_vertexai: bool = False
    google_cloud_project: str | None = None
    google_cloud_location: str | None = None
    gemini_api_key: str | None = None
    google_api_key: str | None = None

    @property
    def google_auth_configured(self) -> bool:
        return bool(
            self.google_search_api_key
            or (
                self.google_genai_use_vertexai
                and self.google_cloud_project
                and self.google_cloud_location
            )
            or self.gemini_api_key
            or self.google_api_key
        )

    @property
    def spanner_configured(self) -> bool:
        return bool(
            self.google_cloud_project
            and self.spanner_instance_id
            and self.spanner_database_id
        )


def load_trader_source_config(env: Mapping[str, str] | None = None) -> TraderSourceConfig:
    env = env or os.environ
    yaml_config = load_source_layer_yaml(env)
    provider = configured_value(env.get("TRADER_RAG_PROVIDER")) or str(
        yaml_config.get("default_provider") or "disabled"
    )
    google_agent_cfg = nested_config(yaml_config, "google_agent_search")
    google_cse_cfg = nested_config(yaml_config, "google_cse")
    spanner_cfg = nested_config(yaml_config, "spanner_rag")
    vertex_cfg = nested_config(yaml_config, "vertex_ai_search")
    mcp_cfg = nested_config(yaml_config, "mcp")
    fixture_cfg = nested_config(yaml_config, "fixture")
    limits_cfg = nested_config(yaml_config, "limits")
    opoint_api_key = value_from_env_or_yaml(
        env,
        env_name="OPOINT_API_KEY",
        config=mcp_cfg,
        value_key="opoint_api_key",
        env_key="opoint_api_key_env",
    )
    mcp_research_command = value_from_env_or_yaml(
        env,
        env_name="MCP_RESEARCH_COMMAND",
        config=mcp_cfg,
        value_key="opoint_command",
        env_key="opoint_command_env",
    )
    if not mcp_research_command and opoint_api_key:
        mcp_research_command = default_opoint_mcp_command()
    return TraderSourceConfig(
        provider=provider.strip().lower(),
        google_agent_search_enabled=parse_bool(
            env.get("GOOGLE_AGENT_SEARCH_ENABLED"),
            parse_bool_value(google_agent_cfg.get("enabled"), False),
        ),
        max_queries=parse_int(
            env.get("TRADER_RAG_MAX_QUERIES"),
            parse_int_value(limits_cfg.get("max_queries"), 3),
        ),
        max_results=parse_int(
            env.get("TRADER_RAG_MAX_RESULTS"),
            parse_int_value(limits_cfg.get("max_results"), 5),
        ),
        timeout_seconds=parse_float(
            env.get("TRADER_RAG_TIMEOUT_SECONDS"),
            parse_float_value(limits_cfg.get("timeout_seconds"), 6.0),
        ),
        min_relevance_score=parse_float(
            env.get("TRADER_RAG_MIN_RELEVANCE_SCORE"),
            parse_float_value(
                limits_cfg.get("min_relevance_score"),
                DEFAULT_MIN_RELEVANCE_SCORE,
            ),
        ),
        google_search_api_key=value_from_env_or_yaml(
            env,
            env_name="GOOGLE_SEARCH_API_KEY",
            config=google_cse_cfg,
            value_key="api_key",
            env_key="api_key_env",
        ),
        google_search_cx=value_from_env_or_yaml(
            env,
            env_name="GOOGLE_SEARCH_CX",
            config=google_cse_cfg,
            value_key="cx",
            env_key="cx_env",
        ),
        spanner_instance_id=value_from_env_or_yaml(
            env,
            env_name="SPANNER_RAG_INSTANCE_ID",
            config=spanner_cfg,
            value_key="instance_id",
            env_key="instance_id_env",
        ),
        spanner_database_id=value_from_env_or_yaml(
            env,
            env_name="SPANNER_RAG_DATABASE_ID",
            config=spanner_cfg,
            value_key="database_id",
            env_key="database_id_env",
        ),
        spanner_documents_table=value_from_env_or_yaml(
            env,
            env_name="SPANNER_RAG_DOCUMENTS_TABLE",
            config=spanner_cfg,
            value_key="documents_table",
            env_key="documents_table_env",
        )
        or "RagDocuments",
        spanner_chunks_table=value_from_env_or_yaml(
            env,
            env_name="SPANNER_RAG_CHUNKS_TABLE",
            config=spanner_cfg,
            value_key="chunks_table",
            env_key="chunks_table_env",
        )
        or "RagChunks",
        spanner_embedding_model=value_from_env_or_yaml(
            env,
            env_name="SPANNER_RAG_EMBEDDING_MODEL",
            config=spanner_cfg,
            value_key="embedding_model",
            env_key="embedding_model_env",
        )
        or "RagEmbeddingModel",
        spanner_search_mode=(
            configured_value(env.get("SPANNER_RAG_SEARCH_MODE"))
            or configured_value(str(spanner_cfg.get("search_mode") or ""))
            or "hybrid"
        ).lower(),
        vertex_ai_search_data_store_id=value_from_env_or_yaml(
            env,
            env_name="VERTEX_AI_SEARCH_DATA_STORE_ID",
            config=vertex_cfg,
            value_key="data_store_id",
            env_key="data_store_id_env",
        ),
        vertex_ai_search_engine_id=value_from_env_or_yaml(
            env,
            env_name="VERTEX_AI_SEARCH_ENGINE_ID",
            config=vertex_cfg,
            value_key="search_engine_id",
            env_key="search_engine_id_env",
        ),
        mcp_research_url=value_from_env_or_yaml(
            env,
            env_name="MCP_RESEARCH_URL",
            config=mcp_cfg,
            value_key="research_url",
            env_key="research_url_env",
        ),
        mcp_research_command=mcp_research_command,
        mcp_research_cwd=value_from_env_or_yaml(
            env,
            env_name="MCP_RESEARCH_CWD",
            config=mcp_cfg,
            value_key="opoint_cwd",
            env_key="opoint_cwd_env",
        ),
        opoint_api_key=opoint_api_key,
        fixture_path=value_from_env_or_yaml(
            env,
            env_name="TRADER_RAG_FIXTURE_PATH",
            config=fixture_cfg,
            value_key="path",
            env_key="path_env",
        ),
        source_layer_config_path=configured_value(
            env.get("TRADER_SOURCE_LAYER_CONFIG")
        )
        or str(DEFAULT_SOURCE_LAYER_CONFIG),
        google_genai_use_vertexai=parse_bool(
            env.get("GOOGLE_GENAI_USE_VERTEXAI"),
            False,
        ),
        google_cloud_project=configured_value(env.get("GOOGLE_CLOUD_PROJECT")),
        google_cloud_location=configured_value(env.get("GOOGLE_CLOUD_LOCATION")),
        gemini_api_key=configured_value(env.get("GEMINI_API_KEY")),
        google_api_key=configured_value(env.get("GOOGLE_API_KEY")),
    )


def load_source_layer_yaml(env: Mapping[str, str]) -> dict[str, Any]:
    path = Path(
        configured_value(env.get("TRADER_SOURCE_LAYER_CONFIG"))
        or DEFAULT_SOURCE_LAYER_CONFIG
    )
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def nested_config(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    return value if isinstance(value, Mapping) else {}


def value_from_env_or_yaml(
    env: Mapping[str, str],
    *,
    env_name: str,
    config: Mapping[str, Any],
    value_key: str,
    env_key: str,
) -> str | None:
    direct = configured_value(env.get(env_name))
    if direct:
        return direct
    configured_env_name = configured_value(str(config.get(env_key) or ""))
    if configured_env_name:
        env_value = configured_value(env.get(configured_env_name))
        if env_value:
            return env_value
    value = config.get(value_key)
    if isinstance(value, str):
        return configured_value(value)
    return None


def run_trader_rag(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
    search_plan: list[SearchPlanItem] | None = None,
) -> TraderRagResult:
    """Build a trader search plan and retrieve evidence when configured."""

    env = env or os.environ
    config = load_trader_source_config(env)
    queries, required_evidence = build_trader_search_plan(text, search_plan=search_plan)
    provider = config.provider
    if not queries:
        return TraderRagResult(
            provider=provider,
            status="not_applicable",
            queries=[],
            required_evidence=[],
            warnings=["No trader/commodity RAG trigger detected."],
        )
    if provider in {"", "disabled", "off", "none"}:
        return TraderRagResult(
            provider="disabled",
            status="planned",
            queries=queries,
            required_evidence=required_evidence,
            warnings=[
                "Search provider is not configured; showing evidence plan only.",
                "For high-stakes trader decisions, large context/model memory is not enough: cited retrieval evidence is required.",
            ],
        )
    provider_names = provider_sequence(provider)
    if len(provider_names) > 1:
        return run_multi_provider(
            provider_names,
            queries,
            required_evidence,
            config=config,
        )
    return run_single_provider(provider, queries, required_evidence, config=config)


def provider_sequence(provider: str) -> list[str]:
    if provider == "all":
        return ["spanner_rag", "mcp", "google_agent_search"]
    parts = [item.strip().lower() for item in re.split(r"[,+]", provider) if item.strip()]
    return parts or [provider]


def run_multi_provider(
    providers: list[str],
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    workers = max(1, min(len(providers), 4))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                lambda provider: run_single_provider(
                    provider,
                    queries,
                    required_evidence,
                    config=config,
                ),
                providers,
            )
        )
    evidence = dedupe_evidence(
        filter_evidence_for_queries(
            [item for result in results for item in result.evidence],
            min_score=config.min_relevance_score,
        )
    )[: config.max_results]
    warnings = [
        f"{result.provider}: {warning}"
        for result in results
        for warning in result.warnings
    ]
    status = joined_provider_status(results, evidence)
    return TraderRagResult(
        provider="+".join(result.provider for result in results),
        status=status,
        queries=queries,
        required_evidence=required_evidence,
        evidence=evidence,
        warnings=warnings,
    )


def joined_provider_status(
    results: list[TraderRagResult],
    evidence: list[SearchEvidence],
) -> str:
    if evidence:
        return "retrieved"
    statuses = [result.status for result in results]
    if not statuses:
        return "empty"
    if all(status == "missing_config" for status in statuses):
        return "missing_config"
    if all(status in {"missing_config", "provider_error"} for status in statuses):
        return "provider_error"
    if any(status == "empty" for status in statuses):
        return "empty"
    if any(status == "configured" for status in statuses):
        return "configured"
    if any(status == "planned" for status in statuses):
        return "planned"
    if any(status == "provider_error" for status in statuses):
        return "provider_error"
    return statuses[0]


def run_single_provider(
    provider: str,
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    if provider in {"google_agent_search", "adk_google_search", "google_search_tool"}:
        return planned_google_agent_search(queries, required_evidence, config=config)
    if provider in {"spanner", "spanner_rag", "cloud_spanner"}:
        return run_spanner_provider(queries, required_evidence, config=config)
    if provider in {"vertex_ai_search", "enterprise_search"}:
        return planned_vertex_ai_search(queries, required_evidence, config=config)
    if provider in {"mcp", "opoint", "opoint_mcp"}:
        return run_mcp_provider(queries, required_evidence, config=config)
    if provider in {"model", "model_only"}:
        return model_only_hypothesis(queries, required_evidence, config=config)
    if provider == "fixture":
        return run_fixture_provider(queries, required_evidence, config=config)
    if provider in {"google_cse", "google", "programmable_search"}:
        return run_google_cse_provider(queries, required_evidence, config=config)
    return TraderRagResult(
        provider=provider,
        status="provider_error",
        queries=queries,
        required_evidence=required_evidence,
        warnings=[f"Unsupported TRADER_RAG_PROVIDER={provider}."],
    )


def build_trader_search_plan(
    text: str,
    *,
    search_plan: list[SearchPlanItem] | None = None,
) -> tuple[list[str], list[str]]:
    if search_plan:
        queries = [item.query for item in search_plan if item.required]
        required = [item.purpose for item in search_plan if item.required]
        return queries, required
    lower = text.lower()
    if not looks_like_trader_query(text):
        return [], []

    return trader_search_queries(lower), trader_required_evidence(lower)


def planned_google_agent_search(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    enabled = config.google_agent_search_enabled
    auth = config.google_auth_configured
    return TraderRagResult(
        provider="google_agent_search",
        status="configured" if enabled and auth else "planned",
        queries=queries,
        required_evidence=required_evidence,
        warnings=[
            "Google Agent SDK search is executed by the ADK workflow search agent; the console records the plan and source status.",
        ]
        if enabled and auth
        else [
            "Set GOOGLE_AGENT_SEARCH_ENABLED=true and Google auth/project env to let the ADK workflow call Google's native search tool.",
        ],
    )


def planned_vertex_ai_search(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    data_store = (
        config.vertex_ai_search_data_store_id or config.vertex_ai_search_engine_id
    )
    return TraderRagResult(
        provider="vertex_ai_search",
        status="configured" if data_store else "missing_config",
        queries=queries,
        required_evidence=required_evidence,
        warnings=[]
        if data_store
        else [
            "Vertex AI Search needs VERTEX_AI_SEARCH_DATA_STORE_ID or VERTEX_AI_SEARCH_ENGINE_ID.",
        ],
    )


def run_mcp_provider(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    provider_name = (
        "opoint_mcp"
        if (config.mcp_research_command or "").lower().find("opoint") >= 0
        else "mcp"
    )
    if not config.mcp_research_url and not config.mcp_research_command:
        return TraderRagResult(
            provider=provider_name,
            status="missing_config",
            queries=queries,
            required_evidence=required_evidence,
            warnings=[
                "MCP RAG needs MCP_RESEARCH_URL or MCP_RESEARCH_COMMAND. For vendored Opoint, set OPOINT_API_KEY and use MCP_RESEARCH_COMMAND='python -m opoint_mcp.server' locally or '/app/.venv/bin/python -m opoint_mcp.server' in Cloud Run.",
            ],
        )
    if (
        config.mcp_research_command
        and "opoint" in config.mcp_research_command.lower()
        and not config.opoint_api_key
    ):
        return TraderRagResult(
            provider=provider_name,
            status="missing_config",
            queries=queries,
            required_evidence=required_evidence,
            warnings=["Opoint MCP is configured but OPOINT_API_KEY is missing."],
        )
    evidence: list[SearchEvidence] = []
    warnings: list[str] = []
    errors: list[str] = []
    per_query_limit = max(1, min(config.max_results, 20))
    for query in queries[: config.max_queries]:
        try:
            records = run_async_mcp(
                fetch_mcp_search_records(query, config=config, limit=per_query_limit)
            )
        except Exception as exc:
            error = f"MCP search failed for `{query}`: {exception_summary(exc)}"
            warnings.append(error)
            errors.append(error)
            continue
        query_evidence = [
            normalize_mcp_record(record, query=query, index=index)
            for index, record in enumerate(records, start=1)
        ]
        relevant_evidence = filter_evidence_by_query(
            query_evidence,
            query,
            min_score=config.min_relevance_score,
        )
        if query_evidence and not relevant_evidence:
            warnings.append(
                f"MCP returned {len(query_evidence)} result(s) for `{query}`, but none matched material query terms."
            )
        evidence.extend(relevant_evidence)
        if evidence:
            break
    deduped = dedupe_evidence(evidence)[: config.max_results]
    return TraderRagResult(
        provider=provider_name,
        status="retrieved" if deduped else "provider_error" if errors else "empty",
        queries=queries,
        required_evidence=required_evidence,
        evidence=deduped,
        warnings=warnings or ([] if deduped else ["MCP returned no article evidence."]),
    )


def filter_evidence_by_query(
    evidence: list[SearchEvidence],
    query: str,
    *,
    min_score: float = DEFAULT_MIN_RELEVANCE_SCORE,
) -> list[SearchEvidence]:
    relevant: list[SearchEvidence] = []
    for item in evidence:
        score, reasons = evidence_relevance(item, query)
        if score >= min_score:
            relevant.append(
                replace(
                    item,
                    relevance_score=round(score, 4),
                    relevance_reasons=tuple(reasons),
                )
            )
    return relevant


def filter_evidence_for_queries(
    evidence: list[SearchEvidence],
    *,
    min_score: float = DEFAULT_MIN_RELEVANCE_SCORE,
) -> list[SearchEvidence]:
    return [
        item
        for query in sorted({item.query for item in evidence})
        for item in filter_evidence_by_query(
            [candidate for candidate in evidence if candidate.query == query],
            query,
            min_score=min_score,
        )
    ]


def search_evidence_matches_query(
    item: SearchEvidence,
    query: str,
    *,
    min_score: float = DEFAULT_MIN_RELEVANCE_SCORE,
) -> bool:
    score, _reasons = evidence_relevance(item, query)
    return score >= min_score


def evidence_relevance(item: SearchEvidence, query: str) -> tuple[float, list[str]]:
    profile = build_query_relevance_profile(query)
    if not profile.terms:
        return 0.0, []
    haystack = normalize_relevance_text(f"{item.title} {item.summary}")
    title_haystack = normalize_relevance_text(item.title)
    matched: list[str] = []
    score = 0.0
    for term in profile.terms:
        normalized_term = normalize_relevance_text(term)
        if not normalized_term or normalized_term not in haystack:
            continue
        matched.append(term)
        weight = evidence_term_weight(term, profile=profile)
        if normalized_term in title_haystack:
            weight += 0.25
        score += weight
    if not matched:
        return 0.0, []
    if profile.material_terms and not any(term in matched for term in profile.material_terms):
        return 0.0, matched
    if item.provider_score is not None:
        score += bounded_provider_score(item.provider_score)
    return score, matched


def evidence_relevance_terms(query: str) -> list[str]:
    return list(build_query_relevance_profile(query).terms)


def build_query_relevance_profile(query: str) -> QueryRelevanceProfile:
    phrases = re.findall(r'"([^"]{2,80})"', query)
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_/-]{1,}", query)
    terms: list[str] = []
    material: list[str] = []

    for value in phrases:
        normalized = " ".join(value.split()).strip(" .,:;")
        normalized = normalize_relevance_text(normalized)
        if not normalized or normalized in GENERIC_RELEVANCE_STOP_TERMS:
            continue
        add_relevance_term(terms, material, normalized, is_material=True)

    for raw in raw_tokens:
        normalized = normalize_relevance_text(raw)
        if not relevance_token_is_usable(normalized):
            continue
        is_material = query_token_is_material(raw)
        add_relevance_term(terms, material, normalized, is_material=is_material)

    if not material:
        for value in terms[:2]:
            add_relevance_term(terms, material, value, is_material=True)

    return QueryRelevanceProfile(
        query=query,
        terms=tuple(terms),
        material_terms=tuple(material),
    )


def add_relevance_term(
    terms: list[str],
    material: list[str],
    value: str,
    *,
    is_material: bool,
) -> None:
    if value not in terms:
        terms.append(value)
    if is_material and value not in material:
        material.append(value)


def relevance_token_is_usable(value: str) -> bool:
    if not value:
        return False
    if value in GENERIC_RELEVANCE_STOP_TERMS:
        return False
    if len(value) < 3 and not any(character.isdigit() for character in value):
        return False
    return True


def query_token_is_material(raw: str) -> bool:
    stripped = raw.strip()
    if not stripped:
        return False
    if "/" in stripped:
        return True
    if any(character.isdigit() for character in stripped):
        return True
    alpha = re.sub(r"[^A-Za-z]", "", stripped)
    if len(alpha) >= 2 and alpha.isupper():
        return True
    normalized = normalize_relevance_text(stripped)
    return len(normalized) >= 6 and normalized not in GENERIC_RELEVANCE_STOP_TERMS


def evidence_term_weight(term: str, *, profile: QueryRelevanceProfile | None = None) -> float:
    normalized = normalize_relevance_text(term)
    if profile and normalized in profile.material_terms:
        return 2.0
    if " " in normalized:
        return 2.0
    if any(character.isdigit() for character in normalized):
        return 1.5
    return 1.0


def bounded_provider_score(value: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score <= 0:
        return 0.0
    return min(score, 1.0)


def normalize_relevance_text(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = lowered.replace("sulphur", "sulfur").replace("aluminium", "aluminum")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def run_async_mcp(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: list[Any] = []
    errors: list[BaseException] = []

    def run_in_thread() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0] if result else None


def default_opoint_mcp_command() -> str:
    return f"{shlex.quote(sys.executable)} -m opoint_mcp.server"


def mcp_stdio_env(config: TraderSourceConfig) -> dict[str, str]:
    env = dict(os.environ)
    if config.opoint_api_key:
        env["OPOINT_API_KEY"] = config.opoint_api_key
    if config.google_cloud_project:
        env["GOOGLE_CLOUD_PROJECT"] = config.google_cloud_project
    if config.google_cloud_location:
        env["GOOGLE_CLOUD_LOCATION"] = config.google_cloud_location
    return env


def exception_summary(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        parts = [exception_summary(item) for item in exc.exceptions]
        return "; ".join(part for part in parts if part) or str(exc)
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__


async def fetch_mcp_search_records(
    query: str,
    *,
    config: TraderSourceConfig,
    limit: int,
) -> list[Mapping[str, Any]]:
    from mcp import StdioServerParameters
    from mcp.client.session import ClientSession
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    if config.mcp_research_url:
        async with streamablehttp_client(
            config.mcp_research_url,
            timeout=config.timeout_seconds,
            sse_read_timeout=max(config.timeout_seconds, 30),
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                return await call_mcp_search_tool(
                    session,
                    query=query,
                    limit=limit,
                    timeout_seconds=config.timeout_seconds,
                )

    if not config.mcp_research_command:
        return []
    parts = shlex.split(config.mcp_research_command)
    if not parts:
        return []
    server_params = StdioServerParameters(
        command=parts[0],
        args=parts[1:],
        env=mcp_stdio_env(config),
        cwd=config.mcp_research_cwd,
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            return await call_mcp_search_tool(
                session,
                query=query,
                limit=limit,
                timeout_seconds=config.timeout_seconds,
            )


async def call_mcp_search_tool(
    session: Any,
    *,
    query: str,
    limit: int,
    timeout_seconds: float,
) -> list[Mapping[str, Any]]:
    await session.initialize()
    tools_result = await session.list_tools()
    tool_name = choose_mcp_search_tool([tool.name for tool in tools_result.tools])
    if not tool_name:
        available = ", ".join(tool.name for tool in tools_result.tools) or "none"
        raise RuntimeError(f"No MCP search/article tool found. Available tools: {available}.")
    call_result = await session.call_tool(
        tool_name,
        arguments=mcp_search_arguments(tool_name, query=query, limit=limit),
        read_timeout_seconds=timedelta(seconds=max(timeout_seconds, 1.0)),
    )
    if getattr(call_result, "isError", False):
        raise RuntimeError(extract_mcp_error(call_result))
    return mcp_call_records(call_result)


def choose_mcp_search_tool(tool_names: list[str]) -> str | None:
    preferred = ["search_site_and_articles", "search_articles", "article_search"]
    for candidate in preferred:
        if candidate in tool_names:
            return candidate
    for name in tool_names:
        lowered = name.lower()
        if lowered == "search_site":
            continue
        if "article" in lowered and "search" in lowered:
            return name
    for name in tool_names:
        lowered = name.lower()
        if "search" in lowered and lowered != "search_site":
            return name
    return None


def mcp_search_arguments(tool_name: str, *, query: str, limit: int) -> dict[str, Any]:
    lowered = tool_name.lower()
    if "article" in lowered or "opoint" in lowered or lowered.startswith("search_"):
        return {"search_text": opoint_query_text(query), "num_articles": limit}
    return {"query": query, "limit": limit}


def opoint_query_text(query: str) -> str:
    cleaned = query.replace('"', " ")
    cleaned = re.sub(r"\b(AND|OR|NOT)\b", " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()) or query


def extract_mcp_error(call_result: Any) -> str:
    content = getattr(call_result, "content", []) or []
    texts = [str(getattr(item, "text", "")).strip() for item in content]
    return "; ".join(text for text in texts if text) or "MCP tool returned an error."


def mcp_call_records(call_result: Any) -> list[Mapping[str, Any]]:
    structured = getattr(call_result, "structuredContent", None)
    records = records_from_mcp_payload(structured)
    if records:
        return records
    for item in getattr(call_result, "content", []) or []:
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        try:
            records = records_from_mcp_payload(json.loads(text))
            if records:
                return records
        except json.JSONDecodeError:
            return [{"title": "MCP search result", "summary": text, "url": "mcp://result"}]
    return []


def records_from_mcp_payload(payload: Any) -> list[Mapping[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("results", "articles", "documents", "items", "data", "result"):
            if key in payload:
                return records_from_mcp_payload(payload.get(key))
        return [payload]
    return []


def normalize_mcp_record(
    raw: Mapping[str, Any],
    *,
    query: str,
    index: int,
) -> SearchEvidence:
    url = clean_mcp_value(
        raw.get("url")
        or raw.get("orig_url")
        or raw.get("link")
        or raw.get("uri")
        or raw.get("opoint_url")
        or raw.get("source_url")
    )
    title = clean_mcp_value(
        raw.get("title") or raw.get("header") or raw.get("name") or raw.get("id_article")
    ) or f"Opoint article {index}"
    source = clean_mcp_value(
        raw.get("source_name")
        or raw.get("site_name")
        or raw.get("source")
        or raw.get("provider")
    ) or "opoint"
    published = clean_mcp_value(
        raw.get("published_date")
        or raw.get("published_at")
        or raw.get("date")
        or raw.get("unix_timestamp")
    )
    body = clean_mcp_value(
        raw.get("summary")
        or raw.get("snippet")
        or raw.get("description")
        or raw.get("text")
    )
    context = [f"source={source}"]
    if published:
        context.append(f"published={published}")
    summary = "; ".join(context)
    if body:
        summary = f"{summary}\n{body}"
    uri = url or f"mcp://opoint/{stable_id(title + query)}"
    return SearchEvidence(
        evidence_id=f"mcp:{stable_id(uri + title)}",
        title=title[:180],
        url=uri,
        summary=summary[:500],
        query=query,
        source="opoint_mcp" if source == "opoint" else source,
        confidence="medium",
    )


def clean_mcp_value(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if text.lower() in {"", "nan", "nat", "none", "null"}:
        return ""
    return text


def run_spanner_provider(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    resource = (
        f"projects/{config.google_cloud_project}/instances/{config.spanner_instance_id}/"
        f"databases/{config.spanner_database_id}"
        if config.spanner_configured
        else ""
    )
    if not config.spanner_configured:
        return TraderRagResult(
            provider="spanner_rag",
            status="missing_config",
            queries=queries,
            required_evidence=required_evidence,
            warnings=[
            "Spanner RAG needs GOOGLE_CLOUD_PROJECT, SPANNER_RAG_INSTANCE_ID, and SPANNER_RAG_DATABASE_ID.",
            "Run scripts/prepare_spanner_rag_json.py first, then load documents.jsonl/chunks.jsonl into Spanner.",
            ],
        )
    evidence: list[SearchEvidence] = []
    warnings: list[str] = []
    errors: list[str] = []
    per_query_limit = max(1, min(config.max_results, 10))
    for query in queries[: config.max_queries]:
        try:
            query_evidence = query_spanner_chunks(
                query,
                config=config,
                limit=per_query_limit,
            )
            evidence.extend(query_evidence)
            if query_evidence:
                break
        except Exception as exc:
            error = f"Spanner RAG failed for `{query}`: {exc}"
            warnings.append(error)
            errors.append(error)
    deduped = dedupe_evidence(evidence)[: config.max_results]
    if not deduped and not warnings:
        warnings.append(
            f"Private corpus target {resource} returned no relevant market/news chunks after relevance filtering and excluding console discussion memory."
        )
    return TraderRagResult(
        provider="spanner_rag",
        status="retrieved" if deduped else "provider_error" if errors else "empty",
        queries=queries,
        required_evidence=required_evidence,
        evidence=deduped,
        warnings=warnings,
    )


def query_spanner_chunks(
    query: str,
    *,
    config: TraderSourceConfig,
    limit: int,
) -> list[SearchEvidence]:
    table = safe_spanner_identifier(config.spanner_chunks_table or "RagChunks")
    model = safe_spanner_identifier(config.spanner_embedding_model or "RagEmbeddingModel")
    search_limit = min(max(limit * 4, limit), 50)
    from google.cloud import spanner

    database = (
        spanner.Client(project=config.google_cloud_project)
        .instance(config.spanner_instance_id)
        .database(config.spanner_database_id)
    )
    try:
        rows = execute_spanner_search(
            database=database,
            table=table,
            model=model,
            query=query,
            limit=search_limit,
            param_types=spanner.param_types,
            mode=config.spanner_search_mode,
        )
    except Exception as search_exc:
        try:
            rows = execute_spanner_scan_fallback(
                database=database,
                table=table,
                query=query,
                limit=search_limit,
                param_types=spanner.param_types,
            )
        except Exception as fallback_exc:
            raise RuntimeError(
                f"full-text search failed ({search_exc}); fallback scan failed ({fallback_exc})"
            ) from fallback_exc
    return spanner_rows_to_evidence(rows, query=query, config=config, limit=limit)


def spanner_rows_to_evidence(
    rows: list[Any],
    *,
    query: str,
    config: TraderSourceConfig,
    limit: int,
) -> list[SearchEvidence]:
    evidence: list[SearchEvidence] = []
    for row in rows:
        if spanner_row_is_self_memory(row):
            continue
        candidate = spanner_row_to_evidence(row, query=query, config=config)
        relevant = filter_evidence_by_query(
            [candidate],
            query,
            min_score=config.min_relevance_score,
        )
        evidence.extend(relevant)
        if len(evidence) >= limit:
            break
    return evidence


def spanner_row_is_self_memory(row: Any) -> bool:
    try:
        _chunk_id, _doc_id, source_dataset, _chunk_text, _published_at, source, _commodities, tags, _provider_score = unpack_spanner_row(row)
    except (TypeError, ValueError):
        return False
    source_value = str(source or "").strip().lower()
    dataset_value = str(source_dataset or "").strip().lower()
    tag_values = {str(item or "").strip().lower() for item in (tags or [])}
    return (
        source_value in EXCLUDED_SPANNER_EVIDENCE_SOURCES
        or dataset_value in EXCLUDED_SPANNER_SOURCE_DATASETS
        or bool(tag_values & EXCLUDED_SPANNER_TAGS)
    )


def execute_spanner_search(
    *,
    database: Any,
    table: str,
    model: str,
    query: str,
    limit: int,
    param_types: Any,
    mode: str,
) -> list[Any]:
    mode = (mode or "hybrid").lower()
    if mode in {"semantic", "text", "fulltext", "full_text"}:
        return execute_spanner_semantic_search(
            database=database,
            table=table,
            query=query,
            limit=limit,
            param_types=param_types,
        )
    if mode in {"vector", "embedding", "ann"}:
        return execute_spanner_vector_search(
            database=database,
            table=table,
            model=model,
            query=query,
            limit=limit,
            param_types=param_types,
        )
    if mode in {"hybrid", "rrf"}:
        return execute_spanner_hybrid_search(
            database=database,
            table=table,
            model=model,
            query=query,
            limit=limit,
            param_types=param_types,
        )
    raise ValueError(f"Unsupported SPANNER_RAG_SEARCH_MODE={mode}")


def execute_spanner_semantic_search(
    *,
    database: Any,
    table: str,
    query: str,
    limit: int,
    param_types: Any,
) -> list[Any]:
    sql = f"""
        SELECT
          ChunkId,
          DocId,
          SourceDataset,
          ChunkText,
          PublishedAt,
          Source,
          Commodities,
          Tags,
          SCORE(ChunkTokens, @query) AS RetrievalScore
        FROM {table}
        WHERE SEARCH(ChunkTokens, @query)
        ORDER BY RetrievalScore DESC
        LIMIT @limit
    """
    with database.snapshot() as snapshot:
        return list(
            snapshot.execute_sql(
                sql,
                params={"query": query, "limit": limit},
                param_types={
                    "query": param_types.STRING,
                    "limit": param_types.INT64,
                },
            )
        )


def execute_spanner_vector_search(
    *,
    database: Any,
    table: str,
    model: str,
    query: str,
    limit: int,
    param_types: Any,
) -> list[Any]:
    sql = f"""
        WITH query_embedding AS (
          SELECT ARRAY(
            SELECT CAST(value AS FLOAT32)
            FROM UNNEST((
              SELECT embeddings.values
              FROM ML.PREDICT(
                MODEL {model},
                (SELECT @query AS content, "RETRIEVAL_QUERY" AS task_type),
                STRUCT(768 AS outputDimensionality)
              )
            )) AS value
          ) AS embedding
        ),
        ranked AS (
          SELECT
            ChunkId,
            DocId,
            SourceDataset,
            ChunkText,
            PublishedAt,
            Source,
            Commodities,
            Tags,
            APPROX_COSINE_DISTANCE(
              query_embedding.embedding,
              Embedding,
              OPTIONS => JSON '{{"num_leaves_to_search": 50}}'
            ) AS Distance
          FROM {table}, query_embedding
          WHERE Embedding IS NOT NULL
        )
        SELECT
          ChunkId,
          DocId,
          SourceDataset,
          ChunkText,
          PublishedAt,
          Source,
          Commodities,
          Tags,
          1.0 - Distance AS RetrievalScore
        FROM ranked
        ORDER BY Distance
        LIMIT @limit
    """
    with database.snapshot() as snapshot:
        return list(
            snapshot.execute_sql(
                sql,
                params={"query": query, "limit": limit},
                param_types={
                    "query": param_types.STRING,
                    "limit": param_types.INT64,
                },
            )
        )


def execute_spanner_hybrid_search(
    *,
    database: Any,
    table: str,
    model: str,
    query: str,
    limit: int,
    param_types: Any,
) -> list[Any]:
    candidate_limit = max(limit * 10, 50)
    sql = f"""
        WITH query_embedding AS (
          SELECT ARRAY(
            SELECT CAST(value AS FLOAT32)
            FROM UNNEST((
              SELECT embeddings.values
              FROM ML.PREDICT(
                MODEL {model},
                (SELECT @query AS content, "RETRIEVAL_QUERY" AS task_type),
                STRUCT(768 AS outputDimensionality)
              )
            )) AS value
          ) AS embedding
        ),
        vector_candidates AS (
          SELECT OFFSET AS rank, candidate.DocId, candidate.ChunkId
          FROM UNNEST(ARRAY(
            SELECT AS STRUCT DocId, ChunkId
            FROM {table}, query_embedding
            WHERE Embedding IS NOT NULL
            ORDER BY APPROX_COSINE_DISTANCE(
              query_embedding.embedding,
              Embedding,
              OPTIONS => JSON '{{"num_leaves_to_search": 50}}'
            )
            LIMIT @candidate_limit
          )) AS candidate WITH OFFSET
        ),
        text_candidates AS (
          SELECT OFFSET AS rank, candidate.DocId, candidate.ChunkId
          FROM UNNEST(ARRAY(
            SELECT AS STRUCT DocId, ChunkId
            FROM {table}
            WHERE SEARCH(ChunkTokens, @query)
            ORDER BY SCORE(ChunkTokens, @query) DESC
            LIMIT @candidate_limit
          )) AS candidate WITH OFFSET
        ),
        fused AS (
          SELECT DocId, ChunkId, SUM(1.0 / (60 + rank)) AS rrf_score
          FROM (
            SELECT DocId, ChunkId, rank FROM vector_candidates
            UNION ALL
            SELECT DocId, ChunkId, rank FROM text_candidates
          )
          GROUP BY DocId, ChunkId
        )
        SELECT
          c.ChunkId,
          c.DocId,
          c.SourceDataset,
          c.ChunkText,
          c.PublishedAt,
          c.Source,
          c.Commodities,
          c.Tags,
          f.rrf_score AS RetrievalScore
        FROM fused AS f
        JOIN {table} AS c
          ON c.DocId = f.DocId
          AND c.ChunkId = f.ChunkId
        ORDER BY f.rrf_score DESC
        LIMIT @limit
    """
    with database.snapshot() as snapshot:
        return list(
            snapshot.execute_sql(
                sql,
                params={
                    "query": query,
                    "limit": limit,
                    "candidate_limit": candidate_limit,
                },
                param_types={
                    "query": param_types.STRING,
                    "limit": param_types.INT64,
                    "candidate_limit": param_types.INT64,
                },
            )
        )


def execute_spanner_scan_fallback(
    *,
    database: Any,
    table: str,
    query: str,
    limit: int,
    param_types: Any,
) -> list[Any]:
    terms = spanner_scan_terms(query)
    if not terms:
        return []
    where = " AND ".join(f"LOWER(TextForEmbedding) LIKE @term{index}" for index, _ in enumerate(terms))
    sql = f"""
        SELECT ChunkId, DocId, SourceDataset, ChunkText, PublishedAt, Source, Commodities, Tags, NULL AS RetrievalScore
        FROM {table}
        WHERE {where}
        LIMIT @limit
    """
    params = {"limit": limit} | {
        f"term{index}": f"%{term.lower()}%" for index, term in enumerate(terms)
    }
    param_types_map = {"limit": param_types.INT64} | {
        f"term{index}": param_types.STRING for index, _ in enumerate(terms)
    }
    with database.snapshot() as snapshot:
        return list(
            snapshot.execute_sql(
                sql,
                params=params,
                param_types=param_types_map,
            )
        )


def spanner_scan_terms(query: str) -> list[str]:
    phrases = re.findall(r'"([^"]{3,80})"', query)
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", query)
    stop = {
        "and",
        "the",
        "for",
        "with",
        "market",
        "price",
        "risk",
        "terms",
        "game",
        "task",
    }
    values: list[str] = []
    for value in [*phrases, *tokens]:
        normalized = " ".join(value.lower().split()).strip(" .,:;")
        if not normalized or normalized in stop:
            continue
        if normalized not in values:
            values.append(normalized)
    return values[:4]


def spanner_row_to_evidence(
    row: Any,
    *,
    query: str,
    config: TraderSourceConfig,
) -> SearchEvidence:
    chunk_id, doc_id, source_dataset, chunk_text, published_at, source, commodities, tags, provider_score = unpack_spanner_row(row)
    text = str(chunk_text or "")
    title = extract_spanner_title(text, source_dataset=str(source_dataset or "spanner_rag"))
    uri = (
        "spanner://"
        f"{config.google_cloud_project}/{config.spanner_instance_id}/"
        f"{config.spanner_database_id}/{config.spanner_chunks_table or 'RagChunks'}/"
        f"{doc_id}/{chunk_id}"
    )
    context = []
    if published_at:
        context.append(f"published={published_at}")
    if source:
        context.append(f"source={source}")
    if commodities:
        context.append(f"commodities={', '.join(str(item) for item in commodities)}")
    if tags:
        context.append(f"tags={', '.join(str(item) for item in tags[:8])}")
    prefix = "; ".join(context)
    summary = f"{prefix}\n{text}" if prefix else text
    return SearchEvidence(
        evidence_id=f"spanner:{stable_id(uri)}",
        title=title[:180],
        url=uri,
        summary=summary[:500],
        query=query,
        source="spanner_rag",
        confidence="medium",
        provider_score=provider_score,
    )


def unpack_spanner_row(row: Any) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, float | None]:
    values = list(row)
    if len(values) == 8:
        return (*values, None)  # type: ignore[return-value]
    if len(values) >= 9:
        provider_score = values[8]
        try:
            score = None if provider_score is None else float(provider_score)
        except (TypeError, ValueError):
            score = None
        return (*values[:8], score)  # type: ignore[return-value]
    raise ValueError(f"Unexpected Spanner RAG row shape: {len(values)} column(s)")


def extract_spanner_title(text: str, *, source_dataset: str) -> str:
    for line in text.splitlines()[:6]:
        if line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            if title:
                return title
    clean = " ".join(text.split())
    return clean[:140] if clean else source_dataset


def safe_spanner_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe Spanner identifier: {value}")
    return value


def model_only_hypothesis(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    model_name = os.environ.get("MUTUAL_SPEC_STRONG_MODEL", "model")
    return TraderRagResult(
        provider="model",
        status="hypothesis_only",
        queries=queries,
        required_evidence=required_evidence,
        warnings=[
            f"{model_name} can propose hypotheses, but model-only evidence remains low confidence and cannot clear price/compliance/counterparty gates.",
        ],
    )


def run_fixture_provider(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    path = config.fixture_path
    if not path:
        return TraderRagResult(
            provider="fixture",
            status="provider_error",
            queries=queries,
            required_evidence=required_evidence,
            warnings=["TRADER_RAG_FIXTURE_PATH is required for fixture RAG."],
        )
    raw = json.loads(Path(path).read_text())
    candidates = [
        normalize_result(item, query=str(item.get("query") or queries[0]), source="fixture")
        for item in raw.get("results", raw if isinstance(raw, list) else [])
    ]
    evidence = dedupe_evidence(
        filter_evidence_for_queries(
            candidates,
            min_score=config.min_relevance_score,
        )
    )[: config.max_results]
    return TraderRagResult(
        provider="fixture",
        status="retrieved" if evidence else "empty",
        queries=queries,
        required_evidence=required_evidence,
        evidence=evidence,
        warnings=[] if evidence else ["Fixture returned no evidence."],
    )


def run_google_cse_provider(
    queries: list[str],
    required_evidence: list[str],
    *,
    config: TraderSourceConfig,
) -> TraderRagResult:
    api_key = config.google_search_api_key
    cx = config.google_search_cx
    if not api_key or not cx:
        return TraderRagResult(
            provider="google_cse",
            status="provider_error",
            queries=queries,
            required_evidence=required_evidence,
            warnings=["GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX are required."],
        )
    max_queries = config.max_queries
    max_results = config.max_results
    timeout = config.timeout_seconds
    evidence: list[SearchEvidence] = []
    warnings: list[str] = []
    for query in queries[:max_queries]:
        params = urllib.parse.urlencode(
            {
                "key": api_key,
                "cx": cx,
                "q": query,
                "num": min(max_results, 10),
            }
        )
        url = f"https://customsearch.googleapis.com/customsearch/v1?{params}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            warnings.append(f"Search failed for `{query}`: {exc}")
            continue
        for item in payload.get("items", []):
            evidence.append(normalize_result(item, query=query, source="google_cse"))
    deduped = dedupe_evidence(
        filter_evidence_for_queries(
            evidence,
            min_score=config.min_relevance_score,
        )
    )[:max_results]
    return TraderRagResult(
        provider="google_cse",
        status="retrieved" if deduped else "empty",
        queries=queries,
        required_evidence=required_evidence,
        evidence=deduped,
        warnings=warnings or ([] if deduped else ["Google CSE returned no results."]),
    )


def apply_rag_to_ledger(ledger: SpecLedger, rag: TraderRagResult) -> None:
    if not rag.queries:
        return
    source_type = source_type_for_provider(rag.provider)
    record_evidence_source(
        ledger,
        EvidenceSourceStatus(
            source_type=source_type,
            name=rag.provider,
            status=status_for_rag(rag),
            detail="; ".join(rag.warnings) if rag.warnings else f"{len(rag.evidence)} result(s)",
            confidence=confidence_for_provider(rag.provider),
        ),
    )
    add_unique(
        ledger.evidence_contract,
        "Trader source layer must retrieve cited evidence for price, logistics, counterparty, documents, compliance, and resale before a go/no-go gate can clear.",
    )
    for item in rag.required_evidence:
        add_unique(ledger.verification_conditions, item)
    for warning in rag.warnings:
        add_unique(ledger.assumptions, f"RAG warning: {warning}")
    for item in rag.evidence:
        evidence_source_type = source_type_for_provider(item.source)
        add_external_evidence(
            ledger,
            source_type=evidence_source_type,
            title=item.title,
            uri=item.url,
            summary=f"{item.summary} [query: {item.query}]",
            source_name=item.source,
            query=item.query,
            confidence=confidence_for_provider(item.source),
            used=True,
        )


def normalize_result(raw: Mapping[str, Any], *, query: str, source: str) -> SearchEvidence:
    url = str(raw.get("link") or raw.get("url") or raw.get("uri") or "")
    title = str(raw.get("title") or raw.get("name") or url or "Untitled search result")
    summary = str(raw.get("snippet") or raw.get("summary") or raw.get("description") or "")
    evidence_id = f"web:{stable_id(url or title)}"
    return SearchEvidence(
        evidence_id=evidence_id,
        title=title[:180],
        url=url,
        summary=summary[:500],
        query=query,
        source=source,
        confidence="high" if source == "google_cse" else "medium",
    )


def dedupe_evidence(items: list[SearchEvidence]) -> list[SearchEvidence]:
    seen: set[str] = set()
    result: list[SearchEvidence] = []
    for item in items:
        keys = {
            normalize_evidence_dedupe_key(item.url),
            normalize_evidence_dedupe_key(item.title),
        }
        keys.discard("")
        if seen & keys:
            continue
        seen.update(keys)
        result.append(item)
    return result


def normalize_evidence_dedupe_key(value: str) -> str:
    lowered = " ".join(str(value or "").lower().split())
    lowered = lowered.removeprefix("https://www.")
    lowered = lowered.removeprefix("http://www.")
    lowered = lowered.removeprefix("https://")
    lowered = lowered.removeprefix("http://")
    return lowered.rstrip("/")


def source_type_for_provider(provider: str) -> str:
    if provider in {"google_cse", "google", "programmable_search"}:
        return "google_cse"
    if provider in {"google_agent_search", "adk_google_search", "google_search_tool"}:
        return "google_agent_search"
    if provider in {"spanner", "spanner_rag", "cloud_spanner"}:
        return "spanner_rag"
    if provider in {"mcp", "opoint", "opoint_mcp"}:
        return "mcp"
    if provider in {"vertex_ai_search", "enterprise_search"}:
        return "vertex_ai_search"
    if provider in {"model", "model_only"}:
        return "model"
    return "rag"


def status_for_rag(rag: TraderRagResult) -> str:
    if rag.status in {"retrieved", "configured"}:
        return "retrieved" if rag.evidence else "configured"
    if rag.status == "deferred":
        return "planned"
    if rag.status in {"provider_error", "missing_config"}:
        return "missing_config"
    if rag.status in {"empty", "failed"}:
        return "failed"
    return "planned"


def confidence_for_provider(provider: str) -> str:
    if provider in {"google_cse", "google", "programmable_search", "google_agent_search", "adk_google_search", "google_search_tool"}:
        return "high"
    if provider in {"spanner", "spanner_rag", "cloud_spanner", "vertex_ai_search", "enterprise_search", "fixture"}:
        return "medium"
    if provider in {"mcp", "opoint", "opoint_mcp"}:
        return "medium"
    if provider in {"model", "model_only"}:
        return "low"
    return "medium"


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


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


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return parse_bool(value, default)
    return default


def parse_int_value(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return parse_int(value, default)
    return default


def parse_float_value(value: Any, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return parse_float(value, default)
    return default


def configured_value(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    lowered = stripped.lower()
    if not stripped or lowered in {"none", "null"} or lowered.startswith("your-"):
        return None
    return stripped


def env_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
