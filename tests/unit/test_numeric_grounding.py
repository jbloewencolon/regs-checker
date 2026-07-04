"""Unit tests for src/core/numeric_grounding.py (EA2-1).

Deterministic numeric-field grounding: typed numeric fields
(max_civil_penalty_usd, cure_period_days, etc.) are LLM-derived integers
with no rule-based check today — a model could attach a fabricated number
to a genuinely-quoted, evidence-verified span and evidence-grounding would
score it as fully verified. This module cross-checks the payload's numeric
value against numbers actually present in the payload's verified evidence
spans.
"""

from __future__ import annotations

from src.core.numeric_grounding import (
    check_numeric_grounding,
    extract_candidates,
    has_numeric_mismatch,
)


def _span(text: str, verified: bool = True, field_name: str = "x") -> dict:
    return {"field_name": field_name, "text": text, "verified": verified}


class TestExtractCandidatesUSD:
    def test_dollar_sign_amount(self):
        assert extract_candidates("a penalty not to exceed $10,000", "usd") == [10000.0]

    def test_dollar_sign_with_cents(self):
        assert extract_candidates("a fine of $2,500.50", "usd") == [2500.5]

    def test_bare_dollars_word_requires_four_digits(self):
        # "5 dollars" is not a realistic penalty amount; the 4-digit floor
        # avoids false positives on incidental small dollar mentions.
        assert extract_candidates("pay 5 dollars in postage", "usd") == []
        assert extract_candidates("a fine of 25000 dollars", "usd") == [25000.0]

    def test_no_dollar_amount_returns_empty(self):
        assert extract_candidates("the developer shall comply", "usd") == []


class TestExtractCandidatesDays:
    def test_simple_days(self):
        assert extract_candidates("within 30 days of notice", "days") == [30.0]

    def test_calendar_days(self):
        assert extract_candidates("within 60 calendar days", "days") == [60.0]

    def test_business_days(self):
        assert extract_candidates("within 10 business days", "days") == [10.0]

    def test_multiple_day_mentions(self):
        assert extract_candidates("30 days to cure, else 90 days to appeal", "days") == [30.0, 90.0]


class TestExtractCandidatesHours:
    def test_simple_hours(self):
        assert extract_candidates("within 72 hours of discovery", "hours") == [72.0]

    def test_hyphenated_hours(self):
        assert extract_candidates("a 24-hour reporting window", "hours") == [24.0]


class TestExtractCandidatesMonths:
    def test_simple_months(self):
        assert extract_candidates("retained for 12 months", "months") == [12.0]

    def test_years_convert_to_months(self):
        assert extract_candidates("retained for a period of 3 years", "months") == [36.0]

    def test_months_and_years_both_captured(self):
        result = extract_candidates("6 months, or up to 2 years in some cases", "months")
        assert 6.0 in result
        assert 24.0 in result


class TestExtractCandidatesCount:
    def test_simple_count(self):
        assert extract_candidates("fewer than 50 employees", "count") == [50.0]

    def test_count_with_commas(self):
        assert extract_candidates("processes data of 100,000 consumers", "count") == [100000.0]

    def test_excludes_dollar_amounts(self):
        # The count family must not accidentally pick up a dollar figure.
        assert extract_candidates("annual revenue of $25,000,000", "count") == []


class TestExtractCandidatesFlops:
    def test_caret_notation(self):
        assert extract_candidates("greater than 10^26 operations", "flops") == [1e26]

    def test_caret_notation_does_not_multiply_by_base(self):
        # Regression: 10^26 must equal 1e26, not 10 * 1e26 (1e27).
        result = extract_candidates("training compute greater than 10^25 flops", "flops")
        assert result == [1e25]

    def test_scientific_notation(self):
        assert extract_candidates("1e26 floating point operations", "flops") == [1e26]

    def test_explicit_multiplication_notation(self):
        # The caret sub-pattern also fires on the embedded "10^26" (yielding
        # a spurious extra 1e26 candidate) in addition to the correct
        # multiplication reading (1e27). This is intentional over-inclusion:
        # an extra candidate can only cause a false "grounded" (safe
        # direction), never a false "mismatch" — see module docstring.
        result = extract_candidates("10 x 10^26 operations", "flops")
        assert 1e27 in result


