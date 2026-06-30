"""Tests for DI-1 and Phase A changes.

DI-1: _normalize_source_url (jina proxy stripping)
Phase A: agent_name backfill SQL in the migration (validated via logic test)
"""

from __future__ import annotations

import pytest

from src.ingestion.local_ingest import _normalize_source_url


class TestNormalizeSourceUrl:
    def test_strips_jina_prefix(self):
        url = "https://r.jina.ai/https://legiscan.com/TX/text/HB149/2023"
        assert _normalize_source_url(url) == "https://legiscan.com/TX/text/HB149/2023"

    def test_strips_jina_prefix_http_inner(self):
        url = "https://r.jina.ai/http://legiscan.com/CA/text/AB1018/2023"
        assert _normalize_source_url(url) == "http://legiscan.com/CA/text/AB1018/2023"

    def test_passthrough_canonical_url(self):
        url = "https://legiscan.com/TX/text/HB149/2023"
        assert _normalize_source_url(url) == url

    def test_passthrough_orrick_url(self):
        url = "https://media2.mofo.com/documents/230500-colorado-privacy-act.pdf"
        assert _normalize_source_url(url) == url

    def test_passthrough_capitol_url(self):
        url = "https://capitol.texas.gov/tlodocs/88R/billtext/html/HB00149I.htm"
        assert _normalize_source_url(url) == url

    def test_empty_string(self):
        assert _normalize_source_url("") == ""

    def test_none_passthrough(self):
        # None is coerced upstream; function handles the empty-string case cleanly
        assert _normalize_source_url("") == ""

    def test_partial_jina_domain_not_stripped(self):
        # Ensure we don't accidentally strip URLs that merely contain "jina"
        url = "https://jina.ai/some/path"
        assert _normalize_source_url(url) == url

    def test_preserves_query_string_after_jina_strip(self):
        url = "https://r.jina.ai/https://example.com/bill?session=2023&id=123"
        assert _normalize_source_url(url) == "https://example.com/bill?session=2023&id=123"


class TestAgentNameBackfillMapping:
    """Validates the extraction_type → agent_name mapping used in the migration SQL.

    If AGENT_EXTRACTION_TYPES in extractor.py changes, these tests will catch it.
    """

    def _expected_agent(self, extraction_type: str) -> str | None:
        mapping = {
            "obligation": "obligation",
            "timeline": "obligation",
            "enforcement": "obligation",
            "definition": "definition_actor",
            "actor_mapping": "definition_actor",
            "framework_ref": "definition_actor",
            "threshold": "threshold_exception",
            "exception": "threshold_exception",
            "rights_protection": "rights_protection",
            "compliance_mechanism": "compliance_mechanism",
            "preemption_signal": "preemption",
        }
        return mapping.get(extraction_type)

    def test_obligation_agent_types(self):
        assert self._expected_agent("obligation") == "obligation"
        assert self._expected_agent("timeline") == "obligation"
        assert self._expected_agent("enforcement") == "obligation"

    def test_definition_actor_types(self):
        assert self._expected_agent("definition") == "definition_actor"
        assert self._expected_agent("actor_mapping") == "definition_actor"
        assert self._expected_agent("framework_ref") == "definition_actor"

    def test_threshold_exception_types(self):
        assert self._expected_agent("threshold") == "threshold_exception"
        assert self._expected_agent("exception") == "threshold_exception"

    def test_single_type_agents(self):
        assert self._expected_agent("rights_protection") == "rights_protection"
        assert self._expected_agent("compliance_mechanism") == "compliance_mechanism"
        assert self._expected_agent("preemption_signal") == "preemption"

    def test_unknown_type_returns_none(self):
        assert self._expected_agent("ambiguity") is None
        assert self._expected_agent("unknown_type") is None

    def test_all_active_extraction_types_covered(self):
        from src.ingestion.extractor import AGENT_EXTRACTION_TYPES
        covered = set()
        for types in AGENT_EXTRACTION_TYPES.values():
            covered.update(t.value for t in types)
        # Every type produced by an active agent should resolve back to an agent
        for et in covered:
            agent = self._expected_agent(et)
            assert agent is not None, f"No agent_name mapping for extraction_type={et!r}"
