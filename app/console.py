"""FastAPI operator console for the Mutual Specification Game."""

from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse

from app.cli_dashboard import (
    design_status,
    estimate_spend,
    google_status,
)
from app.conversation_log import (
    persist_alignment_talk,
    persist_console_talk,
    persist_human_review_talk,
)
from app.formalization import formalize_ledger
from app.jobs import enqueue_async_job
from app.market_math import (
    MarketMathFrame,
    attach_user_market_marks,
    build_market_math_frame,
    format_money,
)
from app.router import (
    async_jobs_enabled,
    route_async_decision,
    route_for_stage,
)
from app.spec_state import (
    ArtifactRef,
    HumanReviewState,
    SearchPlanItem,
    SpecLedger,
    add_evidence_from_artifacts,
    add_route,
    apply_alignment_signal,
    record_stage,
    run_lean_formal_proofs,
    update_ledger_from_user_text,
    update_mutual_spec_game_state,
)
from app.telemetry.domination import (
    RouteCandidate,
    nondominated_candidates,
    route_loss,
)
from app.trader_rag import TraderRagResult, apply_rag_to_ledger, run_trader_rag
from app.verifiers import build_draft_from_ledger, verify_draft

DEFAULT_CONSOLE_TEXT = "look at HO/RB arb and give me risk on this spread"
DEFAULT_UPLOAD_DIR = "app/.adk/console_uploads"
QUERY_FORM = Form("")
SPEECH_TEXT_FORM = Form("")
FILES_FORM = File(None)
JSON_BODY = Body(...)
OPERATOR_REVIEW_TOKEN_HEADER = Header(default=None)


@dataclass(frozen=True)
class ConsoleResult:
    ledger: SpecLedger
    draft: str
    verification_passed: bool
    route_decision: object
    candidates: list[RouteCandidate]
    frontier_keys: set[str]
    artifact_count: int
    rag_result: TraderRagResult
    market_math: MarketMathFrame | None = None


@dataclass(frozen=True)
class TurnState:
    owner: str
    owner_label: str
    title: str
    detail: str
    next_action: str
    css_class: str


app = FastAPI(title="Mutual Spec Console")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return render_page(
        text="",
        result=build_initial_console_result(),
        env=os.environ,
    )


@app.post("/spec", response_class=HTMLResponse)
async def create_spec(
    background_tasks: BackgroundTasks,
    query: str = QUERY_FORM,
    speech_text: str = SPEECH_TEXT_FORM,
    files: list[UploadFile] | None = FILES_FORM,
) -> str:
    artifacts = await save_uploads(files or [])
    text = combine_query_and_speech(query, speech_text, bool(artifacts))
    result = build_console_result(text, artifacts, speech_text)
    background_tasks.add_task(
        persist_console_talk,
        result=result,
        raw_text=text,
        speech_text=speech_text,
        response_channel="html",
    )
    return render_page(text=text, result=result, env=os.environ)


@app.post("/api/spec")
async def create_spec_json(
    background_tasks: BackgroundTasks,
    query: str = QUERY_FORM,
    speech_text: str = SPEECH_TEXT_FORM,
    files: list[UploadFile] | None = FILES_FORM,
) -> JSONResponse:
    artifacts = await save_uploads(files or [])
    text = combine_query_and_speech(query, speech_text, bool(artifacts))
    result = build_console_result(text, artifacts, speech_text)
    background_tasks.add_task(
        persist_console_talk,
        result=result,
        raw_text=text,
        speech_text=speech_text,
        response_channel="json",
    )
    return JSONResponse(
        {
            "ledger": result.ledger.model_dump(mode="json"),
            "verification_passed": result.verification_passed,
            "route_decision": {
                "mode": getattr(result.route_decision, "mode", "sync"),
                "job_kind": getattr(result.route_decision, "job_kind", "unknown"),
                "reasons": list(getattr(result.route_decision, "reasons", [])),
                "risk_score": getattr(result.route_decision, "risk_score", 0),
                "expected_spec_gain": getattr(
                    result.route_decision,
                    "expected_spec_gain",
                    "low",
                ),
            },
            "human_review": result.ledger.human_review.model_dump(mode="json"),
            "provisional_answer": build_provisional_answer(result),
            "skill_compatibility": result.ledger.skill_compatibility.model_dump(mode="json"),
            "proof_obligations": [
                item.model_dump(mode="json") for item in result.ledger.proof_obligations
            ],
            "equilibrium_diagnostics": result.ledger.equilibrium_diagnostics.model_dump(
                mode="json"
            ),
            "formal_proofs": result.ledger.formal_proofs.model_dump(mode="json"),
            "frontier": [candidate.key for candidate in result.candidates if candidate.key in result.frontier_keys],
            "source_layer": {
                "provider": result.rag_result.provider,
                "status": result.rag_result.status,
                "queries": result.rag_result.queries,
                "required_evidence": result.rag_result.required_evidence,
                "warnings": result.rag_result.warnings,
                "evidence": [
                    {
                        "title": item.title,
                        "url": item.url,
                        "summary": item.summary,
                        "query": item.query,
                        "source": item.source,
                    }
                    for item in result.rag_result.evidence
                ],
            },
        }
    )


@app.post("/api/human-review")
async def human_review_json(
    background_tasks: BackgroundTasks,
    payload: dict = JSON_BODY,
    x_operator_review_token: str | None = OPERATOR_REVIEW_TOKEN_HEADER,
) -> JSONResponse:
    expected_token = os.environ.get("OPERATOR_REVIEW_TOKEN", "").strip()
    supplied_token = str(payload.get("operator_token") or x_operator_review_token or "").strip()
    if expected_token and supplied_token != expected_token:
        raise HTTPException(status_code=403, detail="operator review token required")

    ledger_payload = payload.get("ledger")
    if not isinstance(ledger_payload, dict):
        raise HTTPException(status_code=400, detail="ledger payload is required")
    action = str(payload.get("action") or "").strip().lower()
    note = str(payload.get("note") or "").strip()
    operator = str(payload.get("operator") or "console_operator").strip() or "console_operator"
    ledger = SpecLedger.model_validate(ledger_payload)
    message = apply_operator_review(ledger, action=action, note=note, operator=operator)
    update_mutual_spec_game_state(ledger)
    append_human_review_log(ledger, action=action, note=note, operator=operator, message=message)
    background_tasks.add_task(
        persist_human_review_talk,
        ledger=ledger,
        action=action,
        note=note,
        operator=operator,
        message=message,
    )
    return JSONResponse(
        {
            "ledger": ledger.model_dump(mode="json"),
            "human_review": ledger.human_review.model_dump(mode="json"),
            "status": ledger.status,
            "status_label": readable_state(ledger.status),
            "decision_gate": ledger.decision_gate,
            "decision_gate_label": readable_state(ledger.decision_gate),
            "spec_convergence": ledger.spec_convergence.model_dump(mode="json"),
            "operator_message": message,
        }
    )


@app.post("/api/alignment")
async def alignment_json(background_tasks: BackgroundTasks, payload: dict = JSON_BODY) -> JSONResponse:
    ledger_payload = payload.get("ledger")
    if not isinstance(ledger_payload, dict):
        raise HTTPException(status_code=400, detail="ledger payload is required")
    action = str(payload.get("action") or "").strip().lower()
    note = str(payload.get("note") or "").strip()
    raw_fields = payload.get("fields") or ["latent_task", "evidence_contract"]
    fields = [str(item).strip() for item in raw_fields if str(item).strip()]
    ledger = SpecLedger.model_validate(ledger_payload)
    try:
        apply_alignment_signal(ledger, action=action, note=note, fields=fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    message = alignment_message(action, ledger)
    background_tasks.add_task(
        persist_alignment_talk,
        ledger=ledger,
        action=action,
        note=note,
        message=message,
    )
    return JSONResponse(
        {
            "ledger": ledger.model_dump(mode="json"),
            "user_endorsement": ledger.user_endorsement.model_dump(mode="json"),
            "alignment_signals": [
                item.model_dump(mode="json") for item in ledger.alignment_signals
            ],
            "status": ledger.status,
            "status_label": readable_state(ledger.status),
            "decision_gate": ledger.decision_gate,
            "decision_gate_label": readable_state(ledger.decision_gate),
            "spec_convergence": ledger.spec_convergence.model_dump(mode="json"),
            "alignment_message": message,
        }
    )


def build_console_result(
    text: str,
    artifacts: list[ArtifactRef],
    speech_text: str,
) -> ConsoleResult:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, text, artifact_refs=artifacts)
    market_math = build_market_math_frame(text)
    attach_user_market_marks(ledger, market_math)
    for stage in ("ingest", "hypothesize_spec"):
        record_stage(ledger, stage)
        add_route(ledger, route_for_stage(stage, ledger))
    if speech_text.strip():
        ledger.assumptions.append("Browser speech transcript was supplied by the user and should be verified against the audio artifact when material.")
    for artifact in artifacts:
        if artifact.mime_type and artifact.mime_type.startswith("audio/"):
            ledger.verification_conditions.append("If audio content is material, transcribe or verify it before finalizing claims.")
        if artifact.mime_type and artifact.mime_type.startswith("image/"):
            ledger.verification_conditions.append("If image content is material, inspect or embed it before finalizing visual claims.")
    add_evidence_from_artifacts(ledger)
    formalize_ledger(ledger)
    route_decision = route_async_decision(
        ledger,
        mcp_configured=configured_mcp(os.environ),
        telemetry_enabled=flag_enabled("RESOURCE_REGION_DOMINATION_ENABLED"),
        artifact_count=len(artifacts),
    )
    record_stage(ledger, "retrieve_evidence")
    add_route(ledger, route_for_stage("retrieve_evidence", ledger))
    if should_defer_deep_retrieval(route_decision, os.environ):
        rag_result = deferred_rag_result(
            text,
            env=os.environ,
            search_plan=ledger.search_plan,
            route_decision=route_decision,
        )
        if async_jobs_enabled():
            enqueue_async_job(ledger, route_decision)
    else:
        rag_result = run_trader_rag(
            text,
            env=console_sync_rag_env(os.environ),
            search_plan=ledger.search_plan,
        )
    apply_rag_to_ledger(ledger, rag_result)
    record_stage(ledger, "draft_output")
    add_route(ledger, route_for_stage("draft_output", ledger))
    draft = build_draft_from_ledger(ledger)
    record_stage(ledger, "verify")
    add_route(ledger, route_for_stage("verify", ledger))
    verification = verify_draft(ledger, draft)
    ledger.verification_findings = verification.findings
    update_mutual_spec_game_state(ledger)
    if not route_decision.should_enqueue:
        route_decision = route_async_decision(
            ledger,
            mcp_configured=configured_mcp(os.environ),
            telemetry_enabled=flag_enabled("RESOURCE_REGION_DOMINATION_ENABLED"),
            artifact_count=len(artifacts),
            failed_verification=not verification.passed,
        )
    if route_decision.should_enqueue and async_jobs_enabled():
        if not ledger.async_jobs:
            enqueue_async_job(ledger, route_decision)
        ledger.status = "async_pending"
    else:
        ledger.status = (
            "finalized"
            if verification.passed and ledger.decision_gate != "needs_more_info"
            else "clarifying"
        )
    update_mutual_spec_game_state(ledger)
    if flag_enabled("LEAN_PROOF_CHECKS_ENABLED"):
        run_lean_formal_proofs(ledger)
        update_mutual_spec_game_state(ledger)
    candidates = sample_route_candidates(os.environ)
    frontier_keys = {candidate.key for candidate in nondominated_candidates(candidates)}
    return ConsoleResult(
        ledger=ledger,
        draft=draft,
        verification_passed=verification.passed,
        route_decision=route_decision,
        candidates=candidates,
        frontier_keys=frontier_keys,
        artifact_count=len(artifacts),
        rag_result=rag_result,
        market_math=market_math,
    )