class TestCheckNumericGroundingTopLevelField:
    def test_grounded_when_value_matches_evidence(self):
        payload = {"revenue_threshold_usd": 25000000}
        spans = [_span("annual gross revenue in excess of $25,000,000")]
        results = check_numeric_grounding(payload, spans)
        assert results["revenue_threshold_usd"].status == "grounded"
        assert not has_numeric_mismatch(results)

    def test_mismatch_when_value_contradicts_evidence(self):
        # Model quoted the right sentence but attached the wrong number.
        payload = {"revenue_threshold_usd": 50000000}
        spans = [_span("annual gross revenue in excess of $25,000,000")]
        results = check_numeric_grounding(payload, spans)
        assert results["revenue_threshold_usd"].status == "mismatch"
        assert has_numeric_mismatch(results)
        assert results["revenue_threshold_usd"].candidates_found == [25000000.0]

    def test_unverifiable_when_no_numbers_in_evidence(self):
        payload = {"employee_threshold": 50}
        spans = [_span("small businesses are exempt from this section")]
        results = check_numeric_grounding(payload, spans)
        assert results["employee_threshold"].status == "unverifiable"
        assert not has_numeric_mismatch(results)

    def test_field_absent_from_payload_is_not_reported(self):
        payload = {"revenue_threshold_usd": None}
        spans = [_span("some text")]
        results = check_numeric_grounding(payload, spans)
        assert "revenue_threshold_usd" not in results

    def test_only_verified_spans_count_as_evidence(self):
        # An unverified span (hallucinated quote, string didn't appear in
        # passage) must not corroborate a numeric value.
        payload = {"employee_threshold": 50}
        spans = [_span("fewer than 50 employees", verified=False)]
        results = check_numeric_grounding(payload, spans)
        assert results["employee_threshold"].status == "unverifiable"


class TestCheckNumericGroundingNestedField:
    def test_enforcement_nested_penalty_grounded(self):
        payload = {
            "subject": "developer",
            "enforcement": {"max_civil_penalty_usd": 10000, "cure_period_days": 60},
        }
        spans = [
            _span("a civil penalty not to exceed $10,000 per violation"),
            _span("the developer shall have 60 days to cure the violation"),
        ]
        results = check_numeric_grounding(payload, spans)
        assert results["max_civil_penalty_usd"].status == "grounded"
        assert results["cure_period_days"].status == "grounded"

    def test_enforcement_nested_penalty_mismatch(self):
        payload = {
            "subject": "developer",
            "enforcement": {"max_civil_penalty_usd": 99999, "cure_period_days": None},
        }
        spans = [_span("a civil penalty not to exceed $10,000 per violation")]
        results = check_numeric_grounding(payload, spans)
        assert results["max_civil_penalty_usd"].status == "mismatch"
        # cure_period_days is null in the payload -> not reported at all
        assert "cure_period_days" not in results

    def test_missing_enforcement_object_is_safe(self):
        payload = {"subject": "developer", "enforcement": None}
        spans = [_span("a civil penalty not to exceed $10,000 per violation")]
        results = check_numeric_grounding(payload, spans)
        assert "max_civil_penalty_usd" not in results
        assert "cure_period_days" not in results


class TestCheckNumericGroundingMultipleCandidates:
    def test_grounded_if_any_candidate_matches(self):
        # Passage discusses a 30-day cure period and separately a 90-day
        # appeal window; the payload's cure_period_days=30 should still
        # ground even though 90 also appears.
        payload = {"enforcement": {"cure_period_days": 30}}
        spans = [_span("30 days to cure the violation, or 90 days to appeal")]
        results = check_numeric_grounding(payload, spans)
        assert results["cure_period_days"].status == "grounded"


class TestFlopsRelativeTolerance:
    def test_exact_match_grounded(self):
        payload = {"compute_flops": 1e26}
        spans = [_span("greater than 10^26 integer or floating-point operations")]
        results = check_numeric_grounding(payload, spans)
        assert results["compute_flops"].status == "grounded"

    def test_far_off_value_is_mismatch(self):
        payload = {"compute_flops": 1e24}
        spans = [_span("greater than 10^26 integer or floating-point operations")]
        results = check_numeric_grounding(payload, spans)
        assert results["compute_flops"].status == "mismatch"


class TestHasNumericMismatch:
    def test_false_when_all_grounded_or_unverifiable(self):
        results = check_numeric_grounding(
            {"employee_threshold": 50, "revenue_threshold_usd": None},
            [_span("fewer than 50 employees")],
        )
        assert has_numeric_mismatch(results) is False

    def test_true_when_any_field_mismatches(self):
        results = check_numeric_grounding(
            {"employee_threshold": 999, "consumer_data_threshold": 100000},
            [
                _span("fewer than 50 employees"),
                _span("processes data of 100,000 consumers"),
            ],
        )
        assert has_numeric_mismatch(results) is True
