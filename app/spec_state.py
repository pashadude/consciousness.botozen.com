"""Serializable session.state ledger for the Mutual Specification Game."""

from __future__ import annotations

import hashlib
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
    source_type: Literal[
        "artifact",
        "google_agent_search",
        "google_cse",
        "mcp",
        "model",
        "rag",
        "session",
        "spanner_rag",
        "user",
        "vertex_ai_search",
        "web",
    ]
    title: str
    uri: str | None = None
    summary: str
    artifact_id: str | None = None
    source_name: str | None = None
    query: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    used: bool = False


class SearchPlanItem(BaseModel):
    """Evidence retrieval obligation created from the inferred trader spec."""

    query_id: str
    query: str
    purpose: str
    required: bool = True
    preferred_sources: list[
        Literal[
            "google_agent_search",
            "google_cse",
            "mcp",
            "model",
            "opoint",
            "spanner_rag",
            "vertex_ai_search",
        ]
    ] = Field(default_factory=list)
    status: Literal["planned", "searched", "satisfied", "failed"] = "planned"


class EvidenceSourceStatus(BaseModel):
    """Provider status for source adapters that can satisfy SearchPlanItem rows."""

    source_type: Literal[
        "artifact",
        "google_agent_search",
        "google_cse",
        "mcp",
        "model",
        "rag",
        "spanner_rag",
        "vertex_ai_search",
        "web",
    ]
    name: str
    status: Literal[
        "configured",
        "missing_config",
        "planned",
        "queried",
        "retrieved",
        "failed",
        "skipped",
    ]
    detail: str
    confidence: Literal["low", "medium", "high"] = "low"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


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


class GamePlayer(BaseModel):
    """Player in the many-person coordination game."""

    player_id: str
    role: str
    objective: str
    private_information: list[str] = Field(default_factory=list)
    action_space: list[str] = Field(default_factory=list)


class GameStageState(BaseModel):
    """Explicit state for one staged game in the specification process."""

    stage_id: str
    game_type: str
    objective: str
    success_condition: str
    status: Literal["open", "active", "blocked", "satisfied"] = "open"
    blocking_conditions: list[str] = Field(default_factory=list)


class LatentTypeBelief(BaseModel):
    """Harsanyi-style belief over possible latent task types."""

    type_id: str
    description: str
    probability: float
    evidence_signals: list[str] = Field(default_factory=list)
    next_best_action: Literal["ask", "assume", "retrieve", "propose", "verify"] = "ask"


class CommitmentRecord(BaseModel):
    """A commitment made by the user, agent, router, tool, or verifier."""

    commitment_id: str
    player_id: str
    field: str
    value: str
    status: Literal["proposed", "accepted", "needs_confirmation", "rejected"] = "proposed"
    source: Literal["user", "agent", "tool", "verifier", "system"] = "agent"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ClaimRecord(BaseModel):
    """Claim graph node for proof-carrying responses."""

    claim_id: str
    text: str
    claim_type: Literal["fact", "assumption", "inference", "constraint", "decision_gate"]
    support_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    verifier_state: Literal[
        "unverified",
        "supported",
        "needs_evidence",
        "blocked",
        "refuted",
    ] = "unverified"


class UserEndorsementState(BaseModel):
    """Whether the user has endorsed the current shared specification."""

    endorsed_fields: list[str] = Field(default_factory=list)
    rejected_fields: list[str] = Field(default_factory=list)
    pending_fields: list[str] = Field(default_factory=list)
    last_signal: str | None = None


