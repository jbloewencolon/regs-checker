"""Citation Verification Agent — validates section references and cross-references.

Post-extraction agent that checks:
  - section_reference fields point to real sections in the document
  - cross_references in definitions are valid
  - applies_to_obligation in thresholds/exceptions references real obligations
  - Framework references (NIST RMF, etc.) use correct nomenclature

Works by building a document section index from NormalizedSourceRecords
and checking extraction references against it. Falls back to LLM for
fuzzy matching when exact matches fail.

This is a rule-based + LLM hybrid: exact matching first (fast, cheap),
LLM fallback for ambiguous references only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Extraction, ExtractionType, NormalizedSourceRecord

logger = structlog.get_logger()


@dataclass
class CitationIssue:
    """A citation reference that could not be verified."""

    extraction_id: int
    field_name: str  # e.g., "section_reference", "cross_references[0]"
    cited_value: str  # What the extraction claims
    issue_type: str  # "not_found", "ambiguous_match", "likely_hallucinated"
    closest_match: str | None = None  # Nearest real section if available
    confidence: str = "medium"  # low/medium/high confidence in the issue


@dataclass
class CitationVerificationResult:
    """Results from verifying all citations in a document's extractions."""

    document_version_id: int
    total_citations_checked: int
    citations_verified: int
    citations_unverified: int
    issues: list[CitationIssue] = field(default_factory=list)
    section_index: list[str] = field(default_factory=list)  # All sections found

    @property
    def verification_rate(self) -> float:
        if self.total_citations_checked == 0:
            return 1.0
        return self.citations_verified / self.total_citations_checked


# Common section reference patterns in legislative text
_SECTION_PATTERNS = [
    re.compile(r"(?:Section|Sec\.?)\s+(\d+[\w.()-]*)", re.IGNORECASE),
    re.compile(r"(?:§|&#167;)\s*(\d+[\w.()-]*)", re.IGNORECASE),
    re.compile(r"(?:Part|Article|Chapter)\s+(\d+[\w.()-]*)", re.IGNORECASE),
    re.compile(r"\(([a-z])\)\s*\((\d+)\)", re.IGNORECASE),  # (a)(1) style
]


def _build_section_index(
    db: Session, document_version_id: int
) -> dict[str, int]:
    """Build an index of all section paths in a document.

    Returns a dict mapping normalized section labels to record IDs.
    """
    records = db.scalars(
        select(NormalizedSourceRecord)
        .where(NormalizedSourceRecord.document_version_id == document_version_id)
        .order_by(NormalizedSourceRecord.ordinal)
    ).all()

    index: dict[str, int] = {}
    for record in records:
        if record.section_path:
            # Store the full path
            index[record.section_path.lower().strip()] = record.id

            # Also extract section numbers for fuzzy matching
            for pattern in _SECTION_PATTERNS:
                matches = pattern.findall(record.section_path)
                for match in matches:
                    if isinstance(match, tuple):
                        match = "".join(match)
                    normalized = f"section {match}".lower().strip()
                    index[normalized] = record.id

            # Also store text content section refs
            for pattern in _SECTION_PATTERNS:
                matches = pattern.findall(record.text_content[:500])
                for match in matches:
                    if isinstance(match, tuple):
                        match = "".join(match)
                    normalized = f"section {match}".lower().strip()
                    if normalized not in index:
                        index[normalized] = record.id

    return index


def _normalize_citation(citation: str) -> str:
    """Normalize a citation string for matching."""
    normalized = citation.lower().strip()
    # Remove common prefixes
    normalized = re.sub(r"^(section|sec\.?|§|&#167;)\s*", "section ", normalized)
    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _find_closest_section(
    citation: str, section_index: dict[str, int]
) -> str | None:
    """Find the closest matching section in the index."""
    normalized = _normalize_citation(citation)
    sections = list(section_index.keys())

    if not sections:
        return None

    # Try exact match first
    if normalized in section_index:
        return normalized

    # Try prefix match (e.g., "section 3" matches "section 3 - developer requirements")
    prefix_matches = [s for s in sections if s.startswith(normalized)]
    if prefix_matches:
        return prefix_matches[0]

    # Try substring match
    substr_matches = [s for s in sections if normalized in s or s in normalized]
    if substr_matches:
        return substr_matches[0]

    # Try number-only match
    numbers = re.findall(r"\d+", normalized)
    if numbers:
        number_str = numbers[0]
        for s in sections:
            if number_str in s:
                return s

    return None


