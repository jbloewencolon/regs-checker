# Enforcement Fork Decisions — V4a

**Status:** Candidates. Pending VC ratification. Phase 2c enforcement_normalizer.py is the normalization code.

## F1 — Cure period placement
A cure period (e.g., 30 days) gates civil_penalty but is not itself an enforcement type.
**Decision:** Track cure_period_days as a structured field on civil_penalty records; not a separate code.

## F2 — AG vs Regulatory enforcement split
Attorney general enforcement (ag_enforcement) vs. specialized agency enforcement (regulatory_enforcement).
Many laws name both.
**Decision:** Keep split — AG has distinct authority in most states; regulatory agency is different.
