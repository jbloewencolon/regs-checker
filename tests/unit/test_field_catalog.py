"""Unit tests for src/core/field_catalog.py (LC-1b).

The central assertion is test_every_schema_field_has_a_catalog_entry: it
walks the real extraction schemas via reflection (iter_schema_fields) and
fails with the exact missing (model, field) pairs the moment a schema field
lacks a catalog entry — the enforcement mechanism promised in
docs/law_card_decisions.md / tasks.md LC-1b ("CI test: schema field without
a catalog entry fails").
"""
from __future__ import annotations

import pytest

from src.core.field_catalog import (
    BOOLEAN,
    CATALOG,
    DATE,
    LIST_NESTED,
    LIST_TEXT,
    NESTED,
    NUMBER,
    READONLY,
    SELECT,
    TEXT,
    TEXTAREA,
    FieldCatalogEntry,
    FieldCatalogError,
    get_entry,
    iter_schema_fields,
    iter_schema_models,
    material_fields_for,
)
from src.schemas.extraction import (
    ComplianceMechanismPayload,
    ObligationPayload,
    PreemptionSignalPayload,
    RightsProtectionPayload,
    ThresholdExceptionPayload,
)

_VALID_WIDGETS = {
    TEXT, TEXTAREA, SELECT, NUMBER, DATE, BOOLEAN, LIST_TEXT, NESTED, LIST_NESTED, READONLY,
}


class TestSchemaReflection:
    def test_discovers_all_six_clause_payload_roots(self):
        models = iter_schema_models()
        names = set(models.keys())
        for expected in (
            "ObligationPayload", "DefinitionActorPayload", "ThresholdExceptionPayload",
            "RightsProtectionPayload", "ComplianceMechanismPayload", "PreemptionSignalPayload",
        ):
            assert expected in names

    def test_discovers_nested_models(self):
        models = iter_schema_models()
        names = set(models.keys())
        # Nested one level (directly on a payload)
        for expected in ("TimelineInfo", "EnforcementInfo", "SafeHarbor", "ConsentRequirement"):
            assert expected in names, f"{expected} should be reachable from ObligationPayload"
        # Nested list types
        for expected in ("ActorMapping", "FrameworkReference", "ExceptionItem", "RemedyInfo",
                          "AuditRequirement", "CrossLawReference", "InterpretationRisk"):
            assert expected in names, f"{expected} should be reachable via a list[] field"

    def test_iter_schema_fields_returns_nonempty_pairs(self):
        pairs = iter_schema_fields()
        assert len(pairs) > 50  # sanity floor — this schema surface is large
        assert ("ObligationPayload", "subject") in pairs
        assert ("EnforcementInfo", "max_civil_penalty_usd") in pairs


class TestCatalogCoverage:
    def test_every_schema_field_has_a_catalog_entry(self):
        """The load-bearing test: if a schema field is added without a
        catalog entry, this fails with the exact list of gaps."""
        missing = [
            f"{model}.{field}"
            for model, field in iter_schema_fields()
            if model not in CATALOG or field not in CATALOG[model]
        ]
        assert not missing, (
            f"{len(missing)} schema field(s) have no field_catalog entry: {missing}. "
            "Add an entry to src/core/field_catalog.py:CATALOG for each."
        )

    def test_catalog_has_no_orphan_entries_for_removed_fields(self):
        """The inverse check: a catalog entry for a field the schema no
        longer has is stale documentation, not a coverage gap, but worth
        catching so the catalog doesn't accumulate dead entries."""
        real_pairs = set(iter_schema_fields())
        orphans = [
            f"{model}.{field}"
            for model, fields in CATALOG.items()
            for field in fields
            if (model, field) not in real_pairs
            # Some catalog models aren't schema-root-reachable in every test
            # configuration (e.g. AmbiguityPayload is legacy-display-only and
            # not walked from EXTRACTION_TYPE_SCHEMAS roots the same way);
            # skip models iter_schema_models didn't find at all rather than
            # flagging their fields as orphans.
            and model in iter_schema_models()
        ]
        assert not orphans, f"Catalog entries for fields that no longer exist: {orphans}"

    def test_every_entry_has_a_valid_widget(self):
        bad = [
            f"{model}.{field}: {entry.widget}"
            for model, fields in CATALOG.items()
            for field, entry in fields.items()
            if entry.widget not in _VALID_WIDGETS
        ]
        assert not bad, f"Entries with unknown widget types: {bad}"

    def test_select_widgets_always_have_choices(self):
        bad = [
            f"{model}.{field}"
            for model, fields in CATALOG.items()
            for field, entry in fields.items()
            if entry.widget == SELECT and not entry.choices
        ]
        assert not bad, f"SELECT-widget entries missing choices: {bad}"

    def test_every_entry_has_a_nonempty_label_and_help(self):
        bad = [
            f"{model}.{field}"
            for model, fields in CATALOG.items()
            for field, entry in fields.items()
            if not entry.label.strip() or not entry.help.strip()
        ]
        assert not bad, f"Entries with empty label/help: {bad}"

    def test_labels_are_not_raw_schema_keys(self):
        # Design requirement: no field ever gets its raw snake_case name as
        # a label (the defect the old review.html editor had).
        bad = [
            f"{model}.{field}"
            for model, fields in CATALOG.items()
            for field, entry in fields.items()
            if entry.label == field
        ]
        assert not bad, f"Entries whose label is just the raw field name: {bad}"


