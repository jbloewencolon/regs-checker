# Phase 3 Completion Log — Confidence-Only Publish Gate

**Status:** Phase 3 (Remediation — P3) — Core items complete; P3-2 (live PN migration) pending

**Execution period:** 2026-07-02 to 2026-07-06

## Summary

Phase 3 removes the P2 review-status='approved' requirement from the sync gate and
replaces it with a pure confidence-tier gate (A/B/C; D ineligible). To prevent
explicitly-rejected extractions from reaching Policy Navigator despite high
confidence, a safety filter (`review_status != 'rejected'`) acts as an analyst
veto mechanism.

**Result:** Pending/flagged high-confidence extractions can now sync automatically,
while analysts retain power to prevent sync via explicit rejection.

## Completed Items

### P3-1 ✅ Sync gate relaxation (confidence-tier only)

**Committed:** 2026-07-02 (commit 53e1977), re-implemented 2026-07-06 (commit 75590db)

Both sync legs (`sync_extractions()` id-cursor and `sync_updates()` change-propagation)
updated to gate purely on `confidence_tier IN eligible_tiers` instead of requiring
`review_status='approved'`.

**Changes:**
- Removed `review_status='approved'` check from three count/fetch queries in `sync_extractions()`
- Removed `review_status='approved'` check from eligibility evaluation in `sync_updates()`
- Updated module docstring and inline comments to document the new gate

**Safety guard added 2026-07-06:**
- Both legs now apply `review_status != 'rejected'` filter (analyst veto)
- Prevents explicitly-rejected high-confidence items from syncing
- Allows pending/flagged/verified items at A/B/C to sync (improvement over P2)

**Module:** `src/scripts/sync_extractions.py`

### P3-3 ✅ Update propagation leg (change-based sync)

**Committed:** Same as P3-1

The `sync_updates()` leg (P2-6, now P3-3) was updated in sync with P3-1 to apply
the same eligibility rule: `confidence_tier in eligible_tiers and review_status != 'rejected'`.

Previously gated by P2-6's change-watermark mechanism (updated_at > last_synced_at),
it now applies the same confidence-only eligibility as the id-cursor leg, ensuring
both legs use consistent logic.

**Module:** `src/scripts/sync_extractions.py`

### P3-6 ✅ Unit tests for eligibility logic

**Committed:** 2026-07-06 (commit c0b0d3a)

**File:** `tests/unit/test_sync_extractions.py` (22 tests, all passing)

**Test classes:**
1. **TestEligibleTiers** (8 tests)
   - Tier-floor helper edge cases (C, B, A, D defaults, invalid inputs)
   - Case-insensitivity, fallback behavior

2. **TestP3SyncEligibilityLogic** (10 tests)
   - All combinations of tier (A/B/C/D) and review_status (approved/pending/flagged/verified/rejected)
   - Confirms tier-D always ineligible, rejected always ineligible, A/B/C + non-rejected = eligible
   - Validates both sync legs apply the same logic

3. **TestP3RegressionAgainstP2** (4 tests)
   - Tier C + flagged: P2 blocked, P3 syncs (improvement)
   - Tier B + pending: P2 blocked, P3 syncs (improvement)
   - Tier A + approved: P2 synced, P3 syncs (unchanged)
   - Analyst veto works: Tier A + rejected → blocked (new safety mechanism)

**All 1090 unit tests passing** (including 22 new tests).

## Pending Items

### P3-2 ⏳ Policy Navigator live migration

**Status:** Blocked on live Policy Navigator database access

This sandbox environment has no access to the Policy Navigator Supabase project
(`aaxxunfarlhmydvohsrm`). P3-2 requires:
- `CREATE OR REPLACE VIEW rollup_eligible_extractions` on Policy Navigator's schema
- Drop `review_status IN ('approved','verified')` condition (added in P2-3)
- Verification against a scratch Postgres schema first
- NOTIFY pgrst to reload schema after migration

**Dependency:** Requires operator with PN database access.

### P3-4 ⏳ Tier-D extractions dashboard panel

