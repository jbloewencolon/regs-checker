"""Field catalog — plain-language label/help/widget registry for every field
in the clause-level extraction schemas (src/schemas/extraction.py).

This is the single source of truth the Law Card editor (LC-3) renders from:
no field ever gets a raw schema key as a label, and no field is editable
without a widget type telling the UI what control to render. `material=True`
marks the fields EAR-2-1 (tasks.md) requires span-grounding for — this
catalog doesn't compute grounding itself, it just carries the flag other
code reads.

Coverage is enforced structurally, not just by convention: `iter_schema_fields()`
walks every schema in `EXTRACTION_TYPE_SCHEMAS` (plus any BaseModel-valued
field it finds, recursively) via Pydantic's own `model_fields`, so a new field
added to any schema — including a newly-introduced nested model — is
automatically included in what `CATALOG` must cover. See
tests/unit/test_field_catalog.py::test_every_schema_field_has_a_catalog_entry,
which fails CI the moment a schema field lacks an entry here (mirrors the
"every schema field needs a catalog entry" pattern LC-1a's decision doc
promised).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from src.schemas.extraction import EXTRACTION_TYPE_SCHEMAS

# ---------------------------------------------------------------------------
# Widget vocabulary — the finite set of editor controls LC-3's templates
# know how to render. No field maps to "raw JSON" for MVP; NESTED / LIST_NESTED
# fields render as grouped sub-forms built from that nested model's own
# catalog entries (Design Rule set in docs/law_card_design_rules.md).
# ---------------------------------------------------------------------------

TEXT = "text"                # single-line text
TEXTAREA = "textarea"        # multi-line prose
SELECT = "select"            # fixed choice list (ratified vocab or schema-documented enum)
NUMBER = "number"            # integer/float, optionally with a unit label
DATE = "date"                # ISO-8601 date, normalize-on-blur (src/core/date_normalizer.py)
BOOLEAN = "boolean"          # tri-state Yes/No/Unknown — never a bare checkbox (null is real)
LIST_TEXT = "list_text"      # list[str], free-text tags (e.g. cross_references)
NESTED = "nested"            # single nested BaseModel — grouped sub-form
LIST_NESTED = "list_nested"  # list[BaseModel] — repeatable grouped sub-form
READONLY = "readonly"        # system-computed / evidence data — never an editable input.
                              # Correcting a wrong fact means editing the FIELD it supports
                              # (which gets re-verified), not hand-editing the quote itself
                              # to match — that would defeat evidence's whole role as proof.


@dataclass(frozen=True)
class FieldCatalogEntry:
    """One field's presentation contract for the Law Card editor."""

    label: str
    help: str
    widget: str
    material: bool = False
    choices: tuple[str, ...] | None = None
    glossary: str | None = None  # specialist-term definition (LC-5 glossary layer)
    unit: str | None = None      # e.g. "USD", "days", "months" — paired with NUMBER widget


# ---------------------------------------------------------------------------
# Shared choice lists — ported from templates/review.html's existing typed
# selects (the seed of this catalog) plus the enumerated values documented
# directly in each field's `Field(description=...)` in extraction.py. Reusing
# the schema's own documented vocabulary rather than inventing a second list
# keeps this file a presentation layer on top of already-authoritative
# schema documentation, not a competing source of truth.
# ---------------------------------------------------------------------------

