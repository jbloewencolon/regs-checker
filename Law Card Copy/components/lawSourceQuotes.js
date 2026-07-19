// ─────────────────────────────────────────────────────────────────────────────
// lawSourceQuotes — pure helpers that turn a law's extraction chunk (see
// src/services/extractionLoader.js) into displayable "from the law itself"
// passages, and match them to a curated obligation row.
//
// Data honesty notes:
//   - A passage's `quote` is a verbatim evidence span from the source text when
//     the extraction carries one; otherwise we fall back to the extraction's
//     own description/action, which is AI-shaped, not verbatim. `verbatim`
//     distinguishes the two so the UI never presents a paraphrase as a quote.
//   - Matching (matchQuotesToObligation) is lexical similarity, not a curated
//     link — the snapshot's obligations carry no foreign key to extractions
//     (obligation.sourceSection is null corpus-wide today). Callers must
//     present matches as "related passages", not authoritative citations.
// ─────────────────────────────────────────────────────────────────────────────

const STOPWORDS = new Set([
  'must', 'shall', 'with', 'that', 'this', 'from', 'have', 'been', 'were',
  'their', 'they', 'them', 'when', 'where', 'which', 'while', 'would', 'could',
  'should', 'about', 'after', 'before', 'other', 'such', 'than', 'then', 'these',
  'those', 'under', 'upon', 'into', 'each', 'every', 'other', 'others', 'them',
  'against', 'between', 'during', 'through', 'without', 'within', 'including',
  'required', 'requirement', 'requirements', 'provide', 'provides', 'provided',
]);

function tokenize(text) {
  return new Set(
    (text || '')
      .toLowerCase()
      .split(/[^a-z0-9]+/)
      .filter((w) => w.length >= 4 && !STOPWORDS.has(w)),
  );
}

function overlapScore(aSet, bSet) {
  if (!aSet.size || !bSet.size) return 0;
  let inter = 0;
  for (const w of aSet) if (bSet.has(w)) inter++;
  return inter / Math.sqrt(aSet.size * bSet.size);
}

function bestEvidenceText(rec) {
  const spans = rec.evidenceSpans || [];
  // Longest span is usually the substantive sentence rather than a fragment.
  let best = null;
  for (const s of spans) {
    const t = (s?.text || '').trim();
    if (t.length > (best?.length || 0)) best = t;
  }
  return best;
}

/**
 * Flatten a law's extraction record ({obligations, complianceMechanisms, ...})
 * into a single list of quotable passages, ordered mandatory-obligations first.
 *
 * @param {object|null} extractions - result of getExtractionsForLaw()
 * @returns {Array<{id, kind, sectionReference, text, quote, verbatim, confidenceTier}>}
 */
export function collectQuotablePassages(extractions) {
  if (!extractions) return [];
  const out = [];

  const push = (rec, kind, summaryText) => {
    const quote = bestEvidenceText(rec);
    const fallback = (summaryText || '').trim();
    if (!quote && !fallback) return;
    out.push({
      id: `${kind}-${rec.id}`,
      kind,
      sectionReference: rec.sectionReference || null,
      text: fallback || quote,
      quote: quote || null,
      verbatim: !!quote,
      confidenceTier: rec.confidenceTier || null,
    });
  };

  for (const rec of extractions.obligations || []) {
    const summary = [rec.subject, rec.modalityRaw, rec.action].filter(Boolean).join(' ');
    push(rec, 'obligation', summary);
  }
  for (const rec of extractions.complianceMechanisms || []) push(rec, 'mechanism', rec.description);
  for (const rec of extractions.rightsProtections || []) push(rec, 'rights', rec.description);
  for (const rec of extractions.enforcements || []) {
    push(rec, 'enforcement', [rec.enforcementBody, rec.mechanism, rec.penaltyRange].filter(Boolean).join(' — '));
  }
  return out;
}

/**
 * Rank a law's quotable passages by lexical similarity to one curated
 * obligation. Returns at most `limit` passages scoring above `threshold`;
 * empty array when nothing plausibly matches (callers show the honest
 * fallback, never a random passage).
 *
 * @param {object} obligation - snapshot obligation ({description, type, ...})
 * @param {Array} passages - collectQuotablePassages() output
 */
export function matchQuotesToObligation(obligation, passages, { limit = 3, threshold = 0.18 } = {}) {
  const target = tokenize(
    [obligation?.description, obligation?.type, obligation?.requirement_type].filter(Boolean).join(' '),
  );
  if (!target.size) return [];
  return (passages || [])
    .map((p) => ({ passage: p, score: overlapScore(target, tokenize(`${p.text} ${p.quote || ''}`)) }))
    .filter((s) => s.score >= threshold)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((s) => s.passage);
}
