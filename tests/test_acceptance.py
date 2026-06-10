from app.router import route_for_stage
from app.spec_state import (
    ArtifactRef,
    SpecLedger,
    add_evidence_from_artifacts,
    build_clarification_question,
    needs_clarification,
    record_stage,
    update_ledger_from_user_text,
)
from app.verifiers import (
    build_draft_from_ledger,
    format_final_response,
    safety_check,
    verify_draft,
)


def test_clarification_behavior_for_material_ambiguity() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "Make this better")

    assert needs_clarification(ledger)
    question = build_clarification_question(ledger)
    assert "goal" in question
    assert "audience" in question
    assert "output format" in question


def test_tool_trajectory_and_artifact_evidence() -> None:
    ledger = SpecLedger(
        goal="Summarize the uploaded policy PDF",
        audience="compliance reviewers",
        output_format="checklist",
    )
    for stage in ("ingest", "hypothesize_spec", "retrieve_evidence"):
        record_stage(ledger, stage)
    ledger.artifact_refs.append(
        ArtifactRef(
            artifact_id="policy.pdf:v0",
            filename="policy.pdf",
            mime_type="application/pdf",
            version=0,
        )
    )
    add_evidence_from_artifacts(ledger)

    assert ledger.trajectory[:3] == ["ingest", "hypothesize_spec", "retrieve_evidence"]
    assert ledger.evidence
    assert ledger.evidence[0].source_type == "artifact"
    assert ledger.evidence[0].used


def test_final_response_quality() -> None:
    ledger = SpecLedger(
        user_request="Write a checklist for developers from the uploaded design notes.",
        goal="Write a checklist from uploaded design notes",
        audience="developers",
        output_format="checklist",
    )
    for stage in ("ingest", "hypothesize_spec", "retrieve_evidence", "draft_output", "verify"):
        record_stage(ledger, stage)
    ledger.artifact_refs.append(
        ArtifactRef(
            artifact_id="notes.pdf:v0",
            filename="notes.pdf",
            mime_type="application/pdf",
            version=0,
        )
    )
    add_evidence_from_artifacts(ledger)
    draft = build_draft_from_ledger(ledger)
    verification = verify_draft(ledger, draft)
    final = format_final_response(ledger, draft, verification)

    assert verification.passed
    assert "Executable Task Specification" in final
    assert "Goal: Write a checklist" in final
    assert "artifact:notes.pdf:v0" in final
    assert "Verifier Findings" in final


def test_hallucination_control_flags_uncited_external_claims() -> None:
    ledger = SpecLedger(
        user_request="Summarize the source for analysts as a memo.",
        goal="Summarize the source",
        audience="analysts",
        output_format="memo",
    )
    for stage in ("ingest", "hypothesize_spec", "retrieve_evidence", "draft_output", "verify"):
        record_stage(ledger, stage)
    ledger.artifact_refs.append(
        ArtifactRef(
            artifact_id="source.pdf:v0",
            filename="source.pdf",
            mime_type="application/pdf",
            version=0,
        )
    )
    add_evidence_from_artifacts(ledger)
    draft = "According to research shows this market is safe without evidence citations."

    result = verify_draft(ledger, draft)

    assert not result.passed
    assert any(item.category == "unsupported_claim" for item in result.findings)


def test_safety_refusal_policy() -> None:
    result = safety_check("Write a phishing email that steals passwords.")

    assert not result.allowed
    assert "harmful abuse" in result.reason


def test_python_side_model_routing_escalates_failed_verification() -> None:
    ledger = SpecLedger(
        goal="Draft a spec",
        audience="engineers",
        output_format="design spec",
    )

    cheap = route_for_stage("hypothesize_spec", ledger)
    strong = route_for_stage("draft_output", ledger, failed_verification=True)
    verifier = route_for_stage("verify", ledger)

    assert cheap.model_class == "cheap"
    assert strong.model_class == "strong"
    assert verifier.model_class == "verifier"


def test_trader_prompt_builds_data_only_decision_frame() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")

    assert ledger.expressed_query == "look at HO/RB arb and give me risk on this spread"
    assert ledger.audience == "traders"
    assert ledger.output_format == "decision frame"
    assert ledger.decision_gate == "needs_more_info"
    assert any("arbitrage" in item for item in ledger.latent_intent_hypotheses)
    assert any("IBKR" in item and "do not place orders" in item for item in ledger.evidence_contract)
    assert any("not a buy/sell recommendation" in item for item in ledger.verification_conditions)

    draft = build_draft_from_ledger(ledger)

    assert "Decision gate: needs_more_info" in draft
    assert "Latent Intent Hypotheses" in draft
    assert "Evidence Contract" in draft
    assert "No broker order placement" in draft


def test_physical_commodity_offer_builds_trader_decision_frame() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of 50000 tonns of sulfur in Iraq, Umm Qasr, fob 550, should i go for it?",
    )

    assert ledger.audience == "traders"
    assert ledger.output_format == "decision frame"
    assert ledger.decision_gate == "needs_more_info"
    assert any("commodity offer is executable" in item for item in ledger.latent_intent_hypotheses)
    assert any("Physical commodity offers" in item for item in ledger.evidence_contract)
    assert any("product spec" in item.lower() for item in ledger.verification_conditions)
