"""Verifier checks for final response quality, hallucination control, and safety."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.model_armor import sanitize_user_prompt
from app.router import should_refuse_for_safety
from app.spec_state import (
    EvidenceRef,
    FormalizationRecord,
    SpecLedger,
    VerificationFinding,
)


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    findings: list[VerificationFinding]


def safety_check(text: str) -> SafetyResult:
    armor = sanitize_user_prompt(text)
    if armor.checked and not armor.allowed:
        return SafetyResult(allowed=False, reason=armor.reason)
    if not armor.allowed:
        return SafetyResult(allowed=False, reason=armor.reason)
    if should_refuse_for_safety(text):
        return SafetyResult(
            allowed=False,
            reason="The request appears to facilitate harmful abuse such as credential theft, malware, or evasion.",
        )
    return SafetyResult(allowed=True, reason="No high-risk abuse pattern detected.")


def build_draft_from_ledger(ledger: SpecLedger) -> str:
    goal = ledger.goal or "UNRESOLVED: goal"
    audience = ledger.audience or "UNRESOLVED: audience"
    output_format = ledger.output_format or "UNRESOLVED: output format"
    evidence_lines = format_evidence_lines(ledger.evidence)
    latent_lines = format_optional_lines(ledger.latent_intent_hypotheses, empty="- None captured.")
    evidence_contract_lines = format_optional_lines(ledger.evidence_contract, empty="- None captured.")
    game_state_lines = format_game_state_lines(ledger)
    belief_lines = format_latent_belief_lines(ledger)
    commitment_lines = format_commitment_lines(ledger)
    claim_graph_lines = format_claim_graph_lines(ledger)
    proof_obligation_lines = format_proof_obligation_lines(ledger)
    equilibrium_lines = format_equilibrium_lines(ledger)
    human_review_lines = format_human_review_lines(ledger)
    skill_compatibility_lines = format_skill_compatibility_lines(ledger)
    verification_condition_lines = format_optional_lines(
        ledger.verification_conditions,
        empty="- Standard verifier conditions only.",
    )
    formalization_lines = format_formalization_lines(ledger.formalization_records)
    async_job_lines = format_async_job_lines(ledger.async_jobs)
    assumption_lines = "\n".join(f"- {item}" for item in ledger.assumptions) or "- None."
    constraint_lines = "\n".join(f"- {item}" for item in ledger.constraints) or "- None captured."
    criteria_lines = (
        "\n".join(f"- {item}" for item in ledger.success_criteria)
        or "- The response satisfies the accepted goal, audience, output format, and cited evidence."
    )
    return "\n".join(
        [
            "Executable Task Specification",
            f"Expressed query: {ledger.expressed_query or ledger.user_request or 'UNRESOLVED: expressed query'}",
            f"Goal: {goal}",
            f"Audience: {audience}",
            f"Output format: {output_format}",
            f"Decision gate: {ledger.decision_gate}",
            "",
            "Latent Intent Hypotheses",
            latent_lines,
            "",
            "Success Criteria",
            criteria_lines,
            "",
            "Constraints",
            constraint_lines,
            "",
            "Evidence Contract",
            evidence_contract_lines,
            "",
            "Evidence Used",
            evidence_lines,
            "",
            "Stage Games",
            game_state_lines,
            "",
            "Latent Type Beliefs",
            belief_lines,
            "",
            "Commitments",
            commitment_lines,
            "",
            "Claim Graph",
            claim_graph_lines,
            "",
            "Proof Obligations",
            proof_obligation_lines,
            "",
            "Equilibrium Diagnostics",
            equilibrium_lines,
            "",
            "Human Review",
            human_review_lines,
            "",
            "Skill Compatibility",
            skill_compatibility_lines,
            "",
            "Formalization",
            formalization_lines,
            "",
            "Async Jobs",
            async_job_lines,
            "",
            "Assumptions",
            assumption_lines,
            "",
            "Verification Conditions",
            verification_condition_lines,
        ]
    )


def format_game_state_lines(ledger: SpecLedger) -> str:
    if not ledger.game_states:
        return "- No staged game state generated."
    lines = [
        (
            f"- {item.stage_id}: {item.status} | {item.game_type} | "
            f"blocks: {', '.join(item.blocking_conditions) or 'none'}"
        )
        for item in ledger.game_states
    ]
    convergence = ledger.spec_convergence
    lines.append(
        "- convergence: "
        f"{convergence.overall:.2f} ({convergence.status}; "
        f"material={convergence.material_resolution:.2f}, "
        f"evidence={convergence.evidence_resolution:.2f}, "
        f"formal={convergence.formalization_resolution:.2f}, "
        f"endorsement={convergence.endorsement_resolution:.2f}, "
        f"verification={convergence.verification_resolution:.2f}, "
        f"human_review={convergence.human_review_resolution:.2f}, "
        f"skill={convergence.skill_compatibility_resolution:.2f}, "
        f"proof={convergence.proof_obligation_resolution:.2f})"
    )
    return "\n".join(lines)


def format_latent_belief_lines(ledger: SpecLedger) -> str:
    if not ledger.latent_type_beliefs:
        return "- No latent type beliefs generated."
    return "\n".join(
        f"- {item.type_id}: p={item.probability:.2f}; next={item.next_best_action}; signals={', '.join(item.evidence_signals)}"
        for item in ledger.latent_type_beliefs[:6]
    )


def format_commitment_lines(ledger: SpecLedger) -> str:
    if not ledger.commitments:
        return "- No commitments recorded."
    return "\n".join(
        f"- [{item.commitment_id}] {item.field}: {item.value} ({item.status}, {item.player_id})"
        for item in ledger.commitments[:12]
    )


def format_claim_graph_lines(ledger: SpecLedger) -> str:
    if not ledger.claim_graph:
        return "- No claim graph generated."
    return "\n".join(
        f"- [{item.claim_id}] {item.claim_type}/{item.verifier_state}: {item.text}"
        + (f" supports={', '.join(item.support_ids)}" if item.support_ids else "")
        for item in ledger.claim_graph[:16]
    )


def format_proof_obligation_lines(ledger: SpecLedger) -> str:
    if not ledger.proof_obligations:
        return "- No proof obligations generated."
    return "\n".join(
        f"- [{item.obligation_id}] {item.source_type}/{item.status}: {item.statement}"
        + (f" remediation={item.remediation}" if item.remediation else "")
        for item in ledger.proof_obligations[:16]
    )


def format_equilibrium_lines(ledger: SpecLedger) -> str:
    diagnostic = ledger.equilibrium_diagnostics
    payoff_lines = [
        (
            f"- {item.action}: dominated={item.dominated}; "
            f"spec_gain={item.specification_gain:.2f}; "
            f"risk_reduction={item.risk_reduction:.2f}; "
            f"burden={item.user_burden:.2f}; latency={item.latency_cost:.2f}; "
            f"policy={item.policy_penalty:.2f}"
        )
        for item in diagnostic.payoffs
    ]
    conflicts = " | ".join(diagnostic.unresolved_conflicts) or "none"
    return "\n".join(
        [
            f"- solution_concept: {diagnostic.solution_concept}",
            f"- recommended_action: {diagnostic.recommended_action}",
            f"- dominated_actions: {', '.join(diagnostic.dominated_actions) or 'none'}",
            f"- unresolved_conflicts: {conflicts}",
            f"- rationale: {diagnostic.rationale}",
            *payoff_lines,
        ]
    )


def format_human_review_lines(ledger: SpecLedger) -> str:
    review = ledger.human_review
    lines = [
        f"- required: {review.required}",
        f"- status: {review.status}",
        f"- risk_level: {review.risk_level}",
        f"- assigned_player: {review.assigned_player}",
        f"- decision_owner: {review.decision_owner}",
    ]
    if review.reasons:
        lines.append("- reasons: " + " | ".join(review.reasons))
    if review.required_actions:
        lines.append("- required_actions: " + " | ".join(review.required_actions[:8]))
    if review.blocking_claim_ids:
        lines.append("- blocking_claim_ids: " + ", ".join(review.blocking_claim_ids[:8]))
    if review.blocking_evidence:
        lines.append("- blocking_evidence: " + " | ".join(review.blocking_evidence[:8]))
    return "\n".join(lines)


def format_skill_compatibility_lines(ledger: SpecLedger) -> str:
    skill = ledger.skill_compatibility
    lines = [
        f"- inferred_role: {skill.inferred_role}",
        f"- skill_level: {skill.skill_level}",
        f"- domain_familiarity: {skill.domain_familiarity}",
        f"- cognitive_burden: {skill.cognitive_burden}",
        f"- recommended_depth: {skill.recommended_depth}",
        f"- handoff_format: {skill.handoff_format}",
        f"- next_best_action: {skill.next_best_action}",
        f"- learned_from: {', '.join(skill.learned_from) or 'none'}",
    ]
    if skill.compatibility_risks:
        lines.append("- compatibility_risks: " + " | ".join(skill.compatibility_risks[:6]))
    if skill.evidence_required_for_handoff:
        lines.append(
            "- evidence_required_for_handoff: "
            + " | ".join(skill.evidence_required_for_handoff[:6])
        )
    return "\n".join(lines)


def format_optional_lines(items: list[str], *, empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def format_evidence_lines(evidence: list[EvidenceRef]) -> str:
    used = [item for item in evidence if item.used]
    if not used:
        return "- No external or artifact evidence was required."
    return "\n".join(
        f"- [{item.evidence_id}] {item.title}: {item.summary}"
        + (f" ({item.uri})" if item.uri else "")
        for item in used
    )


def format_formalization_lines(records: list[FormalizationRecord]) -> str:
    if not records:
        return "- No formalization record generated."
    latest = records[-1]
    missing = ", ".join(latest.missing_obligations) or "none"
    question = latest.question or "none"
    return "\n".join(
        [
            f"- Task: {latest.task_name}",
            f"- Domain: {latest.domain}",
            f"- Question: {question}",
            f"- is_valid: {latest.is_valid}",
            f"- class_id: {latest.class_id}",
            f"- missing_obligations: {missing}",
        ]
    )


def format_async_job_lines(jobs: list[object]) -> str:
    if not jobs:
        return "- No async jobs queued."
    return "\n".join(
        f"- {getattr(job, 'job_id', 'unknown')}: {getattr(job, 'status', 'unknown')} "
        f"({', '.join(getattr(job, 'reasons', []) or ['no reason recorded'])})"
        for job in jobs
    )


def verify_draft(ledger: SpecLedger, draft: str) -> VerificationResult:
    findings: list[VerificationFinding] = []
    safety = safety_check(ledger.user_request + "\n" + draft)
    if not safety.allowed:
        findings.append(
            VerificationFinding(
                category="safety",
                severity="high",
                message=safety.reason,
                remediation="Refuse harmful assistance and offer a benign alternative.",
            )
        )
    missing = [
        label
        for label, value in (
            ("goal", ledger.goal),
            ("audience", ledger.audience),
            ("output format", ledger.output_format),
        )
        if not value
    ]
    if missing:
        findings.append(
            VerificationFinding(
                category="spec_gap",
                severity="high",
                message=f"Draft is missing required spec field(s): {', '.join(missing)}.",
                remediation="Ask a clarification question before finalizing.",
            )
        )
    if ledger.evidence and not any(item.evidence_id in draft for item in ledger.evidence if item.used):
        findings.append(
            VerificationFinding(
                category="unsupported_claim",
                severity="high",
                message="Draft does not cite the evidence selected in the ledger.",
                remediation="Add evidence IDs beside claims derived from evidence.",
            )
        )
    if ledger.formalization_records:
        latest_formalization = ledger.formalization_records[-1]
        if latest_formalization.is_valid != 1:
            severity = "high" if ledger.decision_gate != "needs_more_info" else "medium"
            missing_obligations = ", ".join(latest_formalization.missing_obligations) or "unknown"
            findings.append(
                VerificationFinding(
                    category="spec_gap",
                    severity=severity,
                    message=(
                        f"Formalization task {latest_formalization.task_name} is incomplete; "
                        f"missing obligation(s): {missing_obligations}."
                    ),
                    remediation="Keep the decision gate blocked or resolve the missing formal obligations.",
                )
            )
    unsupported_claims = [
        item
        for item in ledger.claim_graph
        if item.verifier_state in {"needs_evidence", "unverified"}
        and item.claim_type in {"fact", "inference"}
    ]
    if unsupported_claims and ledger.decision_gate != "needs_more_info":
        findings.append(
            VerificationFinding(
                category="unsupported_claim",
                severity="high",
                message=(
                    "Claim graph has unsupported factual/inference node(s): "
                    + ", ".join(item.claim_id for item in unsupported_claims[:5])
                ),
                remediation="Attach evidence, downgrade to assumption, or keep the gate blocked.",
            )
        )
    if ledger.human_review.required and ledger.human_review.status != "approved":
        severity = "high" if ledger.decision_gate != "needs_more_info" else "medium"
        findings.append(
            VerificationFinding(
                category="trajectory",
                severity=severity,
                message=(
                    "Human review is required but not approved; "
                    f"status is {ledger.human_review.status}."
                ),
                remediation="Keep the decision gate blocked or obtain reviewer approval.",
            )
        )
    open_proofs = [
        item
        for item in ledger.proof_obligations
        if item.required and item.status not in {"satisfied", "waived"}
    ]
    if open_proofs and ledger.decision_gate != "needs_more_info":
        findings.append(
            VerificationFinding(
                category="unsupported_claim",
                severity="high",
                message=(
                    "Proof obligations remain open: "
                    + ", ".join(item.obligation_id for item in open_proofs[:5])
                ),
                remediation="Satisfy, waive with rationale, or keep the gate blocked.",
            )
        )
    findings.extend(detect_uncited_external_claims(draft, ledger.evidence))
    required_stages = ["ingest", "hypothesize_spec", "retrieve_evidence", "draft_output", "verify"]
    missing_stages = [stage for stage in required_stages if stage not in ledger.trajectory]
    if missing_stages:
        findings.append(
            VerificationFinding(
                category="trajectory",
                severity="medium",
                message=f"Workflow trajectory is missing stage(s): {', '.join(missing_stages)}.",
                remediation="Ensure the graph records every required stage in session state.",
            )
        )
    return VerificationResult(
        passed=not any(item.severity == "high" for item in findings),
        findings=findings,
    )


def detect_uncited_external_claims(
    draft: str,
    evidence: list[EvidenceRef],
) -> list[VerificationFinding]:
    if not evidence:
        return []
    findings: list[VerificationFinding] = []
    claim_patterns = (
        r"\baccording to\b",
        r"\bresearch shows\b",
        r"\bstudies show\b",
        r"\bthe docs say\b",
    )
    citation_pattern = "|".join(re.escape(item.evidence_id) for item in evidence)
    for sentence in re.split(r"(?<=[.!?])\s+", draft):
        if any(re.search(pattern, sentence, re.IGNORECASE) for pattern in claim_patterns):
            if not citation_pattern or not re.search(citation_pattern, sentence):
                findings.append(
                    VerificationFinding(
                        category="unsupported_claim",
                        severity="high",
                        message=f"External claim lacks a ledger evidence citation: {sentence[:180]}",
                        remediation="Attach a ledger evidence ID or remove the claim.",
                    )
                )
    return findings


def format_final_response(
    ledger: SpecLedger,
    draft: str,
    verification: VerificationResult,
) -> str:
    if not safety_check(ledger.user_request).allowed:
        return (
            "I cannot help with that request because it appears to facilitate harmful abuse. "
            "I can help rewrite it into a defensive, educational, or compliance-oriented task."
        )
    status = "verified" if verification.passed else "verified with open issues"
    finding_lines = (
        "\n".join(f"- {item.severity}: {item.message}" for item in verification.findings)
        if verification.findings
        else "- None."
    )
    return "\n".join(
        [
            f"Spec ledger `{ledger.ledger_id}` is {status}.",
            "",
            draft,
            "",
            "Verifier Findings",
            finding_lines,
        ]
    )
