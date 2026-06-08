# Rights Fork Decisions — V3b

**Status:** Candidates. Pending VC ratification.

## F1 — Explanation vs incident reporting (IAPP column overlap)
IAPP "Explanation/incident reporting" maps to both `explanation` (right) and `incident_reporting` (obligation family).
**Split rule:** A right entry (rights_protection extraction) → `explanation`. A compliance mechanism entry → `incident_reporting`.

## F2 — Opt out vs appeal (IAPP column overlap)
IAPP "Opt out/appeal" maps to both `opt_out` and `appeal`.
**Split rule:** Right to opt *before* a decision → `opt_out`. Right to challenge *after* a decision → `appeal`.

## F3 — Third-party review vs human_review
IAPP "Third-party review" is an obligation on the entity (third_party_audit), not an individual right.
**Rule:** third_party_review as an obligation → obligation_family third_party_audit. As a right → human_review.

## F4 — Labeling vs notice
IAPP "Labeling/notification" = labeling obligation on the entity. As an individual's right: notice.
**Split rule:** Individual's entitlement to know → `notice`. Entity's duty to label content → `labeling_watermarking` (obligation family).
