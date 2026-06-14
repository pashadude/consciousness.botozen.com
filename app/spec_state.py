"""Serializable session.state ledger for the Mutual Specification Game."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

STATE_KEY = "spec_ledger"
MAX_TRACE_STEPS = 80
MATERIAL_FIELDS: tuple[str, ...] = ("goal", "audience", "output_format")
LEAN_PROOF_PREAMBLE = """\
structure SpecGate where
  needsMoreInfo : Prop
  openProofs : Prop
  humanReviewRequired : Prop
  highSeverityFindings : Prop

def finalizeAllowed (g : SpecGate) : Prop :=
  Not g.needsMoreInfo
  ∧ Not g.openProofs
  ∧ Not g.humanReviewRequired
  ∧ Not g.highSeverityFindings
"""


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


class ProofObligationRecord(BaseModel):
    """Verifier work item that must be proved, waived, or kept blocked."""

    obligation_id: str
    source_type: Literal[
        "artifact",
        "claim",
        "evidence",
        "formalization",
        "human_review",
        "verifier_finding",
    ]
    source_id: str
    statement: str
    required: bool = True
    status: Literal["open", "satisfied", "blocked", "waived"] = "open"
    remediation: str | None = None


class GameActionPayoff(BaseModel):
    """Multicriteria payoff estimate for one possible next action."""

    action: Literal["ask", "retrieve", "review", "propose", "finalize", "defer"]
    specification_gain: float = 0.0
    risk_reduction: float = 0.0
    user_burden: float = 0.0
    latency_cost: float = 0.0
    policy_penalty: float = 0.0
    dominated: bool = False
    rationale: str = ""


class EquilibriumDiagnosticState(BaseModel):
    """Action dominance diagnostic for the current specification game."""

    diagnostic_id: str = Field(default_factory=lambda: f"eq-{uuid4().hex[:12]}")
    recommended_action: Literal["ask", "retrieve", "review", "propose", "finalize", "defer"] = "ask"
    solution_concept: str = "multicriteria_action_dominance"
    payoffs: list[GameActionPayoff] = Field(default_factory=list)
    dominated_actions: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    rationale: str = ""


class FormalProofCheckRecord(BaseModel):
    """Optional Lean check for a narrow formal gate invariant."""

    check_id: str
    backend: Literal["lean"] = "lean"
    theorem_name: str
    source_type: Literal[
        "decision_gate",
        "proof_obligations",
        "human_review",
        "verifier_findings",
        "gate_clear",
    ]
    source_id: str
    statement: str
    status: Literal[
        "not_applicable",
        "generated",
        "checked",
        "failed",
        "unavailable",
        "skipped",
    ] = "generated"
    lean_code: str
    output: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class FormalProofAssistantState(BaseModel):
    """Lean-only bridge for formal, non-empirical proof checks."""

    backend: Literal["lean"] = "lean"
    scope: str = (
        "Lean checks only narrow logical gate invariants. Market, logistics, "
        "sanctions, counterparty, and document claims remain empirical proof obligations."
    )
    status: Literal[
        "not_applicable",
        "unavailable",
        "generated",
        "checked",
        "failed",
    ] = "not_applicable"
    executable_path: str | None = None
    checks: list[FormalProofCheckRecord] = Field(default_factory=list)


class UserEndorsementState(BaseModel):
    """Whether the user has endorsed the current shared specification."""

    endorsed_fields: list[str] = Field(default_factory=list)
    rejected_fields: list[str] = Field(default_factory=list)
    pending_fields: list[str] = Field(default_factory=list)
    last_signal: str | None = None


class HumanReviewState(BaseModel):
    """Operator-facing review packet for high-stakes or blocked specs."""

    review_id: str = Field(default_factory=lambda: f"review-{uuid4().hex[:12]}")
    required: bool = False
    status: Literal[
        "not_required",
        "queued",
        "in_review",
        "approved",
        "changes_requested",
        "rejected",
    ] = "not_required"
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    assigned_player: str = "human_reviewer"
    decision_owner: str = "user"
    reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    blocking_claim_ids: list[str] = Field(default_factory=list)
    blocking_evidence: list[str] = Field(default_factory=list)
    approval_conditions: list[str] = Field(default_factory=list)
    last_reviewer_signal: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SkillCompatibilityState(BaseModel):
    """How the output should fit the user's ability to verify and continue."""

    inferred_role: str = "unknown"
    skill_level: Literal["unknown", "novice", "intermediate", "expert"] = "unknown"
    domain_familiarity: Literal["low", "medium", "high"] = "low"
    cognitive_burden: Literal["low", "medium", "high"] = "medium"
    recommended_depth: Literal["brief", "standard", "deep"] = "standard"
    handoff_format: Literal[
        "clarification_question",
        "decision_frame",
        "implementation_plan",
        "proof_packet",
        "review_packet",
    ] = "clarification_question"
    compatibility_risks: list[str] = Field(default_factory=list)
    evidence_required_for_handoff: list[str] = Field(default_factory=list)
    next_best_action: Literal["ask", "summarize", "retrieve", "review", "execute_spec"] = "ask"
    learned_from: list[str] = Field(default_factory=lambda: ["current_query"])


