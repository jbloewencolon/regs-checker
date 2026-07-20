"""Unit tests for src/core/edit_service.py (LC-1c).

validate_edit() takes a plain Extraction-shaped object (no DB session
needed) so its type/format-validation logic is fully unit-testable in
isolation. propose_edit/apply_edit/revert_edit need a Session (they query
and mutate ExtractionFieldEdit rows) — those are exercised against a real
Postgres in tests/integration/test_law_card_e2e.py, following
this repo's existing integration-test convention (SessionLocal fixture,
same pattern as tests/integration/test_pipeline_e2e.py). This file covers
everything DB-independent: field-path resolution, per-widget validation,
numeric-grounding warnings, and payload deep-copy semantics.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.edit_service import (
    EditServiceError,
    _get_path,
    _resolve_field_entry,
    _set_path,
    validate_edit,
)
from src.core.edit_service import (
    _extraction_type_model_name as _edit_service_model_name,
)
from src.core.law_card_assembler import (
    _extraction_type_model_name as _assembler_model_name,
)
from src.db.models import ExtractionType


def _make_extraction(
    extraction_type=ExtractionType.obligation,
    payload=None,
    evidence_spans=None,
    effective_payload=None,
):
    """Build a lightweight stand-in with exactly the attributes
    validate_edit/_resolve_field_entry touch — avoids constructing a real
    SQLAlchemy-mapped Extraction (which needs a session for relationship
    loading) for pure validation-logic tests."""
    payload = payload if payload is not None else {}
    return SimpleNamespace(
        extraction_type=extraction_type,
        payload=payload,
        effective_payload=effective_payload,
        evidence_spans=evidence_spans or [],
        current_payload=effective_payload if effective_payload is not None else payload,
    )


class TestFieldPathResolution:
    def test_top_level_field_resolves(self):
        ext = _make_extraction()
        entry = _resolve_field_entry(ext, "subject")
        assert entry.label == "Who must comply"

    def test_nested_field_resolves_via_parent_widget(self):
        ext = _make_extraction()
        entry = _resolve_field_entry(ext, "enforcement.max_civil_penalty_usd")
        assert entry.label == "Maximum civil penalty"

    def test_unknown_top_level_field_raises(self):
        ext = _make_extraction()
        with pytest.raises(EditServiceError):
            _resolve_field_entry(ext, "not_a_real_field")

    def test_dotted_path_into_non_nested_field_raises(self):
        ext = _make_extraction()
        with pytest.raises(EditServiceError, match="not a nested field"):
            _resolve_field_entry(ext, "subject.foo")

    def test_dotted_path_into_list_nested_field_raises(self):
        ext = _make_extraction()
        with pytest.raises(EditServiceError, match="not supported"):
            _resolve_field_entry(ext, "interpretation_risks.term")

    def test_three_segment_path_raises(self):
        ext = _make_extraction()
        with pytest.raises(EditServiceError, match="one level of nesting"):
            _resolve_field_entry(ext, "enforcement.a.b")

    def test_rights_protection_type_resolves_against_correct_schema(self):
        ext = _make_extraction(extraction_type=ExtractionType.rights_protection)
        entry = _resolve_field_entry(ext, "right_type")
        assert entry.label == "Right type"

    @pytest.mark.parametrize("ext_type", list(ExtractionType))
    def test_model_name_mapping_matches_assembler(self, ext_type):
        # edit_service.py and law_card_assembler.py each carry their own copy
        # of the ExtractionType -> catalog-model-name mapping (documented as
        # a deliberate small duplication, not a shared import, in
        # edit_service.py's _extraction_type_model_name docstring). This
        # pins them to agreement so a future edit to one that forgets the
        # other fails here instead of routing an edit against the wrong
        # schema silently.
        ext = _make_extraction(extraction_type=ext_type)
        assert _edit_service_model_name(ext) == _assembler_model_name(ext_type.value)


class TestPathGetSet:
    def test_get_path_top_level(self):
        assert _get_path({"subject": "developer"}, "subject") == "developer"

    def test_get_path_nested(self):
        payload = {"enforcement": {"max_civil_penalty_usd": 10000}}
        assert _get_path(payload, "enforcement.max_civil_penalty_usd") == 10000

    def test_get_path_missing_returns_none(self):
        assert _get_path({}, "enforcement.max_civil_penalty_usd") is None

    def test_set_path_top_level_returns_new_dict(self):
        original = {"subject": "developer"}
        result = _set_path(original, "subject", "deployer")
        assert result == {"subject": "deployer"}
        assert original == {"subject": "developer"}  # untouched

    def test_set_path_nested_creates_parent_if_missing(self):
        result = _set_path({}, "enforcement.max_civil_penalty_usd", 5000)
        assert result == {"enforcement": {"max_civil_penalty_usd": 5000}}

    def test_set_path_nested_preserves_sibling_keys(self):
        original = {"enforcement": {"enforcing_body": "AG", "max_civil_penalty_usd": 1000}}
        result = _set_path(original, "enforcement.max_civil_penalty_usd", 2000)
        assert result["enforcement"]["enforcing_body"] == "AG"
        assert result["enforcement"]["max_civil_penalty_usd"] == 2000


class TestValidateEditReadonlyAndNested:
    def test_readonly_field_rejected(self):
        ext = _make_extraction()
        report = validate_edit(ext, "timeline.date_parse_status", "anything")
        assert report.valid is False
        assert "cannot be edited" in report.errors[0]

    def test_top_level_nested_field_rejected(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement", {"enforcing_body": "AG"})
        assert report.valid is False


class TestValidateEditNumber:
    def test_valid_integer_string_coerces(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "20000")
        assert report.valid is True
        assert report.normalized_value == 20000
        assert isinstance(report.normalized_value, int)

    def test_non_numeric_string_rejected(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "a lot of money")
        assert report.valid is False
        assert "isn't a number" in report.errors[0]

    def test_empty_string_normalizes_to_none(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "")
        assert report.valid is True
        assert report.normalized_value is None

    def test_threshold_value_is_text_widget_not_number(self):
        # threshold_value is a TEXT field on ThresholdExceptionPayload (the
        # schema coerces numeric thresholds to str) — sanity check that a
        # plain string is accepted without numeric coercion.
        ext = _make_extraction(extraction_type=ExtractionType.threshold)
        report = validate_edit(ext, "threshold_value", "50 employees")
        assert report.valid is True
        assert report.normalized_value == "50 employees"


class TestValidateEditDate:
    def test_valid_iso_date_passes_through(self):
        ext = _make_extraction()
        report = validate_edit(ext, "timeline.effective_date", "2026-07-01")
        assert report.valid is True
        assert report.normalized_value == "2026-07-01"

    def test_named_month_date_normalizes(self):
        ext = _make_extraction()
        report = validate_edit(ext, "timeline.effective_date", "July 1, 2026")
        assert report.valid is True
        assert report.normalized_value == "2026-07-01"

    def test_unparseable_date_rejected_with_plain_language_message(self):
        ext = _make_extraction()
        report = validate_edit(ext, "timeline.effective_date", "sometime next quarter")
        assert report.valid is False
        assert "2026-07-01" in report.errors[0]  # the example format is shown


class TestValidateEditBoolean:
    def test_true_string_coerces(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.private_right_of_action", "true")
        assert report.valid is True
        assert report.normalized_value is True

    def test_no_string_coerces_to_false(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.private_right_of_action", "no")
        assert report.valid is True
        assert report.normalized_value is False

    def test_empty_coerces_to_none_unknown(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.private_right_of_action", "")
        assert report.valid is True
        assert report.normalized_value is None

    def test_garbage_value_rejected(self):
        ext = _make_extraction()
        report = validate_edit(ext, "enforcement.private_right_of_action", "maybe")
        assert report.valid is False


class TestValidateEditSelect:
    def test_standard_choice_no_warning(self):
        ext = _make_extraction()
        report = validate_edit(ext, "modality", "shall")
        assert report.valid is True
        assert report.warnings == []

    def test_nonstandard_choice_warns_but_is_valid(self):
        # EAR-5-1 spirit: never reject, flag for vocab review instead.
        ext = _make_extraction()
        report = validate_edit(ext, "modality", "ought_to_probably")
        assert report.valid is True
        assert report.normalized_value == "ought_to_probably"
        assert any("standard values" in w for w in report.warnings)


class TestValidateEditListText:
    def test_single_string_wrapped_in_list(self):
        ext = _make_extraction()
        report = validate_edit(ext, "preemption_signals", "notwithstanding state law")
        assert report.valid is True
        assert report.normalized_value == ["notwithstanding state law"]

    def test_list_of_strings_passes_through(self):
        ext = _make_extraction()
        report = validate_edit(ext, "preemption_signals", ["a", "b"])
        assert report.valid is True
        assert report.normalized_value == ["a", "b"]

    def test_list_with_non_string_rejected(self):
        ext = _make_extraction()
        report = validate_edit(ext, "preemption_signals", ["a", 5])
        assert report.valid is False


class TestValidateEditNumericGroundingWarning:
    def test_mismatched_amount_warns(self):
        ext = _make_extraction(
            payload={"enforcement": {"max_civil_penalty_usd": 10000}},
            evidence_spans=[
                {"text": "a civil penalty not to exceed $10,000 per violation", "verified": True},
            ],
        )
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "999999")
        assert report.valid is True  # warning, not a hard error
        assert any("doesn't match" in w for w in report.warnings)

    def test_matched_amount_no_warning(self):
        ext = _make_extraction(
            payload={"enforcement": {"max_civil_penalty_usd": 10000}},
            evidence_spans=[
                {"text": "a civil penalty not to exceed $20,000 per violation", "verified": True},
            ],
        )
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "20000")
        assert report.warnings == []

    def test_no_evidence_at_all_no_warning(self):
        # unverifiable != mismatch — don't penalize edits with no evidence to check against
        ext = _make_extraction(payload={"enforcement": {}}, evidence_spans=[])
        report = validate_edit(ext, "enforcement.max_civil_penalty_usd", "5000")
        assert report.warnings == []


class TestValidateEditTextFields:
    def test_text_field_accepts_any_string(self):
        ext = _make_extraction()
        report = validate_edit(ext, "subject", "A developer of a high-risk AI system")
        assert report.valid is True
        assert report.normalized_value == "A developer of a high-risk AI system"

    def test_textarea_field_accepts_long_string(self):
        ext = _make_extraction()
        report = validate_edit(ext, "action", "must " * 50)
        assert report.valid is True
