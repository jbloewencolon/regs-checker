"""Unit tests for SFH-1f (audit SF-08) — pseudo-Orrick quarantine + key-drift check.

Two related trust-corruption defects:
(a) `--mode enrich-orrick` has an LLM write Orrick-style summaries from the
    same law text extraction reads; those summaries then entered the scoring
    path indistinguishable from real law-firm data — an LLM validating an LLM
    on the same text, worth 50% of the confidence score.
(b) Tracker metadata keys drift silently: 'enforcement' vs
    'enforcement_penalties' was "silently wrong for months" — a missing key
    reads as "no Orrick data" with no error anywhere.
"""

from __future__ import annotations

from src.core.orrick_validation import (
    TRACKER_METADATA_KNOWN_KEYS,
    find_suspicious_tracker_keys,
    validate_extraction_against_orrick,
)

_PAYLOAD = {"subject": "developer", "action": "conduct impact assessment"}


class TestPseudoOrrickQuarantine:
    def test_generated_orrick_scores_as_tracker_absent(self):
        # The core fix: llm_generated provenance → None (tracker-absent),
        # exactly as if no Orrick data existed. No LLM-validates-LLM scoring.
        ctx = {
            "key_requirements": "developers must conduct impact assessments",
            "enforcement_summary": "AG enforcement, $20k per violation",
            "orrick_source": "llm_generated",
        }
        assert validate_extraction_against_orrick(_PAYLOAD, ctx) is None

    def test_real_orrick_still_scores(self):
        ctx = {
            "key_requirements": "developers must conduct impact assessments",
        }
        result = validate_extraction_against_orrick(_PAYLOAD, ctx)
        assert result is not None
        assert result.has_orrick_data is True

    def test_explicit_real_source_still_scores(self):
        # A future provenance stamp for genuine tracker data must not trip
        # the quarantine — only 'llm_generated' does.
        ctx = {
            "key_requirements": "impact assessments required",
            "orrick_source": "orrick_tracker_pdf",
        }
        assert validate_extraction_against_orrick(_PAYLOAD, ctx) is not None

    def test_no_orrick_data_still_none(self):
        assert validate_extraction_against_orrick(_PAYLOAD, {}) is None


class TestFindSuspiciousTrackerKeys:
    def test_canonical_keys_are_clean(self):
        meta = {
            "bill_id": "SB 205",
            "key_requirements": "x",
            "enforcement_penalties": "y",
            "orrick_source": "llm_generated",
            "iapp_status": "enacted",
        }
        assert find_suspicious_tracker_keys(meta) == []

    def test_the_historical_rename_is_flagged(self):
        # The exact drift that was "silently wrong for months": data written
        # under 'enforcement' reads as tracker-absent downstream.
        meta = {"bill_id": "SB 205", "enforcement": "AG enforces, $20k"}
        assert find_suspicious_tracker_keys(meta) == ["enforcement"]

    def test_orrick_variant_key_flagged(self):
        meta = {"orrick_key_reqs": "x"}
        assert find_suspicious_tracker_keys(meta) == ["orrick_key_reqs"]

    def test_non_tracker_keys_ignored(self):
        # metadata_ legitimately carries non-tracker keys — not our business.
        meta = {"amendment_markup_detected": True, "parse_notes": "ok"}
        assert find_suspicious_tracker_keys(meta) == []

    def test_empty_and_none(self):
        assert find_suspicious_tracker_keys(None) == []
        assert find_suspicious_tracker_keys({}) == []

    def test_known_keys_constant_covers_writer_side(self):
        # Guard: the canonical set must contain the keys _build_context reads,
        # or the checker would flag legitimate data.
        for key in ("key_requirements", "enforcement_penalties", "orrick_summary",
                    "ai_scope_summary", "orrick_source"):
            assert key in TRACKER_METADATA_KNOWN_KEYS