**Status:** Not started; depends on P3-1/P3-3 (done) but requires browser testing

This would add a new dashboard view for permanently-ineligible Tier-D extractions,
allowing analysts to see what still needs re-extraction or model/prompt tuning to
reach C+. Pattern mirrors existing `/api/low-confidence/export.csv`.

**Notes:**
- Route implementation (backend) is straightforward
- HTML/JS integration requires browser testing per CLAUDE.md environment constraints
- Deferred until manual testing environment available

### P3-5 ⏳ Audit panel for unreviewed synced extractions

**Status:** Not started; depends on P3-2; requires browser testing

This would add visibility into what high-tier extractions synced to Policy Navigator
without RC analyst approval (now possible under P3's confidence-only gate). Acts as
the transparency backstop that makes removing the P2 approval gate safe.

**Notes:**
- Backend query is straightforward (synced_extractions rows with review_status != 'approved'/'verified')
- UI integration requires browser testing
- Deferred until manual testing environment available

### P3-7 ✅ Completion log and documentation update

**This document** documents P3's core changes (P3-1, P3-3, P3-6).

**Still needed:**
- `docs/remediation_plan.md` forward-pointing addendum noting P3's approval-gate relaxation
  (deferred until P3-2 ships so the "before/after" comparison is complete)
- Update to Phase 2 section in `docs/remediation_plan.md` noting the gate was relaxed in Phase 3

## Integration with Downstream Systems

### Sync behavior change (P3-1/P3-3)

Extractions now sync based on confidence tier alone (A/B/C threshold):

| Review Status | Tier A | Tier B | Tier C | Tier D |
|---|---|---|---|---|
| approved | ✅ | ✅ | ✅ | ❌ |
| verified | ✅ | ✅ | ✅ | ❌ |
| pending | ✅ | ✅ | ✅ | ❌ |
| flagged | ✅ | ✅ | ✅ | ❌ |
| rejected | ❌ | ❌ | ❌ | ❌ |

**Change from P2:** approved-only (all rows blocked unless review_status='approved')
**Change from pure tier-only:** analyst veto preserved (review_status='rejected' blocks sync)

### Operator action items

1. **Run PN migration (P3-2)** — create/update rollup_eligible_extractions view, NOTIFY pgrst
2. **Dashboard testing (P3-4/P3-5)** — manual browser verification of new panels once backend routes added
3. **Monitor synced items (P3-5 audit panel)** — review what shipped "unreviewed" under the new gate

## Risk & Mitigation

### Risk: Unreviewed high-confidence extractions in Policy Navigator

**Scenario:** Analyst makes an error in confidence-tier tuning; high-confidence but
wrong extractions sync without approval.

**Mitigation:**
- P3-5 audit panel provides transparency (show what synced unreviewed)
- Analyst veto (review_status='rejected') still prevents sync
- P3-6 tests prove the logic works as intended
- Product team (Policy Navigator) retains their own review workflow post-sync

### Risk: Confidence tier is unreliable

**Covered by:** EA (Extraction Accuracy) plan, which rebalances confidence under EA3-1
(evidence-first weights) and validates against EA1 gold set before serving. EA3 is
a prerequisite for confidence-only gating to be trustworthy.

## Testing Notes

**Coverage:** P3-6 tests cover the eligibility logic (tier comparison, review_status
evaluation) and prove regression behavior (P2 vs P3 differences). Tests do NOT
cover database operations (requires live DB) or Policy Navigator integration (requires
PN access).

**Missing:** Integration tests for actual sync operations (would require mocked DB
or live test database). These could be added if a test-database fixture becomes
available.

## Next Steps

1. **P3-2:** Operator applies live PN migration (blocked on PN access)
2. **P3-4/P3-5:** Backend routes + dashboard HTML (requires browser testing)
3. **P3-7 (docs):** Update remediation_plan.md addendum post-P3-2
4. **Coordinate with EA3:** Ensure confidence rebalancing doesn't break P3's
   tier-only gate assumption
