import json

from fastapi.testclient import TestClient

import app.console as console_module
from app.console import app
from app.trader_rag import TraderRagResult


def test_console_home_renders_multimodal_operator_surface(monkeypatch) -> None:
    def fail_rag(*args, **kwargs):
        raise AssertionError("home page must not run RAG")

    monkeypatch.setattr(console_module, "run_trader_rag", fail_rag)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Mutual Spec Console" in response.text
    assert 'accept="image/*,audio/*,application/pdf,text/*"' in response.text
    assert "Record Speech" in response.text
    assert "Current Handoff" in response.text
    assert "Ball: User" in response.text
    assert "Verifier: waiting" in response.text
    assert "Chain + Loop State" in response.text
    assert "Route Before Decision" in response.text
    assert "Alignment Loop" in response.text
    assert "Model Handoff Plan" in response.text
    assert "Advanced ledgers and diagnostics" in response.text
    assert "Model-Region Frontier" in response.text


def test_console_deep_retrieval_defers_before_rag(tmp_path, monkeypatch) -> None:
    def fail_rag(*args, **kwargs):
        raise AssertionError("high-risk deep retrieval should be deferred")

    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ASYNC_JOB_ENABLED", "true")
    monkeypatch.setenv("ASYNC_JOB_STORE_PATH", str(tmp_path / "jobs.jsonl"))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "spanner_rag,mcp")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "zenpulsar")
    monkeypatch.setenv("SPANNER_RAG_INSTANCE_ID", "commodity-rag")
    monkeypatch.setenv("SPANNER_RAG_DATABASE_ID", "trader_rag")
    monkeypatch.setenv("MCP_RESEARCH_COMMAND", "python -m opoint_mcp.server")
    monkeypatch.setenv("OPOINT_API_KEY", "test-key")
    monkeypatch.setattr(console_module, "run_trader_rag", fail_rag)
    client = TestClient(app)

    response = client.post(
        "/api/spec",
        data={
            "query": (
                "Look i have an offer of 50000 tonns of sulfur in Iraq, "
                "Umm Qasr, fob 550, should i go for it?"
            )
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["route_decision"]["mode"] == "async"
    assert payload["ledger"]["status"] == "async_pending"
    assert payload["source_layer"]["status"] == "deferred"
    assert payload["ledger"]["async_jobs"]
    assert (tmp_path / "jobs.jsonl").exists()


def test_console_sync_rag_uses_fast_provider_limits(tmp_path, monkeypatch) -> None:
    captured_env: dict[str, str] = {}

    def fake_rag(text, *, env, search_plan):
        captured_env.update(env)
        return TraderRagResult(
            provider=env["TRADER_RAG_PROVIDER"],
            status="empty",
            queries=["commodity offer market price benchmark"],
            required_evidence=[],
        )

    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("ASYNC_JOB_ENABLED", "false")
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "spanner_rag,mcp")
    monkeypatch.delenv("CONSOLE_SYNC_RAG_PROVIDER", raising=False)
    monkeypatch.delenv("CONSOLE_SYNC_RAG_MAX_QUERIES", raising=False)
    monkeypatch.delenv("CONSOLE_SYNC_RAG_MAX_RESULTS", raising=False)
    monkeypatch.delenv("CONSOLE_SYNC_RAG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CONSOLE_SYNC_SPANNER_RAG_SEARCH_MODE", raising=False)
    monkeypatch.setattr(console_module, "run_trader_rag", fake_rag)
    client = TestClient(app)

    response = client.post(
        "/api/spec",
        data={
            "query": (
                "Look i have an offer of 50000 tonns of sulfur in Iraq, "
                "Umm Qasr, fob 550, should i go for it?"
            )
        },
    )

    assert response.status_code == 200
    assert captured_env["TRADER_RAG_PROVIDER"] == "spanner_rag"
    assert captured_env["TRADER_RAG_MAX_QUERIES"] == "1"
    assert captured_env["TRADER_RAG_MAX_RESULTS"] == "3"
    assert captured_env["TRADER_RAG_TIMEOUT_SECONDS"] == "2.5"
    assert captured_env["SPANNER_RAG_SEARCH_MODE"] == "semantic"


def test_console_post_records_image_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/spec",
        data={"query": "look at HO/RB arb and give me risk on this spread"},
        files={"files": ("chart.png", b"fake-png", "image/png")},
    )

    assert response.status_code == 200
    assert "chart.png" in response.text
    assert "image/png" in response.text
    assert "Artifact Evidence" in response.text
    assert "artifact:" in response.text
    assert any(tmp_path.iterdir())


