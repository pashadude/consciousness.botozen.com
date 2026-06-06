from app.model_armor import (
    config_from_env as model_armor_config_from_env,
)
from app.model_armor import (
    sanitize_user_prompt,
)
from app.multimodal import (
    EmbeddedArtifact,
    cosine_similarity,
    is_mime_allowed,
    upsert_multimodal_evidence,
)
from app.multimodal import (
    config_from_env as multimodal_config_from_env,
)
from app.spec_state import ArtifactRef, SpecLedger


def test_multimodal_config_stays_disabled_without_google_credentials(monkeypatch) -> None:
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "MULTIMODAL_RETRIEVAL_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    config = multimodal_config_from_env()

    assert not config.enabled
    assert not config.can_call_google


def test_multimodal_config_uses_google_account_adc(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "grant-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    config = multimodal_config_from_env()

    assert config.enabled
    assert config.use_vertexai
    assert config.can_call_google
    assert config.model == "gemini-embedding-2"
    assert config.output_dimensionality == 768


def test_cosine_similarity_and_mime_policy() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert is_mime_allowed("image/png", ("image/", "application/pdf"))
    assert not is_mime_allowed("application/octet-stream", ("image/",))


def test_upsert_multimodal_evidence_records_ranked_artifact() -> None:
    ledger = SpecLedger()
    artifact = ArtifactRef(
        artifact_id="diagram.png:v0",
        filename="diagram.png",
        mime_type="image/png",
        version=0,
    )
    match = EmbeddedArtifact(
        artifact=artifact,
        score=0.82,
        dimensions=768,
        model="gemini-embedding-2",
        mime_type="image/png",
    )

    upsert_multimodal_evidence(ledger, match)
    upsert_multimodal_evidence(ledger, match)

    assert len(ledger.evidence) == 1
    assert ledger.evidence[0].evidence_id == "multimodal:diagram.png:v0"
    assert "score=0.820" in ledger.evidence[0].summary


def test_model_armor_is_optional_until_template_is_configured(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_ARMOR_TEMPLATE", raising=False)
    monkeypatch.delenv("MODEL_ARMOR_TEMPLATE_ID", raising=False)

    verdict = sanitize_user_prompt("hello")

    assert not verdict.checked
    assert verdict.allowed


def test_model_armor_template_id_uses_project_and_location(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_ARMOR_TEMPLATE", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "grant-project")
    monkeypatch.setenv("MODEL_ARMOR_LOCATION", "us-central1")
    monkeypatch.setenv("MODEL_ARMOR_TEMPLATE_ID", "default-agent-policy")

    config = model_armor_config_from_env()

    assert (
        config.template_name
        == "projects/grant-project/locations/us-central1/templates/default-agent-policy"
    )
