// =============================================================================
// Source-Text Sanitization
//
// The regulation corpus is partly sourced from a multi-column PDF tracker
// (law name | summary | penalties | citation). When that PDF is flattened to
// plain text, two classes of defect leak into `fullSummary`, `enforcementNote`
// and `description`:
//
//   1. Boilerplate / footer junk — page numbers, "Last updated <date>",
//      "For more, visit www.orrick.com/ai", Westlaw reprint notices. These are
//      unambiguous and safe to strip programmatically.
//
//   2. Column interleaving — fragments from an adjacent column (a penalty, a
//      statutory citation, a wrapped law-name) get spliced into the *middle* of
//      a sentence. These cannot be de-interleaved safely by regex without risk
//      of fabricating legal text, so they are NOT auto-rewritten here. Instead:
//        - hand-verified fixes live in src/data/textCorrections.js (build-time),
//        - looksInterleaved() lets the UI fall back to the clean topic label +
//          source link for anything still affected (honest-unknown backstop).
//
// Used by both the live Supabase path (src/services/normalize.js) and the
// build-time snapshot generator (scripts/snapshot.mjs), so cleaning is applied
// consistently no matter where the data is read from.
// =============================================================================

// ── Boilerplate / footer patterns (unambiguous, safe to remove) ──────────────
const BOILERPLATE = [
  // Trailing footer block: "Last updated January 13, 2026 Page 33 of 39 ..." → EOL.
  /\s*Last updated\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}\b.*$/is,
  // Standalone page markers anywhere.
  /\s*Page \d+ of \d+\b\.?/gi,
  // Orrick promo footer.
  /\s*For more,?\s+visit\s+www\.orrick\.com\/ai\.?/gi,
  // Westlaw reprint notice (contiguous form).
  /\s*Reprinted from Westlaw[^.]*\.?/gi,
];

// Westlaw notice can also arrive interleaved across column lines, e.g.
// "... Reprinted from ... Westlaw with the ... permission of ... Thomson Reuters."
// These fragments are distinctive enough to remove individually.
const WESTLAW_FRAGMENTS = [
  /\s*Reprinted from\b/gi,
  /\s*Westlaw with the\b/gi,
  /\s*permission of\b(?=\s|$)/gi,
  /\s*Thomson Reuters\.?/gi,
];

/**
 * Remove unambiguous PDF/source boilerplate from an extracted text value.
 * Leaves substantive content untouched; only strips footer/reprint noise and
 * normalizes the whitespace/punctuation seams left behind.
 */
export function stripSourceBoilerplate(text) {
  if (typeof text !== 'string' || !text.trim()) return null;
  let out = text;
  for (const re of BOILERPLATE) out = out.replace(re, ' ');
  // Only attempt the interleaved-Westlaw scrub when its signature is present,
  // to avoid touching the common words "permission of" in ordinary prose.
  if (/Reprinted from|Thomson Reuters/i.test(out)) {
    for (const re of WESTLAW_FRAGMENTS) out = out.replace(re, ' ');
  }
  return normalizeWhitespace(out);
}

/** Collapse the whitespace/punctuation artifacts left by stripping. */
export function normalizeWhitespace(text) {
  if (typeof text !== 'string') return null;
  let out = text
    .replace(/\s+/g, ' ')             // collapse runs of whitespace
    .replace(/ +([.;:])/g, '$1')      // no space before . ; : (NOT comma — would hit "$1 ,000")
    .replace(/ +,(?=\D)/g, ',')       // space before comma only when not inside a number
    .replace(/;\s*;/g, ';')           // collapse doubled semicolons
    .replace(/\s*;\s*$/g, '')         // drop a dangling trailing semicolon
    .replace(/^\s*[;,]\s*/g, '')      // drop a leading orphan separator
    .trim();
  return out.length ? out : null;
}

// ── Interleaving detector (high-precision backstop for the UI) ───────────────
// Conservative on purpose: a false positive hides a clean summary, so we only
// flag text that carries a strong structural signature of column splicing.

// A penalty/empty-cell value wedged into the *middle* of a prose sentence is a
// splice from the penalty column. In clean text these values START the field
// ("None specified.", "Up to $20,000 per violation."); when they appear right
// after a lowercase prose word ("...tool shall None specified...", "...employer
// to Enforced under...") the column has bled in.
const PENALTY_TOKEN = '(?:None specified|Not specified|N\\/A|Up to \\$[\\d,]+|At least \\$[\\d,]+|Existing [a-z]+ penalties apply|Enforced under)';
const PENALTY_SPLICE = new RegExp(`[a-z]{2,}\\s+${PENALTY_TOKEN}\\b`);

// A statutory citation embedded between prose words, e.g. "111.5 to 111.7" or
// "1-46-101 to 106" or a hyphenated code cite mid-line.
const CITE_RANGE = /\b\d{1,3}\.\d{1,3}\s+to\s+\d/;
const CITE_HYPHEN = /[a-z]{3,}\s+\d{1,3}-\d{1,3}-\d{1,3}\b/i;

/**
 * Heuristic: does this text still look column-interleaved after boilerplate
 * stripping? Used by LawCard to fall back to the clean description + source
 * link rather than display garbled legal text. Returns false for clean prose
 * (including legitimate semicolon-separated requirement lists).
 */
export function looksInterleaved(text) {
  if (!text || typeof text !== 'string') return false;
  if (/Reprinted from|Thomson Reuters/i.test(text)) return true;
  if (PENALTY_SPLICE.test(text)) return true;
  if (CITE_RANGE.test(text)) return true;
  if (CITE_HYPHEN.test(text)) return true;
  return false;
}

// ── Internal provenance markers (D1, design audit 2026-07-13) ────────────────
// Curation provenance belongs in structured fields/badges, never inline in the
// sentence a user reads. Two leaks observed live:
//   - obligation descriptions beginning with a literal "[analyst_curated]" tag
//   - "TMP-*" placeholder canonical IDs rendered where a statute citation belongs

// Leading bracket tag like "[analyst_curated]" (tolerates space/hyphen variants).
const PROVENANCE_PREFIX = /^\s*\[\s*analyst[\s_-]?curated\s*\]\s*/i;

/**
 * Split a leading provenance tag off a free-text value. Returns the cleaned
 * text plus whether the analyst-curated tag was present, so callers can keep
 * the provenance as a structured flag instead of losing it.
 */
export function stripProvenancePrefix(text) {
  if (typeof text !== 'string') return { text, analystCurated: false };
  const analystCurated = PROVENANCE_PREFIX.test(text);
  return analystCurated ? { text: text.replace(PROVENANCE_PREFIX, ''), analystCurated } : { text, analystCurated };
}

/**
 * True for placeholder canonical IDs (TMP-…) that identify a record internally
 * but are not a real bill number / citation. Admin data-quality tooling keys
 * off canonical_law_id; user-facing surfaces must not render these.
 */
export function isInternalLawIdentifier(id) {
  return typeof id === 'string' && id.startsWith('TMP-');
}

/**
 * Convenience: strip boilerplate from the three free-text policy fields.
 * Returns a shallow patch object (only the fields present on input).
 */
export function cleanPolicyText(policy) {
  const patch = {};
  if (policy.description != null) patch.description = stripSourceBoilerplate(policy.description);
  if (policy.fullSummary != null) patch.fullSummary = stripSourceBoilerplate(policy.fullSummary);
  if (policy.enforcementNote != null) patch.enforcementNote = stripSourceBoilerplate(policy.enforcementNote);
  return patch;
}
