"""Deploy mutual-spec-agent to Agent Runtime or optionally Cloud Run."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Sequence

import vertexai
from vertexai import agent_engines

from app.agent_runtime_app import agent_runtime

DEFAULT_REQUIREMENTS = [
    "google-adk>=2.0.0,<3.0.0",
    "google-cloud-aiplatform[agent-engines,evaluation]>=1.150.0",
    "google-cloud-logging>=3.12.0,<4.0.0",
    "python-dotenv>=1.0.1,<2.0.0",
    "pydantic>=2.10.0,<3.0.0",
]


def deploy_agent_runtime(project: str, region: str, display_name: str) -> object:
    vertexai.init(project=project, location=region)
    return agent_engines.create(
        agent_runtime,
        display_name=display_name,
        requirements=DEFAULT_REQUIREMENTS,
        extra_packages=["app"],
    )


def deploy_cloud_run(project: str, region: str, service_name: str) -> None:
    command = [
        "adk",
        "deploy",
        "cloud_run",
        "app",
        "--project",
        project,
        "--region",
        region,
        "--service_name",
        service_name,
    ]
    subprocess.run(command, check=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--region", default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east1"))
    parser.add_argument("--display-name", default="mutual-spec-agent")
    parser.add_argument(
        "--target",
        choices=("agent-runtime", "cloud-run"),
        default="agent-runtime",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.project:
        raise SystemExit("Set --project or GOOGLE_CLOUD_PROJECT.")
    if args.target == "cloud-run":
        deploy_cloud_run(args.project, args.region, args.display_name)
        return
    remote_app = deploy_agent_runtime(args.project, args.region, args.display_name)
    print(f"Deployed Agent Runtime app: {remote_app}")


if __name__ == "__main__":
    main()
