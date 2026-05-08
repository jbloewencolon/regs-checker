"""Pydantic v2 schemas for extraction outputs — strict mode validation.

These schemas enforce the per-type structure within the unified `extractions`
table's JSONB `payload` column (Recommendation #12). Evidence spans are
validated via string matching against the source passage (Recommendation #3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EvidenceSpan(BaseModel):
    """A verbatim text span from the source passage supporting a field."""

    field_name: str | None = Field(default=None, description="Name of the extraction field this evidence supports")
    text: str = Field(description="Verbatim text from the source passage")
    char_start: int | None = Field(default=None, description="Start character offset in passage")
    char_end: int | None = Field(default=None, description="End character offset in passage")


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


class TimelineInfo(BaseModel):
    """Timeline associated with an obligation."""

    effective_date: str | None = Field(default=None, description="ISO date or textual date")
    compliance_deadline: str | None = None
    sunset_date: str | None = None
    phase_in_period: str | None = None
    timeline_text: str | None = Field(default=None, description="Raw timeline language")


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
    modality: str = Field(description="Must / shall / may / should / prohibited")
    action: str = Field(description="What the subject must do or refrain from doing")
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
    consent_requirements: ConsentRequirement | None = Field(
        default=None,
        description="Consent or notice mechanism required for this obligation, if any",
    )
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
    audits: list[AuditRequirement] = Field(
        default_factory=list, description="Specific audit/assessment requirements"
    )
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
        description="high / medium / low — based on likelihood and compliance impact"
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
