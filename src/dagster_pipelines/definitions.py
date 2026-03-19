"""Dagster definitions — the entry point for dagster to discover assets, jobs, and schedules."""

import dagster

from src.dagster_pipelines.assets import (
    bill_status_check,
    bridge_gap_report,
    discovered_legislation,
    extracted_obligations,
    ingested_documents,
    synced_extractions,
)
from src.dagster_pipelines.jobs import orrick_discovery_job

defs = dagster.Definitions(
    assets=[discovered_legislation, ingested_documents, extracted_obligations, synced_extractions, bridge_gap_report, bill_status_check],
    jobs=[orrick_discovery_job],
    schedules=[
        # Daily ingestion: process any pending ingestion jobs at 6 AM UTC
        dagster.ScheduleDefinition(
            name="daily_ingestion",
            cron_schedule="0 6 * * *",
            target=dagster.AssetSelection.keys("ingested_documents"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Weekly extraction: run agents on unprocessed passages every Monday 7 AM UTC
        dagster.ScheduleDefinition(
            name="weekly_extraction",
            cron_schedule="0 7 * * 1",
            target=dagster.AssetSelection.keys("extracted_obligations"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Daily sync: push new extractions to Policy Navigator at 8 AM UTC
        dagster.ScheduleDefinition(
            name="daily_sync",
            cron_schedule="0 8 * * *",
            target=dagster.AssetSelection.keys("synced_extractions"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Weekly bridge gap check: detect unbridged families every Monday 9 AM UTC
        dagster.ScheduleDefinition(
            name="weekly_bridge_gap_check",
            cron_schedule="0 9 * * 1",
            target=dagster.AssetSelection.keys("bridge_gap_report"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Weekly AI bill classification: classify candidate URLs every Monday 5 AM UTC
        dagster.ScheduleDefinition(
            name="weekly_bill_classification",
            cron_schedule="0 5 * * 1",
            target=dagster.AssetSelection.keys("discovered_legislation"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Monthly discovery: scrape Orrick AI tracker on the 1st at midnight UTC
        dagster.ScheduleDefinition(
            name="monthly_orrick_discovery",
            cron_schedule="0 0 1 * *",
            target=orrick_discovery_job,
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
        # Weekly status check: cross-reference Orrick + IAPP every Wednesday 6 AM UTC
        dagster.ScheduleDefinition(
            name="weekly_status_check",
            cron_schedule="0 6 * * 3",
            target=dagster.AssetSelection.keys("bill_status_check"),
            default_status=dagster.DefaultScheduleStatus.STOPPED,
        ),
    ],
)
