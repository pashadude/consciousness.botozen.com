"""Optional Gemini multimodal embedding support for uploaded artifacts."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

from google.genai import types

from app.spec_state import ArtifactRef, EvidenceRef, SpecLedger

DEFAULT_ALLOWED_MIME_TYPES = (
    "application/json",
    "application/pdf",
    "image/",
    "audio/",
    "video/",
    "text/",
)


@dataclass(frozen=True)
class MultimodalConfig:
    enabled: bool
    model: str
    output_dimensionality: int | None
    max_artifact_bytes: int
    max_results: int
    min_similarity: float
    allowed_mime_types: tuple[str, ...]
    api_key: str | None
    project: str | None
    location: str
    use_vertexai: bool
    document_ocr: bool
    audio_track_extraction: bool

    @property
    def can_call_google(self) -> bool:
        return self.enabled and bool(self.api_key or (self.use_vertexai and self.project))


@dataclass(frozen=True)
class EmbeddedArtifact:
    artifact: ArtifactRef
    score: float
    dimensions: int
    model: str
    mime_type: str | None


def config_from_env() -> MultimodalConfig:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    use_vertexai = parse_bool(
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"),
        default=bool(project and not api_key),
    )
    enabled = parse_bool(
        os.environ.get("MULTIMODAL_RETRIEVAL_ENABLED"),
        default=bool(api_key or (use_vertexai and project)),
    )
    return MultimodalConfig(
        enabled=enabled,
        model=os.environ.get("MULTIMODAL_EMBEDDING_MODEL", "gemini-embedding-2"),
        output_dimensionality=parse_optional_int(
            os.environ.get("MULTIMODAL_OUTPUT_DIMENSIONALITY"),
            default=768,
        ),
        max_artifact_bytes=parse_int(
            os.environ.get("MULTIMODAL_MAX_ARTIFACT_BYTES"),
            default=4_000_000,
        ),
        max_results=parse_int(os.environ.get("MULTIMODAL_MAX_RESULTS"), default=5),
        min_similarity=parse_float(
            os.environ.get("MULTIMODAL_MIN_SIMILARITY"),
            default=0.0,
        ),
        allowed_mime_types=parse_mime_types(
            os.environ.get("MULTIMODAL_ALLOWED_MIME_TYPES")
        ),
        api_key=api_key,
        project=project,
        location=location,
        use_vertexai=use_vertexai,
        document_ocr=parse_bool(os.environ.get("MULTIMODAL_DOCUMENT_OCR"), default=True),
        audio_track_extraction=parse_bool(
            os.environ.get("MULTIMODAL_AUDIO_TRACK_EXTRACTION"),
            default=True,
        ),
    )


async def add_multimodal_evidence_from_artifacts(
    ctx: Any,
    ledger: SpecLedger,
    *,
    config: MultimodalConfig | None = None,
) -> SpecLedger:
    """Rank uploaded artifacts against the user request with Gemini embeddings."""

    config = config or config_from_env()
    if not config.can_call_google or not ledger.artifact_refs:
        return ledger

    query = ledger.user_request or ledger.goal or ""
    if not query.strip():
        return ledger

    try:
        client = build_genai_client(config)
        query_embedding = embed_content(client, config, query)
    except Exception as exc:  # pragma: no cover - depends on local GCP auth/network
        add_skip_note(ledger, f"Gemini multimodal query embedding skipped: {exc}")
        return ledger

    matches: list[EmbeddedArtifact] = []
    for artifact in ledger.artifact_refs:
        match = await embed_artifact(ctx, client, config, query_embedding, artifact)
        if match and match.score >= config.min_similarity:
            matches.append(match)

    matches.sort(key=lambda item: item.score, reverse=True)
    for match in matches[: config.max_results]:
        upsert_multimodal_evidence(ledger, match)
    ledger.evidence_used = sorted(
        {item.evidence_id for item in ledger.evidence if item.used}
    )
    return ledger


async def embed_artifact(
    ctx: Any,
    client: Any,
    config: MultimodalConfig,
    query_embedding: list[float],
    artifact: ArtifactRef,
) -> EmbeddedArtifact | None:
    part = await load_artifact_part(ctx, artifact)
    if part is None:
        return None
    mime_type = artifact_mime_type(part, artifact)
    if mime_type and not is_mime_allowed(mime_type, config.allowed_mime_types):
        return None
    byte_count = inline_byte_count(part)
    if byte_count is not None and byte_count > config.max_artifact_bytes:
        add_skip_note(
            None,
            f"Skipping {artifact.filename}: {byte_count} bytes exceeds "
            f"MULTIMODAL_MAX_ARTIFACT_BYTES={config.max_artifact_bytes}.",
        )
        return None
    payload = content_payload_from_part(part, mime_type)
    if payload is None:
        return None
    try:
        artifact_embedding = embed_content(client, config, payload, mime_type=mime_type)
    except Exception:
        return None
    return EmbeddedArtifact(
        artifact=artifact,
        score=cosine_similarity(query_embedding, artifact_embedding),
        dimensions=len(artifact_embedding),
        model=config.model,
        mime_type=mime_type,
    )


async def load_artifact_part(ctx: Any, artifact: ArtifactRef) -> types.Part | None:
    try:
        version = artifact.version if isinstance(artifact.version, int) else None
        return await ctx.load_artifact(artifact.filename, version=version)
    except Exception:
        return None


def build_genai_client(config: MultimodalConfig) -> Any:
    from google import genai

    if config.api_key:
        return genai.Client(api_key=config.api_key)
    return genai.Client(
        vertexai=True,
        project=config.project,
        location=config.location,
    )


def embed_content(
    client: Any,
    config: MultimodalConfig,
    contents: str | types.Part | list[types.Part],
    *,
    mime_type: str | None = None,
) -> list[float]:
    embed_config = types.EmbedContentConfig(
        output_dimensionality=config.output_dimensionality,
        mime_type=mime_type,
        document_ocr=config.document_ocr,
        audio_track_extraction=config.audio_track_extraction,
    )
    result = client.models.embed_content(
        model=config.model,
        contents=contents,
        config=embed_config,
    )
    if not result.embeddings:
        return []
    return [float(value) for value in result.embeddings[0].values]


def upsert_multimodal_evidence(
    ledger: SpecLedger,
    match: EmbeddedArtifact,
) -> None:
    evidence_id = f"multimodal:{match.artifact.artifact_id}"
    summary = (
        f"Gemini multimodal embedding match score={match.score:.3f}; "
        f"model={match.model}; dims={match.dimensions}"
    )
    if match.mime_type:
        summary += f"; mime={match.mime_type}"
    evidence = EvidenceRef(
        evidence_id=evidence_id,
        source_type="artifact",
        title=f"Multimodal match: {match.artifact.filename}",
        uri=f"artifact://{match.artifact.filename}",
        summary=summary,
        artifact_id=match.artifact.artifact_id,
        used=True,
    )
    for index, item in enumerate(ledger.evidence):
        if item.evidence_id == evidence_id:
            ledger.evidence[index] = evidence
            return
    ledger.evidence.append(evidence)


def content_payload_from_part(
    part: types.Part,
    mime_type: str | None,
) -> str | types.Part | None:
    if getattr(part, "text", None):
        return str(part.text)

    inline_data = getattr(part, "inline_data", None)
    if inline_data is not None and getattr(inline_data, "data", None) is not None:
        data = inline_data.data
        if isinstance(data, str):
            data = data.encode()
        return types.Part.from_bytes(
            data=data,
            mime_type=getattr(inline_data, "mime_type", None)
            or mime_type
            or "application/octet-stream",
        )

    file_data = getattr(part, "file_data", None)
    file_uri = getattr(file_data, "file_uri", None)
    if file_uri:
        return types.Part.from_uri(file_uri=file_uri, mime_type=mime_type)
    return None


def artifact_mime_type(part: types.Part, artifact: ArtifactRef) -> str | None:
    inline_data = getattr(part, "inline_data", None)
    file_data = getattr(part, "file_data", None)
    return (
        getattr(inline_data, "mime_type", None)
        or getattr(file_data, "mime_type", None)
        or artifact.mime_type
    )


def inline_byte_count(part: types.Part) -> int | None:
    inline_data = getattr(part, "inline_data", None)
    data = getattr(inline_data, "data", None)
    if data is None:
        return None
    if isinstance(data, str):
        return len(data.encode())
    return len(data)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def is_mime_allowed(mime_type: str, allowed: tuple[str, ...]) -> bool:
    return any(
        mime_type == candidate or (candidate.endswith("/") and mime_type.startswith(candidate))
        for candidate in allowed
    )


def parse_mime_types(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_ALLOWED_MIME_TYPES
    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or DEFAULT_ALLOWED_MIME_TYPES


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def parse_optional_int(value: str | None, *, default: int | None) -> int | None:
    if value is None or value.strip().lower() in {"", "none", "null"}:
        return default
    return parse_int(value, default=default or 0)


def parse_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def add_skip_note(ledger: SpecLedger | None, note: str) -> None:
    if ledger is None:
        return
    if note not in ledger.assumptions:
        ledger.assumptions.append(note)
