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
from app.formalization import formalize_ledger
from app.jobs import enqueue_async_job
from app.router import (
    async_jobs_enabled,
    route_async_decision,
    route_for_stage,
)
from app.spec_state import (
    ArtifactRef,
    SpecLedger,
    add_evidence_from_artifacts,
    add_route,
    record_stage,
    update_ledger_from_user_text,
)
from app.telemetry.domination import (
    RouteCandidate,
    nondominated_candidates,
    route_loss,
)
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
            "frontier": [candidate.key for candidate in result.candidates if candidate.key in result.frontier_keys],
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
    formalize_ledger(ledger)
    record_stage(ledger, "draft_output")
    add_route(ledger, route_for_stage("draft_output", ledger))
    draft = build_draft_from_ledger(ledger)
    record_stage(ledger, "verify")
    add_route(ledger, route_for_stage("verify", ledger))
    verification = verify_draft(ledger, draft)
    ledger.verification_findings = verification.findings
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
        ledger.status = "finalized" if verification.passed else "clarifying"
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
    return f"""
<header class="topbar">
  <div>
    <h1>Mutual Spec Console</h1>
    <p class="subtle">q -> theta -> s | ledger {escape(result.ledger.ledger_id)}</p>
  </div>
  <div class="status-strip">
    <span class="pill {status_class(result.ledger.status)}">{escape(result.ledger.status)}</span>
    <span class="pill {'ok' if result.verification_passed else 'warn'}">verify {'pass' if result.verification_passed else 'open'}</span>
    <span class="pill info">{escape(str(getattr(result.route_decision, 'mode', 'sync')))}</span>
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
    {render_spec_panel(result)}
    {render_frontier_panel(result)}
  </section>
  <section class="right-rail">
    {render_artifacts_panel(result)}
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
    <button type="submit">Run Spec</button>
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


def render_list(items: list[str]) -> str:
    if not items:
        return '<ul class="compact"><li>none</li></ul>'
    return "<ul class=\"compact\">" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


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
        "finalized": "ok",
        "retrieving": "info",
        "drafting": "info",
        "verifying": "info",
        "async_pending": "warn",
        "clarifying": "warn",
    }.get(status, "info")


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
.pill.ok { color: var(--green); border-color: #b9d8c5; }
.pill.warn { color: var(--amber); border-color: #e3cc95; }
.pill.info { color: var(--blue); border-color: #b8cbe2; }
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
.capture-state { color: var(--muted); font-size: 13px; min-height: 18px; }
.status-grid { display: grid; gap: 8px; margin-top: 12px; }
.status-row { display: grid; grid-template-columns: 14px 1fr; gap: 8px; align-items: center; font-size: 13px; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }
.dot.blue { background: var(--blue); }
.dot.yellow { background: var(--amber); }
.spec-grid, .detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.spec-grid p, .detail-grid p, li { font-size: 14px; line-height: 1.45; overflow-wrap: anywhere; }
.detail-grid { margin-top: 14px; }
details { margin-top: 14px; }
summary { cursor: pointer; margin-bottom: 8px; }
.compact { margin: 6px 0 0; padding-left: 18px; }
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
const recordButton = document.querySelector("#recordAudio");
const stopButton = document.querySelector("#stopAudio");
const speechButton = document.querySelector("#transcribeSpeech");
const speechInput = document.querySelector("#speech_text");
const queryInput = document.querySelector("#query");
const captureState = document.querySelector("#captureState");
let recorder = null;
let chunks = [];

function appendFile(file) {
  const transfer = new DataTransfer();
  for (const existing of fileInput.files) transfer.items.add(existing);
  transfer.items.add(file);
  fileInput.files = transfer.files;
}

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
