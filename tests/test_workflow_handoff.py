from app.spec_state import ArtifactRef, SpecLedger, update_ledger_from_user_text
from app.workflow import build_data_agent_request


def test_data_agent_request_is_minimal_retrieval_contract() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(
        ledger,
        "Look i have an offer of 50000 tonns of sulfur in Iraq, Umm Qasr, fob 550, should i go for it?",
        artifact_refs=[
            ArtifactRef(
                artifact_id="offer:v0",
                filename="offer.pdf",
                mime_type="application/pdf",
                note="gs://bucket/offer.pdf",
            )
        ],
    )
    ledger.drafts.append("do not pass drafts to data agents")

    request = build_data_agent_request(ledger, "vertex_search")

    assert request["request_type"] == "data_agent_retrieval"
    assert request["source_kind"] == "vertex_search"
    assert request["expressed_query"].startswith("Look i have an offer")
    assert request["search_plan"]
    assert any("Umm Qasr" in item["query"] for item in request["search_plan"])
    assert request["artifact_refs"] == [
        {
            "artifact_id": "offer:v0",
            "filename": "offer.pdf",
            "mime_type": "application/pdf",
            "source": "upload",
            "note": "gs://bucket/offer.pdf",
        }
    ]
    assert "drafts" not in request
    assert "evidence" not in request