def build_initial_console_result() -> ConsoleResult:
    ledger = SpecLedger(status="clarifying")
    update_mutual_spec_game_state(ledger)
    route_decision = route_async_decision(
        ledger,
        mcp_configured=configured_mcp(os.environ),
        telemetry_enabled=flag_enabled("RESOURCE_REGION_DOMINATION_ENABLED"),
        artifact_count=0,
    )
    ledger.decision_gate = "needs_more_info"
    candidates = sample_route_candidates(os.environ)
    return ConsoleResult(
        ledger=ledger,
        draft="",
        verification_passed=False,
        route_decision=route_decision,
        candidates=candidates,
        frontier_keys={candidate.key for candidate in nondominated_candidates(candidates)},
        artifact_count=0,
        rag_result=TraderRagResult(
            provider=os.environ.get("TRADER_RAG_PROVIDER", "disabled"),
            status="not_applicable",
            queries=[],
            required_evidence=[],
            warnings=["Submit a query to run the source layer."],
        ),
        market_math=None,
    )


def should_defer_deep_retrieval(
    route_decision: object,
    env: Mapping[str, str],
) -> bool:
    if not async_jobs_enabled():
        return False
    if not flag_enabled_in_env(env, "CONSOLE_DEEP_RETRIEVAL_ASYNC", default=True):
        return False
    if not getattr(route_decision, "should_enqueue", False):
        return False
    providers = configured_provider_names(env)
    return (
        configured_mcp(env)
        or "mcp" in providers
        or "vertex_ai_search" in providers
        or "google_agent_search" in providers
    )


def deferred_rag_result(
    text: str,
    *,
    env: Mapping[str, str],
    search_plan: list[SearchPlanItem],
    route_decision: object,
) -> TraderRagResult:
    queries = [item.query for item in search_plan if item.required]
    required_evidence = [item.purpose for item in search_plan if item.required]
    reasons = list(getattr(route_decision, "reasons", []))
    job_kind = str(getattr(route_decision, "job_kind", "deep_research_and_verification"))
    warnings = [
        "Deep retrieval was deferred to a background route to keep the operator response interactive.",
        f"Queued route: {job_kind}.",
    ]
    if reasons:
        warnings.append("Route reasons: " + ", ".join(reasons[:6]) + ".")
    return TraderRagResult(
        provider=env.get("TRADER_RAG_PROVIDER", "disabled"),
        status="deferred",
        queries=queries or [text],
        required_evidence=required_evidence,
        warnings=warnings,
    )


def console_sync_rag_env(env: Mapping[str, str]) -> dict[str, str]:
    sync_env = dict(env)
    if not flag_enabled_in_env(env, "CONSOLE_FAST_RAG_ENABLED", default=True):
        return sync_env

    providers = configured_provider_names(env)
    provider = (env.get("CONSOLE_SYNC_RAG_PROVIDER") or "").strip().lower()
    if not provider:
        if "spanner_rag" in providers:
            provider = "spanner_rag"
        elif "google_cse" in providers:
            provider = "google_cse"
        elif "fixture" in providers:
            provider = "fixture"
        elif "mcp" in providers:
            provider = "mcp"
        elif providers:
            provider = next(iter(providers))

    if provider:
        sync_env["TRADER_RAG_PROVIDER"] = provider
    sync_env["TRADER_RAG_MAX_QUERIES"] = env.get("CONSOLE_SYNC_RAG_MAX_QUERIES", "1")
    sync_env["TRADER_RAG_MAX_RESULTS"] = env.get("CONSOLE_SYNC_RAG_MAX_RESULTS", "3")
    sync_env["TRADER_RAG_TIMEOUT_SECONDS"] = env.get(
        "CONSOLE_SYNC_RAG_TIMEOUT_SECONDS",
        "2.5",
    )
    if sync_env.get("TRADER_RAG_PROVIDER") == "spanner_rag":
        sync_env["SPANNER_RAG_SEARCH_MODE"] = env.get(
            "CONSOLE_SYNC_SPANNER_RAG_SEARCH_MODE",
            "semantic",
        )
    return sync_env


def configured_provider_names(env: Mapping[str, str]) -> set[str]:
    raw = env.get("TRADER_RAG_PROVIDER", "disabled")
    return {
        item.strip().lower()
        for item in re.split(r"[,;+]", raw)
        if item.strip() and item.strip().lower() not in {"disabled", "none", "off"}
    }


async def save_uploads(files: list[UploadFile]) -> list[ArtifactRef]:
    artifacts: list[ArtifactRef] = []
    upload_dir = Path(os.environ.get("CONSOLE_UPLOAD_DIR", DEFAULT_UPLOAD_DIR))
    upload_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        if not file.filename:
            continue
        content = await file.read()
        if not content:
            continue
        mime_type = file.content_type or mimetypes.guess_type(file.filename)[0]
        safe_name = safe_filename(file.filename)
        artifact_id = f"console-{uuid4().hex[:12]}"
        stored_name = f"{artifact_id}-{safe_name}"
        local_path = upload_dir / stored_name
        local_path.write_bytes(content)
        note = local_path.as_posix()
        gcs_uri = maybe_upload_to_gcs(local_path, stored_name, mime_type)
        if gcs_uri:
            note = gcs_uri
        artifacts.append(
            ArtifactRef(
                artifact_id=artifact_id,
                filename=safe_name,
                mime_type=mime_type,
                version=0,
                source="upload",
                note=note,
            )
        )
    return artifacts


def maybe_upload_to_gcs(
    local_path: Path,
    object_name: str,
    mime_type: str | None,
) -> str | None:
    bucket_name = os.environ.get("CONSOLE_UPLOAD_GCS_BUCKET") or os.environ.get(
        "ARTIFACTS_GCS_BUCKET"
    )
    if not bucket_name:
        return None
    try:
        from google.cloud import storage

        client = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
        bucket = client.bucket(bucket_name)
        blob_name = f"console-uploads/{datetime.now(UTC).strftime('%Y/%m/%d')}/{object_name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path.as_posix(), content_type=mime_type)
        return f"gs://{bucket_name}/{blob_name}"
    except Exception:
        return None


def combine_query_and_speech(query: str, speech_text: str, has_artifacts: bool) -> str:
    parts = [query.strip()]
    if speech_text.strip():
        parts.append(f"Speech transcript: {speech_text.strip()}")
    text = "\n\n".join(part for part in parts if part)
    if text:
        return text
    if has_artifacts:
        return "Analyze the uploaded multimodal artifact and reconstruct the executable task specification."
    return DEFAULT_CONSOLE_TEXT


def sample_route_candidates(env: Mapping[str, str]) -> list[RouteCandidate]:
    models = parse_csv(env.get("CONSOLE_MODEL_ZOO"), "gemini-3.5-flash,gemini-3.5-flash-strong")
    regions = parse_csv(env.get("CONSOLE_ROUTE_REGIONS"), "us-central1,us-south1,us-east4")
    base_cost = parse_float(env.get("LOSS_COST_EPSILON_USD"), 0.01)
    spread = parse_float(env.get("LOSS_COMPUTE_SPREAD_STRESS_EPSILON_USD"), 0.01)
    candidates: list[RouteCandidate] = []
    for model_index, model in enumerate(models):
        for region_index, region in enumerate(regions):
            candidates.append(
                RouteCandidate(
                    model=model,
                    region=region,
                    policy_allowed=True,
                    model_quality_loss=0.06 * model_index,
                    latency_loss=80.0 + 90.0 * region_index,
                    all_resource_cost_loss=base_cost * (1 + model_index + region_index),
                    compute_electricity_spread_loss=spread * (1 + abs(1 - region_index)),
                    carbon_context_loss=0.03 * region_index,
                    proxy_confidence_penalty=0.0 if region in {"us-south1", "us-east4"} else 0.05,
                )
            )
    return candidates


def render_page(
    *,
    text: str,
    result: ConsoleResult,
    env: Mapping[str, str],
) -> str:
    spend = estimate_spend(text, env=env, output_tokens=None)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Mutual Spec Console</title>",
            "<style>",
            CSS,
            "</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            render_header(result),
            render_workspace(text, result, spend, env),
            "</main>",
            render_ledger_payload(result),
            "<script>",
            JS,
            "</script>",
            "</body>",
            "</html>",
        ]
    )


def render_ledger_payload(result: ConsoleResult) -> str:
    payload = json.dumps(result.ledger.model_dump(mode="json"), sort_keys=True).replace(
        "</",
        "<\\/",
    )
    return f'<script id="ledgerPayload" type="application/json">{payload}</script>'


def render_header(result: ConsoleResult) -> str:
    gate_class = gate_status_class(result.ledger.decision_gate)
    has_query = bool(result.ledger.expressed_query or result.ledger.user_request)
    verify_class = "verified" if result.verification_passed else "neutral" if not has_query else "blocked"
    verify_label = "passed" if result.verification_passed else "waiting" if not has_query else "blocked"
    route_mode = str(getattr(result.route_decision, "mode", "sync"))
    return f"""
<header class="topbar">
  <div>
    <h1>Mutual Spec Console</h1>
    <p class="subtle">q -> theta -> s | ledger {escape(result.ledger.ledger_id)}</p>
  </div>
  <div class="status-strip">
    <span id="specStatusPill" class="pill {status_class(result.ledger.status)}">Spec: {escape(readable_state(result.ledger.status))}</span>
    <span id="verifierStatusPill" class="pill {verify_class}">Verifier: {escape(verify_label)}</span>
    <span id="gateStatusPill" class="pill {gate_class}">Gate: {escape(readable_state(result.ledger.decision_gate))}</span>
    <span id="routeStatusPill" class="pill {route_status_class(route_mode)}">Route: {escape(readable_state(route_mode))}</span>
  </div>
</header>
"""


def render_workspace(
    text: str,
    result: ConsoleResult,
    spend: object,
    env: Mapping[str, str],
) -> str:
    return f"""
<section class="workspace">
  <section class="left-rail">
    {render_input_form(text)}
    {render_turn_panel(result)}
  </section>
  <section class="center-rail">
    {render_operator_route_panel(result)}
    {render_decision_summary_panel(result)}
    {render_alignment_panel(result)}
  </section>
  <section class="right-rail">
    {render_human_review_panel(result)}
    {render_source_layer_panel(result)}
    {render_artifacts_panel(result)}
    {render_route_panel(result)}
    {render_skill_compatibility_panel(result)}
    {render_spend_panel(spend)}
    {render_status_panel(env)}
  </section>
  <section class="advanced-rail">
    <details class="advanced-drawer">
      <summary>Advanced ledgers and diagnostics</summary>
      <div class="advanced-grid">
        {render_game_panel(result)}
        {render_proof_obligations_panel(result)}
        {render_equilibrium_panel(result)}
        {render_formal_proof_panel(result)}
        {render_spec_panel(result)}
        {render_frontier_panel(result)}
      </div>
    </details>
  </section>
</section>
"""


def render_input_form(text: str) -> str:
    return f"""
<form class="panel input-panel" method="post" action="/spec" enctype="multipart/form-data">
  <div class="panel-title">
    <h2>Task Signal</h2>
    <button id="runSpec" type="submit">Run Spec</button>
  </div>
  <label for="query">Query</label>
  <textarea id="query" name="query" rows="8">{escape(text)}</textarea>
  <input id="speech_text" name="speech_text" type="hidden">
  <label for="files">Artifacts</label>
  <input id="files" name="files" type="file" multiple accept="image/*,audio/*,application/pdf,text/*">
  <div class="button-row">
    <button id="recordAudio" type="button">Record Speech</button>
    <button id="stopAudio" type="button" disabled>Stop</button>
    <button id="transcribeSpeech" type="button">Speech Text</button>
  </div>
  <p id="captureState" class="capture-state">idle</p>
</form>
"""


def render_turn_panel(result: ConsoleResult) -> str:
    turn = current_turn_state(result)
    return f"""
<section class="panel turn-panel {escape(turn.css_class)}">
  <div class="turn-heading">
    <span class="turn-ball">{escape(turn.owner_label[:1])}</span>
    <div>
      <h2>Current Handoff</h2>
      <p id="turnTitle" class="subtle">{escape(turn.title)}</p>
    </div>
  </div>
  <div class="turn-owner-row">
    <span id="turnOwnerPill" class="pill {escape(turn.css_class)}">Ball: {escape(turn.owner_label)}</span>
    <span class="pill neutral">Gate: {escape(readable_state(result.ledger.decision_gate))}</span>
  </div>
  <p id="turnDetail" class="turn-detail">{escape(turn.detail)}</p>
  <div class="next-move">
    <span class="label-inline">Next move</span>
    <p id="turnNext">{escape(turn.next_action)}</p>
  </div>
</section>
"""


