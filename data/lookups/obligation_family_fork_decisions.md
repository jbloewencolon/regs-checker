# Obligation Family Fork Decisions — V3a

**Status:** Candidates. Pending VC ratification.

## F1 — reporting vs disclosure vs incident_reporting
`reporting` (mechanism_type) overlaps `reporting_to_regulator` and `disclosure_to_user` and `incident_reporting`.
- `reporting_to_regulator` — periodic compliance submissions to agency
- `disclosure_to_user` — notice/disclosure to the individual affected
- `incident_reporting` — breach/safety incident reports triggered by events
**Decision pending:** triage by reporting_recipient + trigger context in extraction payload.

## F2 — disclosure vs notification vs labeling_watermarking
`disclosure` and `notification` both appear in mechanism_type.
- `disclosure_to_user` — telling individuals AI is used
- `labeling_watermarking` — marking AI-generated content itself
**Recommended split:** notification → labeling_watermarking when content is AI-generated; disclosure_to_user when the AI interaction is disclosed.

## F3 — modality-based family assignment
`must/shall` modality obligations can map to any family — the family depends on the action, not the modality.
`prohibited` modality → `prohibition` family.
**Decision:** modality alone is not sufficient to assign a family code; requires action field analysis.

## F4 — Nondiscrimination → prohibition
IAPP "Nondiscrimination" column maps to `prohibition` (ban on discriminatory AI use).
**Confirm:** Some laws have active non-discrimination obligations rather than prohibitions.
