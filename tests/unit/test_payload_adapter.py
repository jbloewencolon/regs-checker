"""Tests for payload format adapter."""

from src.core.payload_adapter import adapt_payload_for_sync


class TestAdaptObligation:
    def test_flat_obligation(self):
        payload = {
            "subject": "developer",
            "subject_normalized": "developer",
            "modality": "shall",
            "action": "conduct impact assessment",
            "condition": "before deployment",
            "jurisdiction": "CO",
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["subject"] == "developer"
        assert result["modality"] == "shall"
        assert result["timeline"] is None
        assert result["enforcement"] is None

    def test_nested_timeline_flattened(self):
        payload = {
            "subject": "deployer",
            "modality": "must",
            "action": "notify consumers",
            "timeline": {
                "effective_date": "2026-02-01",
                "compliance_deadline": "2026-08-01",
            },
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert "Effective: 2026-02-01" in result["timeline"]
        assert "Deadline: 2026-08-01" in result["timeline"]

    def test_nested_enforcement_flattened(self):
        payload = {
            "subject": "developer",
            "modality": "shall",
            "action": "maintain records",
            "enforcement": {
                "enforcing_body": "Attorney General",
                "penalty_type": "civil",
                "private_right_of_action": True,
            },
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert "Attorney General" in result["enforcement"]
        assert "Private right of action: Yes" in result["enforcement"]


class TestAdaptThreshold:
    def test_basic_threshold(self):
        payload = {
            "threshold_type": "employee_count",
            "threshold_value": "50",
            "threshold_unit": "employees",
            "threshold_condition": "more than 50 employees",
        }
        result = adapt_payload_for_sync("threshold", payload)
        assert result["threshold_value"] == "50"
        assert result["exceptions"] is None

    def test_exceptions_flattened(self):
        payload = {
            "threshold_type": "revenue",
            "threshold_value": "25000000",
            "exceptions": [
                {"exception_type": "carve-out", "description": "Small businesses exempt"},
                {"exception_type": "safe-harbor", "description": "Research purposes"},
            ],
        }
        result = adapt_payload_for_sync("threshold", payload)
        assert "Small businesses exempt" in result["exceptions"]
        assert "Research purposes" in result["exceptions"]


class TestAdaptDefinition:
    def test_basic_definition(self):
        payload = {
            "term": "artificial intelligence system",
            "definition_text": "means any machine-based system...",
            "scope": "This title",
        }
        result = adapt_payload_for_sync("definition", payload)
        assert result["term"] == "artificial intelligence system"
        assert result["actors"] is None
        assert result["framework_refs"] is None

    def test_actors_flattened(self):
        payload = {
            "term": "deployer",
            "definition_text": "means any person who deploys...",
            "actors": [
                {"actor_name": "deployer", "actor_type": "regulated_entity"},
            ],
        }
        result = adapt_payload_for_sync("definition", payload)
        assert "deployer (regulated_entity)" in result["actors"]

    def test_framework_refs_flattened(self):
        payload = {
            "term": "risk management",
            "definition_text": "means a process...",
            "framework_refs": [
                {"framework_name": "NIST AI RMF", "section_or_standard": "1.0"},
            ],
        }
        result = adapt_payload_for_sync("definition", payload)
        assert "NIST AI RMF (1.0)" in result["framework_refs"]


class TestAdaptAmbiguity:
    def test_all_keys_present(self):
        payload = {
            "ambiguous_text": "reasonable measures",
            "ambiguity_type": "vague_term",
            "severity": "medium",
        }
        result = adapt_payload_for_sync("ambiguity", payload)
        assert result["ambiguous_text"] == "reasonable measures"
        assert result["severity"] == "medium"
        assert result["affected_obligations"] is None
        assert result["interpretation_notes"] is None
        assert result["suggested_clarification"] is None


class TestUnknownType:
    def test_passthrough(self):
        payload = {"foo": "bar"}
        result = adapt_payload_for_sync("unknown_type", payload)
        assert result == payload