def current_turn_state(result: ConsoleResult) -> TurnState:
    ledger = result.ledger
    review = ledger.human_review
    formal_missing = latest_missing_formal_obligations(ledger)
    open_evidence = [
        item for item in ledger.search_plan if item.required and item.status != "satisfied"
    ]

    if not ledger.expressed_query:
        return TurnState(
            owner="user",
            owner_label="User",
            title="Waiting for task signal",
            detail="No query has entered the specification game yet.",
            next_action="Enter a task, attach artifacts if needed, then run the spec.",
            css_class="user",
        )
    if review.required and review.status in {"queued", "in_review"}:
        return TurnState(
            owner="operator",
            owner_label="Operator",
            title="Human review packet is open",
            detail=(
                "The operator can start review, request changes, reject, or approve only "
                "after hard evidence and proof obligations are cleared."
            ),
            next_action=review.required_actions[0]
            if review.required_actions
            else "Review the decision packet and choose a gate action.",
            css_class="operator",
        )
    if review.status in {"changes_requested", "rejected"}:
        return TurnState(
            owner="user_agent",
            owner_label="User/Agent",
            title=f"Review is {readable_state(review.status)}",
            detail="The shared spec needs correction, more evidence, or a revised decision frame.",
            next_action=review.last_reviewer_signal
            or "Resolve the operator note, then rerun or resubmit the review packet.",
            css_class="blocked",
        )
    if result.rag_result.status == "deferred" or ledger.status == "async_pending":
        return TurnState(
            owner="tools",
            owner_label="Agent/Tools",
            title="Deep evidence route is queued",
            detail="The page returned a provisional frame while tool-heavy research is deferred.",
            next_action="Use the source and proof obligations as the research queue before finalizing.",
            css_class="tools",
        )
    if ledger.ambiguities or formal_missing:
        missing = [item.field for item in ledger.ambiguities] + formal_missing
        return TurnState(
            owner="user",
            owner_label="User",
            title="Specification still needs a user move",
            detail="The current spec has unresolved fields: " + ", ".join(missing[:6]) + ".",
            next_action="Answer or correct the unresolved fields in the alignment loop.",
            css_class="user",
        )
    if open_evidence:
        return TurnState(
            owner="tools",
            owner_label="Agent/Tools",
            title="Evidence obligations remain open",
            detail=f"{len(open_evidence)} required evidence item(s) still need support or waiver.",
            next_action=open_evidence[0].purpose,
            css_class="tools",
        )
    if not result.verification_passed:
        return TurnState(
            owner="agent",
            owner_label="Agent",
            title="Verifier blocked the draft",
            detail="The response must be repaired before it can move to a decision gate.",
            next_action="Repair verifier findings and rerun verification.",
            css_class="agent",
        )
    if ledger.decision_gate != "needs_more_info":
        return TurnState(
            owner="gate",
            owner_label="Gate",
            title="Decision frame is ready for the user",
            detail="Hard gates are clear for the current scope.",
            next_action="Use, copy, or correct the frame through the alignment loop.",
            css_class="verified",
        )
    return TurnState(
        owner="agent",
        owner_label="Agent",
        title="Provisional frame needs one more pass",
        detail="The system has not identified a single owner-specific blocker.",
        next_action="Inspect the chain step marked current or blocked.",
        css_class="agent",
    )


def render_operator_route_panel(result: ConsoleResult) -> str:
    steps = build_operator_route_steps(result)
    rows = "\n".join(
        f"""
<li class="{escape(css_class)}">
  <span class="route-index">{index}</span>
  <div>
    <strong>{escape(title)}</strong>
    <span>{escape(state_label)}</span>
    <p>{escape(detail)}</p>
  </div>
</li>
"""
        for index, title, state_label, detail, css_class in steps
    )
    chain_rows = "\n".join(
        f"""
<li class="{escape(node['state'])} {escape(node['owner'])}">
  <span class="chain-owner">{escape(node['owner_label'])}</span>
  <strong>{escape(node['title'])}</strong>
  <p>{escape(node['detail'])}</p>
</li>
"""
        for node in build_process_chain_nodes(result)
    )
    return f"""
<section class="panel route-progress-panel">
  <div class="panel-title">
    <div>
      <h2>Chain + Loop State</h2>
      <p class="subtle">q -> theta -> evidence -> review -> gate.</p>
    </div>
    <span id="routeGatePill" class="pill {gate_status_class(result.ledger.decision_gate)}">{escape(readable_state(result.ledger.decision_gate))}</span>
  </div>
  <ol class="chain-steps">{chain_rows}</ol>
  <details>
    <summary>Route Before Decision</summary>
    <ol class="route-steps">{rows}</ol>
    {render_model_handoff_plan(result)}
  </details>
</section>
"""


def build_operator_route_steps(result: ConsoleResult) -> list[tuple[int, str, str, str, str]]:
    ledger = result.ledger
    formal_missing = latest_missing_formal_obligations(ledger)
    source_status = result.rag_result.status
    review = ledger.human_review
    route_mode = str(getattr(result.route_decision, "mode", "sync"))
    spec_missing = [item.field for item in ledger.ambiguities]
    decision_clear = result.verification_passed and ledger.decision_gate != "needs_more_info"

    source_class = source_status_class(source_status)
    if source_status in {"retrieved", "empty"}:
        source_label = "Complete" if source_status == "retrieved" else "No cited source found"
    elif source_status == "deferred":
        source_label = "Deferred"
    elif source_status in {"missing_config", "provider_error"}:
        source_label = "Blocked"
    else:
        source_label = "Running"

    review_class = review_status_class(review.status) if review.required else "neutral"
    review_label = "Not required" if not review.required else readable_state(review.status)
    review_detail = (
        "No human-review gate applies to this request."
        if not review.required
        else (review.required_actions[0] if review.required_actions else review.reasons[0] if review.reasons else "Reviewer approval is required.")
    )

    return [
        (
            1,
            "Input captured",
            "Complete" if ledger.expressed_query else "Waiting",
            "User query and attached artifacts were accepted by the console.",
            "verified" if ledger.expressed_query else "clarify",
        ),
        (
            2,
            "Shared spec inferred",
            "Needs clarification" if spec_missing else "Complete",
            (
                "Missing fields: " + ", ".join(spec_missing)
                if spec_missing
                else f"goal={ledger.goal or 'set'}, audience={ledger.audience or 'set'}, format={ledger.output_format or 'set'}"
            ),
            "blocked" if spec_missing else "verified",
        ),
        (
            3,
            "Sources and retrieval",
            source_label,
            f"{result.rag_result.provider}: {source_status}; cited items={len(result.rag_result.evidence)}.",
            source_class,
        ),
        (
            4,
            "Formal obligations",
            "Needs clarification" if formal_missing else "Complete",
            (
                "Missing formal spec fields: " + ", ".join(formal_missing)
                if formal_missing
                else "No missing formal obligations in the latest formalization pass."
            ),
            "blocked" if formal_missing else "verified",
        ),
        (
            5,
            "Verifier",
            "Passed" if result.verification_passed else "Blocked",
            "Verifier found no blocking issue." if result.verification_passed else "Verifier still has blocking findings.",
            "verified" if result.verification_passed else "blocked",
        ),
        (
            6,
            "Human gate",
            review_label,
            review_detail,
            review_class,
        ),
        (
            7,
            "Decision frame",
            "Decision-ready" if decision_clear else "Provisional",
            f"Decision gate is {readable_state(ledger.decision_gate)}; route mode is {readable_state(route_mode)}.",
            "verified" if decision_clear else "clarify",
        ),
    ]


def build_process_chain_nodes(result: ConsoleResult) -> list[dict[str, str]]:
    ledger = result.ledger
    review = ledger.human_review
    formal_missing = latest_missing_formal_obligations(ledger)
    open_evidence = [
        item for item in ledger.search_plan if item.required and item.status != "satisfied"
    ]
    has_query = bool(ledger.expressed_query or ledger.user_request)
    spec_ready = has_query and not ledger.ambiguities and bool(ledger.goal)
    evidence_ready = bool(result.rag_result.evidence) or not open_evidence
    evidence_async = result.rag_result.status == "deferred" or ledger.status == "async_pending"
    formal_ready = result.verification_passed and not formal_missing
    review_ready = not review.required or review.status == "approved"
    decision_ready = (
        result.verification_passed
        and ledger.decision_gate != "needs_more_info"
        and review_ready
    )
    active_owner = current_turn_state(result).owner

    nodes = [
        {
            "title": "1. User signal",
            "owner": "user",
            "owner_label": "User",
            "detail": "Query and artifacts enter the shared spec.",
            "state": "complete" if has_query else "current",
        },
        {
            "title": "2. Shared spec",
            "owner": "agent",
            "owner_label": "Agent",
            "detail": "Infer theta, goal, audience, format, and constraints.",
            "state": "complete" if spec_ready else "blocked" if has_query else "waiting",
        },
        {
            "title": "3. Evidence loop",
            "owner": "tools",
            "owner_label": "Tools",
            "detail": "Retrieve, cite, or mark evidence obligations open.",
            "state": "waiting"
            if not has_query
            else "async"
            if evidence_async
            else "complete"
            if evidence_ready
            else "current",
        },
        {
            "title": "4. Verify",
            "owner": "agent",
            "owner_label": "Verifier",
            "detail": "Block unsupported or underspecified claims.",
            "state": "complete" if formal_ready else "blocked" if has_query else "waiting",
        },
        {
            "title": "5. Human review",
            "owner": "operator",
            "owner_label": "Operator",
            "detail": "Approve, request changes, or reject high-stakes packets.",
            "state": (
                "skipped"
                if not review.required
                else "complete"
                if review.status == "approved"
                else "blocked"
                if review.status in {"changes_requested", "rejected"}
                else "current"
            ),
        },
        {
            "title": "6. Decision gate",
            "owner": "gate",
            "owner_label": "Gate",
            "detail": "Only clears when spec, evidence, verifier, and review agree.",
            "state": "complete" if decision_ready else "blocked" if has_query else "waiting",
        },
    ]
    for node in nodes:
        if node["owner"] == active_owner and node["state"] not in {"complete", "skipped"}:
            node["state"] = "current"
    return nodes


def render_model_handoff_plan(result: ConsoleResult) -> str:
    rows = "\n".join(
        f"""
<tr>
  <td>{escape(route.stage)}</td>
  <td>{escape(route.model_class)}</td>
  <td>{escape(route.selected_model)}</td>
  <td>{escape(model_execution_mode(route.stage, result))}</td>
</tr>
"""
        for route in result.ledger.route_history
    )
    if not rows:
        rows = '<tr><td colspan="4">No model route records yet.</td></tr>'
    return f"""
  <details open>
    <summary>Model Handoff Plan</summary>
    <p class="subtle">This console records model selection per stage. Stages marked deterministic did not call a Gemini model in this request.</p>
    <div class="table-wrap">
      <table class="compact-table">
        <thead><tr><th>stage</th><th>model class</th><th>selected model</th><th>actual execution</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </details>
"""


def model_execution_mode(stage: str, result: ConsoleResult) -> str:
    if stage == "retrieve_evidence" and result.rag_result.status == "deferred":
        return "background retrieval queued"
    if stage == "retrieve_evidence" and result.rag_result.status == "retrieved":
        return "tool/RAG retrieval executed"
    if stage == "verify":
        return "deterministic verifier, model route recorded"
    if stage == "draft_output":
        return "deterministic draft builder, model route recorded"
    if stage in {"ingest", "hypothesize_spec"}:
        return "deterministic ledger stage, model route recorded"
    return "model route recorded"


