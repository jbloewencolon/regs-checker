"""Legacy test stub — Orrick web scraper replaced by PDF tracker.

The web scraper (orrick_scraper.py) has been replaced by pdf_tracker.py.
See test_pdf_tracker.py for current tests.
"""

import pytest


class TestOrrickScraperRemoved:
    def test_pdf_tracker_is_replacement(self):
        """Verify the PDF tracker module exists as replacement."""
        from src.ingestion.pdf_tracker import parse_tracker_pdf, seed_from_tracker
        assert callable(parse_tracker_pdf)
        assert callable(seed_from_tracker)
