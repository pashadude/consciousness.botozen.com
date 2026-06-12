"""FastAPI operator console for the Mutual Specification Game."""

from __future__ import annotations

import html
import mimetypes
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from app.cli_dashboard import (
    design_status,
    estimate_spend,
    google_status,
)
from app.conversation_log import persist_console_talk
from app.formalization import formalize_ledger
from app.jobs import enqueue_async_job
from app.router import (
    async_jobs_enabled,
    route_async_decision,
    route_for_stage,
)
from app.spec_state import (
    ArtifactRef,
    HumanReviewState,
    SpecLedger,
    add_evidence_from_artifacts,
    add_route,
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


app = FastAPI(title="Mutual Spec Console")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return render_page(
        text=DEFAULT_CONSOLE_TEXT,
        result=build_console_result(DEFAULT_CONSOLE_TEXT, [], ""),
        env=os.environ,
    )


@app.post("/spec", response_class=HTMLResponse)
async def create_spec(
    query: str = QUERY_FORM,
    speech_text: str = SPEECH_TEXT_FORM,
    files: list[UploadFile] | None = FILES_FORM,
) -> str:
    artifacts = await save_uploads(files or [])
    text = combine_query_and_speech(query, speech_text, bool(artifacts))
    result = build_console_result(text, artifacts, speech_text)
    persist_console_talk(
        result=result,
        raw_text=text,
        speech_text=speech_text,
        response_channel="html",
    )
    return render_page(text=text, result=result, env=os.environ)


@app.post("/api/spec")
async def create_spec_json(
    query: str = QUERY_FORM,
    speech_text: str = SPEECH_TEXT_FORM,
    files: list[UploadFile] | None = FILES_FORM,
) -> JSONResponse:
    artifacts = await save_uploads(files or [])
    text = combine_query_and_speech(query, speech_text, bool(artifacts))
    result = build_console_result(text, artifacts, speech_text)
    persist_console_talk(
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


def build_console_result(
    text: str,
    artifacts: list[ArtifactRef],
    speech_text: str,
) -> ConsoleResult:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, text, artifact_refs=artifacts)
    for stage in ("ingest", "hypothesize_spec", "retrieve_evidence"):
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
    rag_result = run_trader_rag(
        text,
        env=os.environ,
        search_plan=ledger.search_plan,
    )
    apply_rag_to_ledger(ledger, rag_result)
    formalize_ledger(ledger)
    record_stage(ledger, "draft_output")
    add_route(ledger, route_for_stage("draft_output", ledger))
    draft = build_draft_from_ledger(ledger)
    record_stage(ledger, "verify")
    add_route(ledger, route_for_stage("verify", ledger))
    verification = verify_draft(ledger, draft)
    ledger.verification_findings = verification.findings
    update_mutual_spec_game_state(ledger)
    route_decision = route_async_decision(
        ledger,
        mcp_configured=configured_mcp(os.environ),
        telemetry_enabled=flag_enabled("RESOURCE_REGION_DOMINATION_ENABLED"),
        artifact_count=len(artifacts),
        failed_verification=not verification.passed,
    )
    if route_decision.should_enqueue and async_jobs_enabled():
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
    )


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
            "<script>",
            JS,
            "</script>",
            "</body>",
            "</html>",
        ]
    )