class SpecConvergenceState(BaseModel):
    """Compact convergence score for q -> theta -> executable specification."""

    material_resolution: float = 0.0
    evidence_resolution: float = 0.0
    formalization_resolution: float = 0.0
    endorsement_resolution: float = 0.0
    verification_resolution: float = 0.0
    overall: float = 0.0
    status: Literal["diverged", "negotiating", "executable", "verified"] = "diverged"


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
    domain: Literal["general", "trader", "mutual_spec"]
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
    search_plan: list[SearchPlanItem] = Field(default_factory=list)
    evidence_sources: list[EvidenceSourceStatus] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    evidence_used: list[str] = Field(default_factory=list)
    game_players: list[GamePlayer] = Field(default_factory=list)
    game_states: list[GameStageState] = Field(default_factory=list)
    latent_type_beliefs: list[LatentTypeBelief] = Field(default_factory=list)
    commitments: list[CommitmentRecord] = Field(default_factory=list)
    claim_graph: list[ClaimRecord] = Field(default_factory=list)
    user_endorsement: UserEndorsementState = Field(default_factory=UserEndorsementState)
    spec_convergence: SpecConvergenceState = Field(default_factory=SpecConvergenceState)
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
    update_mutual_spec_game_state(ledger)
    return ledger


def update_mutual_spec_game_state(ledger: SpecLedger) -> SpecLedger:
    """Materialize the game-theory layer from the compact spec ledger.

    This is deliberately deterministic: it gives the model and verifier an
    inspectable state object rather than hiding the specification game in a
    prompt. It is not an equilibrium solver or a theorem prover.
    """

    ensure_game_players(ledger)
    ledger.latent_type_beliefs = infer_latent_type_beliefs(ledger)
    sync_commitments(ledger)
    sync_claim_graph(ledger)
    sync_user_endorsement(ledger)
    ledger.game_states = build_game_states(ledger)
    ledger.spec_convergence = compute_spec_convergence(ledger)
    return ledger


def ensure_game_players(ledger: SpecLedger) -> None:
    if ledger.game_players:
        return
    ledger.game_players = [
        GamePlayer(
            player_id="user",
            role="compressed signal owner",
            objective="Minimize cognitive difference between latent task and shared executable spec.",
            private_information=["latent task theta", "risk tolerance", "decision context"],
            action_space=["ask", "answer clarification", "endorse", "reject", "supply artifacts"],
        ),
        GamePlayer(
            player_id="main_agent",
            role="specification mediator",
            objective="Convert q into candidate theta and executable s with minimal user burden.",
            private_information=["model uncertainty", "retrieval gaps", "verification findings"],
            action_space=["ask", "assume", "retrieve", "draft", "verify", "defer"],
        ),
        GamePlayer(
            player_id="router",
            role="model/tool allocator",
            objective="Maximize expected specification gain under cost, latency, risk, and policy constraints.",
            action_space=["cheap_model", "strong_model", "tool", "async_job", "human_review"],
        ),
        GamePlayer(
            player_id="verifier",
            role="adversarial checker",
            objective="Block unsupported, unsafe, or underspecified claims before finalization.",
            action_space=["pass", "flag", "block", "request_repair"],
        ),
        GamePlayer(
            player_id="tool_layer",
            role="evidence producer",
            objective="Provide cited evidence with freshness, confidence, and source limitations.",
            action_space=["search", "retrieve", "inspect_artifact", "return_empty", "fail_closed"],
        ),
        GamePlayer(
            player_id="human_reviewer",
            role="high-stakes reviewer",
            objective="Preserve human decision ownership when risk or ambiguity remains high.",
            action_space=["approve", "reject", "request_more_evidence", "take_over"],
        ),
    ]


