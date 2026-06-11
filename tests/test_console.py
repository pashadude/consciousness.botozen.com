import json

from fastapi.testclient import TestClient

from app.console import app


def test_console_home_renders_multimodal_operator_surface() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Mutual Spec Console" in response.text
    assert 'accept="image/*,audio/*,application/pdf,text/*"' in response.text
    assert "Record Speech" in response.text
    assert "Model-Region Frontier" in response.text


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
    assert "Trader Source Layer" in response.text
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
