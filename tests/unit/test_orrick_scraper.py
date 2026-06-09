"""Legacy test stub — Orrick web scraper and PDF tracker both archived.

The web scraper (connector.py) and PDF tracker (pdf_tracker.py) have been
moved to src/ingestion/_archived/.  The active tracker ingestion path is
src/ingestion/orrick_facts_parser.py (Orrick enrichment from static data).
"""


class TestOrrickScraperRemoved:
    def test_archived_modules_not_on_active_path(self):
        """Confirm archived modules are not importable from the active path."""
        import importlib
        for mod in ("src.ingestion.connector", "src.ingestion.pdf_tracker"):
            spec = importlib.util.find_spec(mod)
            assert spec is None, f"{mod} should not be importable from the active path"