def infer_latent_type_beliefs(ledger: SpecLedger) -> list[LatentTypeBelief]:
    text = (ledger.expressed_query or ledger.user_request).lower()
    candidates: list[tuple[str, str, float, list[str], str]] = [
        (
            "general_specification",
            "User wants a general executable task specification.",
            0.15,
            ["default prior"],
            "ask",
        )
    ]
    if looks_like_trader_query(text):
        candidates.append(
            (
                "trader_decision_frame",
                "Trader needs a proof-carrying decision frame rather than a direct answer.",
                0.35,
                matched_signals(text, ("trader", "trade", "risk", "spread", "offer", "fob")),
                "retrieve",
            )
        )
    if any(token in text for token in ("sulfur", "sulphur", "umm qasr", "fob", "ton", "offer")):
        candidates.append(
            (
                "physical_commodity_offer",
                "Compressed physical commodity offer requiring price, logistics, documents, counterparty, and sanctions verification.",
                0.3,
                matched_signals(text, ("sulfur", "sulphur", "umm qasr", "fob", "offer")),
                "retrieve",
            )
        )
    if any(token in text for token in ("arb", "arbitrage", "ho/rb", "spread", "basis", "brent", "wti")):
        candidates.append(
            (
                "relative_value_or_spread",
                "Relative-value or spread analysis requiring instrument mapping, timeframe, risk, and falsification triggers.",
                0.25,
                matched_signals(text, ("arb", "arbitrage", "ho/rb", "spread", "basis", "brent", "wti")),
                "retrieve",
            )
        )
    if any(token in text for token in ("mutual specification", "specification game", "latent intent", "game theory")):
        candidates.append(
            (
                "architecture_specification_game",
                "User wants the Mutual Specification Game itself implemented as inspectable coordination mechanics.",
                0.4,
                matched_signals(text, ("mutual specification", "specification game", "latent intent", "game theory")),
                "propose",
            )
        )
    if any(token in text for token in ("implement", "build", "code", "deploy")):
        candidates.append(
            (
                "implementation_task",
                "User expects code changes, tests, and deployment rather than an explanatory answer.",
                0.22,
                matched_signals(text, ("implement", "build", "code", "deploy")),
                "verify",
            )
        )
    total = sum(weight for _, _, weight, _, _ in candidates) or 1.0
    beliefs = [
        LatentTypeBelief(
            type_id=type_id,
            description=description,
            probability=round(weight / total, 4),
            evidence_signals=signals or ["prior"],
            next_best_action=next_action,  # type: ignore[arg-type]
        )
        for type_id, description, weight, signals, next_action in candidates
    ]
    return sorted(beliefs, key=lambda item: item.probability, reverse=True)


def matched_signals(text: str, tokens: tuple[str, ...]) -> list[str]:
    return [token for token in tokens if token in text]


def sync_commitments(ledger: SpecLedger) -> None:
    commitments: list[CommitmentRecord] = []
    for field in MATERIAL_FIELDS:
        value = getattr(ledger, field)
        if not value:
            continue
        status = "accepted" if not any(item.field == field for item in ledger.ambiguities) else "needs_confirmation"
        commitments.append(
            CommitmentRecord(
                commitment_id=f"commit:{stable_id(f'{field}:{value}')}",
                player_id="user" if status == "accepted" else "main_agent",
                field=field,
                value=str(value),
                status=status,
                source="user" if status == "accepted" else "agent",
            )
        )
    for index, item in enumerate(ledger.constraints[:12]):
        commitments.append(
            CommitmentRecord(
                commitment_id=f"commit:{stable_id(f'constraint:{item}')}",
                player_id="main_agent",
                field=f"constraint:{index}",
                value=item,
                status="accepted",
                source="system",
            )
        )
    for index, item in enumerate(ledger.verification_conditions[:12]):
        commitments.append(
            CommitmentRecord(
                commitment_id=f"commit:{stable_id(f'verification:{item}')}",
                player_id="verifier",
                field=f"verification_condition:{index}",
                value=item,
                status="accepted",
                source="verifier",
            )
        )
    ledger.commitments = commitments[-MAX_TRACE_STEPS:]


