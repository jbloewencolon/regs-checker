"""Unit tests for BaseExtractionAgent._repair_json."""

from src.agents.base import BaseExtractionAgent


class TestRepairJson:
    repair = staticmethod(BaseExtractionAgent._repair_json)

    def test_valid_json_unchanged(self):
        text = '{"extractions": [{"a": 1}]}'
        assert self.repair(text) == text

    def test_extra_data_after_object(self):
        text = '{"detected": false, "reason": "none"}{"extra": true}'
        result = self.repair(text)
        import json
        parsed = json.loads(result)
        assert parsed == {"detected": False, "reason": "none"}

    def test_extra_data_after_array(self):
        text = '[{"a": 1}, {"b": 2}][{"c": 3}]'
        result = self.repair(text)
        import json
        parsed = json.loads(result)
        assert parsed == [{"a": 1}, {"b": 2}]

    def test_trailing_comma_in_array(self):
        text = '{"extractions": [{"a": 1}, {"b": 2},]}'
        result = self.repair(text)
        import json
        parsed = json.loads(result)
        assert len(parsed["extractions"]) == 2

    def test_trailing_comma_in_object(self):
        text = '{"a": 1, "b": 2,}'
        result = self.repair(text)
        import json
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_stringified_objects_in_extractions(self):
        """gpt-oss-20b wraps inner objects in quotes instead of embedding them."""
        import json
        inner1 = {"ambiguous_text": "foo", "severity": "low"}
        inner2 = {"ambiguous_text": "bar", "severity": "high"}
        text = json.dumps({
            "extractions": [
                inner1,                    # proper object
                json.dumps(inner2),        # stringified object (the bug)
            ]
        })
        result = self.repair(text)
        parsed = json.loads(result)
        assert len(parsed["extractions"]) == 2
        assert parsed["extractions"][0] == inner1
        assert parsed["extractions"][1] == inner2

    def test_empty_string(self):
        assert self.repair("") == ""

    def test_whitespace_only(self):
        assert self.repair("   ") == ""

    def test_non_json_passthrough(self):
        text = "This is not JSON at all"
        assert self.repair(text) == text

    def test_nested_braces_in_strings(self):
        """Ensure bracket matching handles braces inside string values."""
        text = '{"text": "contains {braces} and [brackets]"}extra'
        result = self.repair(text)
        import json
        parsed = json.loads(result)
        assert parsed["text"] == "contains {braces} and [brackets]"

    def test_real_world_ambiguity_error(self):
        """Simulate the actual gpt-oss-20b output pattern that caused failures."""
        import json
        # First object is proper, rest are stringified
        obj1 = {
            "ambiguous_text": "reasonable viewer",
            "ambiguity_type": "vague_term",
            "severity": "medium",
        }
        obj2 = {
            "ambiguous_text": "artificial intelligence",
            "ambiguity_type": "undefined_reference",
            "severity": "low",
        }
        text = json.dumps({
            "extractions": [
                obj1,
                json.dumps(obj2),  # Stringified — the bug pattern
            ]
        })
        result = self.repair(text)
        parsed = json.loads(result)
        assert len(parsed["extractions"]) == 2
        assert all(isinstance(e, dict) for e in parsed["extractions"])
