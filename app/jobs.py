"""Local async job queue for deferred specification work.

This is intentionally small and local-first. It gives the workflow a concrete
handoff point today; the same job envelope can later move to Pub/Sub, Cloud
Tasks, Cloud Run Jobs, or BigQuery without changing routing semantics.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.router import AsyncRouteDecision
from app.spec_state import AsyncJobRef, SpecLedger

DEFAULT_JOB_STORE_PATH = "app/.adk/async_jobs.jsonl"
MAX_LEDGER_JOBS = 20


class AsyncJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job-{uuid4().hex[:12]}")
    ledger_id: str
    kind: str
    status: str = "queued"
    reasons: list[str] = Field(default_factory=list)
    expected_spec_gain: str
    risk_score: int
    selected_models: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    result_ref: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def enqueue_async_job(
    ledger: SpecLedger,
    decision: AsyncRouteDecision,
    *,
    store_path: str | Path | None = None,
) -> AsyncJob:
    job = AsyncJob(
        ledger_id=ledger.ledger_id,
        kind=decision.job_kind,
        reasons=list(decision.reasons),
        expected_spec_gain=decision.expected_spec_gain,
        risk_score=decision.risk_score,
        selected_models=[item.selected_model for item in ledger.route_history[-5:]],
        payload={
            "expressed_query": ledger.expressed_query or ledger.user_request,
            "goal": ledger.goal,
            "audience": ledger.audience,
            "output_format": ledger.output_format,
            "decision_gate": ledger.decision_gate,
            "formalization": (
                ledger.formalization_records[-1].model_dump(mode="json")
                if ledger.formalization_records
                else None
            ),
            "evidence_contract": ledger.evidence_contract,
            "verification_conditions": ledger.verification_conditions,
            "artifact_refs": [item.model_dump(mode="json") for item in ledger.artifact_refs],
        },
    )
    append_job(job, store_path=store_path)
    add_job_ref_to_ledger(ledger, job)
    return job


def append_job(job: AsyncJob, *, store_path: str | Path | None = None) -> None:
    path = resolve_store_path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(job.model_dump(mode="json"), sort_keys=True))
        handle.write("\n")


def read_jobs(*, store_path: str | Path | None = None) -> list[AsyncJob]:
    path = resolve_store_path(store_path)
    if not path.exists():
        return []
    jobs: list[AsyncJob] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                jobs.append(AsyncJob.model_validate_json(line))
    return jobs


def list_pending_jobs(*, store_path: str | Path | None = None) -> list[AsyncJob]:
    return [job for job in read_jobs(store_path=store_path) if job.status == "queued"]


def complete_async_job(
    job_id: str,
    result: dict[str, Any],
    *,
    status: str = "succeeded",
    store_path: str | Path | None = None,
) -> AsyncJob:
    jobs = read_jobs(store_path=store_path)
    updated: AsyncJob | None = None
    now = datetime.now(UTC).isoformat()
    rewritten: list[AsyncJob] = []
    for job in jobs:
        if job.job_id == job_id:
            job.status = status
            job.result = result
            job.result_ref = result.get("result_ref") if isinstance(result, dict) else None
            job.updated_at = now
            updated = job
        rewritten.append(job)
    if updated is None:
        raise ValueError(f"Unknown async job id: {job_id}")
    rewrite_jobs(rewritten, store_path=store_path)
    return updated


def rewrite_jobs(jobs: list[AsyncJob], *, store_path: str | Path | None = None) -> None:
    path = resolve_store_path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for job in jobs:
            handle.write(json.dumps(job.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")


def add_job_ref_to_ledger(ledger: SpecLedger, job: AsyncJob) -> None:
    ledger.async_jobs.append(
        AsyncJobRef(
            job_id=job.job_id,
            kind=job.kind,
            status="queued",
            reasons=job.reasons,
            result_ref=job.result_ref,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    )
    ledger.async_jobs = ledger.async_jobs[-MAX_LEDGER_JOBS:]


def resolve_store_path(store_path: str | Path | None = None) -> Path:
    return Path(store_path or os.environ.get("ASYNC_JOB_STORE_PATH", DEFAULT_JOB_STORE_PATH))