def sync_claim_graph(ledger: SpecLedger) -> None:
    claims: list[ClaimRecord] = []
    query = ledger.expressed_query or ledger.user_request
    if query:
        query_claim_id = f"claim:{stable_id('expressed_query:' + query)}"
        claims.append(
            ClaimRecord(
                claim_id=query_claim_id,
                text=f"Expressed query q: {query}",
                claim_type="fact",
                support_ids=["user_signal"],
                verifier_state="supported",
            )
        )
    else:
        query_claim_id = ""
    for belief in ledger.latent_type_beliefs[:5]:
        claims.append(
            ClaimRecord(
                claim_id=f"claim:{stable_id('belief:' + belief.type_id)}",
                text=f"Latent type belief {belief.type_id}: {belief.description} p={belief.probability}",
                claim_type="inference",
                support_ids=["user_signal"],
                depends_on=[query_claim_id] if query_claim_id else [],
                verifier_state="supported" if belief.evidence_signals != ["prior"] else "unverified",
            )
        )
    for commitment in ledger.commitments[:20]:
        claims.append(
            ClaimRecord(
                claim_id=f"claim:{stable_id('commitment:' + commitment.commitment_id)}",
                text=f"Commitment {commitment.field}: {commitment.value}",
                claim_type="constraint" if commitment.field.startswith("constraint") else "inference",
                support_ids=[commitment.commitment_id],
                verifier_state="supported" if commitment.status == "accepted" else "needs_evidence",
            )
        )
    for evidence in ledger.evidence[:20]:
        claims.append(
            ClaimRecord(
                claim_id=f"claim:{stable_id('evidence:' + evidence.evidence_id)}",
                text=f"Evidence claim from {evidence.source_type}: {evidence.title}",
                claim_type="fact",
                support_ids=[evidence.evidence_id],
                verifier_state="supported" if evidence.used else "unverified",
            )
        )
    for assumption in ledger.assumptions[:12]:
        claims.append(
            ClaimRecord(
                claim_id=f"claim:{stable_id('assumption:' + assumption)}",
                text=assumption,
                claim_type="assumption",
                support_ids=[],
                verifier_state="needs_evidence",
            )
        )
    claims.append(
        ClaimRecord(
            claim_id=f"claim:{stable_id('gate:' + ledger.decision_gate)}",
            text=f"Decision gate remains {ledger.decision_gate}.",
            claim_type="decision_gate",
            support_ids=[record.task_name for record in ledger.formalization_records[-1:]],
            verifier_state="blocked" if ledger.decision_gate == "needs_more_info" else "unverified",
        )
    )
    ledger.claim_graph = dedupe_claims(claims)[-MAX_TRACE_STEPS:]


def dedupe_claims(claims: list[ClaimRecord]) -> list[ClaimRecord]:
    seen: set[str] = set()
    result: list[ClaimRecord] = []
    for claim in claims:
        if claim.claim_id in seen:
            continue
        seen.add(claim.claim_id)
        result.append(claim)
    return result


def sync_user_endorsement(ledger: SpecLedger) -> None:
    present = [field for field in MATERIAL_FIELDS if getattr(ledger, field)]
    missing = [field for field in MATERIAL_FIELDS if not getattr(ledger, field)]
    rejected = sorted({item.field for item in ledger.ambiguities if item.severity == "high"})
    endorsed = [field for field in present if field not in rejected]
    ledger.user_endorsement = UserEndorsementState(
        endorsed_fields=endorsed,
        rejected_fields=rejected,
        pending_fields=missing,
        last_signal=(ledger.expressed_query or ledger.user_request or None),
    )


def build_game_states(ledger: SpecLedger) -> list[GameStageState]:
    material_missing = [field for field in MATERIAL_FIELDS if not getattr(ledger, field)]
    required_search = [item for item in ledger.search_plan if item.required]
    unsatisfied_search = [item.purpose for item in required_search if item.status != "satisfied"]
    high_findings = [item.message for item in ledger.verification_findings if item.severity == "high"]
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    formal_missing = latest_formal.missing_obligations if latest_formal and latest_formal.is_valid != 1 else []
    return [
        GameStageState(
            stage_id="elicitation",
            game_type="cooperative_partial_information",
            objective="Recover theta from lossy query q with minimal user burden.",
            success_condition="Material fields and top latent type are explicit.",
            status="blocked" if material_missing else "satisfied",
            blocking_conditions=material_missing,
        ),
        GameStageState(
            stage_id="dialogue_commitment",
            game_type="signaling_commitment_under_asymmetric_information",
            objective="Convert signals into explicit commitments and accepted assumptions.",
            success_condition="Goal, audience, format, constraints, and proof obligations are committed.",
            status="satisfied" if ledger.commitments and not material_missing else "active",
            blocking_conditions=material_missing,
        ),
        GameStageState(
            stage_id="retrieval",
            game_type="evidence_selection_game",
            objective="Select sources that reduce specification uncertainty.",
            success_condition="Required evidence plan is satisfied or explicitly blocked.",
            status="satisfied" if required_search and not unsatisfied_search else "active" if required_search else "open",
            blocking_conditions=unsatisfied_search[:8],
        ),
        GameStageState(
            stage_id="execution_graph",
            game_type="full_information_graph_game",
            objective="Run model, tool, verifier, async, and human-review nodes under explicit gates.",
            success_condition="Formal obligations are complete before decision-ready output.",
            status="blocked" if formal_missing else "satisfied" if latest_formal else "active",
            blocking_conditions=formal_missing,
        ),
        GameStageState(
            stage_id="verification",
            game_type="adversarial_verification_game",
            objective="Block unsupported claims, unsafe paths, and specification gaming.",
            success_condition="No high-severity verifier findings remain.",
            status="blocked" if high_findings else "satisfied" if ledger.verification_findings else "active",
            blocking_conditions=high_findings,
        ),
    ]


