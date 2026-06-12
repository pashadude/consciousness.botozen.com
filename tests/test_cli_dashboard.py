from app.cli_dashboard import estimate_spend, render_dashboard


def test_dashboard_renders_core_sections_without_color() -> None:
    output = render_dashboard(
        "look at HO/RB arb and give me risk on this spread",
        env={},
        color=False,
    )

    assert "Mutual Specification Game CLI" in output
    assert "== Text ==" in output
    assert "q: look at HO/RB arb and give me risk on this spread" in output
    assert "theta: latent trading task to reconstruct" in output
    assert "s: executable trade / analysis / alert / strategy specification" in output
    assert "== Design Surface ==" in output
    assert "== Google Services / Models ==" in output
    assert "== Token / Electricity Spend Estimate ==" in output
    assert "== Loss / Domination Parameters ==" in output
    assert "decision_rule: pareto_nondominated" in output


def test_dashboard_marks_configured_google_services_ready() -> None:
    env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "grant-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
        "MUTUAL_SPEC_CHEAP_MODEL": "gemini-3.5-flash",
        "MUTUAL_SPEC_STRONG_MODEL": "gemini-3.5-flash",
        "MUTUAL_SPEC_VERIFIER_MODEL": "gemini-3.5-flash",
        "MULTIMODAL_RETRIEVAL_ENABLED": "true",
        "MODEL_ARMOR_TEMPLATE_ID": "default-agent-policy",
        "BQ_ANALYTICS_ENABLED": "true",
        "BQ_ANALYTICS_DATASET_ID": "adk_agent_analytics",
        "MCP_RESEARCH_URL": "https://example.test/mcp",
        "CLI_ESTIMATED_OUTPUT_TOKENS": "100",
        "TOKEN_USD_PER_1K_INPUT": "0.1",
        "TOKEN_USD_PER_1K_OUTPUT": "0.2",
    }

    output = render_dashboard("Brent/WTI bounce?", env=env, color=False)

    assert "OK  Vertex AI Gemini auth" in output
    assert "OK  Cheap model: gemini-3.5-flash" in output
    assert "OK  Strong model: gemini-3.5-flash" in output
    assert "OK  Verifier model: gemini-3.5-flash" in output
    assert "OK  Gemini multimodal embeddings" in output
    assert "OK  Model Armor" in output
    assert "OK  BigQuery analytics: adk_agent_analytics" in output
    assert "OK  MCP research tools" in output
    assert "OK  Trader source layer" in output
    assert "token_cost_est_usd: 0.020400" in output


def test_dashboard_uses_green_yellow_red_ansi_markers_when_color_enabled() -> None:
    env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "grant-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
        "MUTUAL_SPEC_CHEAP_MODEL": "gemini-3.5-flash",
    }

    output = render_dashboard("arb?", env=env, color=True)

    assert "\033[32mOK\033[0m" in output
    assert "\033[33mSKIP\033[0m" in output
    assert "\033[31mOFF\033[0m" in output


def test_dashboard_requires_opoint_key_for_opoint_mcp() -> None:
    output = render_dashboard(
        "test",
        env={"MCP_RESEARCH_COMMAND": "uv run opoint-mcp"},
        color=False,
    )

    assert "OFF  MCP research tools" in output

    output_with_key = render_dashboard(
        "test",
        env={
            "MCP_RESEARCH_COMMAND": "uv run opoint-mcp",
            "OPOINT_API_KEY": "key",
        },
        color=False,
    )

    assert "OK  MCP research tools" in output_with_key


def test_dashboard_marks_live_telemetry_collectors_when_configured() -> None:
    output = render_dashboard(
        "route model and region",
        env={
            "GOOGLE_CLOUD_PROJECT": "zenpulsar",
            "RESOURCE_REGION_DOMINATION_ENABLED": "true",
            "RESOURCE_TELEMETRY_COLLECTORS_ENABLED": "true",
            "TELEMETRY_DATASET_ID": "telemetry",
            "GCP_ASSET_CHANGES_SUBSCRIPTION": "gcp-all-resource-changes-sub",
        },
        color=False,
    )

    assert "OK  Resource-region domination telemetry" in output
    assert "OK  Live resource telemetry collectors" in output


def test_estimate_spend_uses_env_coefficients() -> None:
    spend = estimate_spend(
        "abcd",
        env={
            "TOKEN_USD_PER_1K_INPUT": "1",
            "TOKEN_USD_PER_1K_OUTPUT": "2",
            "ENERGY_WH_PER_1K_TOKENS": "1",
            "POWER_PRICE_USD_PER_MWH": "100",
            "TELEMETRY_PUE": "2",
        },
        output_tokens=9,
    )

    assert spend.input_tokens == 1
    assert spend.output_tokens == 9
    assert spend.total_tokens == 10
    assert spend.token_cost_usd == 0.019
    assert spend.estimated_kwh == 0.00002
    assert spend.electricity_cost_usd == 0.000002
