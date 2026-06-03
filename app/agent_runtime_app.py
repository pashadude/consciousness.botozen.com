"""Agent Runtime wrapper used by deploy/deploy_runtime.py and Agents CLI."""

from __future__ import annotations

import logging
import os
from typing import Any

import vertexai
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app


class MutualSpecAgentRuntimeApp(AdkApp):
    def set_up(self) -> None:
        vertexai.init(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east1"),
        )
        os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        self.logger = cloud_logging.Client().logger("mutual-spec-agent")

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        self.logger.log_struct({"event": "feedback", **feedback}, severity="INFO")

    def register_operations(self) -> dict[str, list[str]]:
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations


def build_artifact_service():
    bucket = os.environ.get("ARTIFACTS_GCS_BUCKET") or os.environ.get("LOGS_BUCKET_NAME")
    if bucket:
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


agent_runtime = MutualSpecAgentRuntimeApp(
    app=adk_app,
    artifact_service_builder=build_artifact_service,
)