_MODALITY_CHOICES = ("must", "shall", "may", "should", "prohibited", "required")
_SEVERITY_CHOICES = ("low", "medium", "high", "critical")
_THRESHOLD_TYPE_CHOICES = (
    "numeric", "categorical", "temporal", "conditional", "geographic", "entity_size",
)
_THRESHOLD_SUB_TYPE_CHOICES = ("scope", "temporal", "exemption", "other")
_RIGHT_TYPE_CHOICES = (
    "notice", "explanation", "opt_out", "appeal", "deletion",
    "human_review", "non_discrimination", "portability", "access",
)
_MECHANISM_TYPE_CHOICES = (
    "impact_assessment", "bias_audit", "registration", "certification",
    "record_keeping", "reporting", "disclosure", "notification",
)
_AMBIGUITY_TYPE_CHOICES = (
    "vague_term", "conflicting_provisions", "undefined_reference",
    "scope_ambiguity", "temporal_ambiguity",
)
_INTERPRETATION_RISK_TYPE_CHOICES = (
    "vague_term", "undefined_reference", "conflicting_provision",
    "scope_ambiguity", "temporal_ambiguity", "conditional_ambiguity",
)
_NORMALIZED_ACTOR_CHOICES = (
    "developer", "deployer", "operator", "vendor", "consumer",
    "employee", "public", "regulator",
)
_CONSENT_TYPE_CHOICES = ("opt_in", "opt_out", "notice", "notice_and_choice", "disclosure")
_CONSENT_TIMING_CHOICES = ("before", "at", "after")
_CONSENT_METHOD_CHOICES = ("written", "electronic", "verbal", "in_app", "posted")
_AUDIT_TYPE_CHOICES = (
    "bias_audit", "impact_assessment", "risk_assessment",
    "algorithmic_audit", "third_party_audit", "self_certification",
)
_REMEDY_TYPE_CHOICES = ("complaint", "appeal", "damages", "injunction", "deletion", "correction")
_EXCEPTION_TYPE_CHOICES = ("carve-out", "safe-harbor", "exemption")
_CONFLICT_TYPE_CHOICES = (
    "federal_preemption", "interstate_commerce", "cross_state_conflict",
    "first_amendment", "dormant_commerce_clause", "agency_jurisdiction", "other",
)
_CROSS_LAW_REFERENCE_TYPE_CHOICES = (
    "supersedes", "incorporates", "conflicts_with", "defined_by",
    "supplements", "notwithstanding", "subject_to",
)

_MODALITY_GLOSSARY = (
    "How strong the requirement is. “Shall” and “must” are legally "
    "equivalent obligations; “may” is optional; “prohibited” forbids the action."
)
_SAFE_HARBOR_GLOSSARY = (
    "A safe harbor lets an organization avoid liability by meeting specific "
    "conditions (e.g. following a named framework)."
)
_PREEMPTION_GLOSSARY = (
    "Preemption means one law overrides or blocks another — usually a "
    "federal law taking priority over a state law."
)
_PRIVATE_RIGHT_OF_ACTION_GLOSSARY = (
    "Whether an individual (not just a regulator) can personally sue over a violation."
)
_TIER_GLOSSARY = (
    "How confident the pipeline is in this extraction, from A (highest) to D (lowest)."
)

# ---------------------------------------------------------------------------
# Catalog — keyed by (schema class name, field name).
# ---------------------------------------------------------------------------

