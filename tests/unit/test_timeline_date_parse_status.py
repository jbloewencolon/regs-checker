"""Unit tests for EA6-5 — TimelineInfo.date_parse_status.

Bug: TimelineInfo's date-field validator does `normalize_date(v) or v` — when
parsing fails, the RAW model text passes through silently. ISO-8601 dates and
arbitrary free text ("the first day of the next legislative session") ended
up indistinguishable in the same column, with nothing downstream able to tell
which case occurred.

Fix: a model_validator(mode="after") classifies each populated date field as
"parsed" (matches YYYY-MM-DD — normalize_date succeeded) or "unparsed" (raw
text passed through) in a new date_parse_status dict. Fields never populated
by the model are absent from the dict entirely.
"""

from __future__ import annotations

from src.schemas.extraction import TimelineInfo


class TestDateParseStatus:
    def test_iso_input_parses_and_flags_parsed(self):
        tl = TimelineInfo(effective_date="2026-01-01")
        assert tl.effective_date == "2026-01-01"
        assert tl.date_parse_status["effective_date"] == "parsed"

    def test_named_month_date_normalizes_and_flags_parsed(self):
        tl = TimelineInfo(effective_date="January 1, 2026")
        assert tl.effective_date == "2026-01-01"
        assert tl.date_parse_status["effective_date"] == "parsed"

    def test_unparseable_text_passes_through_and_flags_unparsed(self):
        tl = TimelineInfo(effective_date="the first day of the next legislative session")
        assert tl.effective_date == "the first day of the next legislative session"
        assert tl.date_parse_status["effective_date"] == "unparsed"

    def test_null_field_absent_from_status(self):
        tl = TimelineInfo(effective_date=None)
        assert "effective_date" not in tl.date_parse_status

    def test_field_never_supplied_absent_from_status(self):
        tl = TimelineInfo()
        assert tl.date_parse_status == {}

    def test_empty_string_absent_from_status(self):
        tl = TimelineInfo(effective_date="   ")
        assert "effective_date" not in tl.date_parse_status

    def test_all_three_date_fields_tracked_independently(self):
        tl = TimelineInfo(
            effective_date="2026-01-01",
            compliance_deadline="within 90 days of enactment",
            sunset_date="2030-12-31",
        )
        assert tl.date_parse_status == {
            "effective_date": "parsed",
            "compliance_deadline": "unparsed",
            "sunset_date": "parsed",
        }

    def test_phase_in_period_and_timeline_text_not_tracked(self):
        # Only the three normalize_date-backed fields are in scope.
        tl = TimelineInfo(
            phase_in_period="6 months after enactment",
            timeline_text="This section takes effect in stages.",
        )
        assert tl.date_parse_status == {}

    def test_model_dump_includes_date_parse_status(self):
        tl = TimelineInfo(effective_date="2026-01-01")
        dumped = tl.model_dump()
        assert dumped["date_parse_status"] == {"effective_date": "parsed"}
