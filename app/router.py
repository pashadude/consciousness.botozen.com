"""Python-side model routing for the ADK workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.spec_state import RouteRecord, SpecLedger, needs_clarification


@dataclass(frozen=True)
class ModelProfile:
    model_class: str
    model_name: str
    intended_use: str


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
