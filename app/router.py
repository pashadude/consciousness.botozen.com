"""Python-side model routing for the ADK workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from app.spec_state import (
    RouteRecord,
    SpecLedger,
    looks_like_trader_query,
    needs_clarification,
)


@dataclass(frozen=True)
class ModelProfile:
    model_class: str
    model_name: str
    intended_use: str


@dataclass(frozen=True)
class AsyncRouteDecision:
    mode: Literal["sync", "async"]
    job_kind: str
    reasons: tuple[str, ...]
    risk_score: int
    expected_spec_gain: Literal["low", "medium", "high"]

    @property
    def should_enqueue(self) -> bool:
        return self.mode == "async"


CHEAP_MODEL = ModelProfile(
    model_class="cheap",
    model_name=os.environ.get("MUTUAL_SPEC_CHEAP_MODEL", "gemini-3.5-flash"),
    intended_use="low-risk classification, extraction, and summarization",
)
STRONG_MODEL = ModelProfile(
    model_class="strong",
    model_name=os.environ.get("MUTUAL_SPEC_STRONG_MODEL", "gemini-3.5-flash"),
    intended_use="high-ambiguity synthesis and failed-verification repair",
)
VERIFIER_MODEL = ModelProfile(
    model_class="verifier",
    model_name=os.environ.get("MUTUAL_SPEC_VERIFIER_MODEL", "gemini-3.5-flash"),
    intended_use="independent verifier pass after drafting",
)


def route_for_stage(
    stage: str,
    ledger: SpecLedger,
    *,
    failed_verification: bool = False,
) -> RouteRecord:
    """Return an auditable routing decision without invoking a model."""

    if stage == "verify":
        profile = VERIFIER_MODEL
        reason = "Verifier pass runs after every draft."
    elif failed_verification:
        profile = STRONG_MODEL
        reason = "Previous verifier pass failed, escalating synthesis."
    elif stage in {"ingest", "hypothesize_spec", "retrieve_evidence"}:
        profile = CHEAP_MODEL
        reason = "Low-risk classification, extraction, or summarization stage."
    elif stage == "draft_output" and needs_clarification(ledger):
        profile = STRONG_MODEL
        reason = "High-impact ambiguity remains during synthesis."
    elif stage == "draft_output" and ledger.loop_count > 0:
        profile = STRONG_MODEL
        reason = "Revision loop requires stronger synthesis."
    else:
        profile = CHEAP_MODEL
        reason = "Spec is low ambiguity and verifier has not failed."
    return RouteRecord(
        stage=stage,
        selected_model=profile.model_name,
        model_class=profile.model_class,  # type: ignore[arg-type]
        reason=reason,
    )


def route_async_decision(
    ledger: SpecLedger,
    *,
    mcp_configured: bool = False,
    telemetry_enabled: bool = False,
    artifact_count: int | None = None,
    failed_verification: bool = False,
) -> AsyncRouteDecision:
    """Decide whether the light route should defer deeper work to a job.

    This does not call a model. It is the deterministic control surface that a
    cheap model can feed later: if any high-gain condition is present, the
    workflow can enqueue a strong/tool-heavy job instead of blocking the turn.
    """

    reasons: list[str] = []
    risk_score = 0
    query = ledger.expressed_query or ledger.user_request
    artifact_count = len(ledger.artifact_refs) if artifact_count is None else artifact_count

    if needs_clarification(ledger):
        return AsyncRouteDecision(
            mode="sync",
            job_kind="clarification",
            reasons=("material_spec_fields_missing",),
            risk_score=0,
            expected_spec_gain="medium",
        )

    has_high_severity_finding = any(item.severity == "high" for item in ledger.verification_findings)
    if failed_verification or has_high_severity_finding:
        reasons.append("verifier_failed")
        risk_score += 3

    required_search_open = any(
        item.required and item.status != "satisfied" for item in ledger.search_plan
    )
    review_required = ledger.human_review.required and ledger.human_review.status != "approved"
    execution_sensitive = is_execution_sensitive_trader_query(query)
    if looks_like_trader_query(query) and (review_required or execution_sensitive):
        reasons.append("high_stakes_trader_decision_frame")
        risk_score += 2
    elif looks_like_trader_query(query) and required_search_open:
        reasons.append("trader_research_evidence_open")
        risk_score += 1

    latest_formalization = ledger.formalization_records[-1] if ledger.formalization_records else None
    formal_obligations_missing = bool(latest_formalization and latest_formalization.is_valid != 1)
    deep_tool_work_needed = any(
        (
            failed_verification,
            has_high_severity_finding,
            required_search_open,
            review_required,
            execution_sensitive,
            formal_obligations_missing,
            artifact_count > 0,
        )
    )

    if mcp_configured and deep_tool_work_needed:
        reasons.append("external_mcp_research_available")
        risk_score += 1

    if telemetry_enabled and deep_tool_work_needed:
        reasons.append("resource_region_telemetry_available")
        risk_score += 1

    if artifact_count > 0:
        reasons.append("artifact_retrieval_or_embedding_needed")
        risk_score += 1

    if formal_obligations_missing:
        reasons.append("formal_obligations_missing")
        risk_score += 1

    if any(token in query.lower() for token in ("legal", "sanction", "counterparty", "route", "position", "execute")):
        reasons.append("risk_or_policy_sensitive_prompt")
        risk_score += 1

    mode: Literal["sync", "async"] = "async" if risk_score >= 2 else "sync"
    gain: Literal["low", "medium", "high"]
    if risk_score >= 4:
        gain = "high"
    elif risk_score >= 2:
        gain = "medium"
    else:
        gain = "low"
    return AsyncRouteDecision(
        mode=mode,
        job_kind="deep_research_and_verification" if mode == "async" else "sync_spec_response",
        reasons=tuple(reasons or ["low_risk_sync_path"]),
        risk_score=risk_score,
        expected_spec_gain=gain,
    )


def async_jobs_enabled() -> bool:
    return os.environ.get("ASYNC_JOB_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_execution_sensitive_trader_query(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "should i",
            "go for it",
            "accept",
            "buy",
            "sell",
            "execute",
            "order",
            "position",
            "size",
            "cargo",
            "counterparty",
            "sanction",
            "fob",
            "cfr",
            "cif",
        )
    )


def safety_risk_score(text: str) -> int:
    lower = text.lower()
    high_risk_terms = (
        "phishing",
        "credential theft",
        "malware",
        "ransomware",
        "bypass safety",
        "evade detection",
        "steal passwords",
    )
    return sum(1 for term in high_risk_terms if term in lower)


def should_refuse_for_safety(text: str) -> bool:
    return safety_risk_score(text) > 0
