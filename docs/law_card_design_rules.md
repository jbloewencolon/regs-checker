# Law Card Design Rules (LC-0c)

Ported from the display conventions in `reference/law_card_bundle/` (verified
against `LawCard.jsx`, `PolicyBadges.jsx`, `lawSourceQuotes.js`), adapted to
regs-checker's real data model, and written as testable assertions. Each rule
below is implemented by LC-2 (rendering) and asserted by LC-2c's template tests
against the fixtures in `tests/fixtures/law_cards/` and real CO SB205 data.
Correction to the source bundle's README: regs-checker's existing dashboard theme
(`static/css/style.css`) is **light** (`--bg: #f8f9fa`), not dark — the ported
`--lc-*` tokens (also light, `--lc-paper: #fdfbf7`) need no dark-scheme
reconciliation for MVP. A `prefers-color-scheme: dark` variant is out of scope
until the base dashboard gets one (tracked as a note, not a blocker).

## Rule 1 — Honest-unknown rendering

A null or absent field renders as an explicit **data-gap badge** ("Not extracted"
/ "Not yet triaged" / etc.), never as a default, a dash standing in for a real
value, or a guessed value. This is the single most load-bearing rule in the
bundle and the one most likely to regress silently — enforce it structurally:
the card partial must branch on presence before rendering, not render-then-hide.

**Assertion (LC-2c-1):** for every field with `value: null` in the assembled card
JSON, the rendered card contains a gap-badge marker for that field's `field_path`
and does NOT contain the field's raw `None`/`null`/empty-string representation.

**Assertion (LC-2c-2):** "Not yet triaged" / no-signal states render with a
hollow/outline treatment, never the same fill style as a real low tier. (Ported
from `priority.js`'s `tier: null` vs. a real low-tier fill — regs-checker's
analog is a `confidence_tier` absence, which cannot occur post-extraction today
but a law with zero extractions must still render this way at the law-card level,
not a fabricated "D".)

## Rule 2 — Enforcement gating

An enforcement note/panel renders only when **all** of:
1. The law/extraction is enacted or in force (regs-checker: bill-level
   `applicability_agent`/`enforcement_agent` payload exists and the
   `DocumentVersion.temporal_status` is not `withdrawn`/`vetoed`/`enjoined`).
2. Enforcement data is actually present (non-null `enforcing_body`,
   `penalty_type`, `penalty_description`, or `max_civil_penalty_usd`).
3. The enforcement text is not itself a placeholder (case-insensitive `tbd`
   anywhere in the free-text fields renders the **panel** but must visibly mark
   the penalty amount as "not yet specified," not omit the panel outright — this
   is a refinement over the bundle's blanket TBD-suppression, made possible by
   regs-checker's typed `max_civil_penalty_usd` distinct from free-text
   `enforcement_text`).

**Assertion (LC-2c-3):** a withdrawn/vetoed/enjoined law's card never renders an
enforcement chip/panel, even when the underlying `BillLevelExtraction.enforcement`
payload is non-empty (ported directly from the bundle's REF_NM fixture, which
exists specifically to test this).

**Assertion (LC-2c-4):** an enacted law with `max_civil_penalty_usd: null` and
`enforcement_text` containing "TBD" renders the panel with an explicit
"penalty amount not yet specified" gap badge on the amount field, not a
suppressed panel (ported REF_CT/REF_NM fixture case, refined per above).

## Rule 3 — Verbatim vs. paraphrase honesty

Evidence text is presented as a **quote** only when it is a verified span.
Regs-checker's verification is stronger than the bundle's (4-tier match with
recorded `match_tier`, vs. the bundle's lexical-similarity "verbatim" flag), so
the rule is stricter here:

- `verified: true, match_tier in (1,2)` → render as a highlighted quote, using
  `char_start`/`char_end` to highlight the exact span in a source-context view.
- `verified: true, match_tier in (3,4)` → render as a quote with a **"near
  match"** marker (no char offsets available — never claim a precise highlight
  position for these tiers, per EA2-2's own scope decision).
- `verified: false` → render as plain text with an explicit **"could not be
  verified against the source text"** warning. Never render unverified text
  inside quote styling (blockquote/quote-icon), even truncated.

**Assertion (LC-2c-5):** no `verified: false` evidence span is ever wrapped in
the card's quote markup (search rendered HTML for quote-icon/blockquote
adjacency to any span flagged unverified — must be zero).

**Assertion (LC-2c-6):** every Tier-3/4 span in rendered output carries the
"near match" marker; every Tier-1/2 span carries `char_start`/`char_end`-derived
highlight markup.

## Rule 4 — Paced disclosure

Information is layered behind explicit user action, not dumped flat:
- **L0** (always visible): law title, jurisdiction, status chip, tier/priority.
- **L1** (one click): summary, obligation list (material fields only).
- **L2** (nested, per-extraction): full field list with evidence, via the
  Extractions tab — this is regs-checker's addition; the bundle's L2 numbering
  is skipped intentionally per its own comment, repurposed here for the
  full-detail tier.
- **L3** (deepest): enforcement panel, raw model reasoning, provenance
  (prompt hash, model id, template version).

Each disclosure toggle is a real `<button aria-expanded="true|false">`, not a
`<div onclick>` (a11y requirement, not just a style choice — see LC-5).

**Assertion (LC-2c-7):** every disclosure toggle in rendered HTML is a `<button>`
element with a paired `aria-expanded` attribute and an `aria-controls` pointing
at an existing id in the same fragment.

## Rule 5 — Status taxonomy consistency

Status labels are humanized via a single lookup table (ported from
`POLICY_STATUSES`), never raw enum values rendered directly
(`in_committee` must never appear un-humanized as `"In Committee"`'s raw form).
Status color-coding always pairs with text/icon — never color alone (a11y
requirement, folded in here because it's also a data-honesty requirement: a
colorblind-inaccessible status is also an unreadable one).

**Assertion (LC-2c-8):** every `TemporalStatus`/status value reachable from real
data has a humanized label in the ported taxonomy table; a template test fails
CI if a new enum value is added without a corresponding label (mirrors LC-1b's
"every schema field needs a catalog entry" pattern).

## Rule 6 — Role/actor display (deferred scope note)

The bundle's role-based obligation filtering (active-role prominent, others
collapse into "Also regulates…") depends on a user profile concept
(`activeRole`) that doesn't exist in regs-checker's single-analyst dashboard
today. **Not implemented in MVP** (LC-2/LC-3) — all obligations render for all
roles, grouped by `subject_normalized`/`actor_type` as plain sections, no
role-switcher UI. Revisit if/when a per-user role concept is added to the
dashboard (out of scope for the Law Card plan).

## Rule 7 — Stub / thin-data routing

A law with a triage decision of `not_relevant` for every passage, or zero
extractions after a full run, routes to a **stub card**: dashed border, no
obligations section, explicit "no AI-relevant provisions extracted" message —
never an empty-looking full card that reads as "we checked and found nothing
notable" when the truth is "nothing was extracted." This distinction (thin data
vs. genuinely-empty law) is a direct port of the bundle's `is_stub` routing,
generalized from a curated tracker flag to a computed condition. Auto-routes on
the condition — never a caller-supplied variant flag (ported directly: "the
`is_stub` flag drives variant, not the caller").

**Assertion (LC-2c-9):** `LawCardAssembler` sets a `render_hint: "stub"` field
when `extraction_count == 0` for the serving run; the template branches on this
field, not on any caller-passed parameter.