CATALOG: dict[str, dict[str, FieldCatalogEntry]] = {
    # -- ObligationPayload -----------------------------------------------
    "ObligationPayload": {
        "subject": FieldCatalogEntry(
            "Who must comply", "The person or organization this requirement applies to, "
            "exactly as the law states it (e.g. “A developer of a high-risk AI system”).",
            TEXT, material=True,
        ),
        "subject_normalized": FieldCatalogEntry(
            "Regulated party (category)", "The general category this party falls into.",
            SELECT, choices=_NORMALIZED_ACTOR_CHOICES,
        ),
        "modality": FieldCatalogEntry(
            "Requirement strength", "How strong this requirement is.",
            SELECT, choices=_MODALITY_CHOICES, material=True, glossary=_MODALITY_GLOSSARY,
        ),
        "action": FieldCatalogEntry(
            "What they must do", "The action required or prohibited.",
            TEXTAREA, material=True,
        ),
        "object": FieldCatalogEntry(
            "What it applies to", "What the action is about (e.g. the system or data involved).",
            TEXT,
        ),
        "condition": FieldCatalogEntry(
            "Conditions or trigger",
            "Any condition that must be true for this requirement to apply.",
            TEXTAREA, material=True,
        ),
        "jurisdiction": FieldCatalogEntry(
            "Jurisdiction", "The state or jurisdiction code this requirement is under.", TEXT,
        ),
        "section_reference": FieldCatalogEntry(
            "Section reference", "Where in the law this requirement appears.", TEXT,
        ),
        "timeline": FieldCatalogEntry(
            "Timeline", "Dates and deadlines tied to this requirement.", NESTED,
        ),
        "enforcement": FieldCatalogEntry(
            "Enforcement", "Who enforces this and what the penalty is.", NESTED,
        ),
        "preemption_signals": FieldCatalogEntry(
            "Preemption language", "Exact wording from the law about overriding another law.",
            LIST_TEXT, glossary=_PREEMPTION_GLOSSARY,
        ),
        "safe_harbor": FieldCatalogEntry(
            "Safe harbor", "A protection from liability for meeting certain conditions, if any.",
            NESTED, glossary=_SAFE_HARBOR_GLOSSARY,
        ),
        "consent_requirements": FieldCatalogEntry(
            "Consent / notice requirement",
            "Any consent or notice this requirement demands.", NESTED,
        ),
        "interpretation_risks": FieldCatalogEntry(
            "Unclear language flags", "Vague or ambiguous wording noticed in this requirement.",
            LIST_NESTED,
        ),
    },
    # -- TimelineInfo (nested on ObligationPayload) -----------------------
    "TimelineInfo": {
        "effective_date": FieldCatalogEntry(
            "Effective date", "When this requirement takes effect.", DATE, material=True,
        ),
        "compliance_deadline": FieldCatalogEntry(
            "Compliance deadline", "The deadline to be in compliance.", DATE, material=True,
        ),
        "sunset_date": FieldCatalogEntry(
            "Sunset date", "When this requirement stops applying, if it does.", DATE,
        ),
        "phase_in_period": FieldCatalogEntry(
            "Phase-in period", "Any gradual rollout period described in the law.", TEXT,
        ),
        "timeline_text": FieldCatalogEntry(
            "Timeline wording", "The original timeline language from the law.", TEXTAREA,
        ),
        "date_parse_status": FieldCatalogEntry(
            "Date parsing status", "Whether each date above was recognized as a standard date.",
            READONLY,
        ),
    },
    # -- EnforcementInfo (nested on ObligationPayload) --------------------
    "EnforcementInfo": {
        "enforcing_body": FieldCatalogEntry(
            "Enforcing body", "Who enforces this (e.g. Attorney General).", TEXT, material=True,
        ),
        "penalty_type": FieldCatalogEntry(
            "Penalty type", "The kind of penalty for violating this requirement.", TEXT,
        ),
        "penalty_description": FieldCatalogEntry(
            "Penalty description", "A description of the penalty.", TEXTAREA,
        ),
        "private_right_of_action": FieldCatalogEntry(
            "Private right of action?", "Can an individual sue over this, not just a regulator?",
            BOOLEAN, glossary=_PRIVATE_RIGHT_OF_ACTION_GLOSSARY,
        ),
        "enforcement_text": FieldCatalogEntry(
            "Enforcement wording", "The original enforcement language from the law.", TEXTAREA,
        ),
        "max_civil_penalty_usd": FieldCatalogEntry(
            "Maximum civil penalty", "The highest dollar penalty stated in the law, if any.",
            NUMBER, material=True, unit="USD",
        ),
        "cure_period_days": FieldCatalogEntry(
            "Cure period", "Days allowed to fix a violation before penalties apply.",
            NUMBER, material=True, unit="days",
        ),
    },
    # -- SafeHarbor (nested on ObligationPayload) -------------------------
    "SafeHarbor": {
        "framework": FieldCatalogEntry(
            "Framework", "The named framework or standard that triggers this safe harbor "
            "(e.g. “NIST AI RMF”).", TEXT,
        ),
        "conditions": FieldCatalogEntry(
            "Conditions", "What the organization must do to qualify.", TEXTAREA,
        ),
        "protection": FieldCatalogEntry(
            "Protection granted", "The legal protection this safe harbor provides.", TEXT,
        ),
        "evidence_text": FieldCatalogEntry(
            "Source wording", "The original safe-harbor language from the law.", TEXTAREA,
        ),
    },
    # -- ConsentRequirement (nested on ObligationPayload) -----------------
    "ConsentRequirement": {
        "consent_type": FieldCatalogEntry(
            "Consent type", "The kind of consent or notice required.",
            SELECT, choices=_CONSENT_TYPE_CHOICES,
        ),
        "timing": FieldCatalogEntry(
            "Timing", "When consent or notice must be given.",
            SELECT, choices=_CONSENT_TIMING_CHOICES,
        ),
        "method": FieldCatalogEntry(
            "Method", "How consent or notice must be delivered.",
            SELECT, choices=_CONSENT_METHOD_CHOICES,
        ),
        "subject_matter": FieldCatalogEntry(
            "What it covers", "What the consent or notice is about.", TEXT,
        ),
    },
    # -- InterpretationRisk (nested list on Obligation + Rights) ----------
    "InterpretationRisk": {
        "risk_type": FieldCatalogEntry(
            "Type of concern", "The kind of unclear language flagged.",
            SELECT, choices=_INTERPRETATION_RISK_TYPE_CHOICES,
        ),
        "term": FieldCatalogEntry(
            "Term flagged", "The specific word or phrase that is unclear.", TEXT,
        ),
        "concern": FieldCatalogEntry(
            "Why it's a concern", "Why this creates compliance uncertainty.", TEXTAREA,
        ),
        "severity": FieldCatalogEntry(
            "Severity", "How serious this uncertainty is.", SELECT, choices=_SEVERITY_CHOICES,
        ),
        "evidence_spans": FieldCatalogEntry(
            "Source quotes", "The exact wording that shows this concern.", READONLY,
        ),
    },
    # -- EvidenceSpan (nested list on InterpretationRisk, and the top-level
    #    evidence_spans column stored alongside every extraction — see
    #    Extraction.evidence_spans in src/db/models.py). Never an editable
    #    field: correcting a fact means editing the field it supports (which
    #    triggers re-verification), not hand-editing the proof quote.
    "EvidenceSpan": {
        "field_name": FieldCatalogEntry(
            "Supports field", "Which field this quote is evidence for.", READONLY,
        ),
        "text": FieldCatalogEntry(
            "Quote", "The exact wording copied from the source law.", READONLY,
        ),
        "char_start": FieldCatalogEntry(
            "Start position", "Where this quote starts in the source text.", READONLY,
        ),
        "char_end": FieldCatalogEntry(
            "End position", "Where this quote ends in the source text.", READONLY,
        ),
        "source_url": FieldCatalogEntry(
            "Source link", "A link to the official law text.", READONLY,
        ),
        "section_anchor": FieldCatalogEntry(
            "Source section", "Which section of the law this quote is from.", READONLY,
        ),
    },
    # -- DefinitionActorPayload --------------------------------------------
    "DefinitionActorPayload": {
        "term": FieldCatalogEntry(
            "Defined term", "The word or phrase being defined.", TEXT, material=True,
        ),
        "definition_text": FieldCatalogEntry(
            "Definition", "The full definition, in the law's own words.", TEXTAREA, material=True,
        ),
        "scope": FieldCatalogEntry(
            "Scope", "Where this definition applies (e.g. a specific section).", TEXT,
        ),
        "cross_references": FieldCatalogEntry(
            "Referenced elsewhere in", "Other sections that use this definition.", LIST_TEXT,
        ),
        "actors": FieldCatalogEntry(
            "Roles mentioned", "People or organizations named in this definition.", LIST_NESTED,
        ),
        "framework_refs": FieldCatalogEntry(
            "Framework references", "Outside standards or frameworks this definition cites.",
            LIST_NESTED,
        ),
    },
    # -- ActorMapping (nested list on DefinitionActorPayload) -------------
    "ActorMapping": {
        "actor_name": FieldCatalogEntry(
            "Role name", "The name of the role (e.g. “developer”).", TEXT,
        ),
        "actor_type": FieldCatalogEntry(
            "Role category", "The general category this role falls into.",
            SELECT, choices=_NORMALIZED_ACTOR_CHOICES,
        ),
        "responsibilities": FieldCatalogEntry(
            "Responsibilities", "What this role is responsible for.", LIST_TEXT,
        ),
    },
    # -- FrameworkReference (nested list on DefinitionActorPayload) -------
    "FrameworkReference": {
        "framework_name": FieldCatalogEntry(
            "Framework name", "The name of the outside standard or framework (e.g. “NIST AI RMF”).",
            TEXT,
        ),
        "section_or_standard": FieldCatalogEntry(
            "Section or standard", "The specific section or standard referenced.", TEXT,
        ),
        "relationship": FieldCatalogEntry(
            "Relationship", "How the law relates to this framework "
            "(e.g. incorporates it, references it).", TEXT,
        ),
    },
    # -- ThresholdExceptionPayload ------------------------------------------
    "ThresholdExceptionPayload": {
        "threshold_sub_type": FieldCatalogEntry(
            "Category", "The general kind of boundary condition this is.",
            SELECT, choices=_THRESHOLD_SUB_TYPE_CHOICES,
        ),
        "threshold_type": FieldCatalogEntry(
            "Threshold type", "The specific kind of threshold.",
            SELECT, choices=_THRESHOLD_TYPE_CHOICES,
        ),
        "threshold_value": FieldCatalogEntry(
            "Threshold value", "The value that triggers this rule.", TEXT, material=True,
        ),
        "threshold_unit": FieldCatalogEntry(
            "Unit", "The unit the threshold value is measured in.", TEXT, material=True,
        ),
        "threshold_condition": FieldCatalogEntry(
            "Condition", "The full condition, in plain terms.", TEXTAREA, material=True,
        ),
        "applies_to_obligation": FieldCatalogEntry(
            "Applies to", "Which requirement this threshold modifies.", TEXT,
        ),
        "exceptions": FieldCatalogEntry(
            "Exceptions", "Carve-outs or exemptions to this rule.", LIST_NESTED,
        ),
        "compute_flops": FieldCatalogEntry(
            "Compute threshold", "The computing-power threshold, if specified.",
            NUMBER, unit="FLOPS",
        ),
        "compute_description": FieldCatalogEntry(
            "Compute threshold (description)",
            "A plain description of the compute threshold.", TEXT,
        ),
        "sector_applicability": FieldCatalogEntry(
            "Sectors covered", "The industries or sectors this applies to.", LIST_TEXT,
        ),
        "revenue_threshold_usd": FieldCatalogEntry(
            "Revenue threshold", "The annual revenue that triggers this rule.", NUMBER, unit="USD",
        ),
        "employee_threshold": FieldCatalogEntry(
            "Employee threshold", "The employee count that triggers this rule.",
            NUMBER, unit="employees",
        ),
        "consumer_data_threshold": FieldCatalogEntry(
            "Consumer data threshold", "The number of consumers' data that triggers this rule.",
            NUMBER, unit="consumers",
        ),
    },
    # -- ExceptionItem (nested list on ThresholdExceptionPayload) ---------
    "ExceptionItem": {
        "exception_type": FieldCatalogEntry(
            "Exception type", "The kind of exception this is.",
            SELECT, choices=_EXCEPTION_TYPE_CHOICES,
        ),
        "description": FieldCatalogEntry(
            "Description", "What this exception covers.", TEXTAREA, material=True,
        ),
        "conditions": FieldCatalogEntry(
            "Conditions", "What must be true for this exception to apply.", TEXTAREA,
        ),
        "scope": FieldCatalogEntry(
            "Scope", "What part of the law this exception applies to.", TEXT,
        ),
    },
    # -- RightsProtectionPayload --------------------------------------------
    "RightsProtectionPayload": {
        "right_holder": FieldCatalogEntry(
            "Who holds this right",
            "The person entitled to this right, exactly as the law states it.",
            TEXT, material=True,
        ),
        "right_holder_normalized": FieldCatalogEntry(
            "Right holder (category)", "The general category of person who holds this right.",
            SELECT, choices=("consumer", "employee", "public"),
        ),
        "protected_categories": FieldCatalogEntry(
            "Protected groups", "Specific groups this right protects (e.g. minors, tenants).",
            LIST_TEXT,
        ),
        "right_type": FieldCatalogEntry(
            "Right type", "The kind of right this is.", SELECT, choices=_RIGHT_TYPE_CHOICES,
            material=True,
        ),
        "right_description": FieldCatalogEntry(
            "Right description", "The full description of this right, in the law's own words.",
            TEXTAREA, material=True,
        ),
        "trigger_condition": FieldCatalogEntry(
            "When it applies", "What has to happen for this right to activate.",
            TEXTAREA, material=True,
        ),
        "duty_bearer": FieldCatalogEntry(
            "Who must provide it", "The person or organization that must fulfill this right.",
            TEXT, material=True,
        ),
        "remedies": FieldCatalogEntry(
            "Available remedies", "What someone can do if this right is violated.", LIST_NESTED,
        ),
        "section_reference": FieldCatalogEntry(
            "Section reference", "Where in the law this right appears.", TEXT,
        ),
        "jurisdiction": FieldCatalogEntry(
            "Jurisdiction", "The state or jurisdiction code this right is under.", TEXT,
        ),
        "interpretation_risks": FieldCatalogEntry(
            "Unclear language flags", "Vague or ambiguous wording noticed in this right.",
            LIST_NESTED,
        ),
    },
    # -- RemedyInfo (nested list on RightsProtectionPayload) --------------
    "RemedyInfo": {
        "remedy_type": FieldCatalogEntry(
            "Remedy type", "The kind of recourse available.", SELECT, choices=_REMEDY_TYPE_CHOICES,
        ),
        "description": FieldCatalogEntry(
            "Description", "What this remedy involves.", TEXTAREA,
        ),
        "available_to": FieldCatalogEntry(
            "Who can use it", "Who is allowed to invoke this remedy.", TEXT,
        ),
        "time_limit": FieldCatalogEntry(
            "Time limit", "The deadline to use this remedy, if any.", TEXT,
        ),
    },
    # -- ComplianceMechanismPayload ------------------------------------------
    "ComplianceMechanismPayload": {
        "mechanism_type": FieldCatalogEntry(
            "Mechanism type", "The kind of compliance activity required.",
            SELECT, choices=_MECHANISM_TYPE_CHOICES, material=True,
        ),
        "description": FieldCatalogEntry(
            "Description", "The full description of this requirement.", TEXTAREA, material=True,
        ),
        "responsible_party": FieldCatalogEntry(
            "Responsible party", "Who must carry out this activity, exactly as the law states it.",
            TEXT, material=True,
        ),
        "responsible_party_normalized": FieldCatalogEntry(
            "Responsible party (category)", "The general category of who is responsible.",
            SELECT, choices=("developer", "deployer", "operator", "vendor"),
        ),
        "audits": FieldCatalogEntry(
            "Audit requirements", "Specific audits or assessments required.", LIST_NESTED,
        ),
        "record_retention_period": FieldCatalogEntry(
            "Retention period (as written)",
            "How long records must be kept, in the law's own words.",
            TEXT,
        ),
        "retention_period_months": FieldCatalogEntry(
            "Retention period", "How long records must be kept.", NUMBER, unit="months",
        ),
        "retention_subject": FieldCatalogEntry(
            "What must be retained", "The records or materials that must be kept.", TEXT,
        ),
        "reporting_frequency": FieldCatalogEntry(
            "Reporting frequency", "How often reports must be filed.", TEXT,
        ),
        "reporting_recipient": FieldCatalogEntry(
            "Reports go to", "Who receives the compliance reports.", TEXT,
        ),
        "section_reference": FieldCatalogEntry(
            "Section reference", "Where in the law this requirement appears.", TEXT,
        ),
        "jurisdiction": FieldCatalogEntry(
            "Jurisdiction", "The state or jurisdiction code this requirement is under.", TEXT,
        ),
        "is_bias_testing": FieldCatalogEntry(
            "Includes bias testing?",
            "Does this involve testing for bias or discrimination?", BOOLEAN,
        ),
        "is_red_teaming": FieldCatalogEntry(
            "Includes adversarial testing?", "Does this involve red-team / adversarial testing?",
            BOOLEAN,
        ),
        "nist_measure_refs": FieldCatalogEntry(
            "NIST references", "Specific NIST AI RMF measures this cites (e.g. “MEASURE-2.1”).",
            LIST_TEXT,
        ),
        "assessment_frequency_months": FieldCatalogEntry(
            "Assessment frequency", "How often this assessment must be repeated.",
            NUMBER, unit="months",
        ),
        "is_third_party_audit": FieldCatalogEntry(
            "Independent audit required?",
            "Must an outside party (not the organization itself) do this?",
            BOOLEAN,
        ),
        "incident_reporting_hours": FieldCatalogEntry(
            "Incident reporting window", "Hours allowed to report an incident to a regulator.",
            NUMBER, unit="hours",
        ),
    },
    # -- AuditRequirement (nested list on ComplianceMechanismPayload) -----
    "AuditRequirement": {
        "audit_type": FieldCatalogEntry(
            "Audit type", "The kind of audit or assessment.", SELECT, choices=_AUDIT_TYPE_CHOICES,
        ),
        "frequency": FieldCatalogEntry(
            "Frequency", "How often this audit happens.", TEXT,
        ),
        "assessor": FieldCatalogEntry(
            "Performed by", "Who performs this audit (internal, third-party, regulator).", TEXT,
        ),
        "scope": FieldCatalogEntry(
            "Scope", "What this audit covers.", TEXT,
        ),
        "reporting_to": FieldCatalogEntry(
            "Reports go to", "Who receives the audit results.", TEXT,
        ),
        "public_disclosure": FieldCatalogEntry(
            "Made public?", "Must the results be disclosed publicly?", BOOLEAN,
        ),
    },
    # -- PreemptionSignalPayload ---------------------------------------------
    "PreemptionSignalPayload": {
        "conflict_type": FieldCatalogEntry(
            "Conflict type", "The kind of cross-jurisdictional conflict.",
            SELECT, choices=_CONFLICT_TYPE_CHOICES, material=True,
        ),
        "description": FieldCatalogEntry(
            "Description", "A plain-language description of the conflict.", TEXTAREA, material=True,
        ),
        "related_authority": FieldCatalogEntry(
            "Related authority", "The other law, agency, or authority involved.",
            TEXT, material=True,
        ),
        "severity": FieldCatalogEntry(
            "Severity", "How likely and how significant this conflict is.",
            SELECT, choices=("high", "medium", "low"),
        ),
        "preemption_language": FieldCatalogEntry(
            "Source wording", "The original preemption clause from the law, if present.",
            TEXTAREA, glossary=_PREEMPTION_GLOSSARY,
        ),
        "cross_law_refs": FieldCatalogEntry(
            "Related laws", "Other laws or statutes this passage references.", LIST_NESTED,
        ),
        "section_reference": FieldCatalogEntry(
            "Section reference", "Where in the law this appears.", TEXT,
        ),
        "jurisdiction": FieldCatalogEntry(
            "Jurisdiction", "The state or jurisdiction code this is under.", TEXT,
        ),
    },
    # -- CrossLawReference (nested list on PreemptionSignalPayload) -------
    "CrossLawReference": {
        "reference_type": FieldCatalogEntry(
            "Relationship", "How this law relates to the one referenced.",
            SELECT, choices=_CROSS_LAW_REFERENCE_TYPE_CHOICES,
        ),
        "law_name": FieldCatalogEntry(
            "Law name", "The name or citation of the referenced law (e.g. “CCPA”).", TEXT,
        ),
        "section": FieldCatalogEntry(
            "Section", "The specific section of the referenced law, if given.", TEXT,
        ),
        "description": FieldCatalogEntry(
            "Description", "A plain-language description of the reference.", TEXT,
        ),
    },
    # -- AmbiguityPayload (retired agent; legacy rows still display) ------
    "AmbiguityPayload": {
        "ambiguous_text": FieldCatalogEntry(
            "Ambiguous text", "The unclear passage.", TEXTAREA, material=True,
        ),
        "ambiguity_type": FieldCatalogEntry(
            "Ambiguity type", "The kind of ambiguity.", SELECT, choices=_AMBIGUITY_TYPE_CHOICES,
        ),
        "severity": FieldCatalogEntry(
            "Severity", "How serious this ambiguity is.", TEXT,
        ),
        "affected_obligations": FieldCatalogEntry(
            "Affected requirements", "Which requirements this ambiguity affects.", LIST_TEXT,
        ),
        "interpretation_notes": FieldCatalogEntry(
            "Notes", "Notes on how this might be interpreted.", TEXTAREA,
        ),
        "suggested_clarification": FieldCatalogEntry(
            "Suggested fix", "A suggested way to clarify this language.", TEXTAREA,
        ),
    },
}

