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


class TestPNE1aObligationPassthrough:
    """PNE-1a: fields extracted+stored all along must survive the adapter.

    The adapters are whitelists; before PNE-1a these fields were silently
    stripped at sync time even though the pipeline extracts them.
    """

    def test_object_passes_through(self):
        # model_dump(by_alias=True) stores ObligationPayload.object_ as "object"
        payload = {"subject": "developer", "modality": "shall", "object": "training data"}
        result = adapt_payload_for_sync("obligation", payload)
        assert result["object"] == "training data"

    def test_safe_harbor_passes_through(self):
        payload = {
            "subject": "deployer",
            "modality": "must",
            "safe_harbor": {"harbor_type": "affirmative_defense", "description": "NIST RMF compliance"},
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["safe_harbor"]["harbor_type"] == "affirmative_defense"

    def test_consent_requirements_pass_through(self):
        payload = {
            "subject": "controller",
            "modality": "must",
            "consent_requirements": {"consent_type": "opt_in", "description": "explicit consent"},
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["consent_requirements"]["consent_type"] == "opt_in"

    def test_interpretation_risks_pass_through(self):
        # Ambiguity findings are embedded on the obligation row (DI-4);
        # stripping them here was the actual gap behind PN Ask 8.
        payload = {
            "subject": "developer",
            "modality": "shall",
            "interpretation_risks": [
                {"risk_type": "vague_term", "term": "promptly", "description": "no deadline given"}
            ],
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["interpretation_risks"][0]["term"] == "promptly"

    def test_preemption_signals_pass_through(self):
        payload = {
            "subject": "deployer",
            "modality": "must",
            "preemption_signals": ["notwithstanding any state law"],
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["preemption_signals"] == ["notwithstanding any state law"]

    def test_missing_fields_default_to_null_or_empty(self):
        payload = {"subject": "developer", "modality": "shall"}
        result = adapt_payload_for_sync("obligation", payload)
        assert result["object"] is None
        assert result["safe_harbor"] is None
        assert result["consent_requirements"] is None
        assert result["interpretation_risks"] == []
        assert result["preemption_signals"] == []

    def test_timeline_structured_ships_alongside_flattened_string(self):
        payload = {
            "subject": "deployer",
            "modality": "must",
            "timeline": {
                "effective_date": "2026-02-01",
                "compliance_deadline": "upon the commissioner's determination",
                "date_parse_status": {
                    "effective_date": "parsed",
                    "compliance_deadline": "unparsed",
                },
            },
        }
        result = adapt_payload_for_sync("obligation", payload)
        # Backward compat: flattened string unchanged
        assert "Effective: 2026-02-01" in result["timeline"]
        # New: structured object preserved, including date_parse_status so PN
        # can skip "unparsed" fields in date arithmetic
        assert result["timeline_structured"]["effective_date"] == "2026-02-01"
        assert result["timeline_structured"]["date_parse_status"]["compliance_deadline"] == "unparsed"

    def test_no_timeline_leaves_structured_null(self):
        payload = {"subject": "developer", "modality": "shall"}
        result = adapt_payload_for_sync("obligation", payload)
        assert result["timeline"] is None
        assert result["timeline_structured"] is None

    def test_string_timeline_keeps_structured_null(self):
        # Legacy rows where timeline was already a string: nothing to structure
        payload = {"subject": "developer", "modality": "shall", "timeline": "by 2027"}
        result = adapt_payload_for_sync("obligation", payload)
        assert result["timeline"] == "by 2027"
        assert result["timeline_structured"] is None


class TestPNE2ObligationDerivations:
    """PNE-2: derived PN fields wired into _adapt_obligation."""

    def test_actor_role_and_enforcement_authority_separate(self):
        payload = {
            "subject": "an employer",
            "subject_normalized": "deployer",
            "modality": "must",
            "action": "provide disclosure to employees",
            "enforcement": {"enforcing_body": "State Attorney General"},
        }
        result = adapt_payload_for_sync("obligation", payload)
        # Alias-aware PN role recovered from the raw term...
        assert result["actor_role_rc"] == "deployer"
        assert result["actor_role"] == "employer"
        # ...and the enforcer is a strictly separate field (Ask 1's whole point).
        assert result["enforcement_authority"] == "State Attorney General"

    def test_obligation_type_derived(self):
        payload = {
            "subject": "developer",
            "modality": "shall",
            "action": "conduct an impact_assessment before deployment",
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["obligation_family"] == "impact_assessment"
        assert result["obligation_type"] == "assessment"

    def test_deadlines_only_from_parsed_dates(self):
        payload = {
            "subject": "deployer",
            "modality": "must",
            "action": "notify",
            "timeline": {
                "effective_date": "2026-01-01",
                "compliance_deadline": "upon the commissioner's determination",
                "date_parse_status": {
                    "effective_date": "parsed",
                    "compliance_deadline": "unparsed",
                },
            },
        }
        result = adapt_payload_for_sync("obligation", payload)
        # Only the parsed date becomes a structured deadline; the prose is skipped.
        assert result["deadlines"] == [
            {"deadline_type": "effective", "deadline_date": "2026-01-01"}
        ]

    def test_no_timeline_yields_empty_deadlines(self):
        payload = {"subject": "developer", "modality": "shall", "action": "x"}
        result = adapt_payload_for_sync("obligation", payload)
        assert result["deadlines"] == []

    def test_enforcer_role_not_shown_as_actor(self):
        payload = {
            "subject": "the Attorney General",
            "subject_normalized": "regulator",
            "modality": "may",
            "action": "bring an enforcement action",
        }
        result = adapt_payload_for_sync("obligation", payload)
        assert result["actor_role_rc"] == "regulator"
        assert result["actor_role"] is None


class TestPNE2ThresholdTrigger:
    """PNE-2d: trigger predicate wired into _adapt_threshold."""

    def test_trigger_present(self):
        payload = {
            "threshold_type": "numeric",
            "threshold_value": "50",
            "threshold_unit": "employees",
            "threshold_condition": "more than 50 employees",
        }
        result = adapt_payload_for_sync("threshold", payload)
        assert result["trigger"]["trigger_type"] == "employee_count"
        assert result["trigger"]["trigger_operator"] == "gt"
        assert result["trigger"]["trigger_value"] == 50.0

    def test_trigger_none_when_no_signal(self):
        payload = {"threshold_type": None, "threshold_value": None}
        result = adapt_payload_for_sync("threshold", payload)
        assert result["trigger"] is None


class TestPNE1aRightsPassthrough:
    def test_interpretation_risks_pass_through(self):
        payload = {
            "right_holder": "consumer",
            "right_type": "opt_out",
            "right_description": "right to opt out of profiling",
            "interpretation_risks": [
                {"risk_type": "undefined_reference", "term": "profiling", "description": "term undefined"}
            ],
        }
        result = adapt_payload_for_sync("rights_protection", payload)
        assert result["interpretation_risks"][0]["term"] == "profiling"

    def test_missing_risks_default_to_empty_list(self):
        payload = {
            "right_holder": "employee",
            "right_type": "notice",
            "right_description": "notice of ADS use",
        }
        result = adapt_payload_for_sync("rights_protection", payload)
        assert result["interpretation_risks"] == []