def compute_spec_convergence(ledger: SpecLedger) -> SpecConvergenceState:
    material = sum(1 for field in MATERIAL_FIELDS if getattr(ledger, field)) / len(MATERIAL_FIELDS)
    required_search = [item for item in ledger.search_plan if item.required]
    evidence = (
        sum(1 for item in required_search if item.status == "satisfied") / len(required_search)
        if required_search
        else 1.0
    )
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    formal = float((latest_formal.metrics or {}).get("coverage", 0.0)) if latest_formal else 0.0
    endorsement_total = (
        len(ledger.user_endorsement.endorsed_fields)
        + len(ledger.user_endorsement.pending_fields)
        + len(ledger.user_endorsement.rejected_fields)
    )
    endorsement = (
        len(ledger.user_endorsement.endorsed_fields) / endorsement_total
        if endorsement_total
        else 0.0
    )
    if any(item.severity == "high" for item in ledger.verification_findings):
        verification = 0.0
    elif ledger.verification_findings:
        verification = 0.65
    elif latest_formal and latest_formal.is_valid == 1:
        verification = 1.0
    else:
        verification = 0.25
    overall = round(
        0.22 * material
        + 0.22 * evidence
        + 0.24 * formal
        + 0.12 * endorsement
        + 0.20 * verification,
        4,
    )
    if overall >= 0.92 and verification == 1.0 and ledger.decision_gate != "needs_more_info":
        status = "verified"
    elif overall >= 0.82 and formal >= 1.0:
        status = "executable"
    elif overall >= 0.45:
        status = "negotiating"
    else:
        status = "diverged"
    return SpecConvergenceState(
        material_resolution=round(material, 4),
        evidence_resolution=round(evidence, 4),
        formalization_resolution=round(formal, 4),
        endorsement_resolution=round(endorsement, 4),
        verification_resolution=round(verification, 4),
        overall=overall,
        status=status,
    )


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
    update_mutual_spec_game_state(ledger)
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
    update_mutual_spec_game_state(ledger)
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
        "Search/tool evidence must be retrieved through Google Agent SDK search tools, MCP/Opoint, Google CSE/RAG, Vertex AI Search, or explicitly marked model-only evidence before market claims are trusted.",
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
    plan_trader_evidence_search(ledger, text)


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
        "trader",
        "trade",
        "mutual specification",
        "specification game",
        "latent intent",
    )
    return any(token in lower for token in trader_tokens)