def apply_operator_review(
    ledger: SpecLedger,
    *,
    action: str,
    note: str,
    operator: str,
) -> str:
    status_by_action = {
        "start": "in_review",
        "start_review": "in_review",
        "approve": "approved",
        "approved": "approved",
        "request_changes": "changes_requested",
        "changes_requested": "changes_requested",
        "reject": "rejected",
        "rejected": "rejected",
    }
    status = status_by_action.get(action)
    if not status:
        raise HTTPException(status_code=400, detail="unsupported human-review action")
    if not ledger.human_review.required and status != "in_review":
        status = "not_required"
        message = "Human review is not required for this spec; the operator note was logged."
    elif status == "approved" and has_open_hard_obligations(ledger):
        status = "changes_requested"
        message = "Operator approval cannot clear yet because source, proof, or verifier obligations remain open."
    elif status == "approved":
        message = "Operator approved the human-review gate."
    elif status == "changes_requested":
        message = "Operator requested changes or more evidence."
    elif status == "rejected":
        message = "Operator rejected the current decision frame."
    else:
        message = "Operator started human review."
    signal = f"{operator}: {action}"
    if note:
        signal = f"{signal} - {note}"
    ledger.human_review = ledger.human_review.model_copy(
        update={
            "status": status,
            "last_reviewer_signal": signal,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    return message


def alignment_message(action: str, ledger: SpecLedger) -> str:
    if action == "endorse":
        return "User endorsed the inferred latent task and shared specification."
    if action == "correct":
        return "User corrected the inferred latent task; the spec is reopened for convergence."
    if action == "request_evidence":
        return "User requested evidence before accepting the shared specification."
    return f"Alignment signal recorded; gate is {readable_state(ledger.decision_gate)}."


def has_open_hard_obligations(ledger: SpecLedger) -> bool:
    return (
        any(item.severity == "high" for item in ledger.verification_findings)
        or any(
            item.required and item.status not in {"satisfied", "waived"}
            for item in ledger.proof_obligations
            if item.source_type in {"evidence", "formalization", "artifact", "verifier_finding"}
        )
        or bool(ledger.ambiguities)
    )


def append_human_review_log(
    ledger: SpecLedger,
    *,
    action: str,
    note: str,
    operator: str,
    message: str,
) -> None:
    path = Path(os.environ.get("HUMAN_REVIEW_LOG_PATH", "/tmp/mutual_spec_human_reviews.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "ledger_id": ledger.ledger_id,
        "review_id": ledger.human_review.review_id,
        "operator": operator,
        "action": action,
        "status": ledger.human_review.status,
        "decision_gate": ledger.decision_gate,
        "message": message,
        "note": note,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def render_decision_summary_panel(result: ConsoleResult) -> str:
    ledger = result.ledger
    answer_lines = build_provisional_answer(result)
    primary = answer_lines[0] if answer_lines else "No answer generated."
    supporting = answer_lines[1:]
    evidence_count = len(result.rag_result.evidence)
    review = ledger.human_review
    source_status = result.rag_result.status
    summary_class = gate_status_class(ledger.decision_gate)
    review_notice = render_human_review_notice(review)
    return f"""
<section class="panel decision-panel {summary_class}" id="decisionPanel">
  <div class="panel-title">
    <div>
      <h2>Answer First / Provisional Decision Frame</h2>
      <p class="subtle">This is the operator answer. Ledgers below explain why.</p>
    </div>
    <div class="button-row">
      <button type="button" id="copyAnswer">Copy Answer</button>
    </div>
  </div>
  <div class="decision-callout">
    <span class="decision-label">{escape(readable_state(ledger.decision_gate))}</span>
    <p>{escape(primary)}</p>
  </div>
  {review_notice}
  {render_list(supporting)}
  <div class="status-strip decision-strip">
    <span class="pill {source_status_class(source_status)}">sources {escape(source_status)} / {evidence_count}</span>
    <span class="pill {review_status_class(review.status)}">human review {escape(review.status)}</span>
    <span class="pill {'verified' if result.verification_passed else 'blocked'}">verification {'pass' if result.verification_passed else 'blocked'}</span>
  </div>
</section>
"""


def render_human_review_notice(review: HumanReviewState) -> str:
    if not review.required:
        return """
  <div class="review-notice neutral">
    <strong>No human-review gate.</strong>
    <p>This task is not currently blocked by high-stakes review policy.</p>
  </div>
"""
    reason = review.reasons[0] if review.reasons else "The task is high stakes or has unresolved proof obligations."
    action = review.required_actions[0] if review.required_actions else "Resolve the open evidence and verification packet."
    return f"""
  <div class="review-notice {review_status_class(review.status)}">
    <strong>Why human review is {escape(readable_state(review.status))}:</strong>
    <p>{escape(reason)}</p>
    <p><span class="label-inline">Next</span> {escape(action)}</p>
    <p><span class="label-inline">Meaning</span> The agent may provide a provisional decision frame, but it must not present this as execution-ready or take/broker action.</p>
  </div>
"""


def render_status_panel(env: Mapping[str, str]) -> str:
    items = design_status(env) + google_status(env)
    rows = "\n".join(
        f"""
<div class="status-row">
  <span class="dot {escape(item.status)}"></span>
  <span class="status-word {escape(item.status)}">{escape(status_word(item.status))}</span>
  <div>
    <strong>{escape(item.label)}</strong>
    <span>{escape(item.detail)}</span>
  </div>
</div>
"""
        for item in items
    )
    return f"""
<section class="panel">
  <h2>Platform State</h2>
  {render_status_legend()}
  <div class="status-grid">{rows}</div>
</section>
"""


def render_alignment_panel(result: ConsoleResult) -> str:
    ledger = result.ledger
    convergence = ledger.spec_convergence
    endorsement = ledger.user_endorsement
    latent_task = current_latent_task(ledger)
    latest = ledger.alignment_signals[-1] if ledger.alignment_signals else None
    signal = (
        f"{latest.action}: {latest.note or ', '.join(latest.fields)}"
        if latest
        else "awaiting user signal"
    )
    pending = ", ".join(endorsement.pending_fields) or "none"
    rejected = ", ".join(endorsement.rejected_fields) or "none"
    endorsed = ", ".join(endorsement.endorsed_fields) or "none"
    return f"""
<section class="panel alignment-panel">
  <div class="panel-title">
    <div>
      <h2>Alignment Loop</h2>
      <p class="subtle">User and agent update the shared spec before the decision frame is trusted.</p>
    </div>
    <span id="alignmentConvergencePill" class="pill {alignment_status_class(convergence.status)}">alignment {convergence.overall:.2f}</span>
  </div>
  <div class="alignment-grid">
    <div>
      <span class="label-inline">q</span>
      <p>{escape(ledger.expressed_query or ledger.user_request or 'none')}</p>
    </div>
    <div>
      <span class="label-inline">theta</span>
      <p>{escape(latent_task)}</p>
    </div>
    <div>
      <span class="label-inline">s</span>
      <p>{escape(f"goal={ledger.goal or 'unresolved'}; audience={ledger.audience or 'unresolved'}; format={ledger.output_format or 'unresolved'}")}</p>
    </div>
  </div>
  <dl class="kv alignment-kv">
    <dt>endorsed</dt><dd id="alignmentEndorsed">{escape(endorsed)}</dd>
    <dt>pending</dt><dd id="alignmentPending">{escape(pending)}</dd>
    <dt>rejected</dt><dd id="alignmentRejected">{escape(rejected)}</dd>
    <dt>last move</dt><dd id="alignmentLastSignal">{escape(signal)}</dd>
  </dl>
  <div class="review-actions alignment-actions">
    <label for="alignmentNote">Correction or evidence request</label>
    <textarea id="alignmentNote" rows="3" placeholder="Example: I meant a 1-week alert, not execution; verify inventory and contract month first."></textarea>
    <div class="button-row">
      <button type="button" data-alignment-action="endorse">Endorse Spec</button>
      <button type="button" data-alignment-action="correct">Correct Theta</button>
      <button type="button" data-alignment-action="request_evidence">Request Evidence</button>
    </div>
    <p id="alignmentResult" class="capture-state">No alignment move submitted in this browser session.</p>
  </div>
</section>
"""


def current_latent_task(ledger: SpecLedger) -> str:
    if ledger.latent_type_beliefs:
        return ledger.latent_type_beliefs[0].description
    if ledger.latent_intent_hypotheses:
        return ledger.latent_intent_hypotheses[0]
    return "Reconstruct the user's latent task from q."


def render_game_panel(result: ConsoleResult) -> str:
    ledger = result.ledger
    material_missing = [item.field for item in ledger.ambiguities]
    gate_clear = result.verification_passed and ledger.decision_gate != "needs_more_info"
    source_evidence = any(
        item.source_type
        in {
            "google_agent_search",
            "google_cse",
            "mcp",
            "rag",
            "spanner_rag",
            "vertex_ai_search",
            "web",
        }
        for item in ledger.evidence
        if item.used
    )
    stages = [
        (
            "1. Elicitation",
            "Cooperative partial-information game",
            "Infer latent task from compressed signal and artifacts.",
            "open" if material_missing else "done",
        ),
        (
            "2. Commitment",
            "Signaling game",
            "Commit to decision frame, evidence contract, and proof obligations.",
            "done" if ledger.evidence_contract else "open",
        ),
        (
            "3. Execution Graph",
            "Full-information graph game",
            "Route to search tools, model reasoning, verifier, model-region frontier, and async work if needed.",
            "done" if source_evidence else "open",
        ),
        (
            "4. Gate",
            "Verification and budget condition",
            "Do not claim go/no-go until missing obligations are resolved.",
            "done" if gate_clear else "blocked",
        ),
    ]
    rows = "\n".join(
        f"""
<li class="{escape(state)}">
  <strong>{escape(title)}</strong>
  <span>{escape(game)}</span>
  <p>{escape(description)}</p>
</li>
"""
        for title, game, description, state in stages
    )
    convergence = ledger.spec_convergence
    actual_stage_rows = "\n".join(
        f"""
<li class="{escape(item.status)}">
  <strong>{escape(item.stage_id)}</strong>
  <span>{escape(item.game_type)}</span>
  <p>{escape(item.objective)} Blocks: {escape(', '.join(item.blocking_conditions) or 'none')}</p>
</li>
"""
        for item in ledger.game_states
    ) or "<li><span>No executable game state generated.</span></li>"
    belief_rows = "\n".join(
        f"""
<li>
  <strong>{escape(item.type_id)}</strong>
  <span>p={item.probability:.2f} | next={escape(item.next_best_action)}</span>
  <p>{escape(item.description)} Signals: {escape(', '.join(item.evidence_signals))}</p>
</li>
"""
        for item in ledger.latent_type_beliefs[:5]
    ) or "<li><span>No latent type beliefs.</span></li>"
    claim_rows = "\n".join(
        f"""
<li class="{escape(item.verifier_state)}">
  <strong>{escape(item.claim_type)}</strong>
  <span>{escape(item.verifier_state)} | {escape(item.claim_id)}</span>
  <p>{escape(item.text)}</p>
</li>
"""
        for item in ledger.claim_graph[:8]
    ) or "<li><span>No claim graph.</span></li>"
    return f"""
<section class="panel game-panel">
  <div class="panel-title">
    <h2>Mutual Specification Game</h2>
    <span class="muted">convergence {convergence.overall:.2f} / {escape(convergence.status)}</span>
  </div>
  <ol class="game-list">{rows}</ol>
  <details open>
    <summary>Executable Stage State</summary>
    <ul class="source-list">{actual_stage_rows}</ul>
  </details>
  <details>
    <summary>Latent Type Beliefs</summary>
    <ul class="source-list">{belief_rows}</ul>
  </details>
  <details>
    <summary>Claim Graph</summary>
    <ul class="source-list">{claim_rows}</ul>
  </details>
</section>
"""


def render_proof_obligations_panel(result: ConsoleResult) -> str:
    obligations = result.ledger.proof_obligations
    open_count = sum(
        1 for item in obligations if item.required and item.status not in {"satisfied", "waived"}
    )
    rows = "\n".join(
        f"""
<li class="{escape(item.status)}">
  <strong>{escape(item.source_type)} / {escape(item.status)}</strong>
  <span>{escape(item.obligation_id)}</span>
  <p>{escape(item.statement)} {escape(item.remediation or '')}</p>
</li>
"""
        for item in obligations[:12]
    ) or "<li><span>No proof obligations generated.</span></li>"
    return f"""
<section class="panel proof-panel">
  <div class="panel-title">
    <h2>Proof Obligations</h2>
    <span class="muted">{open_count} open</span>
  </div>
  <ul class="source-list">{rows}</ul>
</section>
"""


def render_equilibrium_panel(result: ConsoleResult) -> str:
    diagnostic = result.ledger.equilibrium_diagnostics
    rows = "\n".join(
        f"""
<tr class="{'dominated' if item.dominated else 'frontier'}">
  <td>{escape(item.action)}</td>
  <td>{item.specification_gain:.2f}</td>
  <td>{item.risk_reduction:.2f}</td>
  <td>{item.user_burden:.2f}</td>
  <td>{item.latency_cost:.2f}</td>
  <td>{item.policy_penalty:.2f}</td>
</tr>
"""
        for item in diagnostic.payoffs
    )
    conflicts = render_list(diagnostic.unresolved_conflicts)
    return f"""
<section class="panel equilibrium-panel">
  <div class="panel-title">
    <h2>Equilibrium Diagnostics</h2>
    <span class="pill info">{escape(diagnostic.recommended_action)}</span>
  </div>
  <p class="subtle">{escape(diagnostic.rationale)}</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>action</th><th>spec</th><th>risk</th><th>burden</th><th>latency</th><th>policy</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <details>
    <summary>Conflicts</summary>
    {conflicts}
  </details>
</section>
"""


def render_formal_proof_panel(result: ConsoleResult) -> str:
    proofs = result.ledger.formal_proofs
    rows = "\n".join(
        f"""
<li class="{escape(item.status)}">
  <strong>{escape(readable_theorem_name(item.theorem_name))} / {escape(readable_state(item.status))}</strong>
  <span>{escape(item.check_id)}</span>
  <p>{escape(item.statement)}</p>
</li>
"""
        for item in proofs.checks[:8]
    ) or "<li><span>No formal Lean checks apply.</span></li>"
    return f"""
<section class="panel formal-proof-panel">
  <div class="panel-title">
    <h2>Formal Proof Checks</h2>
    <span class="pill {proof_status_class(proofs.status)}">{escape(proofs.status)}</span>
  </div>
  <p class="subtle">{escape(proofs.scope)}</p>
  <dl class="kv">
    <dt>backend</dt><dd>{escape(proofs.backend)}</dd>
    <dt>lean</dt><dd>{escape(proofs.executable_path or 'unavailable')}</dd>
  </dl>
  <ul class="source-list">{rows}</ul>
</section>
"""


def render_source_layer_panel(result: ConsoleResult) -> str:
    ledger = result.ledger
    source_rows = "\n".join(
        f"""
<li>
  <strong>{escape(source.name)}</strong>
  <span>{escape(source.source_type)} | {escape(source.status)} | confidence {escape(source.confidence)}</span>
  <p>{escape(source.detail)}</p>
</li>
"""
        for source in ledger.evidence_sources
    ) or "<li><span>No source adapters recorded.</span></li>"
    plan_rows = "\n".join(
        f"""
<li class="{escape(item.status)}">
  <strong>{escape(item.purpose)}</strong>
  <span>{escape(item.status)} | {escape(', '.join(item.preferred_sources))}</span>
  <p>{escape(item.query)}</p>
</li>
"""
        for item in ledger.search_plan
    ) or "<li><span>No trader search plan.</span></li>"
    evidence_rows = "\n".join(
        f"""
<li>
  <strong>{escape(item.title)}</strong>
  <span>{escape(item.source)} | {escape(item.query)}</span>
  <p>{render_link(item.url)} {escape(item.summary)}</p>
</li>
"""
        for item in result.rag_result.evidence
    ) or "<li><span>No retrieved source evidence in this console run.</span></li>"
    return f"""
<section class="panel source-panel">
  <div class="panel-title">
    <h2>Trader Source Layer</h2>
    <span class="muted">{escape(result.rag_result.provider)} / {escape(result.rag_result.status)}</span>
  </div>
  <details open>
    <summary>Source Adapters</summary>
    <ul class="source-list">{source_rows}</ul>
  </details>
  <details>
    <summary>Search Plan</summary>
    <ul class="source-list">{plan_rows}</ul>
  </details>
  <details>
    <summary>Retrieved Evidence</summary>
    <ul class="source-list">{evidence_rows}</ul>
  </details>
</section>
"""


def render_answer_panel(result: ConsoleResult) -> str:
    lines = build_provisional_answer(result)
    return f"""
<section class="panel answer-panel">
  <div class="panel-title">
    <h2>Provisional Decision Frame</h2>
    <span class="muted">{escape(result.ledger.decision_gate)}</span>
  </div>
  {render_list(lines)}
</section>
"""


def render_spec_panel(result: ConsoleResult) -> str:
    ledger = result.ledger
    hypotheses = render_list(ledger.latent_intent_hypotheses)
    criteria = render_list(ledger.success_criteria)
    missing = render_list(
        [
            f"{item.field}: {item.question}"
            for item in ledger.ambiguities
        ]
    )
    return f"""
<section class="panel spec-panel">
  <div class="panel-title">
    <h2>Shared Specification</h2>
    <span class="muted">{escape(readable_state(ledger.decision_gate))}</span>
  </div>
  <div class="spec-grid">
    <div><span class="label">q</span><p>{escape(ledger.expressed_query or ledger.user_request)}</p></div>
    <div><span class="label">theta</span>{hypotheses}</div>
    <div><span class="label">s</span><p>{escape(ledger.goal or 'unresolved')}</p></div>
  </div>
  <div class="detail-grid">
    <div><span class="label">audience</span><p>{escape(ledger.audience or 'unresolved')}</p></div>
    <div><span class="label">format</span><p>{escape(ledger.output_format or 'unresolved')}</p></div>
    <div><span class="label">missing</span>{missing}</div>
  </div>
  <details open>
    <summary>Success Criteria</summary>
    {criteria}
  </details>
</section>
"""


def render_frontier_panel(result: ConsoleResult) -> str:
    rows = "\n".join(
        f"""
<tr class="{'frontier' if candidate.key in result.frontier_keys else 'dominated'}">
  <td>{escape(candidate.model)}</td>
  <td>{escape(candidate.region)}</td>
  <td>{candidate.model_quality_loss:.3f}</td>
  <td>{candidate.latency_loss:.0f}</td>
  <td>{candidate.all_resource_cost_loss:.3f}</td>
  <td>{candidate.compute_electricity_spread_loss:.3f}</td>
  <td>{candidate.carbon_context_loss:.3f}</td>
  <td>{route_loss(candidate):.3f}</td>
</tr>
"""
        for candidate in result.candidates
    )
    return f"""
<section class="panel">
  <h2>Model-Region Frontier</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>model</th><th>region</th><th>quality</th><th>latency</th>
          <th>cost</th><th>spread</th><th>carbon</th><th>tie</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""


def render_artifacts_panel(result: ConsoleResult) -> str:
    rows = "\n".join(
        f"""
<li>
  <strong>{escape(artifact.filename)}</strong>
  <span>{escape(artifact.mime_type or 'unknown')}</span>
</li>
"""
        for artifact in result.ledger.artifact_refs
    ) or "<li><span>No artifacts.</span></li>"
    evidence = render_list(
        [
            f"{item.evidence_id}: {item.summary}"
            for item in result.ledger.evidence
            if item.used
        ]
    )
    return f"""
<section class="panel">
  <h2>Multimodal Evidence</h2>
  <ul class="artifact-list">{rows}</ul>
  <details open>
    <summary>Artifact Evidence</summary>
    {evidence}
  </details>
</section>
"""


def render_human_review_panel(result: ConsoleResult) -> str:
    review = result.ledger.human_review
    notice = render_human_review_notice(review)
    reason_rows = render_list(review.reasons)
    action_rows = render_list(review.required_actions)
    claim_rows = render_list(review.blocking_claim_ids)
    evidence_rows = render_list(review.blocking_evidence)
    condition_rows = render_list(review.approval_conditions)
    return f"""
<section class="panel review-panel">
  <div class="panel-title">
    <div>
      <h2>Human Review Gate</h2>
      <p class="subtle">Operator review is a gate decision. It does not override missing evidence or verifier obligations.</p>
    </div>
    <span id="humanReviewStatusPill" class="pill {review_status_class(review.status)}">{escape(readable_state(review.status))}</span>
  </div>
  {notice}
  <div class="review-actions">
    <label for="humanReviewNote">Operator note</label>
    <textarea id="humanReviewNote" rows="3" placeholder="Evidence checked, concern, or reason for approval/rejection"></textarea>
    <div class="button-row">
      <button type="button" data-review-action="start_review">Start Review</button>
      <button type="button" data-review-action="approve">Approve Gate</button>
      <button type="button" data-review-action="request_changes">Request Changes</button>
      <button type="button" data-review-action="reject">Reject Frame</button>
    </div>
    <p id="humanReviewResult" class="capture-state">No operator decision submitted in this browser session.</p>
  </div>
  <dl class="kv">
    <dt>required</dt><dd>{escape(str(review.required).lower())}</dd>
    <dt>risk</dt><dd>{escape(review.risk_level)}</dd>
    <dt>assignee</dt><dd>{escape(review.assigned_player)}</dd>
    <dt>owner</dt><dd>{escape(review.decision_owner)}</dd>
  </dl>
  <details>
    <summary>Reasons</summary>
    {reason_rows}
  </details>
  <details open>
    <summary>Required Actions</summary>
    {action_rows}
  </details>
  <details>
    <summary>Blocking Claims</summary>
    {claim_rows}
  </details>
  <details>
    <summary>Blocking Evidence</summary>
    {evidence_rows}
  </details>
  <details>
    <summary>Approval Conditions</summary>
    {condition_rows}
  </details>
</section>
"""


def render_skill_compatibility_panel(result: ConsoleResult) -> str:
    skill = result.ledger.skill_compatibility
    risk_rows = render_list(skill.compatibility_risks)
    evidence_rows = render_list(skill.evidence_required_for_handoff)
    learned_rows = render_list(skill.learned_from)
    return f"""
<section class="panel skill-panel">
  <div class="panel-title">
    <h2>Skill Compatibility</h2>
    <span class="pill info">{escape(skill.next_best_action)}</span>
  </div>
  <dl class="kv">
    <dt>role</dt><dd>{escape(skill.inferred_role)}</dd>
    <dt>skill</dt><dd>{escape(skill.skill_level)}</dd>
    <dt>domain</dt><dd>{escape(skill.domain_familiarity)}</dd>
    <dt>burden</dt><dd>{escape(skill.cognitive_burden)}</dd>
    <dt>depth</dt><dd>{escape(skill.recommended_depth)}</dd>
    <dt>handoff</dt><dd>{escape(skill.handoff_format)}</dd>
  </dl>
  <details open>
    <summary>Compatibility Risks</summary>
    {risk_rows}
  </details>
  <details>
    <summary>Evidence For Handoff</summary>
    {evidence_rows}
  </details>
  <details>
    <summary>Learned From</summary>
    {learned_rows}
  </details>
</section>
"""


def render_route_panel(result: ConsoleResult) -> str:
    reasons = render_list(list(getattr(result.route_decision, "reasons", [])))
    return f"""
<section class="panel">
  <h2>Route Decision</h2>
  <dl class="kv">
    <dt>mode</dt><dd>{escape(str(getattr(result.route_decision, 'mode', 'sync')))}</dd>
    <dt>risk</dt><dd>{escape(str(getattr(result.route_decision, 'risk_score', 0)))}</dd>
    <dt>gain</dt><dd>{escape(str(getattr(result.route_decision, 'expected_spec_gain', 'low')))}</dd>
  </dl>
  {reasons}
</section>
"""


def render_spend_panel(spend: object) -> str:
    return f"""
<section class="panel">
  <h2>Cost Proxy</h2>
  <dl class="kv">
    <dt>tokens</dt><dd>{getattr(spend, 'total_tokens', 0)}</dd>
    <dt>token usd</dt><dd>{getattr(spend, 'token_cost_usd', 0):.6f}</dd>
    <dt>kWh</dt><dd>{getattr(spend, 'estimated_kwh', 0):.8f}</dd>
    <dt>power usd</dt><dd>{getattr(spend, 'electricity_cost_usd', 0):.8f}</dd>
  </dl>
</section>
"""


def build_provisional_answer(result: ConsoleResult) -> list[str]:
    ledger = result.ledger
    if not (ledger.expressed_query or ledger.user_request):
        return [
            "Immediate answer: waiting for a task signal.",
            "Current handoff: the ball is on the user side.",
            "Next action: enter a query, add artifacts if useful, then run the spec.",
        ]
    text = (ledger.expressed_query or ledger.user_request).lower()
    answer: list[str] = []
    if result.market_math is not None and any(token in text for token in ("ho/rb", "rbob", "heating oil")):
        answer.extend(build_market_math_answer_lines(result))
    elif "sulfur" in text or "sulphur" in text:
        answer.extend(
            [
                "Immediate answer: not go-ready from the prompt alone. Treat FOB 550 at Umm Qasr for 50,000 tonnes as an offer to verify, not a trade to accept.",
                "Latent task: determine whether the offer is real, deliverable, legally clean, financeable, insurable, and resellable at positive netback after freight, inspection, demurrage, quality, and payment risk.",
                "Critical missing data: sulfur grade/spec, form, origin, seller identity, title chain, documents, quantity tolerance, laycan, berth/loading terms, inspection agency, payment terms, sanctions screening, and buyer/resale path.",
                "Economics check: compare FOB 550 against current regional sulfur benchmarks, freight from Umm Qasr, port costs, insurance, finance, inspection, losses, and destination resale price.",
                "Gate: stay at needs_more_info until documents and counterparty are verified; a photo is evidence metadata only until visually inspected or embedded.",
            ]
        )
        if result.rag_result.evidence:
            answer.append(
                "Source layer: retrieved search evidence is attached, but it is still not enough to clear the gate without seller documents, sanctions/payment checks, and executable resale economics."
            )
        else:
            answer.append(
                "Source layer: no live cited market/search evidence was retrieved in this run, so FOB 550, port/loading, sanctions, and resale claims remain unverified."
            )
    elif ledger.latent_intent_hypotheses:
        answer.extend(build_generic_answer_lines(result))
    else:
        answer.extend(build_generic_answer_lines(result))
    if ledger.artifact_refs:
        answer.append(
            f"Artifacts attached: {len(ledger.artifact_refs)} file(s). They are included as evidence references, but claims from images/audio need inspection or transcription before finalizing."
        )
    if ledger.ambiguities:
        answer.append(
            "Open spec gaps: "
            + ", ".join(f"{item.field}" for item in ledger.ambiguities)
            + "."
        )
    return answer


def build_market_math_answer_lines(result: ConsoleResult) -> list[str]:
    frame = result.market_math
    if frame is None:
        return build_generic_answer_lines(result)
    marks = frame.marks
    bbl_equivalent = (
        f", equal to {format_money(frame.spread_bbl_equivalent)} per barrel"
        if frame.spread_bbl_equivalent is not None
        else ""
    )
    lines = [
        (
            "Immediate answer: from your supplied marks, HO is trading "
            f"{format_money(frame.spread)} per gallon over RB{bbl_equivalent}."
        ),
        (
            "Deterministic calculation: for a 1x1 NYMEX product spread, 1 cent/gal is about $420; "
            f"your supplied premium is about {format_money(frame.contract_value_usd, 0)} per 42,000 gallon contract spread."
        ),
        (
            "Risk read: the spread is "
            f"{frame.risk_label}; long {marks.leg_a}/short {marks.leg_b} is exposed to gasoline strength, distillate weakness, "
            "refinery yield shifts, inventory surprises, seasonality, and contract-month mismatch."
        ),
    ]
    crack_bits: list[str] = []
    if frame.leg_a_brent_crack is not None and frame.leg_b_brent_crack is not None:
        crack_bits.append(
            "Brent cracks: "
            f"{marks.leg_a} {format_money(frame.leg_a_brent_crack)}/bbl, "
            f"{marks.leg_b} {format_money(frame.leg_b_brent_crack)}/bbl"
        )
    if frame.leg_a_wti_crack is not None and frame.leg_b_wti_crack is not None:
        crack_bits.append(
            "WTI cracks: "
            f"{marks.leg_a} {format_money(frame.leg_a_wti_crack)}/bbl, "
            f"{marks.leg_b} {format_money(frame.leg_b_wti_crack)}/bbl"
        )
    if crack_bits:
        lines.append("Crude context: " + "; ".join(crack_bits) + ".")
    lines.extend(
        [
            (
                "Model reasoning frame: interpreted thesis is relative-value spread risk, "
                f"using {marks.timeframe or 'user-supplied current'} marks; this is analysis, not execution."
            ),
            (
                "Source policy: I did not need live search to do this calculation. "
                "Before calling external market/search tools, ask: do you want me to verify these marks live and pull inventory/crack context?"
            ),
            (
                "Verifier rule: do not convert this into a go/no-go trade until contract month, hedge ratio, "
                "position direction, stop horizon, liquidity, margin, and source freshness are confirmed."
            ),
            (
                "Falsification triggers: HO/RB premium compresses by 5-10 cents/gal, gasoline cracks outperform, "
                "distillate inventories build, refinery runs shift yields, or crude leg explains the move instead of product relative value."
            ),
        ]
    )
    if result.rag_result.evidence:
        lines.append("Source layer: retrieved cited evidence is attached; use it to verify marks and drivers.")
    else:
        lines.append("Source layer: no live cited evidence was retrieved; calculation uses user-supplied marks only.")
    return lines


def build_generic_answer_lines(result: ConsoleResult) -> list[str]:
    ledger = result.ledger
    source_status = result.rag_result.status
    source_count = len(result.rag_result.evidence)
    recommendation = ledger.equilibrium_diagnostics.recommended_action or "propose"
    output_format = ledger.output_format or "response"
    latent_task = (
        ledger.latent_intent_hypotheses[0]
        if ledger.latent_intent_hypotheses
        else (
            ledger.latent_type_beliefs[0].description
            if ledger.latent_type_beliefs
            else "Reconstruct the user's latent task from the submitted query."
        )
    )
    if ledger.decision_gate == "needs_more_info":
        primary = (
            f"Immediate answer: I can answer this as a provisional {output_format}, "
            f"but it is not execution-ready yet; the current best action is {recommendation}."
        )
    else:
        primary = (
            f"Immediate answer: use the current {output_format} as the working answer; "
            f"the current best action is {recommendation}."
        )
    lines = [
        primary,
        "Latent task: " + latent_task,
        (
            "Current spec: "
            f"goal={ledger.goal or 'unresolved'}, "
            f"audience={ledger.audience or 'unresolved'}, "
            f"format={ledger.output_format or 'unresolved'}."
        ),
        *build_working_response_lines(ledger),
        (
            f"Source layer: {source_status} with {source_count} cited item(s); "
            + (
                "use retrieved evidence below before trusting material claims."
                if source_count
                else "no cited evidence was retrieved for this run, so factual claims remain provisional."
            )
        ),
        (
            f"Verification: {'passed' if result.verification_passed else 'blocked'}; "
            f"decision gate={readable_state(ledger.decision_gate)}; "
            f"human review={readable_state(ledger.human_review.status)}."
        ),
    ]
    next_line = next_action_line(ledger, recommendation)
    if next_line:
        lines.append(next_line)
    return lines


def build_working_response_lines(ledger: SpecLedger) -> list[str]:
    goal = ledger.goal or ledger.expressed_query or ledger.user_request or "the submitted task"
    output_format = (ledger.output_format or "").lower()
    if "checklist" in output_format:
        return [
            (
                "Working checklist: "
                "1. Confirm launch owner, scope, audience, and deadline; "
                "2. Verify core dashboard data sources, freshness, permissions, and fallback states; "
                "3. Test primary user flows on desktop and mobile; "
                "4. Check authentication, authorization, secrets, and error handling; "
                "5. Validate charts, tables, empty states, loading states, and accessibility; "
                "6. Confirm observability, alerts, rollback plan, and post-launch owner; "
                "7. Get final signoff against the accepted success criteria."
            )
        ]
    if "table" in output_format:
        return [
            (
                "Working table columns: item, owner, current state, evidence/source, blocker, "
                "next action, due date, and acceptance check."
            )
        ]
    if output_format in {"json", "csv"}:
        return [
            (
                f"Working {ledger.output_format} shape: "
                "task, inferred_intent, assumptions, evidence_needed, answer, next_action, status."
            )
        ]
    if "code" in output_format or "python" in output_format:
        return [
            (
                "Working answer: code generation needs the target file, runtime, expected behavior, "
                "and verification command; without those, treat this as a spec frame before editing."
            )
        ]
    return [f"Working answer: {goal}"]


def next_action_line(ledger: SpecLedger, recommendation: str) -> str:
    if ledger.human_review.required and ledger.human_review.required_actions:
        return "Next action: " + ledger.human_review.required_actions[0]
    formal_missing = latest_missing_formal_obligations(ledger)
    if recommendation == "ask" and formal_missing:
        return "Next action: clarify these formal spec fields: " + ", ".join(formal_missing[:6]) + "."
    if recommendation == "ask" and ledger.ambiguities:
        fields = ", ".join(item.field for item in ledger.ambiguities[:6])
        return f"Next action: clarify these fields: {fields}."
    open_search = [item for item in ledger.search_plan if item.required and item.status != "satisfied"]
    if recommendation == "retrieve" and open_search:
        return "Next action: retrieve evidence for: " + "; ".join(item.purpose for item in open_search[:3])
    if recommendation == "review" and ledger.human_review.reasons:
        return "Next action: review gate reason: " + ledger.human_review.reasons[0]
    if recommendation == "finalize":
        return "Next action: finalize the answer with the cited evidence and accepted assumptions."
    if recommendation == "propose":
        return "Next action: propose the working spec and ask the user to endorse or correct it."
    return ""


def latest_missing_formal_obligations(ledger: SpecLedger) -> list[str]:
    latest_formal = ledger.formalization_records[-1] if ledger.formalization_records else None
    if not latest_formal or latest_formal.is_valid == 1:
        return []
    return latest_formal.missing_obligations


def render_list(items: list[str]) -> str:
    if not items:
        return '<ul class="compact"><li>none</li></ul>'
    return "<ul class=\"compact\">" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def render_link(url: str) -> str:
    if not url:
        return ""
    safe_url = escape(url)
    return f'<a href="{safe_url}" target="_blank" rel="noreferrer">{safe_url}</a>'


def safe_filename(filename: str) -> str:
    name = Path(filename).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return cleaned or f"upload-{uuid4().hex[:8]}"


def escape(value: object) -> str:
    return html.escape(str(value or ""))


def parse_csv(value: str | None, default: str) -> list[str]:
    raw = value or default
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def flag_enabled_in_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: bool = False,
) -> bool:
    value = env.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured_mcp(env: Mapping[str, str]) -> bool:
    remote = env.get("MCP_RESEARCH_URL")
    command = env.get("MCP_RESEARCH_COMMAND")
    if remote and remote.strip() and not remote.startswith("https://your-"):
        return True
    if command and command.strip():
        if "opoint" in command.lower() and not env.get("OPOINT_API_KEY"):
            return False
        return True
    return False


def status_class(status: str) -> str:
    return {
        "finalized": "verified",
        "retrieving": "running",
        "drafting": "running",
        "verifying": "running",
        "async_pending": "async",
        "clarifying": "clarify",
    }.get(status, "neutral")


def review_status_class(status: str) -> str:
    return {
        "approved": "verified",
        "not_required": "verified",
        "queued": "review",
        "in_review": "review",
        "changes_requested": "clarify",
        "rejected": "blocked",
    }.get(status, "neutral")


def proof_status_class(status: str) -> str:
    return {
        "checked": "verified",
        "not_applicable": "verified",
        "generated": "running",
        "unavailable": "clarify",
        "failed": "blocked",
    }.get(status, "neutral")


def alignment_status_class(status: str) -> str:
    return {
        "verified": "verified",
        "executable": "verified",
        "negotiating": "clarify",
        "diverged": "blocked",
    }.get(status, "neutral")


def status_word(status: str) -> str:
    return {
        "green": "Ready",
        "blue": "Running",
        "red": "Off",
        "yellow": "Optional",
        "verified": "Ready",
        "blocked": "Blocked",
        "clarify": "Needs input",
        "running": "Running",
        "async": "Async",
        "review": "Review",
        "neutral": "Info",
    }.get(status, readable_state(status))


def readable_state(value: str) -> str:
    return {
        "analysis_ready": "analysis ready",
        "async": "async job queued",
        "async_pending": "async job pending",
        "blocked": "blocked",
        "clarifying": "needs user clarification",
        "configured": "configured",
        "deferred": "deferred to background",
        "empty": "no evidence returned",
        "finalized": "finalized",
        "go": "go-ready",
        "in_review": "in human review",
        "missing_config": "missing configuration",
        "needs_more_info": "needs more information",
        "not_required": "not required",
        "planned": "planned",
        "provider_error": "provider error",
        "queued": "queued for human review",
        "ready": "ready",
        "retrieved": "retrieved",
        "sync": "sync path",
    }.get(value, value.replace("_", " "))


def readable_theorem_name(value: str) -> str:
    return {
        "no_finalize_when_needs_more_info": "cannot finalize while more information is needed",
        "no_finalize_with_open_proofs": "cannot finalize with open proof obligations",
        "no_finalize_with_required_review": "cannot finalize with required human review",
        "no_finalize_with_high_severity_findings": "cannot finalize with high-severity findings",
        "finalize_allowed_when_no_hard_gates": "finalization allowed when hard gates are clear",
    }.get(value, readable_state(value))


def render_status_legend() -> str:
    return """
  <div class="status-legend">
    <span><i class="dot green"></i>Ready or active</span>
    <span><i class="dot yellow"></i>Optional or needs input</span>
    <span><i class="dot red"></i>Missing or blocked</span>
    <span><i class="dot blue"></i>Running now</span>
    <span><i class="dot async"></i>Async/background job</span>
    <span><i class="dot review"></i>Human review gate</span>
  </div>
"""


def gate_status_class(status: str) -> str:
    return {
        "ready": "verified",
        "analysis_ready": "verified",
        "alert_ready": "verified",
        "decision_frame_ready": "verified",
        "go": "verified",
        "finalized": "verified",
        "needs_more_info": "blocked",
        "blocked": "blocked",
        "clarifying": "clarify",
    }.get(status, "clarify")


def route_status_class(mode: str) -> str:
    return {
        "sync": "neutral",
        "async": "async",
        "async_pending": "async",
        "human_review": "review",
    }.get(mode, "neutral")


def source_status_class(status: str) -> str:
    return {
        "retrieved": "verified",
        "configured": "running",
        "deferred": "async",
        "planned": "clarify",
        "missing_config": "blocked",
        "provider_error": "blocked",
        "empty": "clarify",
    }.get(status, "neutral")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.console:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        reload=flag_enabled("CONSOLE_RELOAD"),
    )


CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f5;
  --ink: #18201d;
  --muted: #68736f;
  --line: #d7ddd8;
  --panel: #ffffff;
  --green: #1f7a4d;
  --red: #b13d32;
  --blue: #2764a6;
  --amber: #9a6700;
  --teal: #0f766e;
  --purple: #7357a5;
  --gray: #59615d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.shell { width: min(1500px, calc(100vw - 32px)); margin: 0 auto; padding: 20px 0 32px; }
.topbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: end;
  border-bottom: 1px solid var(--line);
  padding-bottom: 16px;
}
h1, h2, p { margin: 0; }
h1 { font-size: 24px; line-height: 1.15; letter-spacing: 0; }
h2 { font-size: 15px; line-height: 1.2; letter-spacing: 0; }
.subtle, .muted { color: var(--muted); font-size: 13px; }
.status-strip, .button-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 13px;
  background: #f9faf8;
}
.pill.verified, .pill.ok { color: var(--green); border-color: #9ccdad; background: #eefaf3; }
.pill.blocked { color: var(--red); border-color: #e2a29d; background: #fff3f2; }
.pill.clarify, .pill.warn { color: var(--amber); border-color: #e3cc95; background: #fff8e7; }
.pill.running, .pill.info { color: var(--blue); border-color: #b8cbe2; background: #eff6ff; }
.pill.async { color: var(--purple); border-color: #c8b8e6; background: #f7f2ff; }
.pill.review { color: var(--teal); border-color: #9acbc6; background: #eefaf8; }
.pill.user { color: #7a4a00; border-color: #e3c58b; background: #fff8e7; }
.pill.agent { color: var(--blue); border-color: #b8cbe2; background: #eff6ff; }
.pill.tools { color: var(--purple); border-color: #c8b8e6; background: #f7f2ff; }
.pill.operator { color: var(--teal); border-color: #9acbc6; background: #eefaf8; }
.pill.neutral { color: var(--gray); border-color: var(--line); background: #f9faf8; }
.workspace {
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) minmax(420px, 1.45fr) minmax(280px, 0.9fr);
  gap: 14px;
  margin-top: 16px;
}
.left-rail, .center-rail, .right-rail { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.advanced-rail { grid-column: 1 / -1; min-width: 0; }
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  box-shadow: 0 1px 2px rgba(24, 32, 29, 0.04);
}
.panel-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.input-panel { display: flex; flex-direction: column; gap: 10px; }
label, .label, summary, th, dt { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }
textarea, input[type="file"] {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfb;
  color: var(--ink);
  padding: 10px;
  font: inherit;
}
textarea { resize: vertical; min-height: 148px; }
button {
  border: 1px solid #9db8aa;
  border-radius: 8px;
  background: #edf6f1;
  color: #153d2d;
  padding: 8px 11px;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
button:disabled { cursor: not-allowed; opacity: 0.55; }
form.running button[type="submit"] { color: var(--blue); border-color: #8fb8e8; background: #e9f3ff; }
.capture-state { color: var(--muted); font-size: 13px; min-height: 18px; }
.turn-panel {
  display: grid;
  gap: 12px;
  border-width: 2px;
}
.turn-panel.user { border-color: #e3c58b; }
.turn-panel.agent { border-color: #b8cbe2; }
.turn-panel.tools { border-color: #c8b8e6; }
.turn-panel.operator { border-color: #9acbc6; }
.turn-panel.blocked { border-color: #e2a29d; }
.turn-panel.verified { border-color: #9ccdad; }
.turn-heading {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
}
.turn-ball {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 999px;
  background: #18201d;
  color: white;
  font-weight: 750;
}
.turn-owner-row { display: flex; flex-wrap: wrap; gap: 8px; }
.turn-detail, .next-move p { font-size: 14px; line-height: 1.45; overflow-wrap: anywhere; }
.next-move {
  display: grid;
  gap: 5px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}
.status-grid { display: grid; gap: 8px; margin-top: 12px; }
.status-row { display: grid; grid-template-columns: 12px 70px minmax(0, 1fr); gap: 8px; align-items: start; font-size: 13px; }
.status-row strong { display: block; font-size: 13px; line-height: 1.25; }
.status-row span:last-child, .status-row div span { display: block; color: var(--muted); font-size: 12px; line-height: 1.35; margin-top: 2px; }
.status-word { font-size: 11px; text-transform: uppercase; letter-spacing: 0; line-height: 1.2; }
.status-word.green { color: var(--green); }
.status-word.red { color: var(--red); }
.status-word.blue { color: var(--blue); }
.status-word.yellow { color: var(--amber); }
.status-legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 10px; margin-top: 10px; color: var(--muted); font-size: 12px; }
.status-legend span { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }
.dot.blue { background: var(--blue); }
.dot.yellow { background: var(--amber); }
.dot.async { background: var(--purple); }
.dot.review { background: var(--teal); }
.chain-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}
.chain-steps li {
  position: relative;
  min-height: 142px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: #fbfcfb;
}
.chain-steps li + li::before {
  content: "";
  position: absolute;
  left: -9px;
  top: 50%;
  width: 8px;
  border-top: 2px solid var(--line);
}
.chain-steps li.current { border-color: var(--blue); background: #eff6ff; box-shadow: 0 0 0 2px rgba(39, 100, 166, 0.10); }
.chain-steps li.complete { border-color: #9ccdad; background: #eefaf3; }
.chain-steps li.blocked { border-color: #e2a29d; background: #fff3f2; }
.chain-steps li.async { border-color: #c8b8e6; background: #f7f2ff; }
.chain-steps li.skipped, .chain-steps li.waiting { color: var(--muted); background: #f9faf8; }
.chain-owner {
  display: inline-flex;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 11px;
  text-transform: uppercase;
  color: var(--muted);
  background: white;
}
.chain-steps strong { display: block; font-size: 13px; line-height: 1.25; }
.chain-steps p { font-size: 12px; line-height: 1.4; margin-top: 6px; overflow-wrap: anywhere; }
.route-steps { margin: 10px 0 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.route-steps li {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: #fbfcfb;
}
.route-steps li.verified { border-color: #9ccdad; background: #eefaf3; }
.route-steps li.blocked { border-color: #e2a29d; background: #fff3f2; }
.route-steps li.clarify { border-color: #e3cc95; background: #fff8e7; }
.route-steps li.running { border-color: #b8cbe2; background: #eff6ff; }
.route-steps li.review { border-color: #9acbc6; background: #eefaf8; }
.route-steps li.neutral { border-color: var(--line); background: #f9faf8; }
.route-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: 1px solid currentColor;
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
}
.route-steps strong { display: block; font-size: 14px; line-height: 1.25; }
.route-steps div > span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; text-transform: uppercase; }
.route-steps p { font-size: 13px; line-height: 1.4; margin-top: 5px; overflow-wrap: anywhere; }
.decision-panel {
  border-width: 2px;
  background: #ffffff;
}
.decision-panel.verified { border-color: #9ccdad; box-shadow: 0 0 0 3px rgba(31, 122, 77, 0.08); }
.decision-panel.blocked { border-color: #e2a29d; box-shadow: 0 0 0 3px rgba(177, 61, 50, 0.08); }
.decision-panel.clarify { border-color: #e3cc95; box-shadow: 0 0 0 3px rgba(154, 103, 0, 0.08); }
.decision-callout {
  display: grid;
  gap: 8px;
  border-radius: 8px;
  padding: 12px;
  background: #fbfcfb;
  border: 1px solid var(--line);
}
.decision-label {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0;
}
.decision-callout p {
  font-size: 17px;
  line-height: 1.45;
  font-weight: 650;
}
.decision-strip { margin-top: 12px; }
.review-notice {
  display: grid;
  gap: 6px;
  margin: 12px 0;
  padding: 11px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #fbfcfb;
}
.review-notice.review { border-color: #9acbc6; background: #eefaf8; }
.review-notice.blocked { border-color: #e2a29d; background: #fff3f2; }
.review-notice.clarify { border-color: #e3cc95; background: #fff8e7; }
.review-notice.verified { border-color: #9ccdad; background: #eefaf3; }
.review-notice.neutral { color: var(--gray); }
.review-notice p { font-size: 14px; line-height: 1.45; }
.review-actions {
  display: grid;
  gap: 8px;
  margin: 12px 0;
  padding: 11px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfb;
}
.alignment-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.alignment-grid > div {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: #fbfcfb;
}
.alignment-grid p {
  font-size: 13px;
  line-height: 1.4;
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.alignment-kv { margin-top: 12px; }
.alignment-actions textarea { width: 100%; resize: vertical; }
.label-inline {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  margin-right: 6px;
}
.spec-grid, .detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.spec-grid p, .detail-grid p, li { font-size: 14px; line-height: 1.45; overflow-wrap: anywhere; }
.detail-grid { margin-top: 14px; }
details { margin-top: 14px; }
summary { cursor: pointer; margin-bottom: 8px; }
.compact { margin: 6px 0 0; padding-left: 18px; }
.game-list { margin: 10px 0 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.game-list li { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfcfb; }
.game-list li.done { border-color: #b9d8c5; background: #f0fbf6; }
.game-list li.blocked { border-color: #e3cc95; background: #fffaf0; }
.game-list li.open { border-color: #b8cbe2; background: #f4f8fd; }
.game-list strong { display: block; font-size: 14px; }
.game-list span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.game-list p { font-size: 13px; margin-top: 6px; }
.source-list { margin: 8px 0 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.source-list li { border: 1px solid var(--line); border-radius: 8px; padding: 9px; background: #fbfcfb; }
.source-list li.satisfied { border-color: #b9d8c5; background: #f0fbf6; }
.source-list li.failed { border-color: #e3a29b; background: #fff6f5; }
.source-list strong { display: block; font-size: 13px; }
.source-list span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.source-list p { font-size: 13px; margin-top: 5px; overflow-wrap: anywhere; }
a { color: var(--blue); }
.artifact-list { list-style: none; padding: 0; margin: 10px 0 0; display: grid; gap: 8px; }
.artifact-list li { display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.table-wrap { overflow-x: auto; margin-top: 10px; }
table { width: 100%; border-collapse: collapse; min-width: 700px; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 8px; font-size: 13px; }
tr.frontier td { background: #f0fbf6; }
tr.dominated td { color: var(--muted); }
.kv { display: grid; grid-template-columns: minmax(80px, 0.7fr) minmax(0, 1fr); gap: 8px 12px; margin: 10px 0 0; }
.kv dd { margin: 0; font-size: 14px; overflow-wrap: anywhere; }
.advanced-drawer {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  padding: 12px 14px;
}
.advanced-drawer > summary {
  margin: 0;
  font-size: 13px;
  color: var(--ink);
}
.advanced-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}
@media (max-width: 1180px) {
  .workspace { grid-template-columns: 1fr 1fr; }
  .right-rail { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .advanced-grid { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .shell { width: min(100vw - 20px, 720px); padding-top: 12px; }
  .topbar, .workspace, .right-rail, .spec-grid, .detail-grid, .alignment-grid, .chain-steps { display: flex; flex-direction: column; }
  .chain-steps li + li::before { display: none; }
  h1 { font-size: 21px; }
}
"""


JS = """
const fileInput = document.querySelector("#files");
const inputForm = document.querySelector(".input-panel");
const runButton = document.querySelector("#runSpec");
const recordButton = document.querySelector("#recordAudio");
const stopButton = document.querySelector("#stopAudio");
const speechButton = document.querySelector("#transcribeSpeech");
const speechInput = document.querySelector("#speech_text");
const queryInput = document.querySelector("#query");
const captureState = document.querySelector("#captureState");
const copyAnswerButton = document.querySelector("#copyAnswer");
const decisionPanel = document.querySelector("#decisionPanel");
const ledgerPayload = document.querySelector("#ledgerPayload");
const humanReviewNote = document.querySelector("#humanReviewNote");
const humanReviewResult = document.querySelector("#humanReviewResult");
const humanReviewStatusPill = document.querySelector("#humanReviewStatusPill");
const specStatusPill = document.querySelector("#specStatusPill");
const gateStatusPill = document.querySelector("#gateStatusPill");
const routeGatePill = document.querySelector("#routeGatePill");
const turnPanel = document.querySelector(".turn-panel");
const turnOwnerPill = document.querySelector("#turnOwnerPill");
const turnTitle = document.querySelector("#turnTitle");
const turnDetail = document.querySelector("#turnDetail");
const turnNext = document.querySelector("#turnNext");
const alignmentNote = document.querySelector("#alignmentNote");
const alignmentResult = document.querySelector("#alignmentResult");
const alignmentConvergencePill = document.querySelector("#alignmentConvergencePill");
const alignmentEndorsed = document.querySelector("#alignmentEndorsed");
const alignmentPending = document.querySelector("#alignmentPending");
const alignmentRejected = document.querySelector("#alignmentRejected");
const alignmentLastSignal = document.querySelector("#alignmentLastSignal");
let recorder = null;
let chunks = [];

function currentLedgerPayload() {
  try {
    return JSON.parse(ledgerPayload?.textContent || "{}");
  } catch {
    return {};
  }
}

function setLedgerPayload(ledger) {
  if (ledgerPayload && ledger) ledgerPayload.textContent = JSON.stringify(ledger);
}

function pillClassForReview(status) {
  if (status === "approved" || status === "not_required") return "verified";
  if (status === "queued" || status === "in_review") return "review";
  if (status === "changes_requested") return "clarify";
  if (status === "rejected") return "blocked";
  return "neutral";
}

function readableStatus(status) {
  const labels = {
    approved: "approved",
    changes_requested: "changes requested",
    in_review: "in human review",
    needs_more_info: "needs more information",
    not_required: "not required",
    queued: "queued for human review",
    rejected: "rejected"
  };
  return labels[status] || String(status || "").replaceAll("_", " ");
}

function pillClassForGate(status) {
  if (["analysis_ready", "alert_ready", "decision_frame_ready", "finalized", "ready", "go"].includes(status)) return "verified";
  if (status === "needs_more_info" || status === "blocked") return "blocked";
  if (status === "clarifying") return "clarify";
  return "clarify";
}

function pillClassForSpec(status) {
  if (status === "finalized") return "verified";
  if (status === "async_pending") return "async";
  if (status === "clarifying") return "clarify";
  if (status === "failed") return "blocked";
  return "neutral";
}

function pillClassForAlignment(status) {
  if (status === "verified" || status === "executable") return "verified";
  if (status === "negotiating") return "clarify";
  if (status === "diverged") return "blocked";
  return "neutral";
}

function updateGatePills(status, label) {
  for (const pill of [gateStatusPill, routeGatePill]) {
    if (!pill) continue;
    pill.className = `pill ${pillClassForGate(status)}`;
    pill.textContent = pill === gateStatusPill ? `Gate: ${label}` : label;
  }
}

function setTurnState(cssClass, ownerLabel, title, detail, nextAction) {
  if (turnPanel) turnPanel.className = `panel turn-panel ${cssClass}`;
  if (turnOwnerPill) {
    turnOwnerPill.className = `pill ${cssClass}`;
    turnOwnerPill.textContent = `Ball: ${ownerLabel}`;
  }
  if (turnTitle) turnTitle.textContent = title;
  if (turnDetail) turnDetail.textContent = detail;
  if (turnNext) turnNext.textContent = nextAction;
}

function updateAlignmentPanel(payload) {
  const convergence = payload.spec_convergence || {};
  const endorsement = payload.user_endorsement || {};
  if (alignmentConvergencePill) {
    alignmentConvergencePill.className = `pill ${pillClassForAlignment(convergence.status)}`;
    alignmentConvergencePill.textContent = `alignment ${Number(convergence.overall || 0).toFixed(2)}`;
  }
  if (alignmentEndorsed) alignmentEndorsed.textContent = (endorsement.endorsed_fields || []).join(", ") || "none";
  if (alignmentPending) alignmentPending.textContent = (endorsement.pending_fields || []).join(", ") || "none";
  if (alignmentRejected) alignmentRejected.textContent = (endorsement.rejected_fields || []).join(", ") || "none";
  if (alignmentLastSignal) alignmentLastSignal.textContent = endorsement.last_signal || "none";
}

function appendFile(file) {
  const transfer = new DataTransfer();
  for (const existing of fileInput.files) transfer.items.add(existing);
  transfer.items.add(file);
  fileInput.files = transfer.files;
}

inputForm?.addEventListener("submit", () => {
  inputForm.classList.add("running");
  if (runButton) {
    runButton.disabled = true;
    runButton.textContent = "Running route...";
  }
  if (captureState) {
    captureState.textContent = "running route: input -> spec -> sources -> formal checks -> verifier -> gate -> decision";
  }
});

copyAnswerButton?.addEventListener("click", async () => {
  const text = decisionPanel?.innerText?.trim() || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    copyAnswerButton.textContent = "Copied";
  } catch {
    copyAnswerButton.textContent = "Copy failed";
  }
  setTimeout(() => { copyAnswerButton.textContent = "Copy Answer"; }, 1400);
});

document.querySelectorAll("[data-review-action]").forEach(button => {
  button.addEventListener("click", async () => {
    const action = button.dataset.reviewAction;
    button.disabled = true;
    if (humanReviewResult) humanReviewResult.textContent = "submitting operator review...";
    try {
      const response = await fetch("/api/human-review", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          action,
          note: humanReviewNote?.value || "",
          operator: "console_operator",
          ledger: currentLedgerPayload()
        })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "review failed");
      setLedgerPayload(payload.ledger);
      if (humanReviewStatusPill) {
        humanReviewStatusPill.className = `pill ${pillClassForReview(payload.human_review.status)}`;
        humanReviewStatusPill.textContent = readableStatus(payload.human_review.status);
      }
      if (specStatusPill && payload.status) {
        specStatusPill.className = `pill ${pillClassForSpec(payload.status)}`;
        specStatusPill.textContent = `Spec: ${payload.status_label || readableStatus(payload.status)}`;
      }
      if (payload.decision_gate) {
        updateGatePills(payload.decision_gate, payload.decision_gate_label || readableStatus(payload.decision_gate));
      }
      if (payload.human_review?.status === "in_review" || payload.human_review?.status === "queued") {
        setTurnState(
          "operator",
          "Operator",
          "Human review packet is open",
          "The operator is reviewing the gate packet.",
          payload.human_review.required_actions?.[0] || "Choose an operator gate action."
        );
      } else if (payload.human_review?.status === "approved") {
        setTurnState("verified", "Gate", "Operator approved the review gate", "The packet can move to the remaining decision gate checks.", "Check whether final gate conditions are now clear.");
      } else if (payload.human_review?.status === "changes_requested" || payload.human_review?.status === "rejected") {
        setTurnState("blocked", "User/Agent", `Review is ${readableStatus(payload.human_review.status)}`, "The shared spec needs correction, more evidence, or a revised frame.", payload.operator_message || "Resolve the operator review note.");
      }
      if (humanReviewResult) {
        humanReviewResult.textContent = `${payload.operator_message} Gate: ${payload.decision_gate_label}.`;
      }
    } catch (error) {
      if (humanReviewResult) humanReviewResult.textContent = error.message || "review failed";
    } finally {
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-alignment-action]").forEach(button => {
  button.addEventListener("click", async () => {
    const action = button.dataset.alignmentAction;
    button.disabled = true;
    if (alignmentResult) alignmentResult.textContent = "submitting alignment move...";
    try {
      const response = await fetch("/api/alignment", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          action,
          note: alignmentNote?.value || "",
          fields: ["latent_task", "evidence_contract"],
          ledger: currentLedgerPayload()
        })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "alignment failed");
      setLedgerPayload(payload.ledger);
      updateAlignmentPanel(payload);
      if (specStatusPill && payload.status) {
        specStatusPill.className = `pill ${pillClassForSpec(payload.status)}`;
        specStatusPill.textContent = `Spec: ${payload.status_label || readableStatus(payload.status)}`;
      }
      if (payload.decision_gate) {
        updateGatePills(payload.decision_gate, payload.decision_gate_label || readableStatus(payload.decision_gate));
      }
      if (action === "correct" || action === "request_evidence") {
        setTurnState("user", "User", "Alignment loop changed the spec", "The current theta or evidence contract was reopened.", payload.alignment_message || "Rerun the spec after the correction is reflected.");
      } else if (action === "endorse") {
        setTurnState("agent", "Agent", "User endorsed the shared spec", "The agent can continue through evidence, verification, and gate checks.", payload.alignment_message || "Continue the route.");
      }
      if (alignmentResult) alignmentResult.textContent = payload.alignment_message || "alignment move recorded";
    } catch (error) {
      if (alignmentResult) alignmentResult.textContent = error.message || "alignment failed";
    } finally {
      button.disabled = false;
    }
  });
});

recordButton?.addEventListener("click", async () => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = event => chunks.push(event.data);
  recorder.onstop = () => {
    const blob = new Blob(chunks, { type: "audio/webm" });
    appendFile(new File([blob], `speech-${Date.now()}.webm`, { type: "audio/webm" }));
    stream.getTracks().forEach(track => track.stop());
    captureState.textContent = "audio attached";
  };
  recorder.start();
  recordButton.disabled = true;
  stopButton.disabled = false;
  captureState.textContent = "recording";
});

stopButton?.addEventListener("click", () => {
  if (recorder && recorder.state !== "inactive") recorder.stop();
  recordButton.disabled = false;
  stopButton.disabled = true;
});

speechButton?.addEventListener("click", () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    captureState.textContent = "speech text unavailable";
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.onresult = event => {
    const transcript = event.results[0][0].transcript;
    speechInput.value = transcript;
    queryInput.value = `${queryInput.value.trim()}\\n\\n${transcript}`.trim();
    captureState.textContent = "speech text captured";
  };
  recognition.onerror = () => { captureState.textContent = "speech text error"; };
  recognition.start();
  captureState.textContent = "listening";
});
"""


if __name__ == "__main__":
    main()
