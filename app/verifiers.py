"""Verifier checks for final response quality, hallucination control, and safety."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.model_armor import sanitize_user_prompt
from app.router import should_refuse_for_safety
from app.spec_state import EvidenceRef, SpecLedger, VerificationFinding


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
    assumption_lines = "\n".join(f"- {item}" for item in ledger.assumptions) or "- None."
    constraint_lines = "\n".join(f"- {item}" for item in ledger.constraints) or "- None captured."
    criteria_lines = (
        "\n".join(f"- {item}" for item in ledger.success_criteria)
        or "- The response satisfies the accepted goal, audience, output format, and cited evidence."
    )
    return "\n".join(
        [
            "Executable Task Specification",
            f"Goal: {goal}",
            f"Audience: {audience}",
            f"Output format: {output_format}",
            "",
            "Success Criteria",
            criteria_lines,
            "",
            "Constraints",
            constraint_lines,
            "",
            "Evidence Used",
            evidence_lines,
            "",
            "Assumptions",
            assumption_lines,
        ]
    )


def format_evidence_lines(evidence: list[EvidenceRef]) -> str:
    used = [item for item in evidence if item.used]
    if not used:
        return "- No external or artifact evidence was required."
    return "\n".join(
        f"- [{item.evidence_id}] {item.title}: {item.summary}"
        + (f" ({item.uri})" if item.uri else "")
        for item in used
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
        r"\bsource\b",
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
