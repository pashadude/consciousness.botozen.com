"""ADK 2.x graph workflow for the Mutual Specification Game."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk import Agent, Context, Event, Workflow
from google.adk.events import RequestInput
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.workflow import node
from google.genai import types
from mcp import StdioServerParameters

from app.formalization import formalize_ledger
from app.jobs import enqueue_async_job
from app.multimodal import add_multimodal_evidence_from_artifacts
from app.router import (
    CHEAP_MODEL,
    async_jobs_enabled,
    route_async_decision,
    route_for_stage,
    should_refuse_for_safety,
)
from app.spec_state import (
    ArtifactRef,
    add_evidence_from_artifacts,
    add_mcp_evidence_placeholder,
    add_route,
    apply_clarification_answer,
    build_clarification_question,
    ledger_from_state,
    ledger_to_state,
    needs_clarification,
    record_stage,
    text_from_content,
    update_ledger_from_user_text,
)
from app.verifiers import build_draft_from_ledger, format_final_response, verify_draft

WORKFLOW_STAGE_ORDER = [
    "ingest",
    "hypothesize_spec",
    "ask_clarification",
    "enqueue_async_job",
    "retrieve_evidence",
    "draft_output",
    "verify",
    "finalize_or_loop",
]


def build_mcp_research_tools() -> list[McpToolset]:
    """Build MCP toolsets from env without requiring them for local tests."""

    remote_url = os.environ.get("MCP_RESEARCH_URL")
    if remote_url:
        return [
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=remote_url),
                tool_name_prefix="research",
            )
        ]

    command_line = os.environ.get("MCP_RESEARCH_COMMAND")
    if command_line:
        if "opoint" in command_line.lower() and not os.environ.get("OPOINT_API_KEY"):
            return []
        parts = shlex.split(command_line)
        if not parts:
            return []
        return [
            McpToolset(
                connection_params=StdioConnectionParams(
                    server_params=StdioServerParameters(
                        command=parts[0],
                        args=parts[1:],
                        cwd=os.environ.get("MCP_RESEARCH_CWD"),
                    )
                ),
                tool_name_prefix="research",
            )
        ]
    return []


MCP_RESEARCH_TOOLS = build_mcp_research_tools()

mcp_research_agent = Agent(
    name="mcp_research_agent",
    model=CHEAP_MODEL.model_name,
    instruction=(
        "Use the configured MCP research tools to retrieve evidence for the "
        "current task specification. Return compact bullet points with source "
        "titles, source URIs, and the exact facts used. Do not invent sources."
    ),
    tools=MCP_RESEARCH_TOOLS,
    output_schema=str,
)


@node(name="ingest")
async def ingest_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "ingest")
    artifact_refs = await persist_upload_parts_as_artifacts(ctx)
    text = text_from_content(node_input) or text_from_content(ctx.user_content)
    update_ledger_from_user_text(ledger, text, artifact_refs=artifact_refs)
    add_route(ledger, route_for_stage("ingest", ledger))
    return Event(output=ledger.model_dump(mode="json"), state=ledger_to_state(ledger))


@node(name="hypothesize_spec")
def hypothesize_spec_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "hypothesize_spec")
    add_route(ledger, route_for_stage("hypothesize_spec", ledger))
    formalize_ledger(ledger)
    if should_refuse_for_safety(ledger.user_request):
        route = "retrieve"
    elif needs_clarification(ledger):
        build_clarification_question(ledger)
        route = "clarify"
    elif async_jobs_enabled() and route_async_decision(
        ledger,
        mcp_configured=bool(MCP_RESEARCH_TOOLS),
        telemetry_enabled=env_flag("RESOURCE_REGION_DOMINATION_ENABLED"),
    ).should_enqueue:
        route = "async"
    else:
        route = "retrieve"
    return Event(
        output=ledger.model_dump(mode="json"),
        route=route,
        state=ledger_to_state(ledger),
    )


@node(name="ask_clarification")
def ask_clarification_node(ctx: Context, node_input: Any = None):
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "ask_clarification")
    question = build_clarification_question(ledger)
    yield Event(output=ledger.model_dump(mode="json"), state=ledger_to_state(ledger))
    yield RequestInput(
        message=question,
        payload={
            "ledger_id": ledger.ledger_id,
            "missing_fields": [item.field for item in ledger.ambiguities],
        },
        response_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "goal": {"type": "string"},
                "audience": {"type": "string"},
                "output_format": {"type": "string"},
            },
        },
    )


@node(name="apply_clarification")
def apply_clarification_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "apply_clarification")
    apply_clarification_answer(ledger, node_input)
    formalize_ledger(ledger)
    if needs_clarification(ledger):
        route = "clarify"
    elif async_jobs_enabled() and route_async_decision(
        ledger,
        mcp_configured=bool(MCP_RESEARCH_TOOLS),
        telemetry_enabled=env_flag("RESOURCE_REGION_DOMINATION_ENABLED"),
    ).should_enqueue:
        route = "async"
    else:
        route = "retrieve"
    return Event(
        output=ledger.model_dump(mode="json"),
        route=route,
        state=ledger_to_state(ledger),
    )


@node(name="enqueue_async_job")
def enqueue_async_job_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "enqueue_async_job")
    decision = route_async_decision(
        ledger,
        mcp_configured=bool(MCP_RESEARCH_TOOLS),
        telemetry_enabled=env_flag("RESOURCE_REGION_DOMINATION_ENABLED"),
    )
    job = enqueue_async_job(ledger, decision)
    ledger.status = "async_pending"
    message = "\n".join(
        [
            f"Queued async specification job `{job.job_id}`.",
            f"Kind: {job.kind}",
            f"Expected spec gain: {job.expected_spec_gain}",
            f"Reasons: {', '.join(job.reasons)}",
            "The light route has preserved the current spec and deferred deeper tool/model work.",
        ]
    )
    return Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=message)]),
        output={"job_id": job.job_id, "status": job.status, "reasons": job.reasons},
        state=ledger_to_state(ledger),
    )


@node(name="retrieve_evidence")
async def retrieve_evidence_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "retrieve_evidence")
    ledger.status = "retrieving"
    add_route(ledger, route_for_stage("retrieve_evidence", ledger))
    add_evidence_from_artifacts(ledger)
    await add_multimodal_evidence_from_artifacts(ctx, ledger)
    if MCP_RESEARCH_TOOLS:
        source = os.environ.get("MCP_RESEARCH_URL") or os.environ.get("MCP_RESEARCH_COMMAND") or "mcp://configured"
        add_mcp_evidence_placeholder(ledger, source)
        route = "mcp"
    else:
        route = "local"
    return Event(
        output=ledger.model_dump(mode="json"),
        route=route,
        state=ledger_to_state(ledger),
    )


@node(name="merge_mcp_research")
def merge_mcp_research_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "merge_mcp_research")
    if node_input:
        evidence_id = f"mcp:summary:{uuid4().hex[:8]}"
        ledger.evidence.append(
            {
                "evidence_id": evidence_id,
                "source_type": "mcp",
                "title": "MCP research summary",
                "uri": os.environ.get("MCP_RESEARCH_URL") or "mcp://stdio",
                "summary": str(node_input)[:1000],
                "used": True,
            }
        )
        ledger.evidence_used.append(evidence_id)
    return Event(output=ledger.model_dump(mode="json"), state=ledger_to_state(ledger))


@node(name="local_evidence_ready")
def local_evidence_ready_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "local_evidence_ready")
    return Event(output=ledger.model_dump(mode="json"), state=ledger_to_state(ledger))


@node(name="draft_output")
def draft_output_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "draft_output")
    failed_verification = any(
        item.severity == "high" for item in ledger.verification_findings
    )
    add_route(
        ledger,
        route_for_stage(
            "draft_output",
            ledger,
            failed_verification=failed_verification,
        ),
    )
    ledger.status = "drafting"
    draft = build_draft_from_ledger(ledger)
    if failed_verification:
        draft += "\n\nRevision note: this draft was regenerated after verifier feedback."
    ledger.drafts.append(draft)
    return Event(output=draft, state=ledger_to_state(ledger))


@node(name="verify")
def verify_output_node(ctx: Context, node_input: str | None = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "verify")
    add_route(ledger, route_for_stage("verify", ledger))
    ledger.status = "verifying"
    draft = node_input or (ledger.drafts[-1] if ledger.drafts else "")
    result = verify_draft(ledger, draft)
    ledger.verification_findings = result.findings
    safety_failure = any(item.category == "safety" for item in result.findings)
    route = "finalize" if result.passed or safety_failure or ledger.loop_count >= 1 else "loop"
    if route == "loop":
        ledger.loop_count += 1
    return Event(
        output={"passed": result.passed, "draft": draft},
        route=route,
        state=ledger_to_state(ledger),
    )


@node(name="finalize")
def finalize_node(ctx: Context, node_input: Any = None) -> Event:
    ledger = ledger_from_state(ctx.state)
    record_stage(ledger, "finalize")
    ledger.status = "finalized"
    draft = ""
    if isinstance(node_input, dict):
        draft = str(node_input.get("draft") or "")
    draft = draft or (ledger.drafts[-1] if ledger.drafts else build_draft_from_ledger(ledger))
    verification = verify_draft(ledger, draft)
    final = format_final_response(ledger, draft, verification)
    return Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=final)]),
        output=final,
        state=ledger_to_state(ledger),
    )


async def persist_upload_parts_as_artifacts(ctx: Context) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    content = getattr(ctx, "user_content", None)
    parts = getattr(content, "parts", None) or []
    for index, part in enumerate(parts):
        if getattr(part, "text", None):
            continue
        inline_data = getattr(part, "inline_data", None)
        file_data = getattr(part, "file_data", None)
        if not inline_data and not file_data:
            continue
        mime_type = getattr(inline_data, "mime_type", None) or getattr(file_data, "mime_type", None)
        source_name = getattr(file_data, "file_uri", None) or f"upload_{index}"
        filename = safe_artifact_filename(source_name, mime_type=mime_type)
        version = None
        note = "Stored as ADK artifact metadata in state; binary remains in artifact service."
        if inline_data:
            try:
                version = await ctx.save_artifact(filename, part)
            except Exception:
                note = "Artifact service unavailable; recorded upload metadata only."
        refs.append(
            ArtifactRef(
                artifact_id=f"{filename}:v{version if version is not None else 'external'}",
                filename=filename,
                mime_type=mime_type,
                version=version,
                source="upload",
                note=note,
            )
        )
    return refs


def safe_artifact_filename(source_name: str, *, mime_type: str | None = None) -> str:
    basename = Path(source_name).name or "upload"
    basename = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in basename)
    if "." not in basename and mime_type:
        extension = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "video/mp4": ".mp4",
            "text/plain": ".txt",
            "text/markdown": ".md",
            "text/csv": ".csv",
            "application/json": ".json",
        }.get(mime_type, "")
        basename += extension
    return basename[:120] or "upload"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_workflow() -> Workflow:
    return Workflow(
        name="root_agent",
        description=(
            "Mutual Specification Game workflow: ingest -> hypothesize spec -> "
            "clarify when high-impact ambiguity exists -> retrieve evidence -> "
            "draft -> verify -> finalize or loop."
        ),
        edges=[
            ("START", ingest_node, hypothesize_spec_node),
            (
                hypothesize_spec_node,
                {
                    "clarify": (ask_clarification_node, apply_clarification_node),
                    "async": enqueue_async_job_node,
                    "retrieve": retrieve_evidence_node,
                },
            ),
            (
                apply_clarification_node,
                {
                    "clarify": ask_clarification_node,
                    "async": enqueue_async_job_node,
                    "retrieve": retrieve_evidence_node,
                },
            ),
            (
                retrieve_evidence_node,
                {
                    "mcp": mcp_research_agent,
                    "local": local_evidence_ready_node,
                },
            ),
            (mcp_research_agent, merge_mcp_research_node),
            (merge_mcp_research_node, draft_output_node),
            (local_evidence_ready_node, draft_output_node),
            (draft_output_node, verify_output_node),
            (
                verify_output_node,
                {
                    "finalize": finalize_node,
                    "loop": (hypothesize_spec_node,),
                },
            ),
        ],
    )
