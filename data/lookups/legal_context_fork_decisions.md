# Legal Context Fork Decisions — V4b

**Status:** Locked. Implemented in src/core/legal_context.py (Phase 2d).

## Design decisions already in code
- `other` with cross_law_refs only (and no preemption_language, no related_authority) → `cross_law_reference` (display=True)
- `other` with preemption_language or related_authority → `unclassified` (display=False)
- `unknown/unrecognized` conflict_type → `unclassified`
- Case-insensitive matching with whitespace strip on input

No open forks. classify_legal_context() in legal_context.py is the normalization function.
