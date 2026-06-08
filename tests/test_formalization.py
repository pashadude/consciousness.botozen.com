from app.formalization import FORMALIZATION_REGISTRY, formalize_ledger
from app.spec_state import SpecLedger, update_ledger_from_user_text
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


def test_verifier_blocks_ready_gate_when_formal_obligations_are_missing() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    ledger.decision_gate = "decision_frame_ready"
    formalize_ledger(ledger)

    draft = build_draft_from_ledger(ledger)
    result = verify_draft(ledger, draft)

    assert not result.passed
    assert any(
        item.severity == "high" and "horizon_or_timeframe" in item.message
        for item in result.findings
    )