def test_console_api_accepts_speech_text_and_audio(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/spec",
        data={
            "query": "Brent/WTI bounce?",
            "speech_text": "make this a decision frame for traders",
        },
        files={"files": ("speech.webm", b"fake-audio", "audio/webm")},
    )

    payload = response.json()

    assert response.status_code == 200
    assert payload["ledger"]["artifact_refs"][0]["filename"] == "speech.webm"
    assert payload["ledger"]["artifact_refs"][0]["mime_type"] == "audio/webm"
    assert "Speech transcript:" in payload["ledger"]["user_request"]
    assert payload["route_decision"]["mode"] in {"sync", "async"}
    assert payload["frontier"]
    assert payload["ledger"]["game_states"]
    assert payload["ledger"]["latent_type_beliefs"]
    assert payload["ledger"]["claim_graph"]
    assert payload["ledger"]["spec_convergence"]["overall"] > 0
    assert payload["human_review"]["assigned_player"] == "human_reviewer"
    assert payload["skill_compatibility"]["handoff_format"] in {
        "decision_frame",
        "review_packet",
        "clarification_question",
    }
    assert isinstance(payload["proof_obligations"], list)
    assert payload["equilibrium_diagnostics"]["recommended_action"] in {
        "ask",
        "retrieve",
        "review",
        "propose",
        "finalize",
        "defer",
    }
    assert payload["formal_proofs"]["backend"] == "lean"
    assert payload["provisional_answer"]
    assert payload["provisional_answer"][0].startswith("Immediate answer:")


