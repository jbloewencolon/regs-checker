"""Archived fetch-based ingestion modules.

These modules implemented URL-based document fetching from state legislature
websites. They have been superseded by the local file ingestion system in
src/ingestion/local_ingest.py, which reads pre-fetched documents from the
data/ and output/ directories.

Archived modules:
  - connector.py     — HTTP connectors (Colorado, NIST, Orrick tracker)
  - web_search.py    — Web search fallback for stale URLs
  - pdf_tracker.py   — Orrick PDF tracker parser + seeder
  - iapp_pdf_tracker.py — IAPP legislation tracker parser
"""
