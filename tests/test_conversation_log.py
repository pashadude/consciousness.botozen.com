import json

from app.conversation_log import persist_console_talk, persist_human_review_talk
from app.spec_state import SpecLedger, update_ledger_from_user_text
from app.trader_rag import TraderRagResult


class DummyResult:
    def __init__(self, ledger: SpecLedger) -> None:
        self.ledger = ledger
        self.rag_result = TraderRagResult(
            provider="spanner_rag",
            status="configured",
            queries=["sulfur Umm Qasr FOB"],
            required_evidence=["Verify price benchmark."],
        )
        self.verification_passed = False
        self.draft = "Draft decision frame"


def test_persist_console_talk_writes_local_trace(tmp_path) -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of sulfur FOB Umm Qasr, should i go for it?",
    )

    uri = persist_console_talk(
        result=DummyResult(ledger),
        raw_text=ledger.user_request,
        speech_text="",
        response_channel="json",
        env={
            "USER_TALKS_RAG_LOG_ENABLED": "true",
            "USER_TALKS_LOCAL_DIR": str(tmp_path),
        },
    )

    assert uri
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["kind"] == "mutual_spec_user_talk"
    assert payload["source_layer"]["provider"] == "spanner_rag"
    assert payload["game_states"]
    assert payload["latent_type_beliefs"]
    assert payload["claim_graph"]
    assert payload["proof_obligations"]
    assert payload["equilibrium_diagnostics"]["recommended_action"] == "review"
    assert payload["formal_proofs"]["backend"] == "lean"
    assert payload["human_review"]["assigned_player"] == "human_reviewer"
    assert payload["skill_compatibility"]["inferred_role"] == "commodity trader"
    assert payload["spec_convergence"]["overall"] > 0


def test_persist_human_review_talk_writes_rag_memory_record(tmp_path) -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of sulfur FOB Umm Qasr, should i go for it?",
    )

    uri = persist_human_review_talk(
        ledger=ledger,
        action="request_changes",
        note="Need seller KYC and inspection docs.",
        operator="desk_operator",
        message="Operator requested changes or more evidence.",
        env={
            "USER_TALKS_RAG_LOG_ENABLED": "true",
            "USER_TALKS_LOCAL_DIR": str(tmp_path),
        },
    )

    assert uri
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["kind"] == "mutual_spec_human_review"
    assert payload["operator"] == "desk_operator"
    assert payload["review_action"] == "request_changes"
    assert payload["operator_note"] == "Need seller KYC and inspection docs."
    assert payload["human_review"]["assigned_player"] == "human_reviewer"
