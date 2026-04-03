"""Tests for _repair_truncated_json() in src/agents/base.py.

This function attempts to salvage truncated JSON from LLM output using two strategies:
  Strategy 1: Find the last complete array element at depth-1 (only works when the
              root IS an array; truncates to last element followed by ','). Note this
              is also triggered on already-complete top-level arrays, dropping the last
              element — known trade-off for truncation safety.
  Strategy 2: Fallback — close all open brackets. Result may still be invalid if
              truncation happened mid-value.

Note: test_json_repair.py covers _repair_json() (the higher-level repair).
This file covers _repair_truncated_json() specifically.
"""

import json

import pytest

from src.agents.base import _repair_truncated_json


class TestCompleteJSON:
    """Already-complete or well-formed JSON behavior."""

    def test_complete_object_unchanged(self):
        """A complete standalone object passes through unchanged."""
        text = '{"key": "value"}'
        result = _repair_truncated_json(text)
        assert json.loads(result) == {"key": "value"}

    def test_complete_nested_object_unchanged(self):
        """A complete nested object passes through unchanged."""
        text = '{"extractions": [{"type": "obligation", "data": {"subject": "dev"}}]}'
        result = _repair_truncated_json(text)
        parsed = json.loads(result)
        assert parsed["extractions"][0]["type"] == "obligation"

    def test_complete_top_level_array_drops_last_element(self):
        """Known behavior: Strategy 1 fires on top-level arrays, dropping the last element.

        When the root is an array, Strategy 1 finds depth-1 '}' followed by ','
        and truncates there. The last element (with no trailing ',') is dropped.
        This is a trade-off: on a REAL truncated array this is correct; on a complete
        array it's overly aggressive.
        """
        text = '[{"a": 1}, {"b": 2}]'
        result = _repair_truncated_json(text)
        parsed = json.loads(result)
        # Only the first element survives — expected behavior for this function
        assert parsed == [{"a": 1}]

    def test_empty_string(self):
        result = _repair_truncated_json("")
        assert result == ""

    def test_non_json_passthrough(self):
        """Non-JSON text (doesn't start with { or [) passes through unchanged."""
        result = _repair_truncated_json("just plain text")
        assert result == "just plain text"

    def test_whitespace_only(self):
        result = _repair_truncated_json("   ")
        assert result.strip() == ""


class TestFallbackRepair:
    """Strategy 2 fallback: closes open brackets without truncating.
    Result is parseable only when truncation was outside a value.
    """

    def test_truncated_nested_object_closed(self):
        """Truncated inside nested object — fallback closes brackets."""
        text = '{"key": "value", "nested": {"inner": true'
        result = _repair_truncated_json(text)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["nested"]["inner"] is True

    def test_just_opening_brace(self):
        """Bare { is closed to {}."""
        result = _repair_truncated_json("{")
        assert result == "{}"
        assert json.loads(result) == {}

    def test_just_opening_bracket(self):
        """Bare [ is closed to []."""
        result = _repair_truncated_json("[")
        assert result == "[]"
        assert json.loads(result) == []

    def test_already_balanced_passthrough(self):
        """Already balanced brackets pass through (strategy 2 stack empties)."""
        text = '{"a": [1, 2, 3]}'
        result = _repair_truncated_json(text)
        assert json.loads(result) == {"a": [1, 2, 3]}


class TestTruncatedMidValue:
    """When truncated mid-value, repair closes brackets but result may not be parseable.
    These tests verify structural properties rather than full parseability.
    """

    def test_array_truncated_mid_value_brackets_balanced(self):
        """Array truncated mid-value: brackets are closed even if content invalid."""
        text = '[{"a": 1, "b":'
        result = _repair_truncated_json(text)
        # Brackets are balanced (equal open/close counts for each type)
        assert result.count("[") == result.count("]")
        assert result.count("{") == result.count("}")

    def test_truncated_mid_string_brackets_balanced(self):
        """Truncated inside a string value: brackets are balanced."""
        text = '{"extractions": [{"text": "trunc'
        result = _repair_truncated_json(text)
        assert result.count("[") == result.count("]")
        assert result.count("{") == result.count("}")

    def test_brackets_in_string_values_not_confused(self):
        """Brackets inside completed strings are not counted as structural."""
        # The string value "has {brackets} and [arrays]" is complete.
        # Only "next": is truncated, so fallback closes one {.
        text = '{"text": "has {brackets} and [arrays]", "next":'
        result = _repair_truncated_json(text)
        # The string value with brackets should be preserved intact
        assert '"has {brackets} and [arrays]"' in result
        # Structural brackets are balanced
        # Outer { is the only structural open bracket
        assert result.endswith("}")

    def test_escaped_quotes_handled(self):
        """Escaped quotes inside strings don't confuse the string tracking."""
        text = '{"text": "has \\"escaped\\" quotes"}'
        result = _repair_truncated_json(text)
        parsed = json.loads(result)
        assert "escaped" in parsed["text"]


class TestWrappedArrayTruncation:
    """The extraction pipeline wraps arrays: {"extractions": [...]}.
    Strategy 1 does NOT fire for this format (depth-1 '}' condition not met).
    Strategy 2 (fallback) runs instead.
    """

    def test_wrapped_array_truncated_mid_element_fallback(self):
        """Wrapped array truncated mid-element: strategy 2 fallback closes brackets."""
        text = '{"extractions": [{"a": 1}, {"b": 2, "c":'
        result = _repair_truncated_json(text)
        # Fallback closes {, [, { → appends }]}
        assert result.count("[") == result.count("]")
        assert result.count("{") == result.count("}")

    def test_complete_wrapped_array_unchanged(self):
        """Complete wrapped array stays intact (strategy 1 never fires at depth > 1)."""
        text = '{"extractions": [{"a": 1}, {"b": 2}]}'
        result = _repair_truncated_json(text)
        parsed = json.loads(result)
        # Both elements survive because strategy 1 doesn't touch non-root arrays
        assert len(parsed["extractions"]) == 2
