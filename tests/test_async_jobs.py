from app.formalization import formalize_ledger
from app.jobs import complete_async_job, enqueue_async_job, list_pending_jobs, read_jobs
from app.jobs_cli import main as jobs_cli_main
from app.router import async_jobs_enabled, route_async_decision
from app.spec_state import SpecLedger, update_ledger_from_user_text


def test_async_route_decision_escalates_trader_risk_prompt() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    formalize_ledger(ledger)

    decision = route_async_decision(
        ledger,
        mcp_configured=True,
        telemetry_enabled=True,
    )

    assert decision.mode == "async"
    assert decision.job_kind == "deep_research_and_verification"
    assert decision.expected_spec_gain == "high"
    assert "high_stakes_trader_decision_frame" in decision.reasons
    assert "external_mcp_research_available" in decision.reasons
    assert "resource_region_telemetry_available" in decision.reasons


def test_async_route_decision_keeps_material_clarification_sync() -> None:
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "Make this better")

    decision = route_async_decision(ledger, mcp_configured=True)

    assert decision.mode == "sync"
    assert decision.job_kind == "clarification"
    assert decision.reasons == ("material_spec_fields_missing",)


def test_async_jobs_enabled_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("ASYNC_JOB_ENABLED", raising=False)
    assert not async_jobs_enabled()

    monkeypatch.setenv("ASYNC_JOB_ENABLED", "true")
    assert async_jobs_enabled()


def test_enqueue_and_complete_async_job(tmp_path) -> None:
    store_path = tmp_path / "jobs.jsonl"
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    formalize_ledger(ledger)
    decision = route_async_decision(ledger, mcp_configured=True)

    job = enqueue_async_job(ledger, decision, store_path=store_path)

    assert job.job_id
    assert job.status == "queued"
    assert job.payload["expressed_query"] == "look at HO/RB arb and give me risk on this spread"
    assert ledger.async_jobs[0].job_id == job.job_id
    assert list_pending_jobs(store_path=store_path)[0].job_id == job.job_id

    completed = complete_async_job(
        job.job_id,
        {"result_ref": "local://result/1", "summary": "done"},
        store_path=store_path,
    )

    assert completed.status == "succeeded"
    assert completed.result_ref == "local://result/1"
    assert read_jobs(store_path=store_path)[0].status == "succeeded"
    assert list_pending_jobs(store_path=store_path) == []


def test_jobs_cli_lists_jobs(tmp_path, capsys) -> None:
    store_path = tmp_path / "jobs.jsonl"
    ledger = SpecLedger()
    update_ledger_from_user_text(ledger, "look at HO/RB arb and give me risk on this spread")
    formalize_ledger(ledger)
    job = enqueue_async_job(
        ledger,
        route_async_decision(ledger, mcp_configured=True),
        store_path=store_path,
    )

    exit_code = jobs_cli_main(["list", "--store-path", str(store_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert job.job_id in output
    assert "deep_research_and_verification" in output
