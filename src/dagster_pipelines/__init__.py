"""Dagster pipeline definitions for the regs-checker platform.

Asset-based lineage model for orchestrating:
1. Ingestion: fetch → parse → normalize
2. Extraction: run 4 consolidated agents per document
3. Review: route to human review queue based on confidence tier
4. Serving: refresh materialized views on approval
"""