def render_header(result: ConsoleResult) -> str:
    gate_class = gate_status_class(result.ledger.decision_gate)
    verify_class = "verified" if result.verification_passed else "blocked"
    route_mode = str(getattr(result.route_decision, "mode", "sync"))
    return f"""
<header class="topbar">
  <div>
    <h1>Mutual Spec Console</h1>
    <p class="subtle">q -> theta -> s | ledger {escape(result.ledger.ledger_id)}</p>
  </div>
  <div class="status-strip">
    <span class="pill {status_class(result.ledger.status)}">{escape(result.ledger.status)}</span>
    <span class="pill {verify_class}">verify {'pass' if result.verification_passed else 'blocked'}</span>
    <span class="pill {gate_class}">gate {escape(result.ledger.decision_gate)}</span>
    <span class="pill {route_status_class(route_mode)}">{escape(route_mode)}</span>
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
    {render_status_panel(env)}
  </section>
  <section class="center-rail">
    {render_decision_summary_panel(result)}
    {render_game_panel(result)}
    {render_source_layer_panel(result)}
    {render_proof_obligations_panel(result)}
    {render_equilibrium_panel(result)}
    {render_formal_proof_panel(result)}
    {render_spec_panel(result)}
    {render_frontier_panel(result)}
  </section>
  <section class="right-rail">
    {render_artifacts_panel(result)}
    {render_human_review_panel(result)}
    {render_skill_compatibility_panel(result)}
    {render_route_panel(result)}
    {render_spend_panel(spend)}
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
      <button type="button" id="markReviewed">Acknowledge Gate</button>
    </div>
  </div>
  <div class="decision-callout">
    <span class="decision-label">{escape(ledger.decision_gate)}</span>
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
    <strong>Why human review is {escape(review.status)}:</strong>
    <p>{escape(reason)}</p>
    <p><span class="label-inline">Next</span> {escape(action)}</p>
    <p><span class="label-inline">Meaning</span> The agent may provide a provisional decision frame, but it must not present this as execution-ready or take/broker action.</p>
  </div>
"""


def render_status_panel(env: Mapping[str, str]) -> str:
    items = design_status(env) + google_status(env)
    rows = "\n".join(
        f'<div class="status-row"><span class="dot {escape(item.status)}"></span><span>{escape(item.label)}</span></div>'
        for item in items
    )
    return f"""
<section class="panel">
  <h2>Platform State</h2>
  <div class="status-grid">{rows}</div>
</section>
"""


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
  <strong>{escape(item.theorem_name)} / {escape(item.status)}</strong>
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
  <details open>
    <summary>Search Plan</summary>
    <ul class="source-list">{plan_rows}</ul>
  </details>
  <details open>
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
    <span class="muted">{escape(ledger.decision_gate)}</span>
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
      <p class="subtle">Queued means policy review required, not a frozen backend job.</p>
    </div>
    <span class="pill {review_status_class(review.status)}">{escape(review.status)}</span>
  </div>
  {notice}
  <dl class="kv">
    <dt>required</dt><dd>{escape(str(review.required).lower())}</dd>
    <dt>risk</dt><dd>{escape(review.risk_level)}</dd>
    <dt>assignee</dt><dd>{escape(review.assigned_player)}</dd>
    <dt>owner</dt><dd>{escape(review.decision_owner)}</dd>
  </dl>
  <details open>
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
    text = (ledger.expressed_query or ledger.user_request).lower()
    answer: list[str] = []
    if "sulfur" in text or "sulphur" in text:
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
            f"decision gate={ledger.decision_gate}; "
            f"human review={ledger.human_review.status}."
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
.pill.neutral { color: var(--gray); border-color: var(--line); background: #f9faf8; }
.workspace {
  display: grid;
  grid-template-columns: minmax(280px, 0.85fr) minmax(420px, 1.45fr) minmax(280px, 0.9fr);
  gap: 14px;
  margin-top: 16px;
}
.left-rail, .center-rail, .right-rail { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
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
.status-grid { display: grid; gap: 8px; margin-top: 12px; }
.status-row { display: grid; grid-template-columns: 14px 1fr; gap: 8px; align-items: center; font-size: 13px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }
.dot.blue { background: var(--blue); }
.dot.yellow { background: var(--amber); }
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
@media (max-width: 1180px) {
  .workspace { grid-template-columns: 1fr 1fr; }
  .right-rail { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .shell { width: min(100vw - 20px, 720px); padding-top: 12px; }
  .topbar, .workspace, .right-rail, .spec-grid, .detail-grid { display: flex; flex-direction: column; }
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
const markReviewedButton = document.querySelector("#markReviewed");
const decisionPanel = document.querySelector("#decisionPanel");
let recorder = null;
let chunks = [];

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
    runButton.textContent = "Running...";
  }
  if (captureState) {
    captureState.textContent = "running spec game: retrieving sources, verifying, and building answer";
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

markReviewedButton?.addEventListener("click", () => {
  markReviewedButton.textContent = "Gate Acknowledged";
  markReviewedButton.disabled = true;
  decisionPanel?.querySelectorAll(".pill.review, .pill.clarify").forEach(item => {
    item.classList.remove("review", "clarify");
    item.classList.add("verified");
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
