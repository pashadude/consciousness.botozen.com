from app.router import route_for_stage
from app.spec_state import (
    ArtifactRef,
    SpecLedger,
    add_evidence_from_artifacts,
    apply_alignment_signal,
    build_clarification_question,
    needs_clarification,
    record_stage,
    run_lean_formal_proofs,
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
    assert ledger.search_plan
    assert any("Umm Qasr" in item.query for item in ledger.search_plan)
    assert any("Price benchmark" in item.purpose for item in ledger.search_plan)
    assert any(source.name == "model_hypothesis" for source in ledger.evidence_sources)
    assert ledger.human_review.required
    assert ledger.human_review.status == "queued"
    assert ledger.human_review.risk_level == "high"
    assert ledger.human_review.blocking_evidence
    assert any(item.stage_id == "human_review" and item.status == "blocked" for item in ledger.game_states)
    assert ledger.proof_obligations
    assert any(item.source_type == "evidence" for item in ledger.proof_obligations)
    assert any(item.stage_id == "proof_obligations" for item in ledger.game_states)
    assert ledger.equilibrium_diagnostics.recommended_action == "review"
    assert ledger.equilibrium_diagnostics.payoffs
    assert ledger.formal_proofs.backend == "lean"
    assert ledger.formal_proofs.checks
    assert any(item.theorem_name == "no_finalize_when_needs_more_info" for item in ledger.formal_proofs.checks)
    assert any(item.stage_id == "formal_proofs" for item in ledger.game_states)
    assert ledger.skill_compatibility.inferred_role == "commodity trader"
    assert ledger.skill_compatibility.handoff_format == "review_packet"
    assert ledger.skill_compatibility.next_best_action == "review"
    assert ledger.skill_compatibility.compatibility_risks


def test_mutual_spec_game_state_is_materialized_from_query() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "/goal Mutual Specification Game for traders: reconstruct latent intent and verify before execution.",
    )

    player_ids = {item.player_id for item in ledger.game_players}
    stage_ids = {item.stage_id for item in ledger.game_states}
    belief_ids = {item.type_id for item in ledger.latent_type_beliefs}

    assert {"user", "main_agent", "router", "verifier", "tool_layer", "human_reviewer"}.issubset(player_ids)
    assert {"elicitation", "dialogue_commitment", "retrieval", "execution_graph", "verification"}.issubset(stage_ids)
    assert "architecture_specification_game" in belief_ids
    assert ledger.commitments
    assert ledger.claim_graph
    assert ledger.proof_obligations is not None
    assert ledger.equilibrium_diagnostics.recommended_action in {
        "ask",
        "retrieve",
        "review",
        "propose",
        "finalize",
        "defer",
    }
    assert ledger.user_endorsement.endorsed_fields
    assert ledger.skill_compatibility.handoff_format == "implementation_plan"
    assert ledger.spec_convergence.overall > 0


def test_user_alignment_signal_endorses_latent_task() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "look at HO/RB arb today with HO at $3.40 and RBOB at $3.05 and give me risk",
    )

    assert "latent_task" in ledger.user_endorsement.pending_fields
    assert ledger.spec_convergence.status != "verified"

    apply_alignment_signal(ledger, action="endorse", note="yes, this is the spread risk task")

    stage = next(item for item in ledger.game_states if item.stage_id == "mutual_alignment")
    assert stage.status == "satisfied"
    assert "latent_task" in ledger.user_endorsement.endorsed_fields
    assert "latent_task" not in ledger.user_endorsement.pending_fields
    assert ledger.alignment_signals[-1].action == "endorse"


def test_user_alignment_correction_reopens_shared_spec() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "look at HO/RB arb today with HO at $3.40 and RBOB at $3.05 and give me risk",
    )

    apply_alignment_signal(
        ledger,
        action="correct",
        note="not execution, build a one-week alert spec and verify inventory first",
    )

    assert ledger.status == "clarifying"
    assert ledger.decision_gate == "needs_more_info"
    assert "latent_task" in ledger.user_endorsement.rejected_fields
    assert any(item.field == "latent_task" for item in ledger.ambiguities)


def test_lean_bridge_generates_only_gate_invariants(monkeypatch) -> None:
    monkeypatch.setattr("app.spec_state.shutil.which", lambda name: None)
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of 50000 tonns of sulfur in Iraq, Umm Qasr, fob 550, should i go for it?",
    )

    state = run_lean_formal_proofs(ledger)
    lean_code = "\n".join(item.lean_code for item in state.checks).lower()

    assert state.backend == "lean"
    assert state.status == "unavailable"
    assert state.checks
    assert "finalizeallowed" in lean_code
    assert "no_finalize_when_needs_more_info" in lean_code
    assert "sulfur" not in lean_code
    assert "umm qasr" not in lean_code