class SpecConvergenceState(BaseModel):
    """Compact convergence score for q -> theta -> executable specification."""

    material_resolution: float = 0.0
    evidence_resolution: float = 0.0
    formalization_resolution: float = 0.0
    endorsement_resolution: float = 0.0
    verification_resolution: float = 0.0
    human_review_resolution: float = 0.0
    skill_compatibility_resolution: float = 0.0
    proof_obligation_resolution: float = 0.0
    formal_proof_resolution: float = 0.0
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
    proof_obligations: list[ProofObligationRecord] = Field(default_factory=list)
    equilibrium_diagnostics: EquilibriumDiagnosticState = Field(
        default_factory=EquilibriumDiagnosticState
    )
    formal_proofs: FormalProofAssistantState = Field(default_factory=FormalProofAssistantState)
    user_endorsement: UserEndorsementState = Field(default_factory=UserEndorsementState)
    human_review: HumanReviewState = Field(default_factory=HumanReviewState)
    skill_compatibility: SkillCompatibilityState = Field(default_factory=SkillCompatibilityState)
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
    prompt. It is not a full equilibrium solver and only runs an external
    Lean theorem prover when explicitly requested through the console path.
    """

    ensure_game_players(ledger)
    ledger.latent_type_beliefs = infer_latent_type_beliefs(ledger)
    sync_commitments(ledger)
    sync_claim_graph(ledger)
    sync_user_endorsement(ledger)
    sync_human_review_state(ledger)
    sync_skill_compatibility_state(ledger)
    sync_proof_obligations(ledger)
    sync_decision_gate_state(ledger)
    sync_claim_graph(ledger)
    sync_human_review_state(ledger)
    sync_skill_compatibility_state(ledger)
    sync_proof_obligations(ledger)
    sync_equilibrium_diagnostics(ledger)
    sync_formal_proof_checks(ledger)
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
    open_proofs = [
        item.statement
        for item in ledger.proof_obligations
        if item.status not in {"satisfied", "waived"}
    ]
    blocked_proofs = [
        item.statement for item in ledger.proof_obligations if item.status == "blocked"
    ]
    states = [
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
            stage_id="proof_obligations",
            game_type="proof_carrying_response_game",
            objective="Reduce claims, evidence gaps, review requirements, and verifier findings to explicit proof work.",
            success_condition="Every required proof obligation is satisfied or explicitly waived.",
            status="blocked" if blocked_proofs else "active" if open_proofs else "satisfied",
            blocking_conditions=(blocked_proofs or open_proofs)[:8],
        ),
        GameStageState(
            stage_id="equilibrium_diagnostics",
            game_type="multicriteria_action_dominance_game",
            objective="Identify which next actions are dominated under specification gain, risk reduction, burden, latency, and policy constraints.",
            success_condition="The recommended action is nondominated and its conflicts are explicit.",
            status="satisfied" if ledger.equilibrium_diagnostics.payoffs else "open",
            blocking_conditions=ledger.equilibrium_diagnostics.unresolved_conflicts[:8],
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
    review = ledger.human_review
    if review.required:
        review_status = "satisfied" if review.status == "approved" else "blocked"
        review_blocks = review.required_actions[:8] or review.reasons[:8]
    else:
        review_status = "satisfied"
        review_blocks = []
    states.append(
        GameStageState(
            stage_id="human_review",
            game_type="skill_compatible_review_game",
            objective="Keep high-stakes decisions compatible with the user's skill, evidence, and decision ownership.",
            success_condition="Human reviewer approves, rejects, or requests more evidence before a decision-ready gate clears.",
            status=review_status,
            blocking_conditions=review_blocks,
        )
    )
    proof_status = ledger.formal_proofs.status
    if proof_status == "failed":
        formal_proof_status = "blocked"
        proof_blocks = [
            item.theorem_name
            for item in ledger.formal_proofs.checks
            if item.status == "failed"
        ]
    elif proof_status in {"checked", "not_applicable"}:
        formal_proof_status = "satisfied"
        proof_blocks = []
    else:
        formal_proof_status = "active"
        proof_blocks = [
            "Lean executable is unavailable; generated checks are inspectable but not machine-checked."
            if proof_status == "unavailable"
            else "Lean checks are generated but not executed."
        ]
    states.append(
        GameStageState(
            stage_id="formal_proofs",
            game_type="lean_gate_invariant_game",
            objective="Machine-check narrow logical gate invariants when Lean is available.",
            success_condition="Lean checks pass or no formal gate invariant applies.",
            status=formal_proof_status,
            blocking_conditions=proof_blocks[:8],
        )
    )
    return states


def sync_human_review_state(ledger: SpecLedger) -> None:
    previous = ledger.human_review
    text = (ledger.expressed_query or ledger.user_request).lower()
    required_search = [item for item in ledger.search_plan if item.required]
    unsatisfied_search = [item for item in required_search if item.status != "satisfied"]
    high_findings = [item for item in ledger.verification_findings if item.severity == "high"]
    medium_findings = [item for item in ledger.verification_findings if item.severity == "medium"]
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    formal_missing = latest_formal.missing_obligations if latest_formal and latest_formal.is_valid != 1 else []
    blocking_claims = [
        item
        for item in ledger.claim_graph
        if item.verifier_state in {"needs_evidence", "blocked", "refuted"}
    ]
    artifact_blocks = [
        artifact.filename
        for artifact in ledger.artifact_refs
        if artifact.mime_type
        and (artifact.mime_type.startswith("image/") or artifact.mime_type.startswith("audio/"))
    ]

    reasons: list[str] = []
    required_actions: list[str] = []
    blocking_evidence: list[str] = []
    approval_conditions = [
        "No high-severity verifier findings remain.",
        "Required evidence is retrieved, inspected, or explicitly waived with rationale.",
        "Unsupported claim-graph nodes are supported, downgraded to assumptions, or removed.",
        "The user remains final decision owner; the agent does not execute trades or broker actions.",
    ]

    high_stakes = requires_human_review_for_query(text)
    if high_stakes:
        reasons.append("Compressed high-stakes trader or physical commodity decision signal.")
        required_actions.append("Confirm the trader-facing decision frame and preserve user decision ownership.")
    if ledger.decision_gate == "needs_more_info" and high_stakes:
        reasons.append("Decision gate is still needs_more_info for a high-stakes prompt.")
    if high_findings:
        reasons.append("High-severity verifier findings block approval.")
        required_actions.extend(f"Resolve verifier finding: {item.message}" for item in high_findings[:6])
    if formal_missing and high_stakes:
        reasons.append("Formalization obligations are incomplete.")
        required_actions.append("Resolve missing formal obligation(s): " + ", ".join(formal_missing[:8]))
    if unsatisfied_search and high_stakes:
        reasons.append("Required source/evidence plan is not fully satisfied.")
        for item in unsatisfied_search[:8]:
            blocking_evidence.append(item.purpose)
            required_actions.append(f"Retrieve or attach evidence for: {item.purpose}")
    if blocking_claims and high_stakes:
        reasons.append("Claim graph contains blocked or evidence-needed nodes.")
        required_actions.append("Review blocking claim IDs and either support, waive, or remove them.")
    if artifact_blocks and high_stakes:
        reasons.append("Uploaded image/audio artifacts need inspection before material claims can clear.")
        required_actions.extend(f"Inspect or transcribe artifact before relying on it: {name}" for name in artifact_blocks[:6])
    if medium_findings and high_stakes:
        reasons.append("Medium-severity verifier findings require reviewer awareness.")

    required = bool(reasons)
    if high_findings:
        risk_level = "critical"
    elif high_stakes:
        risk_level = "high"
    else:
        risk_level = "low"

    approval_still_valid = previous.status == "approved" and not (
        high_findings
        or (
            high_stakes
            and (formal_missing or unsatisfied_search or blocking_claims or artifact_blocks)
        )
    )
    if not required:
        status = "not_required"
    elif approval_still_valid:
        status = "approved"
    elif previous.status in {"in_review", "changes_requested", "rejected"}:
        status = previous.status
    else:
        status = "queued"

    ledger.human_review = HumanReviewState(
        review_id=previous.review_id,
        required=required,
        status=status,
        risk_level=risk_level,
        assigned_player=previous.assigned_player or "human_reviewer",
        decision_owner=previous.decision_owner or "user",
        reasons=dedupe_text(reasons)[:12],
        required_actions=dedupe_text(required_actions)[:16],
        blocking_claim_ids=[item.claim_id for item in blocking_claims[:16]],
        blocking_evidence=dedupe_text(blocking_evidence)[:16],
        approval_conditions=approval_conditions,
        last_reviewer_signal=previous.last_reviewer_signal,
        created_at=previous.created_at,
        updated_at=datetime.now(UTC).isoformat(),
    )


def requires_human_review_for_query(text: str) -> bool:
    if not looks_like_trader_query(text):
        return False
    decision_tokens = (
        "should i",
        "go for it",
        "accept",
        "buy",
        "sell",
        "execute",
        "order",
        "position",
        "offer",
        "fob",
        "cfr",
        "cif",
        "counterparty",
        "sanction",
        "cargo",
        "ton",
        "tonne",
        "tonns",
        "mt",
    )
    architecture_tokens = (
        "mutual specification",
        "specification game",
        "model zoo",
        "implement",
        "architecture",
    )
    if any(token in text for token in architecture_tokens) and not any(
        token in text for token in ("should i", "go for it", "offer", "buy", "sell", "execute")
    ):
        return False
    return any(token in text for token in decision_tokens)


def dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def sync_proof_obligations(ledger: SpecLedger) -> None:
    obligations: list[ProofObligationRecord] = []
    for item in ledger.search_plan:
        if not item.required:
            continue
        obligations.append(
            ProofObligationRecord(
                obligation_id=f"proof:{stable_id('evidence:' + item.query_id)}",
                source_type="evidence",
                source_id=item.query_id,
                statement=f"Evidence plan must satisfy: {item.purpose}",
                status="satisfied" if item.status == "satisfied" else "open",
                remediation=f"Retrieve, attach, or explicitly waive evidence for query: {item.query}",
            )
        )
    for claim in ledger.claim_graph:
        if claim.claim_type not in {"fact", "inference"}:
            continue
        if claim.verifier_state == "supported":
            continue
        status = "blocked" if claim.verifier_state in {"blocked", "refuted"} else "open"
        obligations.append(
            ProofObligationRecord(
                obligation_id=f"proof:{stable_id('claim:' + claim.claim_id)}",
                source_type="claim",
                source_id=claim.claim_id,
                statement=f"Claim requires support or downgrade: {claim.text}",
                status=status,
                remediation="Attach support IDs, downgrade to assumption, remove the claim, or keep the gate blocked.",
            )
        )
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    if latest_formal and latest_formal.is_valid != 1:
        for missing in latest_formal.missing_obligations:
            obligations.append(
                ProofObligationRecord(
                    obligation_id=f"proof:{stable_id('formal:' + latest_formal.task_name + ':' + missing)}",
                    source_type="formalization",
                    source_id=latest_formal.task_name,
                    statement=f"Formalization obligation is missing: {missing}",
                    status="open",
                    remediation="Resolve the formal obligation or keep the current decision gate blocked.",
                )
            )
    if ledger.human_review.required:
        obligations.append(
            ProofObligationRecord(
                obligation_id=f"proof:{stable_id('human_review:' + ledger.human_review.review_id)}",
                source_type="human_review",
                source_id=ledger.human_review.review_id,
                statement="Human review approval is required before a decision-ready handoff.",
                status="satisfied" if ledger.human_review.status == "approved" else "open",
                remediation="Approve, reject, or request more evidence in the human-review workflow.",
            )
        )
    for artifact in ledger.artifact_refs:
        if not artifact.mime_type or not (
            artifact.mime_type.startswith("image/") or artifact.mime_type.startswith("audio/")
        ):
            continue
        obligations.append(
            ProofObligationRecord(
                obligation_id=f"proof:{stable_id('artifact:' + artifact.artifact_id)}",
                source_type="artifact",
                source_id=artifact.artifact_id,
                statement=f"Artifact must be inspected or transcribed before material claims rely on it: {artifact.filename}",
                status="open",
                remediation="Inspect the image/PDF/audio or attach a verified transcript/extraction.",
            )
        )
    for index, finding in enumerate(ledger.verification_findings):
        if finding.severity == "low":
            continue
        obligations.append(
            ProofObligationRecord(
                obligation_id=f"proof:{stable_id(f'finding:{index}:{finding.message}')}",
                source_type="verifier_finding",
                source_id=f"finding:{index}",
                statement=f"Verifier finding must be resolved: {finding.message}",
                status="blocked" if finding.severity == "high" else "open",
                remediation=finding.remediation,
            )
        )
    ledger.proof_obligations = dedupe_proof_obligations(obligations)[-MAX_TRACE_STEPS:]


def sync_decision_gate_state(ledger: SpecLedger) -> None:
    unsatisfied_evidence = any(
        item.required and item.status != "satisfied" for item in ledger.search_plan
    )
    review_required = ledger.human_review.required and ledger.human_review.status != "approved"
    open_proofs = any(
        item.required and item.status not in {"satisfied", "waived"}
        for item in ledger.proof_obligations
    )
    high_findings = any(item.severity == "high" for item in ledger.verification_findings)
    material_artifact_open = any(
        item.source_type == "artifact" and item.status not in {"satisfied", "waived"}
        for item in ledger.proof_obligations
    )

    if high_findings:
        ledger.decision_gate = "blocked"
    elif ledger.ambiguities or unsatisfied_evidence or review_required or open_proofs or material_artifact_open:
        ledger.decision_gate = "needs_more_info"
    else:
        ledger.decision_gate = "analysis_ready"


def dedupe_proof_obligations(
    obligations: list[ProofObligationRecord],
) -> list[ProofObligationRecord]:
    seen: set[str] = set()
    result: list[ProofObligationRecord] = []
    for obligation in obligations:
        if obligation.obligation_id in seen:
            continue
        seen.add(obligation.obligation_id)
        result.append(obligation)
    return result


def sync_equilibrium_diagnostics(ledger: SpecLedger) -> None:
    previous = ledger.equilibrium_diagnostics
    missing_material = bool(ledger.ambiguities)
    unsatisfied_evidence = any(item.required and item.status != "satisfied" for item in ledger.search_plan)
    review_required = ledger.human_review.required and ledger.human_review.status != "approved"
    open_proofs = any(
        item.required and item.status not in {"satisfied", "waived"}
        for item in ledger.proof_obligations
    )
    open_formal_proofs = any(
        item.required
        and item.source_type == "formalization"
        and item.status not in {"satisfied", "waived"}
        for item in ledger.proof_obligations
    )
    high_findings = any(item.severity == "high" for item in ledger.verification_findings)
    gate_blocked = ledger.decision_gate == "needs_more_info"

    payoffs = [
        GameActionPayoff(
            action="ask",
            specification_gain=0.85 if missing_material or open_formal_proofs else 0.25,
            risk_reduction=0.45 if missing_material or open_formal_proofs else 0.15,
            user_burden=0.65,
            latency_cost=0.10,
            policy_penalty=0.0,
            rationale="Best when material fields or formal specification obligations are missing; otherwise adds user burden.",
        ),
        GameActionPayoff(
            action="retrieve",
            specification_gain=0.85 if unsatisfied_evidence or (open_proofs and not open_formal_proofs) else 0.25,
            risk_reduction=0.70 if unsatisfied_evidence or (open_proofs and not open_formal_proofs) else 0.20,
            user_burden=0.20,
            latency_cost=0.55,
            policy_penalty=0.0,
            rationale="Best when external evidence or non-formal proof obligations are open.",
        ),
        GameActionPayoff(
            action="review",
            specification_gain=0.70 if review_required else 0.15,
            risk_reduction=0.90 if review_required or high_findings else 0.20,
            user_burden=0.55,
            latency_cost=0.50,
            policy_penalty=0.0,
            rationale="Best when user agency, high stakes, or reviewer approval is required.",
        ),
        GameActionPayoff(
            action="propose",
            specification_gain=0.55 if not missing_material else 0.20,
            risk_reduction=0.35 if not high_findings else 0.05,
            user_burden=0.30,
            latency_cost=0.20,
            policy_penalty=0.25 if open_proofs or review_required else 0.0,
            rationale="Useful for a provisional spec, but unsafe if proof/review gates are still open.",
        ),
        GameActionPayoff(
            action="finalize",
            specification_gain=0.35 if not (gate_blocked or open_proofs or review_required) else 0.05,
            risk_reduction=0.20 if not (gate_blocked or open_proofs or review_required) else 0.0,
            user_burden=0.10,
            latency_cost=0.10,
            policy_penalty=1.0 if gate_blocked or open_proofs or review_required or high_findings else 0.0,
            rationale="Only nondominated when all hard gates are clear.",
        ),
        GameActionPayoff(
            action="defer",
            specification_gain=0.45 if high_findings else 0.20,
            risk_reduction=0.80 if high_findings else 0.35,
            user_burden=0.10,
            latency_cost=0.85,
            policy_penalty=0.0,
            rationale="Useful when async repair or deeper research is better than immediate response.",
        ),
    ]
    payoffs = mark_dominated_actions(payoffs)
    dominated_actions = [item.action for item in payoffs if item.dominated]
    conflicts = equilibrium_conflicts(
        gate_blocked=gate_blocked,
        open_proofs=open_proofs,
        open_formal_proofs=open_formal_proofs,
        review_required=review_required,
        high_findings=high_findings,
        unsatisfied_evidence=unsatisfied_evidence,
    )
    recommended = recommended_equilibrium_action(
        missing_material=missing_material,
        unsatisfied_evidence=unsatisfied_evidence,
        review_required=review_required,
        open_formal_proofs=open_formal_proofs,
        open_proofs=open_proofs,
        high_findings=high_findings,
        gate_blocked=gate_blocked,
    )
    ledger.equilibrium_diagnostics = EquilibriumDiagnosticState(
        diagnostic_id=previous.diagnostic_id,
        recommended_action=recommended,
        payoffs=payoffs,
        dominated_actions=dominated_actions,
        unresolved_conflicts=conflicts,
        rationale=equilibrium_rationale(recommended, conflicts),
    )


def sync_formal_proof_checks(ledger: SpecLedger) -> None:
    """Generate Lean checks for formal gate invariants only.

    This function never validates empirical market/logistics facts and does not
    execute Lean. Call ``run_lean_formal_proofs`` when a Lean executable is
    intentionally available.
    """

    previous_checks = {item.check_id: item for item in ledger.formal_proofs.checks}
    checks = []
    for item in build_formal_proof_checks(ledger):
        previous = previous_checks.get(item.check_id)
        if previous and previous.lean_code == item.lean_code and previous.status in {
            "checked",
            "failed",
        }:
            checks.append(previous)
        else:
            checks.append(item)
    executable_path = shutil.which("lean")
    if not checks:
        status = "not_applicable"
    elif executable_path:
        if any(item.status == "failed" for item in checks):
            status = "failed"
        elif all(item.status == "checked" for item in checks):
            status = "checked"
        else:
            status = "generated"
    else:
        status = "unavailable"
        checks = [
            item
            if item.status in {"checked", "failed"}
            else item.model_copy(update={"status": "unavailable"})
            for item in checks
        ]
    ledger.formal_proofs = FormalProofAssistantState(
        status=status,
        executable_path=executable_path,
        checks=checks,
    )


def build_formal_proof_checks(ledger: SpecLedger) -> list[FormalProofCheckRecord]:
    checks: list[FormalProofCheckRecord] = []
    open_proofs = any(
        item.required and item.status not in {"satisfied", "waived"}
        for item in ledger.proof_obligations
    )
    review_required = ledger.human_review.required and ledger.human_review.status != "approved"
    high_findings = any(item.severity == "high" for item in ledger.verification_findings)
    if ledger.decision_gate == "needs_more_info":
        checks.append(
            lean_check_record(
                theorem_name="no_finalize_when_needs_more_info",
                source_type="decision_gate",
                source_id=ledger.decision_gate,
                statement="A spec that needs more information cannot be finalized.",
                theorem_body=(
                    "theorem no_finalize_when_needs_more_info (g : SpecGate) :\n"
                    "  g.needsMoreInfo -> Not (finalizeAllowed g) := by\n"
                    "  intro h allowed\n"
                    "  exact allowed.left h\n"
                ),
            )
        )
    if open_proofs:
        checks.append(
            lean_check_record(
                theorem_name="no_finalize_with_open_proofs",
                source_type="proof_obligations",
                source_id="open_proof_obligations",
                statement="A spec with open proof obligations cannot be finalized.",
                theorem_body=(
                    "theorem no_finalize_with_open_proofs (g : SpecGate) :\n"
                    "  g.openProofs -> Not (finalizeAllowed g) := by\n"
                    "  intro h allowed\n"
                    "  exact allowed.right.left h\n"
                ),
            )
        )
    if review_required:
        checks.append(
            lean_check_record(
                theorem_name="no_finalize_with_required_review",
                source_type="human_review",
                source_id=ledger.human_review.review_id,
                statement="A spec requiring unapproved human review cannot be finalized.",
                theorem_body=(
                    "theorem no_finalize_with_required_review (g : SpecGate) :\n"
                    "  g.humanReviewRequired -> Not (finalizeAllowed g) := by\n"
                    "  intro h allowed\n"
                    "  exact allowed.right.right.left h\n"
                ),
            )
        )
    if high_findings:
        checks.append(
            lean_check_record(
                theorem_name="no_finalize_with_high_severity_findings",
                source_type="verifier_findings",
                source_id="high_severity_findings",
                statement="A spec with high-severity verifier findings cannot be finalized.",
                theorem_body=(
                    "theorem no_finalize_with_high_severity_findings (g : SpecGate) :\n"
                    "  g.highSeverityFindings -> Not (finalizeAllowed g) := by\n"
                    "  intro h allowed\n"
                    "  exact allowed.right.right.right h\n"
                ),
            )
        )
    if not (
        ledger.decision_gate == "needs_more_info"
        or open_proofs
        or review_required
        or high_findings
    ):
        checks.append(
            lean_check_record(
                theorem_name="finalize_allowed_when_no_hard_gates",
                source_type="gate_clear",
                source_id="all_hard_gates_clear",
                statement="A spec can pass the formal finalize gate when no hard formal gates remain.",
                theorem_body=(
                    "theorem finalize_allowed_when_no_hard_gates (g : SpecGate) :\n"
                    "  Not g.needsMoreInfo ->\n"
                    "  Not g.openProofs ->\n"
                    "  Not g.humanReviewRequired ->\n"
                    "  Not g.highSeverityFindings ->\n"
                    "  finalizeAllowed g := by\n"
                    "  intro noNeeds noProofs noReview noFindings\n"
                    "  exact And.intro noNeeds (And.intro noProofs (And.intro noReview noFindings))\n"
                ),
            )
        )
    return checks


def lean_check_record(
    *,
    theorem_name: str,
    source_type: str,
    source_id: str,
    statement: str,
    theorem_body: str,
) -> FormalProofCheckRecord:
    lean_code = LEAN_PROOF_PREAMBLE + "\n\n" + theorem_body
    return FormalProofCheckRecord(
        check_id=f"lean:{stable_id(theorem_name + ':' + source_id)}",
        theorem_name=theorem_name,
        source_type=source_type,  # type: ignore[arg-type]
        source_id=source_id,
        statement=statement,
        lean_code=lean_code,
    )


def run_lean_formal_proofs(ledger: SpecLedger) -> FormalProofAssistantState:
    """Execute generated Lean checks when Lean is installed."""

    if not ledger.formal_proofs.checks:
        sync_formal_proof_checks(ledger)
    executable_path = shutil.which("lean")
    if not ledger.formal_proofs.checks:
        ledger.formal_proofs = FormalProofAssistantState(
            status="not_applicable",
            executable_path=executable_path,
            checks=[],
        )
        return ledger.formal_proofs
    if not executable_path:
        ledger.formal_proofs = ledger.formal_proofs.model_copy(
            update={
                "status": "unavailable",
                "executable_path": None,
                "checks": [
                    item.model_copy(update={"status": "unavailable"})
                    for item in ledger.formal_proofs.checks
                ],
            }
        )
        return ledger.formal_proofs

    checked: list[FormalProofCheckRecord] = []
    any_failed = False
    with tempfile.TemporaryDirectory(prefix="mutual-spec-lean-") as tmp_dir:
        for item in ledger.formal_proofs.checks:
            path = Path(tmp_dir) / f"{item.theorem_name}.lean"
            path.write_text(item.lean_code, encoding="utf-8")
            try:
                result = subprocess.run(
                    [executable_path, path.as_posix()],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                output = (result.stdout + result.stderr).strip() or None
                failed = result.returncode != 0
            except (OSError, subprocess.TimeoutExpired) as exc:
                output = str(exc)
                failed = True
            if failed:
                any_failed = True
                checked.append(item.model_copy(update={"status": "failed", "output": output}))
            else:
                checked.append(item.model_copy(update={"status": "checked", "output": output}))
    ledger.formal_proofs = FormalProofAssistantState(
        status="failed" if any_failed else "checked",
        executable_path=executable_path,
        checks=checked,
    )
    return ledger.formal_proofs


def mark_dominated_actions(payoffs: list[GameActionPayoff]) -> list[GameActionPayoff]:
    result: list[GameActionPayoff] = []
    for item in payoffs:
        dominated = any(dominates_action(other, item) for other in payoffs if other.action != item.action)
        result.append(item.model_copy(update={"dominated": dominated}))
    return result


def dominates_action(a: GameActionPayoff, b: GameActionPayoff) -> bool:
    better_or_equal = (
        a.specification_gain >= b.specification_gain
        and a.risk_reduction >= b.risk_reduction
        and a.user_burden <= b.user_burden
        and a.latency_cost <= b.latency_cost
        and a.policy_penalty <= b.policy_penalty
    )
    strictly_better = (
        a.specification_gain > b.specification_gain
        or a.risk_reduction > b.risk_reduction
        or a.user_burden < b.user_burden
        or a.latency_cost < b.latency_cost
        or a.policy_penalty < b.policy_penalty
    )
    return better_or_equal and strictly_better


def recommended_equilibrium_action(
    *,
    missing_material: bool,
    unsatisfied_evidence: bool,
    review_required: bool,
    open_formal_proofs: bool,
    open_proofs: bool,
    high_findings: bool,
    gate_blocked: bool,
) -> str:
    if high_findings:
        return "defer"
    if review_required:
        return "review"
    if missing_material or open_formal_proofs or gate_blocked:
        return "ask"
    if unsatisfied_evidence or open_proofs:
        return "retrieve"
    return "finalize"


def equilibrium_conflicts(
    *,
    gate_blocked: bool,
    open_proofs: bool,
    open_formal_proofs: bool,
    review_required: bool,
    high_findings: bool,
    unsatisfied_evidence: bool,
) -> list[str]:
    conflicts: list[str] = []
    if gate_blocked:
        conflicts.append("Finalize conflicts with a gate that still needs more information.")
    if open_proofs:
        conflicts.append("Finalize/propose conflicts with open proof obligations.")
    if open_formal_proofs:
        conflicts.append("Formal specification obligations must be clarified before a decision-ready response.")
    if review_required:
        conflicts.append("Finalize conflicts with required human review approval.")
    if high_findings:
        conflicts.append("Immediate response conflicts with high-severity verifier findings.")
    if unsatisfied_evidence:
        conflicts.append("Low-latency response conflicts with unsatisfied evidence obligations.")
    return conflicts


def equilibrium_rationale(recommended: str, conflicts: list[str]) -> str:
    if conflicts:
        return f"Recommended action `{recommended}` because hard conflicts remain: " + " ".join(conflicts)
    return f"Recommended action `{recommended}` because no hard conflicts remain."


def sync_skill_compatibility_state(ledger: SpecLedger) -> None:
    text = (ledger.expressed_query or ledger.user_request).lower()
    risks: list[str] = []
    evidence_required: list[str] = []
    learned_from = ["current_query"]
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    formal_missing = latest_formal.missing_obligations if latest_formal and latest_formal.is_valid != 1 else []

    if is_mutual_spec_architecture_text(text):
        inferred_role = "technical product builder"
        skill_level = "expert"
        domain_familiarity = "high"
        cognitive_burden = "high"
        recommended_depth = "deep"
        handoff_format = "implementation_plan"
    elif looks_like_trader_query(text):
        inferred_role = "commodity trader"
        skill_level = "expert"
        domain_familiarity = "high"
        cognitive_burden = "medium"
        recommended_depth = "standard"
        handoff_format = "review_packet" if ledger.human_review.required else "decision_frame"
        risks.extend(
            [
                "Trader may be compressing a larger strategy, counterparty, or logistics problem into a short query.",
                "A fluent yes/no answer could reduce decision ownership or encourage confirmation bias.",
            ]
        )
        evidence_required.extend(item.purpose for item in ledger.search_plan if item.required)
    elif ledger.output_format in {"code", "Python code", "design spec"}:
        inferred_role = "technical operator"
        skill_level = "intermediate"
        domain_familiarity = "medium"
        cognitive_burden = "medium"
        recommended_depth = "standard"
        handoff_format = "implementation_plan"
    else:
        inferred_role = ledger.audience or "unknown"
        skill_level = "unknown"
        domain_familiarity = "low"
        cognitive_burden = "low"
        recommended_depth = "brief"
        handoff_format = "clarification_question" if ledger.ambiguities else "proof_packet"

    if ledger.ambiguities:
        risks.append("Material fields remain unresolved, so a detailed answer may hide the actual task gap.")
    if ledger.human_review.required:
        risks.append("High-stakes review is required before a decision-ready handoff is compatible.")
    if any(item.verifier_state in {"needs_evidence", "blocked"} for item in ledger.claim_graph):
        risks.append("Some claims still need support, waiver, or downgrade before the user can carry them forward.")

    if ledger.human_review.required:
        next_action = "review"
    elif formal_missing:
        next_action = "ask"
    elif any(item.status != "satisfied" for item in ledger.search_plan if item.required):
        next_action = "retrieve"
    elif ledger.ambiguities:
        next_action = "ask"
    elif handoff_format == "implementation_plan":
        next_action = "execute_spec"
    else:
        next_action = "summarize"

    ledger.skill_compatibility = SkillCompatibilityState(
        inferred_role=inferred_role,
        skill_level=skill_level,  # type: ignore[arg-type]
        domain_familiarity=domain_familiarity,  # type: ignore[arg-type]
        cognitive_burden=cognitive_burden,  # type: ignore[arg-type]
        recommended_depth=recommended_depth,  # type: ignore[arg-type]
        handoff_format=handoff_format,  # type: ignore[arg-type]
        compatibility_risks=dedupe_text(risks)[:10],
        evidence_required_for_handoff=dedupe_text(evidence_required)[:12],
        next_best_action=next_action,  # type: ignore[arg-type]
        learned_from=learned_from,
    )


def is_mutual_spec_architecture_text(text: str) -> bool:
    return any(
        token in text
        for token in (
            "mutual specification",
            "specification game",
            "model zoo",
            "proof-carrying",
            "game theory",
        )
    ) and any(token in text for token in ("implement", "architecture", "build", "goal"))


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
    review_state = ledger.human_review
    if not review_state.required or review_state.status == "approved":
        human_review = 1.0
    elif review_state.status == "in_review":
        human_review = 0.5
    elif review_state.status == "changes_requested":
        human_review = 0.35
    elif review_state.status == "rejected":
        human_review = 0.0
    else:
        human_review = 0.25
    skill_state = ledger.skill_compatibility
    if skill_state.next_best_action in {"review", "retrieve", "ask"}:
        skill = 0.55
    elif skill_state.compatibility_risks:
        skill = 0.75
    else:
        skill = 1.0
    required_proofs = [item for item in ledger.proof_obligations if item.required]
    proof = (
        sum(1 for item in required_proofs if item.status in {"satisfied", "waived"})
        / len(required_proofs)
        if required_proofs
        else 1.0
    )
    formal_proof_status = ledger.formal_proofs.status
    if formal_proof_status in {"not_applicable", "checked"}:
        formal_proof = 1.0
    elif formal_proof_status == "failed":
        formal_proof = 0.0
    elif formal_proof_status == "generated":
        formal_proof = 0.65
    else:
        formal_proof = 0.55
    overall = round(
        0.15 * material
        + 0.15 * evidence
        + 0.17 * formal
        + 0.08 * endorsement
        + 0.12 * verification
        + 0.10 * human_review
        + 0.09 * skill
        + 0.09 * proof
        + 0.05 * formal_proof,
        4,
    )
    if human_review == 0.0 and review_state.required:
        status = "diverged"
    elif (
        overall >= 0.92
        and verification == 1.0
        and human_review == 1.0
        and ledger.decision_gate != "needs_more_info"
    ):
        status = "verified"
    elif overall >= 0.82 and formal >= 1.0 and human_review >= 0.25:
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
        human_review_resolution=round(human_review, 4),
        skill_compatibility_resolution=round(skill, 4),
        proof_obligation_resolution=round(proof, 4),
        formal_proof_resolution=round(formal_proof, 4),
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
                audience = " ".join(trimmed[:8])
                if not looks_like_market_phrase(audience):
                    return audience
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
    if not ledger.audience or looks_like_market_phrase(ledger.audience):
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

    evidence_contracts = [
        "IBKR may be used for futures history and market data only; do not place orders or expose broker execution.",
        "Yahoo Finance may be used only as a proxy/reference source and must carry calibration assumptions.",
        "Commodity feeds must record freshness, entitlement, units, transform, confidence, and lookahead guard.",
        "Search/tool evidence must be retrieved through Google Agent SDK search tools, MCP/Opoint, Google CSE/RAG, Vertex AI Search, or explicitly marked model-only evidence before market claims are trusted.",
    ]
    if is_physical_offer_query(lower):
        evidence_contracts.append(
            "Physical commodity offers must verify product specification, quantity tolerance, Incoterms, load port, laycan, counterparty identity, title chain, payment terms, sanctions exposure, freight, insurance, inspection, and resale path."
        )
    if is_relative_value_spread_query(lower):
        evidence_contracts.append(
            "Relative-value spread frames must map legs, units, contract months, ratio, source timestamp, sensitivity, liquidity, and falsification triggers before execution."
        )
    for item in evidence_contracts:
        add_unique(ledger.evidence_contract, item)

    for item in (
        "Separate fact, assumption, bet, and unknown.",
        "Include legal, logistics, sanctions, market, basis, and counterparty risk flags when relevant.",
        "Return a decision frame for the trader, not a buy/sell recommendation.",
        "Include falsification triggers and missing data before marking the frame ready.",
    ):
        add_unique(ledger.verification_conditions, item)
    if is_physical_offer_query(lower):
        add_unique(
            ledger.verification_conditions,
            "For physical offers, do not mark go/no-go ready until product spec, documents, counterparty, payment, logistics, and market exit are verified.",
        )
    if is_relative_value_spread_query(lower):
        add_unique(
            ledger.verification_conditions,
            "For relative-value spreads, calculate unit-normalized spread risk from supplied marks and ask before live verification search.",
        )

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


def looks_like_market_phrase(text: str) -> bool:
    lower = text.lower()
    market_terms = (
        "heating oil",
        "rbob",
        "rb ",
        "gasoline",
        "brent",
        "wti",
        "crude",
        "sulfur",
        "sulphur",
        "fob",
        "$",
        "per gallon",
        "per barrel",
    )
    return any(term in lower for term in market_terms)


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
    optional_live_search = is_relative_value_spread_query(lower) and has_user_supplied_market_marks(lower)
    for index, (query, purpose) in enumerate(zip(queries, required, strict=False), start=1):
        add_search_plan_item(
            ledger,
            query=query,
            purpose=purpose,
            query_id=f"trader:{stable_id(query)}:{index}",
            required=not optional_live_search,
        )
    if optional_live_search:
        add_search_plan_item(
            ledger,
            query="HO RB RBOB Heating Oil gasoline spread live prices inventory crack spread",
            purpose="Optional live verification of user-supplied market marks and current HO/RB spread drivers.",
            query_id=f"trader:{stable_id('optional-live-ho-rb-verification')}:optional",
            required=False,
        )
    for purpose in required:
        add_unique(ledger.verification_conditions, purpose)
    if optional_live_search:
        add_unique(
            ledger.verification_conditions,
            "Ask whether the user wants live source verification before calling external market/search tools; user-supplied marks support provisional math only.",
        )
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
    physical_offer = [
        "Price benchmark and market context near the offer date.",
        "Product specification, grade, quantity tolerance, and inspection standard.",
        "Counterparty identity, title chain, documents, and payment terms.",
        "Port/loading terms, laycan, berth constraints, demurrage, freight, and insurance.",
        "Sanctions, compliance, route, bankability, and political risk.",
        "Resale path, buyer demand, hedge/proxy availability, and netback economics.",
    ]
    if "sulfur" in lower_text or "sulphur" in lower_text:
        return physical_offer
    if is_relative_value_spread_query(lower_text):
        return [
            "User-supplied or live-verified HO/RB, Brent, and WTI marks with units and timestamp.",
            "Contract and leg mapping: HO, RBOB/RB, contract month, ratio, and unit conversion.",
            "Spread risk frame: dollar sensitivity, crude-relative cracks, basis, liquidity, and margin risk.",
            "Falsification triggers: inventory, seasonality, refinery runs, demand shocks, and crack-spread regime changes.",
        ]
    return physical_offer[:4]


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
    if is_relative_value_spread_query(lower_text):
        return [
            '"HO/RB" "Heating Oil" RBOB spread price',
            '"heating oil" RBOB gasoline spread crack',
            '"HO" "RB" futures contract specifications gallons',
            '"distillate" gasoline inventories heating oil RBOB spread risk',
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


def is_relative_value_spread_query(lower_text: str) -> bool:
    return any(token in lower_text for token in ("ho/rb", "rbob", "heating oil", "brent", "wti", "spread", "arb", "arbitrage"))


def is_physical_offer_query(lower_text: str) -> bool:
    return any(
        token in lower_text
        for token in (
            "offer",
            "fob",
            "cfr",
            "cif",
            "cargo",
            "ton",
            "tonne",
            "tonns",
            "mt",
            "counterparty",
            "umm qasr",
        )
    )


def has_user_supplied_market_marks(lower_text: str) -> bool:
    has_price = bool(re.search(r"\$?\s*\d+(?:\.\d+)?", lower_text))
    return has_price and any(token in lower_text for token in ("today", "current", "sits", "trading", "price"))


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
    required: bool = True,
) -> SearchPlanItem:
    item = SearchPlanItem(
        query_id=query_id or f"search:{stable_id(query)}",
        query=query,
        purpose=purpose,
        required=required,
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
                    "required": item.required,
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