def add_unique(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def plan_trader_evidence_search(ledger: SpecLedger, text: str) -> SpecLedger:
    """Attach evidence search obligations for compressed trader queries."""

    if not looks_like_trader_query(text):
        return ledger
    lower = text.lower()
    required = trader_required_evidence(lower)
    queries = trader_search_queries(lower)
    for index, (query, purpose) in enumerate(zip(queries, required, strict=False), start=1):
        add_search_plan_item(
            ledger,
            query=query,
            purpose=purpose,
            query_id=f"trader:{stable_id(query)}:{index}",
        )
    for purpose in required:
        add_unique(ledger.verification_conditions, purpose)
    record_evidence_source(
        ledger,
        EvidenceSourceStatus(
            source_type="model",
            name="model_hypothesis",
            status="planned",
            detail=(
                "Model reasoning may propose hypotheses, but market facts need "
                "search/tool evidence before the gate can clear."
            ),
            confidence="low",
        ),
    )
    return ledger


def trader_required_evidence(lower_text: str) -> list[str]:
    if "mutual specification" in lower_text or "specification game" in lower_text:
        return [
            "Prior Mutual Specification Game mechanics and user-talk ledgers.",
            "Game-theory framing for hidden types, signaling, commitment, and verification.",
            "Human-AI collaboration constraints: compatibility, bounded memory, and true user welfare.",
            "Proof-carrying response structure: assumptions, claims, dependencies, tests, and verifier states.",
        ]
    base = [
        "Price benchmark and market context near the offer date.",
        "Product specification, grade, quantity tolerance, and inspection standard.",
        "Counterparty identity, title chain, documents, and payment terms.",
        "Port/loading terms, laycan, berth constraints, demurrage, freight, and insurance.",
        "Sanctions, compliance, route, bankability, and political risk.",
        "Resale path, buyer demand, hedge/proxy availability, and netback economics.",
    ]
    if "sulfur" in lower_text or "sulphur" in lower_text:
        return base
    return base[:4]


def trader_search_queries(lower_text: str) -> list[str]:
    if "mutual specification" in lower_text or "specification game" in lower_text:
        return [
            '"Mutual Specification Game" trader latent task',
            '"specification ledger" "proof-carrying response"',
            '"Bayesian game" "signaling game" "human-AI"',
            '"trader" "latent intent" "executable specification"',
        ]
    if "sulfur" in lower_text or "sulphur" in lower_text:
        return [
            '"sulfur" "Umm Qasr" FOB price',
            '"Iraq" sulfur export "Umm Qasr"',
            '"sulfur" market price Middle East FOB',
            '"Umm Qasr" port sulfur cargo loading inspection',
            '"Iraq" "Umm Qasr" sanctions shipping payment sulfur',
            '"sulfur" 50000 tonnes FOB offer counterparty risk',
        ]
    terms = extract_trader_search_terms(lower_text)
    base = " ".join(terms[:5]) or "commodity offer"
    return [
        f"{base} market price benchmark",
        f"{base} logistics freight port risk",
        f"{base} counterparty payment terms sanctions",
        f"{base} specification inspection documents",
    ]


def extract_trader_search_terms(lower_text: str) -> list[str]:
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
        "trader",
        "trade",
        "mutual specification",
        "specification game",
        "latent intent",
    )
    return [term for term in candidates if term in lower_text]


def add_search_plan_item(
    ledger: SpecLedger,
    *,
    query: str,
    purpose: str,
    query_id: str | None = None,
    preferred_sources: list[
        Literal[
            "google_agent_search",
            "google_cse",
            "mcp",
            "model",
            "opoint",
            "spanner_rag",
            "vertex_ai_search",
        ]
    ]
    | None = None,
) -> SearchPlanItem:
    item = SearchPlanItem(
        query_id=query_id or f"search:{stable_id(query)}",
        query=query,
        purpose=purpose,
        preferred_sources=preferred_sources
        or [
            "google_agent_search",
            "mcp",
            "google_cse",
            "opoint",
            "spanner_rag",
            "vertex_ai_search",
            "model",
        ],
    )
    for index, existing in enumerate(ledger.search_plan):
        if existing.query_id == item.query_id or existing.query == item.query:
            ledger.search_plan[index] = existing.model_copy(
                update={
                    "purpose": item.purpose,
                    "preferred_sources": item.preferred_sources,
                }
            )
            return ledger.search_plan[index]
    ledger.search_plan.append(item)
    return item


def record_evidence_source(
    ledger: SpecLedger,
    source: EvidenceSourceStatus,
) -> SpecLedger:
    for index, existing in enumerate(ledger.evidence_sources):
        if existing.source_type == source.source_type and existing.name == source.name:
            ledger.evidence_sources[index] = source
            return ledger
    ledger.evidence_sources.append(source)
    return ledger


