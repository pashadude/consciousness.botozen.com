from app.formalization import FORMALIZATION_REGISTRY, formalize_ledger
from app.router import route_for_stage
from app.spec_state import SpecLedger, add_route, update_ledger_from_user_text
from app.verifiers import build_draft_from_ledger, verify_draft


def test_general_formalization_uses_problem_question_answer_contract() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "Make this better")

    record = formalize_ledger(ledger)

    assert record.task_name == "general_spec_completion"
    assert record.domain == "general"
    assert record.problem["expressed_query"] == "Make this better"
    assert record.question == "Which material specification fields must be resolved before drafting?"
    assert record.answer["required_fields"] == ["goal", "audience", "output_format"]
    assert record.is_valid == 0
    assert set(record.missing_obligations) == {"goal", "audience", "output_format"}
    assert "TASK" in record.tokens
    assert "general_spec_completion" in FORMALIZATION_REGISTRY


def test_trader_formalization_marks_missing_timeframe_obligation() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")

    record = formalize_ledger(ledger)

    assert record.task_name == "trader_decision_frame"
    assert record.domain == "trader"
    assert record.problem["detected_instruments"] == ["HO/RB", "RBOB", "ULSD", "basis spread"]
    assert record.problem["detected_actions"] == ["risk", "arbitrage", "spread_analysis"]
    assert record.is_valid == 0
    assert record.metrics["coverage"] < 1
    assert record.missing_obligations == ["horizon_or_timeframe"]


def test_trader_formalization_can_validate_complete_decision_frame() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb next week and give me risk on this spread")

    record = formalize_ledger(ledger)

    assert record.is_valid == 1
    assert record.missing_obligations == []
    assert record.metrics["coverage"] == 1.0
    assert record.class_id == 243


def test_physical_offer_formalization_maps_sulfur_and_fob_cargo() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of 50000 tonns of sulfur in Iraq, Umm Qasr, fob 550, should i go for it?",
    )

    record = formalize_ledger(ledger)

    assert "sulfur" in record.problem["detected_instruments"]
    assert "FOB physical cargo" in record.problem["detected_instruments"]
    assert "physical_offer" in record.problem["detected_actions"]
    assert "instrument_mapping" not in record.missing_obligations


def test_draft_and_verifier_surface_formalization_records() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    formalize_ledger(ledger)

    draft = build_draft_from_ledger(ledger)
    result = verify_draft(ledger, draft)

    assert "Formalization" in draft
    assert "Task: trader_decision_frame" in draft
    assert "missing_obligations: horizon_or_timeframe" in draft
    assert result.passed
    assert any("horizon_or_timeframe" in item.message for item in result.findings)
    assert all(item.severity != "high" for item in result.findings)


def test_mutual_specification_game_formalization_checks_architecture_obligations() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "/goal Mutual Specification Game for traders with model zoo routing, verifiers, tools, human review, and proof-carrying response.",
    )
    for stage in ("ingest", "hypothesize_spec", "retrieve_evidence"):
        add_route(ledger, route_for_stage(stage, ledger))

    record = formalize_ledger(ledger)

    assert record.task_name == "mutual_specification_game"
    assert record.domain == "mutual_spec"
    assert record.is_valid == 1
    assert record.missing_obligations == []
    assert record.metrics["coverage"] == 1.0
    assert "players" in record.answer["obligations"]
    assert "claim_graph" in record.answer["obligations"]
    assert "proof_obligations" in record.answer["obligations"]
    assert "equilibrium_diagnostics" in record.answer["obligations"]
    assert "lean_formal_bridge" in record.answer["obligations"]
    assert "human_review_gate" in record.answer["obligations"]
    assert "skill_compatible_handoff" in record.answer["obligations"]
    assert record.hypothesis["human_review"]["assigned_player"] == "human_reviewer"
    assert record.hypothesis["skill_compatibility"]["handoff_format"] == "implementation_plan"
    assert record.hypothesis["equilibrium_diagnostics"]["recommended_action"]
    assert record.hypothesis["formal_proofs"]["backend"] == "lean"


def test_formalization_blocks_ready_gate_when_obligations_are_missing() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    ledger.decision_gate = "decision_frame_ready"
    formalize_ledger(ledger)

    assert ledger.decision_gate == "needs_more_info"
    assert any(
        item.source_type == "formalization" and "horizon_or_timeframe" in item.statement
        for item in ledger.proof_obligations
    )

    draft = build_draft_from_ledger(ledger)
    result = verify_draft(ledger, draft)

    assert any(
        item.severity == "medium" and "horizon_or_timeframe" in item.message
        for item in result.findings
    )
