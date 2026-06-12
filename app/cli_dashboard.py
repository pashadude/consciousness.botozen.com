"""Terminal dashboard for the Mutual Specification Game prototype."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.trader_rag import load_trader_source_config

ANSI = {
    "green": "\033[32m",
    "red": "\033[31m",
    "blue": "\033[34m",
    "yellow": "\033[33m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

DEFAULT_TEXT = "look at HO/RB arb and give me risk on this spread"


@dataclass(frozen=True)
class StatusItem:
    label: str
    status: str
    detail: str


@dataclass(frozen=True)
class SpendEstimate:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    token_cost_usd: float
    estimated_kwh: float
    electricity_cost_usd: float
    energy_wh_per_1k_tokens: float
    power_price_usd_per_mwh: float
    pue: float


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show Mutual Specification Game status in the terminal."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Compressed trader/user text to estimate token and spec state for.",
    )
    parser.add_argument("--text", dest="text_flag", help="Text to display.")
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=None,
        help="Estimated output token budget. Defaults to env or 800.",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    args = parser.parse_args(argv)

    text = args.text_flag or " ".join(args.text).strip() or DEFAULT_TEXT
    color = not args.no_color and "NO_COLOR" not in os.environ
    print(render_dashboard(text, env=os.environ, output_tokens=args.output_tokens, color=color))
    return 0


def render_dashboard(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
    output_tokens: int | None = None,
    color: bool = True,
) -> str:
    env = env or {}
    spend = estimate_spend(text, env=env, output_tokens=output_tokens)
    sections = [
        title("Mutual Specification Game CLI", color=color),
        render_text_section(text, color=color),
        render_status_section("Design Surface", design_status(env), color=color),
        render_status_section("Google Services / Models", google_status(env), color=color),
        render_spend_section(spend, color=color),
        render_loss_parameters(env, color=color),
    ]
    return "\n\n".join(sections)


def render_text_section(text: str, *, color: bool) -> str:
    return "\n".join(
        [
            header("Text", color=color),
            f"q: {text}",
            "theta: latent trading task to reconstruct",
            "s: executable trade / analysis / alert / strategy specification",
        ]
    )


def design_status(env: Mapping[str, str]) -> list[StatusItem]:
    telemetry_enabled = parse_bool(env.get("RESOURCE_REGION_DOMINATION_ENABLED"), False)
    live_collectors_enabled = (
        telemetry_enabled
        and parse_bool(env.get("RESOURCE_TELEMETRY_COLLECTORS_ENABLED"), False)
        and configured_value(env.get("GOOGLE_CLOUD_PROJECT"))
        and configured_value(env.get("TELEMETRY_DATASET_ID"))
        and configured_value(env.get("GCP_ASSET_CHANGES_SUBSCRIPTION"))
    )
    return [
        StatusItem(
            "Mutual Specification Game ledger",
            "green",
            "implemented in app/spec_state.py",
        ),
        StatusItem(
            "Trader decision-state inference",
            "green",
            "theta/q/s, evidence contract, decision_gate",
        ),
        StatusItem(
            "Data-only IBKR/Yahoo evidence framing",
            "green",
            "decision support only; no broker execution",
        ),
        StatusItem(
            "P/Q/A formalization evaluator",
            "green",
            "implemented in app/formalization.py",
        ),
        StatusItem(
            "Async escalation jobs",
            "green" if parse_bool(env.get("ASYNC_JOB_ENABLED"), False) else "red",
            "light route can enqueue strong/tool-heavy jobs",
        ),
        StatusItem(
            "Multimodal artifact retrieval",
            "green" if parse_bool(env.get("MULTIMODAL_RETRIEVAL_ENABLED"), False) else "red",
            "enabled by MULTIMODAL_RETRIEVAL_ENABLED",
        ),
        StatusItem(
            "Resource-region domination telemetry",
            "green" if telemetry_enabled else "red",
            "BigQuery criteria layer for model-region routing",
        ),
        StatusItem(
            "Live resource telemetry collectors",
            "green" if live_collectors_enabled else "red",
            "mutual-spec-telemetry CLI for Asset, Monitoring, Billing, and power proxies",
        ),
    ]


def google_status(env: Mapping[str, str]) -> list[StatusItem]:
    project = configured_value(env.get("GOOGLE_CLOUD_PROJECT"))
    location = configured_value(env.get("GOOGLE_CLOUD_LOCATION"))
    vertex_enabled = parse_bool(env.get("GOOGLE_GENAI_USE_VERTEXAI"), False)
    api_key = configured_value(env.get("GEMINI_API_KEY")) or configured_value(
        env.get("GOOGLE_API_KEY")
    )
    has_google_auth = bool((vertex_enabled and project and location) or api_key)
    model_armor = configured_value(env.get("MODEL_ARMOR_TEMPLATE")) or (
        configured_value(env.get("MODEL_ARMOR_TEMPLATE_ID")) and project
    )
    mcp = configured_mcp(env)
    trader_config = load_trader_source_config(env)
    trader_source_provider = trader_config.provider
    provider_key = (trader_source_provider or "").lower()
    google_agent_search = trader_config.google_agent_search_enabled or provider_key in {
        "google_agent_search",
        "adk_google_search",
        "google_search_tool",
    }
    google_cse = trader_config.google_search_api_key and trader_config.google_search_cx
    vertex_search = (
        trader_config.vertex_ai_search_data_store_id
        or trader_config.vertex_ai_search_engine_id
    )
    spanner_rag = trader_config.spanner_configured
    trader_source_configured = bool(
        trader_source_provider
        and provider_key not in {"disabled", "off", "none"}
        and (
            (google_agent_search and has_google_auth)
            or google_cse
            or mcp
            or spanner_rag
            or vertex_search
            or provider_key in {"fixture", "model", "model_only"}
        )
    )
    return [
        StatusItem(
            "Vertex AI Gemini auth",
            "blue" if vertex_enabled and project and location else "red",
            "GOOGLE_GENAI_USE_VERTEXAI + project + location",
        ),
        StatusItem(
            "Gemini API key fallback",
            "blue" if api_key or (vertex_enabled and project and location) else "red",
            "configured" if api_key else "not needed when Vertex AI auth is active",
        ),
        StatusItem(
            "Cheap model",
            "blue" if has_google_auth else "red",
            env.get("MUTUAL_SPEC_CHEAP_MODEL", "gemini-3.5-flash"),
        ),
        StatusItem(
            "Strong model",
            "blue" if has_google_auth else "red",
            env.get("MUTUAL_SPEC_STRONG_MODEL", "gemini-3.5-flash"),
        ),
        StatusItem(
            "Verifier model",
            "blue" if has_google_auth else "red",
            env.get("MUTUAL_SPEC_VERIFIER_MODEL", "gemini-3.5-flash"),
        ),
        StatusItem(
            "Gemini multimodal embeddings",
            "blue"
            if has_google_auth
            and parse_bool(env.get("MULTIMODAL_RETRIEVAL_ENABLED"), False)
            else "red",
            env.get("MULTIMODAL_EMBEDDING_MODEL", "gemini-embedding-001"),
        ),
        StatusItem(
            "Model Armor",
            "blue" if model_armor else "red",
            "MODEL_ARMOR_TEMPLATE or TEMPLATE_ID",
        ),
        StatusItem(
            "BigQuery analytics",
            "blue"
            if project
            and parse_bool(env.get("BQ_ANALYTICS_ENABLED"), False)
            and configured_value(env.get("BQ_ANALYTICS_DATASET_ID"))
            else "red",
            env.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics"),
        ),
        StatusItem(
            "MCP research tools",
            "blue" if mcp else "red",
            "MCP_RESEARCH_URL or MCP_RESEARCH_COMMAND (+ OPOINT_API_KEY for Opoint)",
        ),
        StatusItem(
            "Trader source layer",
            "blue" if trader_source_configured else "red",
            "Spanner RAG, Google search, google_cse keys, Vertex AI Search, or MCP/Opoint",
        ),
    ]


def estimate_spend(
    text: str,
    *,
    env: Mapping[str, str],
    output_tokens: int | None,
) -> SpendEstimate:
    input_tokens = estimate_tokens(text)
    out_tokens = output_tokens or parse_int(env.get("CLI_ESTIMATED_OUTPUT_TOKENS"), 800)
    total_tokens = input_tokens + out_tokens
    input_usd_per_1k = parse_float(env.get("TOKEN_USD_PER_1K_INPUT"), 0.0)
    output_usd_per_1k = parse_float(env.get("TOKEN_USD_PER_1K_OUTPUT"), 0.0)
    token_cost = (input_tokens / 1000.0 * input_usd_per_1k) + (
        out_tokens / 1000.0 * output_usd_per_1k
    )
    wh_per_1k = parse_float(env.get("ENERGY_WH_PER_1K_TOKENS"), 0.2)
    pue = parse_float(env.get("TELEMETRY_PUE"), 1.1)
    price_mwh = parse_float(env.get("POWER_PRICE_USD_PER_MWH"), 80.0)
    estimated_kwh = (total_tokens / 1000.0) * wh_per_1k / 1000.0 * pue
    electricity_cost = estimated_kwh * price_mwh / 1000.0
    return SpendEstimate(
        input_tokens=input_tokens,
        output_tokens=out_tokens,
        total_tokens=total_tokens,
        token_cost_usd=token_cost,
        estimated_kwh=estimated_kwh,
        electricity_cost_usd=electricity_cost,
        energy_wh_per_1k_tokens=wh_per_1k,
        power_price_usd_per_mwh=price_mwh,
        pue=pue,
    )


def render_spend_section(spend: SpendEstimate, *, color: bool) -> str:
    return "\n".join(
        [
            header("Token / Electricity Spend Estimate", color=color),
            f"input_tokens_est: {spend.input_tokens}",
            f"output_tokens_est: {spend.output_tokens}",
            f"total_tokens_est: {spend.total_tokens}",
            f"token_cost_est_usd: {spend.token_cost_usd:.6f} (set TOKEN_USD_PER_1K_INPUT/OUTPUT)",
            f"energy_proxy_kwh: {spend.estimated_kwh:.8f}",
            f"electricity_proxy_usd: {spend.electricity_cost_usd:.8f}",
            (
                "coefficients: "
                f"{spend.energy_wh_per_1k_tokens} Wh/1k tokens, "
                f"PUE {spend.pue}, "
                f"${spend.power_price_usd_per_mwh}/MWh"
            ),
        ]
    )


def render_loss_parameters(env: Mapping[str, str], *, color: bool) -> str:
    params = {
        "decision_rule": env.get("LOSS_DECISION_RULE", "pareto_nondominated"),
        "latency_epsilon_ms": env.get("LOSS_LATENCY_EPSILON_MS", "250"),
        "quality_epsilon": env.get("LOSS_MODEL_QUALITY_EPSILON", "0.05"),
        "cost_epsilon_usd": env.get("LOSS_COST_EPSILON_USD", "0.01"),
        "compute_spread_stress_epsilon_usd": env.get(
            "LOSS_COMPUTE_SPREAD_STRESS_EPSILON_USD",
            "0.01",
        ),
        "watts_per_vcpu": env.get("TELEMETRY_WATTS_PER_VCPU", "8.0"),
        "watts_per_gpu": env.get("TELEMETRY_WATTS_PER_GPU", "300.0"),
        "watts_per_tpu": env.get("TELEMETRY_WATTS_PER_TPU", "300.0"),
        "low_confidence_penalty": env.get("LOSS_LOW_CONFIDENCE_PENALTY", "0.50"),
    }
    lines = [header("Loss / Domination Parameters", color=color)]
    lines.extend(f"{key}: {value}" for key, value in params.items())
    return "\n".join(lines)


def render_status_section(title_text: str, items: list[StatusItem], *, color: bool) -> str:
    lines = [header(title_text, color=color)]
    for item in items:
        marker = {
            "green": "OK",
            "blue": "ON",
            "red": "NO",
            "yellow": "??",
        }.get(item.status, "--")
        lines.append(
            f"{paint(marker, item.status, color=color):>12}  {item.label}: {item.detail}"
        )
    return "\n".join(lines)


def title(text: str, *, color: bool) -> str:
    return paint(text, "bold", color=color)


def header(text: str, *, color: bool) -> str:
    return paint(f"== {text} ==", "bold", color=color)


def paint(text: str, color_name: str, *, color: bool) -> str:
    if not color:
        return text
    return f"{ANSI[color_name]}{text}{ANSI['reset']}"


def estimate_tokens(text: str) -> int:
    compact = " ".join(text.split())
    if not compact:
        return 0
    return max(1, round(len(compact) / 4))


def configured_value(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if not lowered or lowered.startswith("your-") or lowered in {"none", "null"}:
        return None
    return value


def configured_mcp(env: Mapping[str, str]) -> str | None:
    remote = configured_value(env.get("MCP_RESEARCH_URL"))
    if remote:
        return remote
    command = configured_value(env.get("MCP_RESEARCH_COMMAND"))
    if not command:
        return None
    if "opoint" in command.lower() and not configured_value(env.get("OPOINT_API_KEY")):
        return None
    return command


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
