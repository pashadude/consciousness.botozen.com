"""Optional Google Cloud Model Armor checks."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelArmorConfig:
    template_name: str | None
    location: str
    timeout_seconds: float
    fail_closed: bool
    enable_multi_language_detection: bool

    @property
    def enabled(self) -> bool:
        return bool(self.template_name)

    @property
    def endpoint(self) -> str:
        return f"https://modelarmor.{self.location}.rep.googleapis.com"


@dataclass(frozen=True)
class ModelArmorVerdict:
    checked: bool
    allowed: bool
    reason: str
    raw_filter_match_state: str | None = None


def config_from_env() -> ModelArmorConfig:
    location = os.environ.get("MODEL_ARMOR_LOCATION") or os.environ.get(
        "GOOGLE_CLOUD_LOCATION",
        "us-central1",
    )
    template_name = os.environ.get("MODEL_ARMOR_TEMPLATE")
    template_id = os.environ.get("MODEL_ARMOR_TEMPLATE_ID")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    if not template_name and template_id and project:
        template_name = f"projects/{project}/locations/{location}/templates/{template_id}"
    return ModelArmorConfig(
        template_name=template_name,
        location=location,
        timeout_seconds=parse_float(os.environ.get("MODEL_ARMOR_TIMEOUT_SECONDS"), 5.0),
        fail_closed=parse_bool(os.environ.get("MODEL_ARMOR_FAIL_CLOSED"), False),
        enable_multi_language_detection=parse_bool(
            os.environ.get("MODEL_ARMOR_MULTI_LANGUAGE_DETECTION"),
            True,
        ),
    )


def sanitize_user_prompt(
    text: str,
    *,
    config: ModelArmorConfig | None = None,
) -> ModelArmorVerdict:
    config = config or config_from_env()
    if not config.enabled:
        return ModelArmorVerdict(
            checked=False,
            allowed=True,
            reason="Model Armor is not configured.",
        )
    if not text.strip():
        return ModelArmorVerdict(
            checked=True,
            allowed=True,
            reason="Empty prompt.",
        )
    try:
        response = call_model_armor(config, text)
    except Exception as exc:  # pragma: no cover - depends on local GCP auth/network
        return ModelArmorVerdict(
            checked=False,
            allowed=not config.fail_closed,
            reason=f"Model Armor check failed: {exc}",
        )
    result = response.get("sanitizationResult", {})
    match_state = result.get("filterMatchState")
    if match_state == "MATCH_FOUND":
        return ModelArmorVerdict(
            checked=True,
            allowed=False,
            reason="Model Armor found a policy match in the user prompt.",
            raw_filter_match_state=match_state,
        )
    return ModelArmorVerdict(
        checked=True,
        allowed=True,
        reason="Model Armor found no blocking policy match.",
        raw_filter_match_state=match_state,
    )


def call_model_armor(config: ModelArmorConfig, text: str) -> dict:
    token = access_token()
    body: dict[str, object] = {"userPromptData": {"text": text}}
    if config.enable_multi_language_detection:
        body["multiLanguageDetectionMetadata"] = {
            "enableMultiLanguageDetection": True,
        }
    url = f"{config.endpoint}/v1/{config.template_name}:sanitizeUserPrompt"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {payload}") from exc


def access_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return str(credentials.token)


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
