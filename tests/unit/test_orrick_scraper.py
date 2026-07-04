"""Legacy test stub — Orrick web scraper and PDF tracker off the active path.

The web scraper (connector.py) was deleted with src/ingestion/_archived/
(RC3-3; retrievable from git history). The PDF tracker (pdf_tracker.py)
lives in src/ingestion/legacy/ — old but still used by dashboard and
seed_pipeline. The active tracker ingestion path is
src/ingestion/orrick_facts_parser.py (Orrick enrichment from static data).
"""


class TestOrrickScraperRemoved:
    def test_archived_modules_not_on_active_path(self):
        """Confirm archived modules are not importable from the active path."""
        import importlib
        for mod in ("src.ingestion.connector", "src.ingestion.pdf_tracker"):
            spec = importlib.util.find_spec(mod)
            assert spec is None, f"{mod} should not be importable from the active path"
