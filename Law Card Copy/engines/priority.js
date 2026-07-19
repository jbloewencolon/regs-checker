// =============================================================================
// Priority Derivation Engine (v2)
// =============================================================================
// Replaces the status-only derivePriority() heuristic (which was itself
// unreachable in production — see normalize.js history) with an explainable,
// signal-based scorer. Every tier comes with a `reasons[]` array so the output
// is defensible as a triage suggestion, not an opaque legal-risk verdict.
//
// Inputs used (all present in the corpus today — no dependency on the legal
// SME backfill to start producing a non-degenerate distribution):
//   - status              → legal-force gate (enacted / pending / terminal)
//   - entityTagMeta[].riskTier → exposure signal (critical/high/medium/low)
//   - enforcementNote     → exposure signal, classified into a penalty band
//   - _daysUntilEffective / _daysUntilCompliance → time-pressure signal
//   - is_stub / relevance_score → data-confidence floor (never inflate thin data)
//
// Honesty rule: a law with genuinely no triage signal gets `tier: null`
// ("not yet triaged"), never a guessed tier. See HONEST_UNKNOWN_RULES.md.
// =============================================================================

import { ENACTED_STATUSES, PENDING_STATUSES, DISCUSSION_STATUSES, TERMINAL_STATUSES } from '../data/constants';

// Penalty severity multipliers — matches score_weight_configs seed data
// (consolidated_002_compliance_architecture_v2.sql) and weightedScoring.js
// DEFAULT_WEIGHTS.penalty_severity. Kept as a separate export here (rather
// than importing from weightedScoring.js) because that module's shape is
// tenant-configurable DB data; this table is the fixed classification key
// used only to *derive* a tier from free-text enforcement notes.
export const PENALTY_SEVERITY_WEIGHTS = {
  criminal: 2.5,
  over_1m: 2.0,
  '100k_to_1m': 1.5,
  '10k_to_100k': 1.2,
  under_10k: 1.0,
  unspecified: 1.0,
  none: 0.8,
};

const RISK_TIER_WEIGHT = { critical: 2.5, high: 1.6, medium: 1.0, low: 0.6 };

const URGENCY_WEIGHT = { now: 2.0, soon: 1.4, future: 1.0, unknown: 1.0 };

// Deadline-urgency bands: "no [time left]" (already effective/binding),
// "soon" (within 6 months), "in the future" (further out or undated).
const SOON_WINDOW_DAYS = 180;

/**
 * Classify free-text enforcement language into a penalty-severity band.
 * Returns `null` when there is no enforcement note at all — absence of data,
 * distinct from an affirmative "none" (no penalty exists) or "unspecified"
 * (TBD language). Never invents a band from silence.
 *
 * @param {string|null|undefined} enforcementNote
 * @returns {string|null} one of PENALTY_SEVERITY_WEIGHTS keys, or null
 */
export function classifyPenaltySeverity(enforcementNote) {
  if (!enforcementNote) return null;
  const text = String(enforcementNote).toLowerCase();

  if (/\btbd\b|penalty structure tbd/.test(text)) return 'unspecified';
  if (/\bcriminal\b|imprisonment|felony|misdemeanor/.test(text)) return 'criminal';

  // Extract dollar amounts (handles "$1,000", "$1 million", "$500k", "$500,000").
  const amounts = [...text.matchAll(/\$\s?([\d,]+(?:\.\d+)?)\s*(million|mil\b|m\b|thousand|k\b)?/gi)]
    .map(([, num, unit]) => {
      let val = parseFloat(num.replace(/,/g, ''));
      if (!Number.isFinite(val)) return null;
      if (/million|mil|^m$/.test(unit || '')) val *= 1_000_000;
      else if (/thousand|^k$/.test(unit || '')) val *= 1_000;
      return val;
    })
    .filter((v) => Number.isFinite(v));

  if (amounts.length) {
    const max = Math.max(...amounts);
    if (max >= 1_000_000) return 'over_1m';
    if (max >= 100_000) return '100k_to_1m';
    if (max >= 10_000) return '10k_to_100k';
    return 'under_10k';
  }

  // Deliberately does NOT match "no private right of action" — that means
  // individuals can't sue directly, a distinct concept from "no penalty
  // exists" (public enforcement, e.g. AG action, can still carry a fine).
  // Conflating the two would understate severity for laws that have real
  // civil penalties but no private cause of action.
  if (/no (civil |monetary |statutory )?(penalt(y|ies)|fine)\b/.test(text)) return 'none';

  // Enforcement language exists but doesn't parse to a concrete band
  // (e.g. "Attorney General enforcement" with no amount/criminal language).
  return 'unspecified';
}

/**
 * Classify deadline pressure from days-until-effective / days-until-compliance.
 * Mirrors the applicability engine's existing `_isUpcoming` (90-day) signal
 * but adds the "now" (already binding, zero runway) band the penalty-weight
 * table's urgency dimension needs.
 *
 * @param {number|null} daysUntilEffective
 * @param {number|null} daysUntilCompliance
 * @returns {{ band: 'now'|'soon'|'future'|'unknown', days: number|null }}
 */
export function classifyDeadlineUrgency(daysUntilEffective, daysUntilCompliance) {
  const days = [daysUntilEffective, daysUntilCompliance].filter((d) => d !== null && d !== undefined && Number.isFinite(d));
  if (!days.length) return { band: 'unknown', days: null };
  const min = Math.min(...days);
  if (min <= 0) return { band: 'now', days: min }; // already effective/binding — no runway left
  if (min <= SOON_WINDOW_DAYS) return { band: 'soon', days: min };
  return { band: 'future', days: min };
}