class TestGetEntry:
    def test_returns_entry_for_known_field(self):
        entry = get_entry("ObligationPayload", "modality")
        assert isinstance(entry, FieldCatalogEntry)
        assert entry.label == "Requirement strength"
        assert entry.widget == SELECT

    def test_raises_field_catalog_error_for_unknown_model(self):
        with pytest.raises(FieldCatalogError, match="NoSuchModel.foo"):
            get_entry("NoSuchModel", "foo")

    def test_raises_field_catalog_error_for_unknown_field(self):
        with pytest.raises(FieldCatalogError, match="ObligationPayload.nonexistent_field"):
            get_entry("ObligationPayload", "nonexistent_field")


class TestMaterialFields:
    def test_obligation_material_fields_match_ear_2_1_spec(self):
        # docs/law_card_dashboard_plan.md's EAR-2-1 spec names these four.
        assert material_fields_for("ObligationPayload") >= {
            "subject", "modality", "action", "condition",
        }

    def test_rights_material_fields_match_ear_2_1_spec(self):
        assert material_fields_for("RightsProtectionPayload") >= {
            "right_type", "trigger_condition", "duty_bearer",
        }

    def test_threshold_material_fields_include_value_and_condition(self):
        assert material_fields_for("ThresholdExceptionPayload") >= {
            "threshold_value", "threshold_condition",
        }

    def test_compliance_material_fields_match_ear_2_1_spec(self):
        assert material_fields_for("ComplianceMechanismPayload") >= {
            "mechanism_type", "responsible_party",
        }

    def test_preemption_material_fields_match_ear_2_1_spec(self):
        assert material_fields_for("PreemptionSignalPayload") >= {
            "conflict_type", "related_authority",
        }

    def test_non_material_field_not_included(self):
        # jurisdiction is display metadata, not a material legal-meaning field.
        assert "jurisdiction" not in material_fields_for("ObligationPayload")

    def test_unknown_model_returns_empty_set_not_error(self):
        assert material_fields_for("NoSuchModel") == frozenset()


class TestRealPayloadCompatibility:
    """Every field name the real Pydantic schemas expose must resolve via
    get_entry() without raising — the actual contract LC-1c's assembler
    will rely on when it builds card JSON from real ORM payloads."""

    @pytest.mark.parametrize("schema_cls", [
        ObligationPayload, ThresholdExceptionPayload, RightsProtectionPayload,
        ComplianceMechanismPayload, PreemptionSignalPayload,
    ])
    def test_schema_fields_all_resolve(self, schema_cls):
        for field_name, info in schema_cls.model_fields.items():
            storage_key = info.alias if info.alias else field_name
            get_entry(schema_cls.__name__, storage_key)  # must not raise

    def test_object_field_resolves_by_its_storage_alias(self):
        # Regression pin for the alias bug this reflection design caught:
        # ObligationPayload's Python attribute is `object_` (Python keyword
        # collision avoidance) but it's declared alias="object" and stored
        # under "object" in real payloads (model_dump(by_alias=True)).
        entry = get_entry("ObligationPayload", "object")
        assert entry.label != "object"
        with pytest.raises(FieldCatalogError):
            get_entry("ObligationPayload", "object_")
