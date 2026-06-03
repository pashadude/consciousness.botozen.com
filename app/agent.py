"""ADK app entrypoint for `adk web` and Agents CLI."""

from __future__ import annotations

import logging
import os

from google.adk.apps import App

from app.workflow import build_workflow

root_agent = build_workflow()


def build_observability_plugins() -> list[object]:
    """Enable optional BigQuery Agent Analytics when env vars are present."""

    if os.environ.get("BQ_ANALYTICS_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return []
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logging.warning("BQ_ANALYTICS_ENABLED is set but GOOGLE_CLOUD_PROJECT is missing.")
        return []
    try:
        from google.adk.plugins.bigquery_agent_analytics_plugin import (
            BigQueryAgentAnalyticsPlugin,
            BigQueryLoggerConfig,
        )
        from google.cloud import bigquery

        dataset_id = os.environ.get("BQ_ANALYTICS_DATASET_ID", "adk_agent_analytics")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east1")
        bigquery.Client(project=project_id).create_dataset(
            f"{project_id}.{dataset_id}",
            exists_ok=True,
        )
        return [
            BigQueryAgentAnalyticsPlugin(
                project_id=project_id,
                dataset_id=dataset_id,
                location=location,
                config=BigQueryLoggerConfig(
                    gcs_bucket_name=os.environ.get("BQ_ANALYTICS_GCS_BUCKET"),
                    connection_id=os.environ.get("BQ_ANALYTICS_CONNECTION_ID"),
                ),
            )
        ]
    except Exception as exc:  # pragma: no cover - depends on local GCP auth
        logging.warning("Failed to initialize BigQuery Agent Analytics: %s", exc)
        return []


app = App(
    name="mutual_spec_agent",
    root_agent=root_agent,
    plugins=build_observability_plugins(),
)
