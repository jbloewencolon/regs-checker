# Law card reference fixtures

Four law objects converted (LC-0b, 2026-07-19) from
`reference/law_card_bundle/fixtures/refLaws.js` — machine-converted via Node
(`node -e "import(...)"`, not hand-transcribed) so the values are byte-identical to
the source. Regenerate with the same approach if the source `refLaws.js` changes.

These span the LawCard design matrix and double as the render-contract test corpus
for the Law Card Dashboard (Phase LC, `tasks.md`):

| Fixture | Status | Data quality | Actor roles | Enforcement | Effective date |
|---|---|---|---|---|---|
| `ref_co.json` | enacted | curated | deployer + developer | null | future (2026-06-30) |
| `ref_ct.json` | effective | curated | deployer + developer + provider | present + TBD | null |
| `ref_nm.json` | withdrawn | curated | deployer + developer | present + TBD | null |
| `ref_ny.json` | effective | stub (`is_stub: true`) | developer only | null | null |

Each exercises a distinct branch of the design rules in
`docs/law_card_design_rules.md`:
- `ref_co` — null-enforcement honest-unknown; multi-actor collapse/expand; deadline
  chip / future-effective-date rendering.
- `ref_ct` — enforcement display on an enacted law; multi-role toggle including the
  `provider` role.
- `ref_nm` — enforcement-gating (must NOT show for a withdrawn law even though a
  note exists); "TBD" honest-unknown handling.
- `ref_ny` — stub-variant routing (`is_stub` drives rendering, not the caller);
  single-actor card (no role toggle needed).

**Note on shape:** this is the *curated tracker* law shape (flat `obligations[]`
array on the law object), not regs-checker's `Extraction.payload` shape
(`ObligationPayload` etc., see `src/schemas/extraction.py`). The Law Card
Dashboard's `law_card_assembler.py` and `field_catalog.py` work from
regs-checker's real schemas; these fixtures are for testing the *display/design
contract* (badges, gating rules, disclosure states), not the assembler's field
mapping. See `docs/law_card_dashboard_plan.md` §1.3 for the shape mismatch this
implies for any direct reuse.
