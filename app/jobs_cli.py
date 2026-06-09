"""Terminal helper for local async specification jobs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app.jobs import complete_async_job, list_pending_jobs, read_jobs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect local async spec jobs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List queued and completed jobs.")
    list_parser.add_argument("--pending", action="store_true", help="Show only queued jobs.")
    list_parser.add_argument("--store-path", help="Override ASYNC_JOB_STORE_PATH.")

    complete_parser = subparsers.add_parser("complete", help="Mark a local job complete.")
    complete_parser.add_argument("job_id")
    complete_parser.add_argument("--result-ref", required=True)
    complete_parser.add_argument("--summary", default="")
    complete_parser.add_argument("--store-path", help="Override ASYNC_JOB_STORE_PATH.")

    args = parser.parse_args(argv)
    if args.command == "list":
        jobs = (
            list_pending_jobs(store_path=args.store_path)
            if args.pending
            else read_jobs(store_path=args.store_path)
        )
        if not jobs:
            print("No async jobs.")
            return 0
        for job in jobs:
            reasons = ", ".join(job.reasons) or "none"
            print(
                f"{job.job_id}  {job.status}  {job.kind}  "
                f"risk={job.risk_score}  reasons={reasons}"
            )
        return 0

    if args.command == "complete":
        job = complete_async_job(
            args.job_id,
            {"result_ref": args.result_ref, "summary": args.summary},
            store_path=args.store_path,
        )
        print(f"{job.job_id}  {job.status}  result_ref={job.result_ref}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