def add_external_evidence(
    ledger: SpecLedger,
    *,
    source_type: Literal[
        "google_agent_search",
        "google_cse",
        "mcp",
        "model",
        "rag",
        "spanner_rag",
        "vertex_ai_search",
        "web",
    ],
    title: str,
    summary: str,
    uri: str | None = None,
    source_name: str | None = None,
    query: str | None = None,
    confidence: Literal["low", "medium", "high"] = "medium",
    used: bool = True,
) -> EvidenceRef:
    key = uri or f"{source_type}:{source_name or title}:{summary[:120]}"
    evidence = EvidenceRef(
        evidence_id=f"{source_type}:{stable_id(key)}",
        source_type=source_type,
        title=title[:180] or source_type,
        uri=uri,
        summary=summary[:1500],
        source_name=source_name,
        query=query,
        confidence=confidence,
        used=used,
    )
    for index, existing in enumerate(ledger.evidence):
        if existing.evidence_id == evidence.evidence_id:
            ledger.evidence[index] = evidence
            refresh_evidence_used(ledger)
            return ledger.evidence[index]
    ledger.evidence.append(evidence)
    satisfy_search_plan_from_query(ledger, query)
    refresh_evidence_used(ledger)
    update_mutual_spec_game_state(ledger)
    return evidence


def add_model_evidence_summary(
    ledger: SpecLedger,
    *,
    model_name: str,
    summary: str,
    query: str | None = None,
) -> EvidenceRef:
    record_evidence_source(
        ledger,
        EvidenceSourceStatus(
            source_type="model",
            name=model_name,
            status="retrieved",
            detail="Model-generated evidence summary; use as hypothesis unless grounded by search/tool evidence.",
            confidence="low",
        ),
    )
    return add_external_evidence(
        ledger,
        source_type="model",
        title=f"Model evidence summary from {model_name}",
        summary=summary,
        uri=f"model://{model_name}",
        source_name=model_name,
        query=query,
        confidence="low",
    )


def satisfy_search_plan_from_query(ledger: SpecLedger, query: str | None) -> None:
    if not query:
        return
    for index, item in enumerate(ledger.search_plan):
        if item.query == query:
            ledger.search_plan[index] = item.model_copy(update={"status": "satisfied"})


def refresh_evidence_used(ledger: SpecLedger) -> None:
    ledger.evidence_used = sorted({item.evidence_id for item in ledger.evidence if item.used})


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def add_evidence_from_artifacts(ledger: SpecLedger) -> SpecLedger:
    if ledger.artifact_refs:
        record_evidence_source(
            ledger,
            EvidenceSourceStatus(
                source_type="artifact",
                name="adk_artifacts",
                status="retrieved",
                detail=f"{len(ledger.artifact_refs)} artifact reference(s) available.",
                confidence="medium",
            ),
        )
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
                source_name="adk_artifacts",
                confidence="medium",
                used=True,
            )
        )
    refresh_evidence_used(ledger)
    update_mutual_spec_game_state(ledger)
    return ledger


def add_mcp_evidence_placeholder(ledger: SpecLedger, source_uri: str) -> SpecLedger:
    record_evidence_source(
        ledger,
        EvidenceSourceStatus(
            source_type="mcp",
            name="configured_mcp_research",
            status="configured",
            detail=source_uri,
            confidence="medium",
        ),
    )
    evidence_id = f"mcp:{stable_id(source_uri)}"
    if not any(item.evidence_id == evidence_id for item in ledger.evidence):
        ledger.evidence.append(
            EvidenceRef(
                evidence_id=evidence_id,
                source_type="mcp",
                title="Configured MCP research source",
                uri=source_uri,
                summary="External evidence retrieval is configured through an MCP toolset.",
                source_name="configured_mcp_research",
                confidence="medium",
                used=True,
            )
        )
    refresh_evidence_used(ledger)
    update_mutual_spec_game_state(ledger)
    return ledger
