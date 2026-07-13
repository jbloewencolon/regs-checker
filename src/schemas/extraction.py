"""Pydantic v2 schemas for extraction outputs — strict mode validation.

These schemas enforce the per-type structure within the unified `extractions`
table's JSONB `payload` column (Recommendation #12). Evidence spans are
validated via string matching against the source passage (Recommendation #3).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class EvidenceSpan(BaseModel):
    """A verbatim text span from the source passage supporting a field."""

    field_name: str | None = Field(default=None, description="Name of the extraction field this evidence supports")
    text: str = Field(description="Verbatim text from the source passage")
    char_start: int | None = Field(default=None, description="Start character offset in passage")
    char_end: int | None = Field(default=None, description="End character offset in passage")
    source_url: str | None = Field(default=None, description="URL of the authoritative source document")
    section_anchor: str | None = Field(default=None, description="Section path within the source document")


class AbstentionResult(BaseModel):
    """Returned when an agent determines the passage contains no extractable content."""

    detected: bool = False
    reason: str = Field(description="Why no extraction was possible")


# ---------------------------------------------------------------------------
# Interpretation risk annotation (embedded on obligation + rights payloads)
#
# Replaces the standalone AmbiguityAgent. The obligation and rights agents
# now populate this field during their primary extraction pass — zero extra
# LLM calls, findings attached to the obligation or right they affect.
# ---------------------------------------------------------------------------


class InterpretationRisk(BaseModel):
    """A term, provision, or condition that creates compliance uncertainty.

    Populated inline by the obligation and rights_protection agents when they
    encounter vague language, undefined references, or conflicting provisions
    while performing their primary extraction. Not a standalone extraction type.
    """

    risk_type: Literal[
        "vague_term",
        "undefined_reference",
        "conflicting_provision",
        "scope_ambiguity",
        "temporal_ambiguity",
        "conditional_ambiguity",
    ] = Field(description="Category of interpretation risk")
    term: str = Field(
        description="The specific term, phrase, or provision that is ambiguous"
    )
    concern: str = Field(
        description="Why this creates compliance uncertainty (1-2 sentences)"
    )
    severity: Literal["low", "medium", "high", "critical"] = Field(
        default="medium",
        description="Compliance impact: low=minor drafting imprecision, "
        "critical=obligation scope is genuinely unclear",
    )
    evidence_spans: list[EvidenceSpan] = Field(
        default_factory=list,
        description="Verbatim quotes from the passage containing the ambiguous term",
    )


# ---------------------------------------------------------------------------
# Obligation Agent output (absorbs: obligation + timeline + enforcement)
# Recommendation #1
# ---------------------------------------------------------------------------


class SafeHarbor(BaseModel):
    """An affirmative defense or safe harbor provision embedded in an obligation."""

    framework: str | None = Field(
        default=None,
        description="The framework or standard that triggers the safe harbor "
        "(e.g., 'NIST AI RMF', 'ISO/IEC 42001', 'FTC guidelines')",
    )
    conditions: str | None = Field(
        default=None,
        description="What the entity must do to qualify for the safe harbor",
    )
    protection: str | None = Field(
        default=None,
        description="What legal protection the safe harbor provides "
        "(e.g., 'affirmative defense against liability', 'rebuttable presumption of compliance')",
    )
    evidence_text: str | None = Field(
        default=None, description="Verbatim safe harbor language from the passage"
    )


class ConsentRequirement(BaseModel):
    """A notice or consent mechanism associated with an obligation."""

    consent_type: str | None = Field(
        default=None,
        description="Type: opt_in, opt_out, notice, notice_and_choice, disclosure",
    )
    timing: str | None = Field(
        default=None,
        description="When consent/notice must be obtained: before, at, after (the AI interaction or decision)",
    )
    method: str | None = Field(
        default=None,
        description="How consent must be obtained: written, electronic, verbal, in_app, posted",
    )
    subject_matter: str | None = Field(
        default=None,
        description="What the consent or notice covers (AI use, data processing, automated decision)",
    )


# EA6-5: fields TimelineInfo attempts to normalize to ISO-8601.
_TIMELINE_DATE_FIELDS = ("effective_date", "compliance_deadline", "sunset_date")
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TimelineInfo(BaseModel):
    """Timeline associated with an obligation."""

    effective_date: str | None = Field(default=None, description="ISO date or textual date")
    compliance_deadline: str | None = None
    sunset_date: str | None = None
    phase_in_period: str | None = None
    timeline_text: str | None = Field(default=None, description="Raw timeline language")
    date_parse_status: dict[str, str] = Field(
        default_factory=dict,
        description="Per-field parse outcome for effective_date/compliance_deadline/"
        "sunset_date: 'parsed' when normalize_date() produced ISO-8601, 'unparsed' "
        "when the raw model text was passed through unchanged because it couldn't be "
        "parsed. A field absent from this dict was never populated (null/empty) — "
        "distinct from being populated but unparseable. Downstream date arithmetic "
        "(e.g. earliest-deadline sorting) must skip 'unparsed' fields rather than "
        "treat free text as if it were ISO-8601.",
    )

    @field_validator("effective_date", "compliance_deadline", "sunset_date", mode="before")
    @classmethod
    def _normalize_date_field(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        from src.core.date_normalizer import normalize_date
        return normalize_date(v) or v

    @model_validator(mode="after")
    def _set_date_parse_status(self) -> TimelineInfo:
        status: dict[str, str] = {}
        for field_name in _TIMELINE_DATE_FIELDS:
            value = getattr(self, field_name)
            if not value or not value.strip():
                continue
            status[field_name] = "parsed" if _ISO_DATE_PATTERN.match(value.strip()) else "unparsed"
        self.date_parse_status = status
        return self


class EnforcementInfo(BaseModel):
    """Enforcement mechanism for an obligation."""

    enforcing_body: str | None = None
    penalty_type: str | None = None
    penalty_description: str | None = None
    private_right_of_action: bool | None = None
    enforcement_text: str | None = Field(default=None, description="Raw enforcement language")
    max_civil_penalty_usd: int | None = Field(
        default=None,
        description="Maximum civil penalty in USD if specified (e.g., 10000)",
    )
    cure_period_days: int | None = Field(
        default=None,
        description="Cure period in days before enforcement action (e.g., 60)",
    )


class ObligationPayload(BaseModel):
    """Extraction payload for the consolidated Obligation Agent.

    Co-extracts obligation, timeline, and enforcement in a single pass
    because these are structurally co-located in legislative text.
    """

    subject: str = Field(description="Who must comply (the regulated entity)")
    subject_normalized: str | None = Field(
        default=None, description="Normalized subject category"
    )

    @field_validator("subject_normalized", mode="before")
    @classmethod
    def _sanitize_subject_normalized(cls, v: Any) -> Any:
        from src.core.actor_normalizer import sanitize_normalized_actor
        return sanitize_normalized_actor(v) if isinstance(v, str) else v

    modality: str = Field(default="", description="Must / shall / may / should / prohibited")

    @field_validator("modality", mode="before")
    @classmethod
    def _normalize_modality(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        _MODALITY_MAP = {
            "must": "must", "shall": "shall", "required": "must",
            "is required to": "must", "are required to": "must",
            "may": "may", "is permitted": "may", "are permitted": "may",
            "should": "should", "ought to": "should", "is recommended": "should",
            "prohibited": "prohibited", "shall not": "shall_not",
            "must not": "must_not", "may not": "may_not",
            "is not permitted": "prohibited", "are not permitted": "prohibited",
            "is prohibited": "prohibited", "are prohibited": "prohibited",
            "cannot": "prohibited", "can not": "prohibited",
            "is forbidden": "prohibited", "are forbidden": "prohibited",
        }
        return _MODALITY_MAP.get(v.strip().lower(), v)

    action: str = Field(default="", description="What the subject must do or refrain from doing")
    object_: str | None = Field(
        default=None, alias="object", description="What the action applies to"
    )
    condition: str | None = Field(default=None, description="Conditions or triggers")
    jurisdiction: str | None = None
    section_reference: str | None = None
    timeline: TimelineInfo | None = None
    enforcement: EnforcementInfo | None = None
    preemption_signals: list[str] = Field(
        default_factory=list,
        description="Verbatim preemption language found in the passage "
        "(e.g., 'this section does not preempt', 'notwithstanding any state law')",
    )
    safe_harbor: SafeHarbor | None = Field(
        default=None,
        description="Safe harbor or affirmative defense provision associated with this obligation, if any",
    )

    @field_validator("safe_harbor", mode="before")
    @classmethod
    def _coerce_safe_harbor(cls, v: Any) -> Any:
        # LLMs occasionally emit a list when only one value is expected.
        # Take the first element to keep the schema singular.
        if isinstance(v, list):
            return v[0] if v else None
        return v

    consent_requirements: ConsentRequirement | None = Field(
        default=None,
        description="Consent or notice mechanism required for this obligation, if any",
    )

    @field_validator("consent_requirements", mode="before")
    @classmethod
    def _coerce_consent_requirements(cls, v: Any) -> Any:
        if isinstance(v, list):
            return v[0] if v else None
        return v
    interpretation_risks: list[InterpretationRisk] = Field(
        default_factory=list,
        description="Vague terms, undefined references, or conflicting provisions "
        "noticed during extraction that create compliance uncertainty. "
        "Omit if none found.",
    )


# ---------------------------------------------------------------------------
# Definition & Actor Agent output (absorbs: definition + actor_mapping + framework_ref)
# Recommendation #1
# ---------------------------------------------------------------------------


class ActorMapping(BaseModel):
    """An actor/role identified in the regulatory text."""

    actor_name: str
    actor_type: str | None = None  # e.g. "regulator", "developer", "deployer"
    responsibilities: list[str] = Field(default_factory=list)

    @field_validator("actor_type", mode="before")
    @classmethod
    def _sanitize_actor_type(cls, v: Any) -> Any:
        from src.core.actor_normalizer import sanitize_normalized_actor
        return sanitize_normalized_actor(v) if isinstance(v, str) else v

    @field_validator("responsibilities", mode="before")
    @classmethod
    def _coerce_responsibilities(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return v


class FrameworkReference(BaseModel):
    """A reference to an external framework or standard."""

    framework_name: str
    section_or_standard: str | None = None
    relationship: str | None = None  # e.g. "incorporates", "references", "aligns with"


class DefinitionActorPayload(BaseModel):
    """Extraction payload for the consolidated Definition & Actor Agent.

    Co-extracts definitions, actor mappings, and framework references
    because all are 'what do the words mean' tasks operating on preamble/
    definitions sections.
    """

    term: str = Field(description="The defined term")
    definition_text: str = Field(description="The full definition")
    scope: str | None = Field(default=None, description="Scope or applicability of the definition")
    cross_references: list[str] = Field(
        default_factory=list, description="Other sections referencing this definition"
    )
    actors: list[ActorMapping] = Field(
        default_factory=list, description="Actor roles mentioned in this definition context"
    )
    framework_refs: list[FrameworkReference] = Field(
        default_factory=list, description="External framework references"
    )


# ---------------------------------------------------------------------------
# Threshold & Exception Agent output (absorbs: threshold + exception)
# Recommendation #1
# ---------------------------------------------------------------------------


class ThresholdExceptionPayload(BaseModel):
    """Extraction payload for the consolidated Threshold & Exception Agent.

    Co-extracts thresholds and exceptions because both are 'boundary condition'
    extractions — when does the obligation apply, and when doesn't it?
    """

    threshold_sub_type: str | None = Field(
        default=None,
        description="High-level category: "
        "scope=who/what triggers applicability (size, volume, FLOPS, sector); "
        "temporal=deadlines, response windows, phase-in periods; "
        "exemption=carve-outs, safe harbors, excluded entity types; "
        "other=doesn't fit the above",
    )
    threshold_type: str | None = Field(
        default=None,
        description="Specific type within the sub_type (numeric, categorical, "
        "monetary, date, compute, entity_type, sector, etc.)",
    )
    threshold_value: str | None = None

    @field_validator("threshold_value", mode="before")
    @classmethod
    def _coerce_threshold_value(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v)
    threshold_unit: str | None = None
    threshold_condition: str | None = Field(
        default=None, description="The condition expression"
    )
    applies_to_obligation: str | None = Field(
        default=None, description="Which obligation this threshold modifies"
    )
    exceptions: list[ExceptionItem] | None = None

    # Matrix fields — structured data for State AI Regulation Matrix
    compute_flops: float | None = Field(
        default=None,
        description="Compute threshold in FLOPS if specified (e.g., 10e26)",
    )
    compute_description: str | None = Field(
        default=None,
        description="Human-readable compute threshold description",
    )
    sector_applicability: list[str] | None = Field(
        default=None,
        description="Consequential decision sectors: healthcare, employment, "
        "credit, housing, insurance, criminal_justice, education, government",
    )

    @field_validator("sector_applicability", mode="before")
    @classmethod
    def _normalize_sectors(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        _SECTOR_MAP = {
            "health care": "healthcare", "health-care": "healthcare",
            "medical": "healthcare", "hospital": "healthcare",
            "law enforcement": "criminal_justice", "policing": "criminal_justice",
            "criminal justice": "criminal_justice", "public safety": "criminal_justice",
            "lending": "credit", "financial services": "financial_services",
            "banking": "financial_services", "fintech": "financial_services",
            "higher education": "education", "schools": "education",
            "university": "education", "k-12": "education",
            "real estate": "housing", "rental": "housing",
            "worker": "employment", "workplace": "employment", "labor": "employment",
        }
        return [_SECTOR_MAP.get(s.strip().lower(), s) for s in v if isinstance(s, str)]

    # Scope sub-type structured fields
    revenue_threshold_usd: int | None = Field(
        default=None,
        description="Annual revenue threshold in USD that triggers applicability (scope sub-type only)",
    )
    employee_threshold: int | None = Field(
        default=None,
        description="Employee count threshold that triggers applicability (scope sub-type only)",
    )
    consumer_data_threshold: int | None = Field(
        default=None,
        description="Number of consumers' data processed that triggers applicability (scope sub-type only)",
    )


class ExceptionItem(BaseModel):
    """A single exception to a regulatory obligation."""

    exception_type: str = Field(description="Type: carve-out, safe-harbor, exemption, etc.")
    description: str
    conditions: str | None = None
    scope: str | None = None

    @field_validator("conditions", "scope", mode="before")
    @classmethod
    def _coerce_list_to_str(cls, v: Any) -> Any:
        # LLMs sometimes emit a list of conditions; join them into a string.
        if isinstance(v, list):
            return "; ".join(str(item) for item in v) if v else None
        return v


# Forward reference update
ThresholdExceptionPayload.model_rebuild()


# ---------------------------------------------------------------------------
# Ambiguity Agent output (unchanged — meta-analysis, stays separate)
# Recommendation #1
# ---------------------------------------------------------------------------


class AmbiguityPayload(BaseModel):
    """Extraction payload for the Ambiguity Agent.

    This is a meta-analysis agent identifying vague or ambiguous language.
    Kept separate because it's genuinely different from extraction.
    """

    ambiguous_text: str = Field(description="The ambiguous passage")
    ambiguity_type: str = Field(
        description="Type: vague_term, conflicting_provisions, undefined_reference, etc."
    )
    severity: str = Field(description="low / medium / high / critical")
    affected_obligations: list[str] = Field(
        default_factory=list, description="Obligation references affected"
    )
    interpretation_notes: str | None = None
    suggested_clarification: str | None = None


# ---------------------------------------------------------------------------
# Rights & Protections Agent output (individual rights granted by AI laws)
# ---------------------------------------------------------------------------


class RemedyInfo(BaseModel):
    """A remedy or recourse available to the rights holder."""

    remedy_type: str = Field(
        description="Type: complaint, appeal, damages, injunction, deletion, correction"
    )
    description: str
    available_to: str | None = Field(
        default=None, description="Who can invoke this remedy"
    )
    time_limit: str | None = Field(
        default=None, description="Deadline to exercise the remedy"
    )


class RightsProtectionPayload(BaseModel):
    """Extraction payload for the Rights & Protections Agent.

    Captures individual rights and protections granted by AI legislation —
    the flip side of obligations. While obligations define what entities
    must do, rights define what individuals are entitled to.
    """

    right_holder: str = Field(
        description="Who holds the right (e.g., consumer, employee, applicant, data subject)"
    )
    right_holder_normalized: str | None = Field(
        default=None, description="Normalized category (consumer, employee, public)"
    )

    @field_validator("right_holder_normalized", mode="before")
    @classmethod
    def _sanitize_right_holder_normalized(cls, v: Any) -> Any:
        from src.core.actor_normalizer import sanitize_normalized_actor
        return sanitize_normalized_actor(v) if isinstance(v, str) else v
    protected_categories: list[str] = Field(
        default_factory=list,
        description="Subject categories explicitly protected: consumer, employee, candidate, "
        "student, patient, minor, tenant, borrower, job_applicant",
    )
    right_type: str = Field(
        description="Type: notice, explanation, opt_out, appeal, deletion, "
        "human_review, non_discrimination, portability, access"
    )
    right_description: str = Field(
        description="Full description of the right in legal language"
    )
    trigger_condition: str | None = Field(
        default=None, description="When the right is activated (e.g., adverse decision, AI interaction)"
    )
    duty_bearer: str | None = Field(
        default=None, description="Who must fulfill this right (developer, deployer, employer)"
    )
    remedies: list[RemedyInfo] = Field(
        default_factory=list, description="Available remedies if right is violated"
    )
    section_reference: str | None = None
    jurisdiction: str | None = None
    interpretation_risks: list[InterpretationRisk] = Field(
        default_factory=list,
        description="Vague terms, undefined references, or conflicting provisions "
        "noticed during extraction that create compliance uncertainty. "
        "Omit if none found.",
    )


# ---------------------------------------------------------------------------
# Compliance Mechanisms Agent output (procedural requirements)
# ---------------------------------------------------------------------------


class AuditRequirement(BaseModel):
    """A specific audit or assessment requirement."""

    audit_type: str = Field(
        description="Type: bias_audit, impact_assessment, risk_assessment, "
        "algorithmic_audit, third_party_audit, self_certification"
    )
    frequency: str | None = Field(
        default=None, description="How often (annual, before deployment, ongoing)"
    )
    assessor: str | None = Field(
        default=None, description="Who performs it (internal, third-party, regulator)"
    )
    scope: str | None = Field(
        default=None, description="What is assessed"
    )
    reporting_to: str | None = Field(
        default=None, description="Who receives the results"
    )
    public_disclosure: bool | None = Field(
        default=None, description="Whether results must be made public"
    )


class ComplianceMechanismPayload(BaseModel):
    """Extraction payload for the Compliance Mechanisms Agent.

    Captures procedural compliance requirements: impact assessments, audits,
    registration, certification, record-keeping, and reporting mandates.
    These are structured procedural obligations with specific parameters
    (who audits, how often, what's assessed, where results go).
    """

    mechanism_type: str = Field(
        description="Type: impact_assessment, bias_audit, registration, "
        "certification, record_keeping, reporting, disclosure, notification"
    )
    description: str = Field(
        description="Full description of the compliance requirement"
    )
    responsible_party: str = Field(
        description="Who must perform this compliance activity"
    )
    responsible_party_normalized: str | None = Field(
        default=None, description="Normalized: developer, deployer, operator, vendor"
    )

    @field_validator("responsible_party_normalized", mode="before")
    @classmethod
    def _sanitize_responsible_party_normalized(cls, v: Any) -> Any:
        from src.core.actor_normalizer import sanitize_normalized_actor
        return sanitize_normalized_actor(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def _reconcile_responsible_party_normalized(self) -> "ComplianceMechanismPayload":
        # QA-3: the prompt offers only four normalization buckets, so the
        # model force-fits parties that match none of them ("person who acts
        # as a creator" → "developer"). Keep the LLM value only when the raw
        # phrase supports it; otherwise defer to the ratified alias table or
        # null (which routes the term to vocab review).
        from src.core.actor_normalizer import reconcile_normalized_actor
        self.responsible_party_normalized = reconcile_normalized_actor(
            self.responsible_party, self.responsible_party_normalized
        )
        return self
    audits: list[AuditRequirement] = Field(
        default_factory=list, description="Specific audit/assessment requirements"
    )

    @field_validator("audits", "nist_measure_refs", mode="before")
    @classmethod
    def _coerce_none_to_empty_list(cls, v: Any) -> Any:
        # LLMs sometimes emit null for empty list fields; coerce to [].
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v
    record_retention_period: str | None = Field(
        default=None, description="How long records must be kept (raw text, e.g. '3 years')"
    )
    retention_period_months: int | None = Field(
        default=None,
        description="Record retention period in months (e.g. 36 for 3 years, 12 for 1 year)",
    )
    retention_subject: str | None = Field(
        default=None,
        description="What must be retained (e.g. 'impact assessments', 'audit results', "
        "'consumer requests', 'training data documentation')",
    )
    reporting_frequency: str | None = Field(
        default=None, description="How often reports must be filed"
    )
    reporting_recipient: str | None = Field(
        default=None, description="Who receives compliance reports"
    )
    section_reference: str | None = None
    jurisdiction: str | None = None

    # Matrix flags — classification booleans for State AI Regulation Matrix
    is_bias_testing: bool = Field(
        default=False,
        description="True if this mechanism involves bias/discrimination testing",
    )
    is_red_teaming: bool = Field(
        default=False,
        description="True if this mechanism involves adversarial/red-team testing",
    )
    nist_measure_refs: list[str] | None = Field(
        default=None,
        description="Specific NIST AI RMF measure references (e.g., 'MEASURE-2.1')",
    )
    assessment_frequency_months: int | None = Field(
        default=None,
        description="Impact assessment frequency in months if specified (e.g., 12 for annual)",
    )
    is_third_party_audit: bool = Field(
        default=False,
        description="True if an independent third party must perform the audit/assessment",
    )
    incident_reporting_hours: int | None = Field(
        default=None,
        description="Hours within which incidents must be reported to AG/regulator",
    )


# ---------------------------------------------------------------------------
# Preemption Signal Agent output (cross-jurisdictional conflict detection)
# ---------------------------------------------------------------------------


class CrossLawReference(BaseModel):
    """A structured reference to another law or statute."""

    reference_type: str = Field(
        description="Relationship: supersedes, incorporates, conflicts_with, "
        "defined_by, supplements, notwithstanding, subject_to"
    )
    law_name: str | None = Field(
        default=None,
        description="Name or citation of the referenced law "
        "(e.g., 'CCPA', 'Federal AI Act', '15 U.S.C. § 45')",
    )
    section: str | None = Field(
        default=None, description="Specific section of the referenced law, if given"
    )
    description: str | None = Field(
        default=None, description="Plain-language description of the reference"
    )


class PreemptionSignalPayload(BaseModel):
    """Extraction payload for the Preemption Signal Agent.

    Detects cross-jurisdictional conflicts: federal preemption, Commerce Clause
    tensions, cross-state contradictions, and First Amendment challenges.
    """

    conflict_type: str = Field(
        description="Type: federal_preemption, interstate_commerce, cross_state_conflict, "
        "first_amendment, dormant_commerce_clause, agency_jurisdiction, other"
    )
    description: str = Field(
        description="Plain-language description of the conflict or preemption risk"
    )
    related_authority: str | None = Field(
        default=None,
        description="The preempting authority (e.g., 'Dec 2025 Federal EO on AI', "
        "'US Constitution Art. I § 8')",
    )
    severity: str = Field(
        default="medium",
        description="high / medium / low — based on likelihood and compliance impact. "
        "Defaults to 'medium' when LLM omits the field.",
    )
    preemption_language: str | None = Field(
        default=None,
        description="Verbatim preemption clause from the passage if present "
        "(e.g., 'nothing in this section shall preempt federal law')",
    )
    cross_law_refs: list[CrossLawReference] = Field(
        default_factory=list,
        description="Structured references to other laws or statutes this passage "
        "references, incorporates, supersedes, or conflicts with",
    )
    section_reference: str | None = None
    jurisdiction: str | None = None


# ---------------------------------------------------------------------------
# Registry mapping extraction types to payload schemas
# ---------------------------------------------------------------------------

EXTRACTION_TYPE_SCHEMAS: dict[str, type[BaseModel]] = {
    "obligation": ObligationPayload,
    "definition": DefinitionActorPayload,
    "actor_mapping": DefinitionActorPayload,
    "framework_ref": DefinitionActorPayload,
    "threshold": ThresholdExceptionPayload,
    "exception": ThresholdExceptionPayload,
    "ambiguity": AmbiguityPayload,
    "rights_protection": RightsProtectionPayload,
    "compliance_mechanism": ComplianceMechanismPayload,
    "preemption_signal": PreemptionSignalPayload,
}
