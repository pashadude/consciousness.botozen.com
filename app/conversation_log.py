"""Persist user-agent dialogue traces for the Mutual Specification corpus."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def persist_console_talk(
    *,
    result: Any,
    raw_text: str,
    speech_text: str,
    response_channel: str,
    env: dict[str, str] | None = None,
) -> str | None:
    env = env or dict(os.environ)
    if not env_flag(env.get("USER_TALKS_RAG_LOG_ENABLED"), default=True):
        return None
    ledger = result.ledger
    created_at = datetime.now(UTC).isoformat()
    record = {
        "schema_version": 1,
        "kind": "mutual_spec_user_talk",
        "created_at": created_at,
        "response_channel": response_channel,
        "ledger_id": ledger.ledger_id,
        "raw_text": raw_text,
        "speech_text": speech_text or None,
        "expressed_query": ledger.expressed_query or ledger.user_request,
        "latent_intent_hypotheses": ledger.latent_intent_hypotheses,
        "goal": ledger.goal,
        "audience": ledger.audience,
        "output_format": ledger.output_format,
        "decision_gate": ledger.decision_gate,
        "status": ledger.status,
        "ambiguities": [item.model_dump(mode="json") for item in ledger.ambiguities],
        "search_plan": [item.model_dump(mode="json") for item in ledger.search_plan],
        "evidence_contract": ledger.evidence_contract,
        "verification_conditions": ledger.verification_conditions,
        "assumptions": ledger.assumptions,
        "artifact_refs": [item.model_dump(mode="json") for item in ledger.artifact_refs],
        "game_players": [item.model_dump(mode="json") for item in ledger.game_players],
        "game_states": [item.model_dump(mode="json") for item in ledger.game_states],
        "latent_type_beliefs": [
            item.model_dump(mode="json") for item in ledger.latent_type_beliefs
        ],
        "commitments": [item.model_dump(mode="json") for item in ledger.commitments],
        "claim_graph": [item.model_dump(mode="json") for item in ledger.claim_graph],
        "proof_obligations": [
            item.model_dump(mode="json") for item in ledger.proof_obligations
        ],
        "equilibrium_diagnostics": ledger.equilibrium_diagnostics.model_dump(mode="json"),
        "user_endorsement": ledger.user_endorsement.model_dump(mode="json"),
        "human_review": ledger.human_review.model_dump(mode="json"),
        "skill_compatibility": ledger.skill_compatibility.model_dump(mode="json"),
        "spec_convergence": ledger.spec_convergence.model_dump(mode="json"),
        "route_history": [item.model_dump(mode="json") for item in ledger.route_history],
        "source_layer": {
            "provider": result.rag_result.provider,
            "status": result.rag_result.status,
            "queries": result.rag_result.queries,
            "warnings": result.rag_result.warnings,
        },
        "verification_passed": result.verification_passed,
        "draft": result.draft,
    }
    local_uri = write_local_record(record, env)
    maybe_upload_record(record, created_at, ledger.ledger_id, env)
    return local_uri


def write_local_record(record: dict[str, Any], env: dict[str, str]) -> str:
    out_dir = Path(env.get("USER_TALKS_LOCAL_DIR", "app/.adk/user_talks"))
    out_dir.mkdir(parents=True, exist_ok=True)
    created = str(record["created_at"]).replace(":", "").replace("+", "Z")
    path = out_dir / f"{created}-{record['ledger_id']}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path.as_posix()


def maybe_upload_record(
    record: dict[str, Any],
    created_at: str,
    ledger_id: str,
    env: dict[str, str],
) -> str | None:
    bucket_name = env.get("USER_TALKS_GCS_BUCKET") or env.get("SPANNER_RAG_GCS_BUCKET")
    if not bucket_name:
        return None
    prefix = env.get("USER_TALKS_GCS_PREFIX", "user_talks").strip("/")
    try:
        from google.cloud import storage

        created = datetime.fromisoformat(created_at)
        object_name = (
            f"{prefix}/{created:%Y/%m/%d}/"
            f"{created:%Y%m%dT%H%M%S}-{ledger_id}.json"
        )
        client = storage.Client(project=env.get("GOOGLE_CLOUD_PROJECT"))
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        blob.upload_from_string(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n",
            content_type="application/json",
        )
        return f"gs://{bucket_name}/{object_name}"
    except Exception:
        return None


def env_flag(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
