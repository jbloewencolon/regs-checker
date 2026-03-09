"""Dagster definitions — the entry point for dagster to discover assets and jobs."""

import dagster

from src.dagster_pipelines.assets import extracted_obligations, ingested_documents

defs = dagster.Definitions(
    assets=[ingested_documents, extracted_obligations],
    schedules=[
        dagster.ScheduleDefinition(
            name="daily_ingestion",
            cron_schedule="0 6 * * *",
            target=dagster.AssetSelection.keys("ingested_documents"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
    ],
)