# ---------------------------------------------------------------------------
# Fields that are deliberately excluded from the catalog's coverage
# requirement — internal/computed, not user-facing editable data. Kept as an
# explicit allow-list (not a blanket "underscore-prefixed" rule) so a genuine
# oversight doesn't silently hide behind this list.
# ---------------------------------------------------------------------------

_EXCLUDED_FIELDS: dict[str, set[str]] = {
    # date_parse_status is a model_validator-computed field, not model output
    # or user input — it's already cataloged above as display-only, kept out
    # of the exclusion list intentionally so it stays visible in the editor.
}


# ---------------------------------------------------------------------------
# Reflection: walk the real Pydantic schemas so catalog coverage is checked
# against the schemas as they exist today, not a hand-maintained mirror list.
# ---------------------------------------------------------------------------


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
    """Return the BaseModel class inside an annotation, if any.

    Handles `Model | None`, `list[Model]`, and `list[Model] | None` — the
    three nesting shapes present in src/schemas/extraction.py. Returns None
    for annotations with no BaseModel inside (plain scalars, list[str], etc).
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    origin = get_origin(annotation)
    if origin is not None:
        for arg in get_args(annotation):
            found = _unwrap_model(arg)
            if found is not None:
                return found
    return None


def iter_schema_models(roots: list[type[BaseModel]] | None = None) -> dict[str, type[BaseModel]]:
    """Return every Pydantic model reachable from the clause-level schemas.

    Starts from EXTRACTION_TYPE_SCHEMAS (or an explicit `roots` list for
    testing) and recursively follows BaseModel-valued fields, so a model
    added to a schema's nested structure in the future is discovered
    automatically rather than requiring a matching update here.
    """
    roots = roots if roots is not None else list(EXTRACTION_TYPE_SCHEMAS.values())
    found: dict[str, type[BaseModel]] = {}
    stack = list(roots)
    while stack:
        model = stack.pop()
        name = model.__name__
        if name in found:
            continue
        found[name] = model
        hints = get_type_hints(model)
        for field_name in model.model_fields:
            annotation = hints.get(field_name)
            if annotation is None:
                continue
            nested = _unwrap_model(annotation)
            if nested is not None and nested.__name__ not in found:
                stack.append(nested)
    return found


def _storage_key(model: type[BaseModel], field_name: str) -> str:
    """Return the name this field is actually stored/serialized under.

    Extraction rows are persisted via `model_dump(by_alias=True)`
    (src/agents/base.py), so a field declared with `alias="object"` on the
    Python attribute `object_` is stored as `"object"` in the JSONB payload
    — that's the name the assembler will look up, and the name this catalog
    must be keyed by. Falls back to the Python field name when no alias is
    set (the common case).
    """
    info = model.model_fields[field_name]
    return info.alias if info.alias else field_name


def iter_schema_fields(roots: list[type[BaseModel]] | None = None) -> list[tuple[str, str]]:
    """Return every (model_name, storage_field_name) pair reachable from the
    schemas, using each field's alias when one is declared (see
    `_storage_key`) so the pairs match real stored-payload keys, not
    Python-only attribute names.

    This is the coverage surface `CATALOG` must fully cover — used by
    tests/unit/test_field_catalog.py to fail CI when a schema field has no
    catalog entry.
    """
    pairs: list[tuple[str, str]] = []
    for model_name, model in iter_schema_models(roots).items():
        excluded = _EXCLUDED_FIELDS.get(model_name, set())
        for field_name in model.model_fields:
            field_name = _storage_key(model, field_name)
            if field_name in excluded:
                continue
            pairs.append((model_name, field_name))
    return pairs


class FieldCatalogError(KeyError):
    """Raised when a (model_name, field_name) pair has no catalog entry."""


def get_entry(model_name: str, field_name: str) -> FieldCatalogEntry:
    """Look up a field's presentation contract.

    Raises FieldCatalogError (not a bare KeyError) so callers get a message
    naming exactly which field is missing, rather than a generic dict
    lookup failure surfacing deep in template rendering.
    """
    model_entries = CATALOG.get(model_name)
    if model_entries is None or field_name not in model_entries:
        raise FieldCatalogError(
            f"No field_catalog entry for {model_name}.{field_name} — "
            "add one to src/core/field_catalog.py:CATALOG."
        )
    return model_entries[field_name]


def material_fields_for(model_name: str) -> frozenset[str]:
    """Return the material-field set for a schema (EAR-2-1).

    These are the fields that must carry a verified evidence span with a
    matching field_name when populated — see docs/law_card_dashboard_plan.md
    §Part 3 (EAR-2-1) for the grounding rule this flag feeds.
    """
    return frozenset(
        name for name, entry in CATALOG.get(model_name, {}).items() if entry.material
    )


def nested_model_name(model_name: str, field_name: str) -> str | None:
    """Return the catalog model name a NESTED/LIST_NESTED field points to.

    Used by src/core/edit_service.py to resolve a dotted field_path like
    "enforcement.max_civil_penalty_usd": the leaf ("max_civil_penalty_usd")
    is looked up against whatever this returns for ("ObligationPayload",
    "enforcement") — i.e. "EnforcementInfo" — not against the parent model.
    Returns None if the field isn't NESTED/LIST_NESTED or isn't found.
    """
    model = iter_schema_models().get(model_name)
    if model is None:
        return None
    hints = get_type_hints(model)
    annotation = hints.get(field_name)
    if annotation is None:
        return None
    nested = _unwrap_model(annotation)
    return nested.__name__ if nested is not None else None