def test_console_generic_query_returns_answer_frame(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    client = TestClient(app)

    response = client.post(
        "/api/spec",
        data={"query": "Draft a launch checklist for the new dashboard"},
    )

    payload = response.json()

    assert response.status_code == 200
    assert payload["provisional_answer"][0].startswith("Immediate answer:")
    assert any("Latent task:" in item for item in payload["provisional_answer"])
    assert any("Working checklist:" in item for item in payload["provisional_answer"])
    assert any("Source layer:" in item for item in payload["provisional_answer"])
    assert payload["ledger"]["expressed_query"] == "Draft a launch checklist for the new dashboard"
    assert payload["ledger"]["decision_gate"] == "analysis_ready"


def test_console_ho_rb_marks_produce_calculated_analysis_frame(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    monkeypatch.setenv("ASYNC_JOB_ENABLED", "true")
    client = TestClient(app)

    query = (
        "look at HO/RB arb and give me risk on this spread, today Heating Oil "
        "(HO) and RBOB Gasoline (RB) sits at roughly $3.40 per gallon for "
        "Heating Oil and $3.05 per gallon for RBOB Gasoline, while Brent Crude "
        "is $87.33 per barrel, and WTI is trading at $84.88 per barrel"
    )
    response = client.post("/api/spec", data={"query": query})
    payload = response.json()

    assert response.status_code == 200
    assert payload["ledger"]["audience"] == "traders"
    assert payload["ledger"]["decision_gate"] == "analysis_ready"
    assert payload["ledger"]["status"] == "finalized"
    assert payload["route_decision"]["mode"] == "sync"
    assert payload["human_review"]["required"] is False
    assert any("HO is trading $0.35 per gallon over RB" in item for item in payload["provisional_answer"])
    assert any("Source policy:" in item for item in payload["provisional_answer"])
    assert any(item["source_type"] == "user" for item in payload["ledger"]["evidence"])
    assert all(not item["required"] for item in payload["ledger"]["search_plan"])


def test_console_alignment_api_records_user_correction(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("USER_TALKS_LOCAL_DIR", str(tmp_path / "talks"))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    client = TestClient(app)

    spec_response = client.post(
        "/api/spec",
        data={
            "query": "look at HO/RB arb today with HO at $3.40 and RBOB at $3.05 and give me risk"
        },
    )
    spec_payload = spec_response.json()

    response = client.post(
        "/api/alignment",
        json={
            "ledger": spec_payload["ledger"],
            "action": "correct",
            "note": "not execution, build a one-week alert spec and verify inventories first",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["decision_gate"] == "needs_more_info"
    assert payload["status"] == "clarifying"
    assert payload["alignment_signals"][-1]["action"] == "correct"
    assert "latent_task" in payload["user_endorsement"]["rejected_fields"]
    assert any((tmp_path / "talks").iterdir())


def test_console_sulfur_offer_builds_trader_game_frame(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    client = TestClient(app)

    query = (
        "Look i have an offer of 50000 tonns of sulfur in Iraq, "
        "Umm Qasr, fob 550, should i go for it?"
    )
    response = client.post(
        "/spec",
        data={"query": query},
        files={"files": ("offer.png", b"fake-offer-image", "image/png")},
    )

    assert response.status_code == 200
    assert "Mutual Specification Game" in response.text
    assert "Ball: Operator" in response.text
    assert "Chain + Loop State" in response.text
    assert "Trader Source Layer" in response.text
    assert "Operator Review Workflow" in response.text
    assert "Use buttons here. User prompt text does not approve the gate." in response.text
    assert "Review status: queued for human review" in response.text
    assert "Prompt text cannot approve this gate" in response.text
    assert "Start Review" in response.text
    assert "Run Evidence Search" in response.text
    assert "Approve Gate" in response.text
    assert "Request Changes" in response.text
    assert "Reject Frame" in response.text
    assert "Evidence Tasks" in response.text
    assert "Skill Compatibility" in response.text
    assert "Proof Obligations" in response.text
    assert "Equilibrium Diagnostics" in response.text
    assert "Formal Proof Checks" in response.text
    assert "&quot;sulfur&quot; &quot;Umm Qasr&quot; FOB price" in response.text
    assert "Provisional Decision Frame" in response.text
    assert "not go-ready" in response.text
    assert "no live cited market/search evidence" in response.text
    assert 'li class="blocked"' in response.text
    assert "needs_more_info" in response.text
    assert "audience</span><p>traders" in response.text
    assert "format</span><p>decision frame" in response.text
    assert "Reconstruct whether the commodity offer is executable" in response.text
    assert "offer.png" in response.text
    assert "image/png" in response.text
    assert "Compressed high-stakes trader or physical commodity decision signal." in response.text
    assert "review_packet" in response.text


def test_console_sulfur_offer_renders_fixture_source_evidence(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "search.json"
    fixture.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "title": "Sulfur FOB benchmark",
                        "url": "https://example.test/sulfur-fob",
                        "snippet": "A source-backed benchmark needed before accepting the offer.",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "fixture")
    monkeypatch.setenv("TRADER_RAG_FIXTURE_PATH", str(fixture))
    client = TestClient(app)

    response = client.post(
        "/spec",
        data={
            "query": (
                "Look i have an offer of 50000 tonns of sulfur in Iraq, "
                "Umm Qasr, fob 550, should i go for it?"
            )
        },
    )

    assert response.status_code == 200
    assert "Trader Source Layer" in response.text
    assert "Sulfur FOB benchmark" in response.text
    assert "https://example.test/sulfur-fob" in response.text
    assert "retrieved search evidence is attached" in response.text


def test_console_predeal_sulfur_negotiation_returns_negotiation_brief(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    monkeypatch.setenv("ASYNC_JOB_ENABLED", "false")
    client = TestClient(app)

    query = (
        "I would like to sell 20000 mt of Kazakh sulfur to Nitron. Logistics is theirs. "
        "They tend to buy time as Hormuz situation is unclear. Nitron already has 400000 MT "
        "sulphur locked in Iraq. Attached document provides sulphur specifications. "
        "Deal is on planning stage, no LOI even to the seller which works with the factory in Pavlodar. "
        "My goal is to convince Nitron to start official negotiations on the deal."
    )
    response = client.post("/api/spec", data={"query": query})
    payload = response.json()

    assert response.status_code == 200
    assert payload["ledger"]["output_format"] == "negotiation brief"
    assert payload["ledger"]["audience"] == "Nitron commercial buyer"
    assert payload["ledger"]["decision_gate"] == "analysis_ready"
    assert payload["human_review"]["required"] is False
    assert all(not item["required"] for item in payload["ledger"]["search_plan"])
    assert any("Nitron" in item["query"] for item in payload["ledger"]["search_plan"])
    assert not any("Umm Qasr" in item["query"] for item in payload["ledger"]["search_plan"])
    assert any("negotiation-ready, not deal-ready" in item for item in payload["provisional_answer"])
    assert any("no cargo is executable yet" in item for item in payload["provisional_answer"])


def test_console_operator_can_submit_human_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("HUMAN_REVIEW_LOG_PATH", str(tmp_path / "reviews.jsonl"))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "disabled")
    client = TestClient(app)

    spec_response = client.post(
        "/api/spec",
        data={
            "query": (
                "Look i have an offer of 50000 tonns of sulfur in Iraq, "
                "Umm Qasr, fob 550, should i go for it?"
            )
        },
    )
    ledger = spec_response.json()["ledger"]

    review_response = client.post(
        "/api/human-review",
        json={
            "ledger": ledger,
            "action": "request_changes",
            "note": "Need seller KYC and inspection docs before this is decision-ready.",
            "operator": "test_operator",
        },
    )
    payload = review_response.json()

    assert review_response.status_code == 200
    assert payload["human_review"]["status"] == "changes_requested"
    assert payload["decision_gate"] == "needs_more_info"
    assert "requested changes" in payload["operator_message"]
    assert (tmp_path / "reviews.jsonl").exists()


def test_console_operator_can_run_review_evidence_search(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "search.json"
    fixture.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "title": "Sulfur FOB benchmark",
                        "url": "https://example.test/sulfur-fob",
                        "snippet": "A source-backed benchmark needed before accepting the offer.",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("CONSOLE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("HUMAN_REVIEW_LOG_PATH", str(tmp_path / "reviews.jsonl"))
    monkeypatch.setenv("TRADER_RAG_PROVIDER", "fixture")
    monkeypatch.setenv("TRADER_RAG_FIXTURE_PATH", str(fixture))
    client = TestClient(app)

    spec_response = client.post(
        "/api/spec",
        data={
            "query": (
                "Look i have an offer of 50000 tonns of sulfur in Iraq, "
                "Umm Qasr, fob 550, should i go for it?"
            )
        },
    )
    ledger = spec_response.json()["ledger"]

    review_response = client.post(
        "/api/human-review",
        json={
            "ledger": ledger,
            "action": "run_evidence_search",
            "note": "Operator wants cited price evidence before gate decision.",
            "operator": "test_operator",
        },
    )
    payload = review_response.json()

    assert review_response.status_code == 200
    assert payload["human_review"]["status"] == "in_review"
    assert payload["source_layer"]["status"] == "retrieved"
    assert payload["source_layer"]["evidence"][0]["title"] == "Sulfur FOB benchmark"
    assert "Evidence search attached" in payload["operator_message"]
    assert payload["ledger"]["human_review"]["last_reviewer_signal"].startswith(
        "test_operator: run_evidence_search"
    )
    assert (tmp_path / "reviews.jsonl").exists()
