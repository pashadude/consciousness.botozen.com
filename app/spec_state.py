"""Serializable session.state ledger for the Mutual Specification Game."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

STATE_KEY = "spec_ledger"
MAX_TRACE_STEPS = 80
MATERIAL_FIELDS: tuple[str, ...] = ("goal", "audience", "output_format")


class ArtifactRef(BaseModel):
    """Metadata-only reference to an artifact stored by ADK's artifact service."""

    artifact_id: str
    filename: str
    mime_type: str | None = None
    version: int | None = None
    source: Literal["upload", "generated", "external"] = "upload"
    note: str | None = None


class EvidenceRef(BaseModel):
    evidence_id: str
    source_type: Literal["artifact", "mcp", "web", "session", "user"]
    title: str
    uri: str | None = None
    summary: str
    artifact_id: str | None = None
    used: bool = False


class Ambiguity(BaseModel):
    field: str
    severity: Literal["low", "medium", "high"] = "medium"
    impact: str
    question: str


class RouteRecord(BaseModel):
    stage: str
    selected_model: str
    model_class: Literal["cheap", "strong", "verifier"]
    reason: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class AsyncJobRef(BaseModel):
    """Reference to deferred work kicked off by a light-model route decision."""

    job_id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    reasons: list[str] = Field(default_factory=list)
    result_ref: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class VerificationFinding(BaseModel):
    category: Literal[
        "spec_gap",
        "unsupported_claim",
        "trajectory",
        "safety",
        "quality",
    ]
    severity: Literal["low", "medium", "high"]
    message: str
    remediation: str | None = None


class FormalizationRecord(BaseModel):
    """Problem/question/answer formalization record for a spec-game task."""

    task_name: str
    domain: Literal["general", "trader"]
    problem: dict[str, Any] = Field(default_factory=dict)
    question: str | None = None
    answer: dict[str, Any] = Field(default_factory=dict)
    hypothesis: dict[str, Any] = Field(default_factory=dict)
    tokens: list[str] = Field(default_factory=list)
    is_valid: Literal[-1, 0, 1]
    metrics: dict[str, Any] = Field(default_factory=dict)
    missing_obligations: list[str] = Field(default_factory=list)
    class_id: int | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SpecLedger(BaseModel):
    """Compact JSON object persisted under session.state[STATE_KEY]."""

    ledger_id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:12]}")
    version: int = 1
    project_name: str = "mutual-spec-agent"
    turn_count: int = 0
    status: Literal[
        "ingesting",
        "clarifying",
        "retrieving",
        "drafting",
        "verifying",
        "async_pending",
        "finalized",
    ] = "ingesting"
    user_request: str = ""
    expressed_query: str = ""
    goal: str | None = None
    audience: str | None = None
    output_format: str | None = None
    latent_intent_hypotheses: list[str] = Field(default_factory=list)
    evidence_contract: list[str] = Field(default_factory=list)
    verification_conditions: list[str] = Field(default_factory=list)
    decision_gate: Literal[
        "needs_more_info",
        "analysis_ready",
        "alert_ready",
        "decision_frame_ready",
    ] = "needs_more_info"
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    clarification_answers: list[str] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    route_history: list[RouteRecord] = Field(default_factory=list)
    async_jobs: list[AsyncJobRef] = Field(default_factory=list)
    formalization_records: list[FormalizationRecord] = Field(default_factory=list)
    trajectory: list[str] = Field(default_factory=list)
    drafts: list[str] = Field(default_factory=list)
    verification_findings: list[VerificationFinding] = Field(default_factory=list)
    loop_count: int = 0
    last_updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def empty_ledger() -> SpecLedger:
    return SpecLedger()


def ledger_from_state(state: dict[str, Any] | None) -> SpecLedger:
    if not state:
        return empty_ledger()
    raw = state.get(STATE_KEY, {})
    if isinstance(raw, SpecLedger):
        return raw
    if isinstance(raw, dict):
        return SpecLedger.model_validate(raw)
    return empty_ledger()


def ledger_to_state(ledger: SpecLedger) -> dict[str, Any]:
    ledger.last_updated_at = datetime.now(UTC).isoformat()
    return {STATE_KEY: ledger.model_dump(mode="json")}