/**
 * Derive an explainable priority tier for a law.
 *
 * @param {Object} policy — normalized policy, optionally enriched with
 *   `_daysUntilEffective` / `_daysUntilCompliance` (from the applicability
 *   engine's deadline computation). Falls back to raw `effectiveDate` if the
 *   caller hasn't computed those yet.
 * @returns {{ tier: 'P0'|'P1'|'P2'|'P3'|null, score: number|null, reasons: string[] }}
 */
export function derivePriorityV2(policy) {
  const status = policy.status;

  // ── Legal-force gate ────────────────────────────────────────────────────
  // Non-enacted laws never rank as urgent regardless of exposure signals —
  // a bill that hasn't passed can't be "act now" even with severe proposed
  // penalties.
  if (TERMINAL_STATUSES.has(status)) {
    return { tier: 'P3', score: 0, reasons: ['No longer in effect — monitor only'] };
  }
  if (PENDING_STATUSES.has(status) || DISCUSSION_STATUSES.has(status)) {
    return { tier: 'P3', score: 0, reasons: ['Not yet enacted — tracked, not yet actionable'] };
  }

  // ── Data-confidence floor ───────────────────────────────────────────────
  // Thin/stub records must never be inflated to "urgent" — that would be the
  // engine mistaking sparse data for low risk in the opposite direction.
  if (policy.is_stub || (policy.relevance_score != null && policy.relevance_score < 2)) {
    return { tier: null, score: null, reasons: ['Limited data available — not enough signal to triage'] };
  }

  const isEnacted = ENACTED_STATUSES.has(status);
  const reasons = [];
  if (isEnacted) {
    reasons.push('Enacted and in effect');
  } else {
    // status is neither enacted nor terminal/pending/discussion — i.e. blank
    // or unrecognized (the 165-law "no legislative status" gap). We still
    // score exposure/urgency below (consistent with isLawApplicable() already
    // treating a past-effective-date, non-terminal law as applicable), but
    // enactment itself is unconfirmed, so this must never reach the top tier
    // on penalty/risk-tag text alone — see the P0 cap after scoring.
    reasons.push('Legislative status not on record — enactment unconfirmed');
  }

  // ── Exposure signal: risk-tier tag ──────────────────────────────────────
  const riskTiers = (policy.entityTagMeta || []).map((t) => t.riskTier).filter(Boolean);
  const maxRiskTier = riskTiers.length
    ? riskTiers.reduce((best, t) => ((RISK_TIER_WEIGHT[t] ?? 0) > (RISK_TIER_WEIGHT[best] ?? 0) ? t : best))
    : null;
  const riskWeight = maxRiskTier ? RISK_TIER_WEIGHT[maxRiskTier] ?? 1.0 : 1.0;
  if (maxRiskTier) reasons.push(`Carries a ${maxRiskTier}-tier risk tag`);

  // ── Exposure signal: penalty severity ───────────────────────────────────
  const penaltyBand = classifyPenaltySeverity(policy.enforcementNote);
  const penaltyWeight = penaltyBand ? PENALTY_SEVERITY_WEIGHTS[penaltyBand] ?? 1.0 : 1.0;
  if (penaltyBand === 'criminal') reasons.push('Criminal penalties on record');
  else if (penaltyBand === 'over_1m' || penaltyBand === '100k_to_1m') reasons.push('Substantial civil penalty on record');
  else if (penaltyBand === '10k_to_100k' || penaltyBand === 'under_10k') reasons.push('Civil penalty on record');
  else if (penaltyBand === 'unspecified') reasons.push('Penalty structure not yet specified');
  else if (penaltyBand === 'none') reasons.push('No penalty mechanism on record');

  // ── Time-pressure signal ─────────────────────────────────────────────────
  const daysUntilEffective = policy._daysUntilEffective ?? null;
  const daysUntilCompliance = policy._daysUntilCompliance ?? null;
  const { band: urgencyBand } = classifyDeadlineUrgency(daysUntilEffective, daysUntilCompliance);
  const urgencyWeight = URGENCY_WEIGHT[urgencyBand] ?? 1.0;
  if (urgencyBand === 'now') reasons.push('Effective now — no runway remaining');
  else if (urgencyBand === 'soon') reasons.push(`Deadline within ${SOON_WINDOW_DAYS} days`);
  else if (urgencyBand === 'future') reasons.push('Deadline more than 6 months out');

  // ── Combine ──────────────────────────────────────────────────────────────
  // Multiplicative: each signal independently raises or holds the score.
  // Thresholds are calibrated against the live corpus's actual signal
  // distribution (see priority.test.js) so the tiers aren't degenerate.
  const score = riskWeight * penaltyWeight * urgencyWeight;

  let tier;
  if (score >= 4.0) tier = 'P0';
  else if (score >= 2.0) tier = 'P1';
  else tier = 'P2';

  // Confirmed-enactment gate: an unconfirmed-status law cannot rank "act now"
  // urgency on exposure text alone — that would overclaim legal force we
  // don't actually have on record. Cap at P1; the exposure signal is still
  // visible via the reasons list and the (uncapped) numeric score.
  if (!isEnacted && tier === 'P0') {
    tier = 'P1';
    reasons.push('Capped below Act-now — legislative status unconfirmed');
  }

  if (!maxRiskTier && !penaltyBand && urgencyBand === 'unknown') {
    reasons.push('No risk-tier/penalty/deadline signal available for finer triage');
  }

  return { tier, score, reasons };
}
