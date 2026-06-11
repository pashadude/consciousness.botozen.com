"""Evidence-first RAG for trader and physical commodity decision frames."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class SearchEvidence:
    evidence_id: str
    title: str
    url: str
    summary: str
    query: str
    source: str
    confidence: str = "medium"


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
class TraderSourceConfig:
    provider: str
    google_agent_search_enabled: bool
    max_queries: int
    max_results: int
    timeout_seconds: float
    google_search_api_key: str | None = None
    google_search_cx: str | None = None
    spanner_instance_id: str | None = None
    spanner_database_id: str | None = None
    spanner_documents_table: str | None = None
    spanner_chunks_table: str | None = None
    spanner_embedding_model: str | None = None
    vertex_ai_search_data_store_id: str | None = None
    vertex_ai_search_engine_id: str | None = None
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
    fixture_cfg = nested_config(yaml_config, "fixture")
    limits_cfg = nested_config(yaml_config, "limits")
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
    if provider in {"google_agent_search", "adk_google_search", "google_search_tool"}:
        return planned_google_agent_search(queries, required_evidence, config=config)
    if provider in {"spanner", "spanner_rag", "cloud_spanner"}:
        return run_spanner_provider(queries, required_evidence, config=config)
    if provider in {"vertex_ai_search", "enterprise_search"}:
        return planned_vertex_ai_search(queries, required_evidence, config=config)
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
    per_query_limit = max(1, min(config.max_results, 10))
    for query in queries[: config.max_queries]:
        try:
            evidence.extend(
                query_spanner_chunks(
                    query,
                    config=config,
                    limit=per_query_limit,
                )
            )
        except Exception as exc:
            warnings.append(f"Spanner RAG failed for `{query}`: {exc}")
    deduped = dedupe_evidence(evidence)[: config.max_results]
    if not deduped and not warnings:
        warnings.append(f"Private corpus target {resource} returned no matching chunks.")
    return TraderRagResult(
        provider="spanner_rag",
        status="retrieved" if deduped else "provider_error" if warnings else "empty",
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
    sql = f"""
        SELECT ChunkId, DocId, SourceDataset, ChunkText, PublishedAt, Source, Commodities, Tags
        FROM {table}
        WHERE SEARCH(ChunkTokens, @query)
        LIMIT @limit
    """
    from google.cloud import spanner

    database = (
        spanner.Client(project=config.google_cloud_project)
        .instance(config.spanner_instance_id)
        .database(config.spanner_database_id)
    )
    with database.snapshot() as snapshot:
        rows = list(
            snapshot.execute_sql(
                sql,
                params={"query": query, "limit": limit},
                param_types={
                    "query": spanner.param_types.STRING,
                    "limit": spanner.param_types.INT64,
                },
            )
        )
    evidence: list[SearchEvidence] = []
    for row in rows:
        evidence.append(spanner_row_to_evidence(row, query=query, config=config))
    return evidence


def spanner_row_to_evidence(
    row: Any,
    *,
    query: str,
    config: TraderSourceConfig,
) -> SearchEvidence:
    chunk_id, doc_id, source_dataset, chunk_text, published_at, source, commodities, tags = row
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
    )


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
    evidence = [
        normalize_result(item, query=str(item.get("query") or queries[0]), source="fixture")
        for item in raw.get("results", raw if isinstance(raw, list) else [])
    ]
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
    deduped = dedupe_evidence(evidence)[:max_results]
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
        add_external_evidence(
            ledger,
            source_type=source_type,
            title=item.title,
            uri=item.url,
            summary=f"{item.summary} [query: {item.query}]",
            source_name=rag.provider,
            query=item.query,
            confidence=confidence_for_provider(rag.provider),
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
        key = item.url or item.title
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def extract_search_terms(lower: str) -> list[str]:
    candidates = (
        "brent",
        "wti",
        "rbob",
        "ulsd",
        "sulfur",
        "sulphur",
        "iraq",
        "umm qasr",
        "fob",
        "cfr",
        "cargo",
        "freight",
        "counterparty",
        "sanctions",
    )
    return [term for term in candidates if term in lower]


def source_type_for_provider(provider: str) -> str:
    if provider in {"google_cse", "google", "programmable_search"}:
        return "google_cse"
    if provider in {"google_agent_search", "adk_google_search", "google_search_tool"}:
        return "google_agent_search"
    if provider in {"spanner", "spanner_rag", "cloud_spanner"}:
        return "spanner_rag"
    if provider in {"vertex_ai_search", "enterprise_search"}:
        return "vertex_ai_search"
    if provider in {"model", "model_only"}:
        return "model"
    return "rag"


def status_for_rag(rag: TraderRagResult) -> str:
    if rag.status in {"retrieved", "configured"}:
        return "retrieved" if rag.evidence else "configured"
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