def record_stage(ledger: SpecLedger, stage: str) -> SpecLedger:
    ledger.trajectory.append(stage)
    ledger.trajectory = ledger.trajectory[-MAX_TRACE_STEPS:]
    return ledger


def add_route(ledger: SpecLedger, route: RouteRecord) -> SpecLedger:
    ledger.route_history.append(route)
    ledger.route_history = ledger.route_history[-MAX_TRACE_STEPS:]
    return ledger


def text_from_content(content: Any) -> str:
    """Extract text from ADK/genai Content-like values without storing binaries."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts = content.get("parts") or []
    else:
        parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def update_ledger_from_user_text(
    ledger: SpecLedger,
    text: str,
    *,
    artifact_refs: list[ArtifactRef] | None = None,
) -> SpecLedger:
    text = (text or "").strip()
    if text:
        ledger.user_request = text
        ledger.expressed_query = text
        ledger.turn_count += 1
        infer_spec_fields(ledger, text)
    for artifact in artifact_refs or []:
        if not any(existing.artifact_id == artifact.artifact_id for existing in ledger.artifact_refs):
            ledger.artifact_refs.append(artifact)
    ledger.ambiguities = detect_material_ambiguities(ledger)
    ledger.status = "clarifying" if ledger.ambiguities else "retrieving"
    return ledger


def apply_clarification_answer(ledger: SpecLedger, answer: str | dict[str, Any]) -> SpecLedger:
    if isinstance(answer, dict):
        text = str(answer.get("answer") or answer.get("text") or "").strip()
        for field in MATERIAL_FIELDS:
            value = answer.get(field)
            if value:
                setattr(ledger, field, str(value).strip())
    else:
        text = str(answer or "").strip()
    if text:
        ledger.clarification_answers.append(text)
        infer_spec_fields(ledger, text, fill_only=True)
    ledger.ambiguities = detect_material_ambiguities(ledger)
    ledger.status = "retrieving" if not ledger.ambiguities else "clarifying"
    return ledger


def detect_material_ambiguities(ledger: SpecLedger) -> list[Ambiguity]:
    missing = [field for field in MATERIAL_FIELDS if not getattr(ledger, field)]
    ambiguities: list[Ambiguity] = []
    for field in missing:
        ambiguities.append(
            Ambiguity(
                field=field,
                severity="high",
                impact=f"The executable task cannot be accepted without a clear {field.replace('_', ' ')}.",
                question=question_for_missing_field(field),
            )
        )
    return ambiguities


def needs_clarification(ledger: SpecLedger) -> bool:
    return any(item.severity == "high" and item.field in MATERIAL_FIELDS for item in ledger.ambiguities)


def build_clarification_question(ledger: SpecLedger) -> str:
    if not ledger.ambiguities:
        return "Please confirm the goal, audience, and output format before I continue."
    fields = [item.field.replace("_", " ") for item in ledger.ambiguities]
    if len(fields) == 1:
        field_phrase = fields[0]
    elif len(fields) == 2:
        field_phrase = f"{fields[0]} and {fields[1]}"
    else:
        field_phrase = f"{', '.join(fields[:-1])}, and {fields[-1]}"
    question = (
        f"To lock the executable spec, what should I use for the {field_phrase}? "
        "A short answer is enough."
    )
    if question not in ledger.clarification_questions:
        ledger.clarification_questions.append(question)
    return question


def question_for_missing_field(field: str) -> str:
    prompts = {
        "goal": "What concrete outcome should the agent produce?",
        "audience": "Who is the intended audience or user of the output?",
        "output_format": "What output format should the agent return?",
    }
    return prompts.get(field, f"What should the {field} be?")


def infer_spec_fields(ledger: SpecLedger, text: str, *, fill_only: bool = False) -> None:
    lower = text.lower()
    if not fill_only or not ledger.goal:
        goal = infer_goal(text)
        if goal:
            ledger.goal = goal
    if not fill_only or not ledger.audience:
        audience = infer_audience(text)
        if audience:
            ledger.audience = audience
    if not fill_only or not ledger.output_format:
        output_format = infer_output_format(lower)
        if output_format:
            ledger.output_format = output_format
    for criterion in infer_success_criteria(text):
        if criterion not in ledger.success_criteria:
            ledger.success_criteria.append(criterion)
    for constraint in infer_constraints(text):
        if constraint not in ledger.constraints:
            ledger.constraints.append(constraint)
    infer_trader_decision_context(ledger, text)


def infer_goal(text: str) -> str | None:
    cleaned = " ".join(text.strip().split())
    if len(cleaned.split()) < 4:
        return None
    vague_patterns = (
        r"^(help|do this|make this|fix this|improve this|work on this)\b",
        r"^(can you|please|need you to)\s+(help|do|make|fix|improve)\b",
    )
    if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in vague_patterns):
        return None
    return cleaned[:240]


def infer_audience(text: str) -> str | None:
    patterns = [
        r"\bfor (?:an?|the|my|our)?\s*([a-z][a-z0-9 ,/&-]{2,60})",
        r"\baudience(?: is|:)?\s*([a-z][a-z0-9 ,/&-]{2,60})",
        r"\bto (?:an?|the|my|our)?\s*([a-z][a-z0-9 ,/&-]{2,60})",
    ]
    stop_words = {"use", "with", "in", "as", "by", "about", "on", "that"}
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            phrase = match.group(1).strip(" .,:;")
            words = phrase.split()
            trimmed: list[str] = []
            for word in words:
                if word.lower() in stop_words:
                    break
                trimmed.append(word)
            if trimmed:
                return " ".join(trimmed[:8])
    audience_keywords = {
        "executives": "executives",
        "engineers": "engineers",
        "developers": "developers",
        "students": "students",
        "customers": "customers",
        "users": "users",
        "traders": "traders",
        "investors": "investors",
    }
    for keyword, audience in audience_keywords.items():
        if keyword in text.lower():
            return audience
    return None


def infer_output_format(lower_text: str) -> str | None:
    formats = {
        "json": "JSON",
        "table": "table",
        "csv": "CSV",
        "memo": "memo",
        "email": "email",
        "report": "report",
        "slide": "slide deck outline",
        "slides": "slide deck outline",
        "readme": "README",
        "design spec": "design spec",
        "python": "Python code",
        "code": "code",
        "checklist": "checklist",
        "bullets": "bullet list",
    }
    for keyword, output_format in formats.items():
        if keyword in lower_text:
            return output_format
    if "format" in lower_text:
        return None
    return None


def infer_success_criteria(text: str) -> list[str]:
    criteria: list[str] = []
    for marker in ("acceptance criteria", "success criteria", "must pass", "done when"):
        if marker in text.lower():
            criteria.append(text.strip()[:220])
            break
    return criteria


def infer_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for sentence in sentences:
        lower = sentence.lower()
        if any(token in lower for token in ("must", "should", "do not", "don't", "use ", "avoid")):
            constraints.append(sentence[:220])
    return constraints[:8]


def infer_trader_decision_context(ledger: SpecLedger, text: str) -> None:
    if not looks_like_trader_query(text):
        return
    if not ledger.audience:
        ledger.audience = "traders"
    if not ledger.output_format:
        ledger.output_format = "decision frame"
    if not ledger.goal:
        ledger.goal = "Reconstruct and verify the trading task specification from a compressed trader query."

    add_unique(
        ledger.latent_intent_hypotheses,
        "The trader may be asking for a trade, analysis, alert, or strategy spec rather than a direct answer.",
    )
    lower = text.lower()
    if any(token in lower for token in ("arb", "arbitrage", "ho/rb", "rbob", "ulsd", "crack")):
        add_unique(ledger.latent_intent_hypotheses, "Check whether a physical or relative-value arbitrage exists.")
    if any(token in lower for token in ("turkey", "iraq", "umm qasr", "sanction", "legal", "counterparty", "cheating")):
        add_unique(ledger.latent_intent_hypotheses, "Check legal, sanctions, port, route, payment, title, and counterparty risk before any trader decision.")
    if any(token in lower for token in ("sulfur", "sulphur", "fob", "ton", "tonne", "tonns", "mt", "offer")):
        add_unique(ledger.latent_intent_hypotheses, "Reconstruct whether the commodity offer is executable: specification, quantity, origin, Incoterms, price, logistics, payment, counterparty, and resale hedge.")
    if any(token in lower for token in ("spread", "brent", "wti", "basis", "risk", "fob", "offer")):
        add_unique(ledger.latent_intent_hypotheses, "Estimate basis, market, liquidity, and falsification risk for the spread or thesis.")

    for item in (
        "IBKR may be used for futures history and market data only; do not place orders or expose broker execution.",
        "Yahoo Finance may be used only as a proxy/reference source and must carry calibration assumptions.",
        "Commodity feeds must record freshness, entitlement, units, transform, confidence, and lookahead guard.",
        "Physical commodity offers must verify product specification, quantity tolerance, Incoterms, load port, laycan, counterparty identity, title chain, payment terms, sanctions exposure, freight, insurance, inspection, and resale path.",
    ):
        add_unique(ledger.evidence_contract, item)

    for item in (
        "Separate fact, assumption, bet, and unknown.",
        "Include legal, logistics, sanctions, market, basis, and counterparty risk flags when relevant.",
        "Return a decision frame for the trader, not a buy/sell recommendation.",
        "Include falsification triggers and missing data before marking the frame ready.",
        "For physical offers, do not mark go/no-go ready until product spec, documents, counterparty, payment, logistics, and market exit are verified.",
    ):
        add_unique(ledger.verification_conditions, item)

    for item in (
        "No broker order placement, execution management, or live trading workflow.",
        "Trader keeps final decision ownership.",
    ):
        add_unique(ledger.constraints, item)

    for item in (
        "Output includes interpreted thesis, missing information, risk ledger, assumptions, verification checklist, and falsification triggers.",
        "Output is a proof-carrying decision frame that the trader can inspect and explain.",
        "Output separates immediate answer, required verification, hidden risks, economics, logistics, and go/no-go gate.",
    ):
        add_unique(ledger.success_criteria, item)


def looks_like_trader_query(text: str) -> bool:
    lower = text.lower()
    trader_tokens = (
        "arb",
        "arbitrage",
        "basis",
        "brent",
        "wti",
        "ho/rb",
        "rbob",
        "ulsd",
        "spread",
        "bitumen",
        "oil",
        "refined",
        "sulfur",
        "sulphur",
        "fertilizer",
        "fob",
        "cfr",
        "cif",
        "incoterm",
        "umm qasr",
        "iraq",
        "mt",
        "ton",
        "tonne",
        "tonns",
        "cargo",
        "offer",
        "counterparty",
        "sanction",
        "turkey",
        "route",
        "cargo",
        "inventory",
        "hedge",
        "liquidity",
    )
    return any(token in lower for token in trader_tokens)


def add_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def add_evidence_from_artifacts(ledger: SpecLedger) -> SpecLedger:
    for artifact in ledger.artifact_refs:
        evidence_id = f"artifact:{artifact.artifact_id}"
        if any(item.evidence_id == evidence_id for item in ledger.evidence):
            continue
        ledger.evidence.append(
            EvidenceRef(
                evidence_id=evidence_id,
                source_type="artifact",
                title=artifact.filename,
                uri=f"artifact://{artifact.filename}",
                summary=f"Uploaded artifact metadata: {artifact.filename}"
                + (f" ({artifact.mime_type})" if artifact.mime_type else ""),
                artifact_id=artifact.artifact_id,
                used=True,
            )
        )
    ledger.evidence_used = sorted({item.evidence_id for item in ledger.evidence if item.used})
    return ledger


def add_mcp_evidence_placeholder(ledger: SpecLedger, source_uri: str) -> SpecLedger:
    evidence_id = f"mcp:{abs(hash(source_uri))}"
    if not any(item.evidence_id == evidence_id for item in ledger.evidence):
        ledger.evidence.append(
            EvidenceRef(
                evidence_id=evidence_id,
                source_type="mcp",
                title="Configured MCP research source",
                uri=source_uri,
                summary="External evidence retrieval is configured through an MCP toolset.",
                used=True,
            )
        )
    ledger.evidence_used = sorted({item.evidence_id for item in ledger.evidence if item.used})
    return ledger