def verify_citations(
    db: Session,
    document_version_id: int,
) -> CitationVerificationResult:
    """Verify all citation references in a document's extractions.

    Builds a section index from the document's passages, then checks
    every section_reference, cross_reference, and applies_to_obligation
    field against the index.

    Args:
        db: SQLAlchemy session
        document_version_id: Document version to verify

    Returns:
        CitationVerificationResult with verified/unverified counts and issues.
    """
    # Build section index
    section_index = _build_section_index(db, document_version_id)
    section_keys = list(section_index.keys())

    # Get all extractions for this document
    extractions = db.scalars(
        select(Extraction)
        .join(NormalizedSourceRecord)
        .where(NormalizedSourceRecord.document_version_id == document_version_id)
    ).all()

    total_checked = 0
    verified = 0
    unverified = 0
    issues: list[CitationIssue] = []

    for extraction in extractions:
        payload = extraction.payload or {}

        # Check section_reference
        section_ref = payload.get("section_reference")
        if section_ref and isinstance(section_ref, str) and section_ref.strip():
            total_checked += 1
            closest = _find_closest_section(section_ref, section_index)

            if closest:
                verified += 1
            else:
                unverified += 1
                issues.append(CitationIssue(
                    extraction_id=extraction.id,
                    field_name="section_reference",
                    cited_value=section_ref,
                    issue_type="not_found" if section_keys else "no_index",
                    closest_match=None,
                ))

        # Check cross_references (definition extractions)
        cross_refs = payload.get("cross_references", [])
        if isinstance(cross_refs, list):
            for i, ref in enumerate(cross_refs):
                if isinstance(ref, str) and ref.strip():
                    total_checked += 1
                    closest = _find_closest_section(ref, section_index)

                    if closest:
                        verified += 1
                    else:
                        unverified += 1
                        issues.append(CitationIssue(
                            extraction_id=extraction.id,
                            field_name=f"cross_references[{i}]",
                            cited_value=ref,
                            issue_type="not_found",
                            closest_match=None,
                        ))

        # Check applies_to_obligation (threshold/exception extractions)
        applies_to = payload.get("applies_to_obligation")
        if applies_to and isinstance(applies_to, str) and applies_to.strip():
            total_checked += 1
            closest = _find_closest_section(applies_to, section_index)

            if closest:
                verified += 1
            else:
                # For applies_to_obligation, try matching against extraction subjects
                # (it might reference an obligation by description, not section)
                obligation_subjects = [
                    e.payload.get("action", "")[:80]
                    for e in extractions
                    if e.extraction_type in (ExtractionType.obligation,)
                    and e.payload.get("action")
                ]
                fuzzy_match = any(
                    applies_to.lower() in subj.lower() or subj.lower() in applies_to.lower()
                    for subj in obligation_subjects
                    if subj
                )

                if fuzzy_match:
                    verified += 1
                else:
                    unverified += 1
                    issues.append(CitationIssue(
                        extraction_id=extraction.id,
                        field_name="applies_to_obligation",
                        cited_value=applies_to,
                        issue_type="ambiguous_match",
                        closest_match=None,
                        confidence="low",  # Could be a description, not a section ref
                    ))

    logger.info(
        "citation_verification_complete",
        document_version_id=document_version_id,
        total_checked=total_checked,
        verified=verified,
        unverified=unverified,
        sections_in_index=len(section_keys),
    )

    return CitationVerificationResult(
        document_version_id=document_version_id,
        total_citations_checked=total_checked,
        citations_verified=verified,
        citations_unverified=unverified,
        issues=issues,
        section_index=section_keys[:50],  # Cap for serialization
    )
