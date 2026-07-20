import { useState, useEffect } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Calendar,
  ExternalLink,
  Check,
  Gavel,
  Shield,
  Eye,
  FileText,
  Brain,
  User,
  CircleDashed,
  Quote,
} from 'lucide-react';
import {
  ENACTED_STATUSES,
  OBLIGATION_CATEGORY_MAP,
  TRIAGE_LABELS,
  CATEGORY_SHORT_LABEL,
  DOMAIN_TAG_MAP,
  POLICY_STATUSES,
} from '../data/constants';
import { DataGapBadge, ThresholdBadge, SourceAuthorityBadge, SourceProvenanceBadge, AuthorityTypeBadge } from './PolicyBadges';
import CoverageCard from './CoverageCard';
import { looksInterleaved } from '../services/textSanitize';
import { normalizeTags } from '../services/normalize';
import { getExtractionsForLaw } from '../services/extractionLoader';
import { collectQuotablePassages, matchQuotesToObligation } from './lawSourceQuotes';

// ── Obligation formatter ──────────────────────────────────────────────────────

export function formatObligation(ob, { truncate = false } = {}) {
  let text;
  if (ob.description && ob.description !== ob.type && ob.description.length > 10) {
    text = ob.description;
  } else {
    const typeLabel = (ob.type || ob.requirement_type || '').replace(/_/g, ' ');
    text = typeLabel.charAt(0).toUpperCase() + typeLabel.slice(1);
  }
  text = text.replace(/^\[[\w_]+\]\s*/g, '');
  if (truncate && text.length > 120) {
    return text.slice(0, 117) + '...';
  }
  return text;
}

// ── Substantive summary ───────────────────────────────────────────────────────
// Cards have two text fields: `description` (ai_scope_summary — usually a short
// topic label like "AI in Political") and `fullSummary` (key_requirements_raw —
// the substance: what the law requires or prohibits). Prefer the richer one so
// the card actually tells the user what to comply with. Many laws have a useful
// fullSummary but a near-empty description, which is why cards looked blank.
export function getLawSummary(law) {
  const full = (law.fullSummary || '').trim();
  const desc = (law.description || '').trim();
  // Backstop: the corpus is de-interleaved at build time, but if a future/live
  // record still carries column-spliced text, don't show garbled legal prose —
  // fall back to the clean topic label (descriptions are short and never
  // interleaved). The source link on the card lets users verify the original.
  if (full && looksInterleaved(full)) return desc || full;
  if (full && full.length > desc.length) return full;
  return desc || full;
}

function summarySnippet(law, max = 150) {
  const s = getLawSummary(law);
  if (!s) return '';
  const clean = s.replace(/^[•\s]+/, '').trim();
  return clean.length > max ? clean.slice(0, max - 1).trimEnd() + '…' : clean;
}

// ── Honest-unknown helpers ────────────────────────────────────────────────────

function isEnacted(law) {
  if (ENACTED_STATUSES.has(law.status)) return true;
  // The snapshot leaves `status` blank for most in-force laws (they carry an
  // effective date and isActive instead of a status string). Treat those as
  // enacted so their penalties surface. Laws with an explicit pending/withdrawn
  // status are routed elsewhere and never reach this branch as "blank".
  if (!law.status && (law.isActive === true || !!law.effectiveDate)) return true;
  return false;
}
function isTBDEnforcement(note) {
  return !!note && note.toLowerCase().includes('tbd');
}
function shouldShowEnforcement(law) {
  // Rule 1: null enforcementNote → no L3.
  // Rule 3: withdrawn → suppress L3 even if note exists.
  return !!(isEnacted(law) && law.enforcementNote && !isTBDEnforcement(law.enforcementNote));
}
// Phase 1e (normalize.js) routes an obligation's actor to `enforcementAuthority`
// when it's a recognized enforcer (Attorney General, DOJ, etc.) instead of
// `actorRole`, so an enforcer never displays as a regulated deployer/developer.
// Surfaces the first one found across a law's obligations, deduped by label —
// answers "who enforces this" without duplicating identical values per row.
// Honest-unknown: returns null (renders nothing, per Rule 1) when no
// obligation carries a recognized enforcement actor — never a guessed value.
function getEnforcementAuthority(law) {
  const found = (law?.obligations || [])
    .map((ob) => ob.enforcementAuthority)
    .filter(Boolean);
  if (!found.length) return null;
  const unique = [...new Set(found.map((v) => v.toLowerCase()))];
  return unique.map((v) => v.replace(/\b\w/g, (c) => c.toUpperCase())).join(', ');
}
function isWithdrawn(law) {
  // Rule 3: withdrawn → strikethrough title + collapse matchReasons.
  return law?.status === 'withdrawn';
}
function getEffectiveCountdown(iso) {
  // Rule 4: future effective date → show countdown.
  if (!iso) return null;
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return null;
  const days = Math.round((target - Date.now()) / (1000 * 60 * 60 * 24));
  if (days <= 0) return null;
  if (days < 30) return `In ${days} day${days === 1 ? '' : 's'}`;
  const months = Math.round(days / 30);
  if (months < 12) return `In ${months} month${months === 1 ? '' : 's'}`;
  const years = Math.round(months / 12);
  return `In ${years} year${years === 1 ? '' : 's'}`;
}

// Map obligation type → category-id → short label + icon component
const CATEGORY_ICON = {
  risk_assessment: Shield,
  transparency: Eye,
  governance: FileText,
  training: Brain,
  consumer_rights: User,
};
function categoryFor(ob) {
  const type = ob.type || ob.requirement_type;
  const catId = OBLIGATION_CATEGORY_MAP[type];
  return {
    id: catId,
    short: catId ? CATEGORY_SHORT_LABEL[catId] : 'Other',
    Icon: catId ? CATEGORY_ICON[catId] : FileText,
  };
}

// ── Taxonomy display helpers ─────────────────────────────────────────────────
// Domain tags arrive as raw entity_tag_mappings keys (e.g. "automated_decisioning",
// "healthcare_ai"). Normalize them to the engine vocabulary, then resolve the
// canonical label + color from DOMAIN_TAG_MAP so chips read "Automated Decisions"
// (not the raw lowercase key) and carry consistent color coding. Tags outside the
// taxonomy fall back to a title-cased key. De-duplicates post-normalization so a
// law carrying both "automated_decisioning" and "automated_decisions" shows once.
const DOMAIN_DOT_COLOR = {
  red: '#dc2626', orange: '#ea580c', yellow: '#ca8a04', green: '#16a34a',
  emerald: '#059669', teal: '#0d9488', blue: '#2563eb', indigo: '#4f46e5',
  purple: '#7c3aed', gray: '#6b7280',
};
const LEVEL_LABEL = { federal: 'Federal', state: 'State', local: 'Local' };

function titleCase(s) {
  return (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function domainTagChips(law) {
  const seen = new Set();
  const out = [];
  for (const raw of law.domainTags || []) {
    const [norm] = normalizeTags([raw]);
    if (!norm || seen.has(norm)) continue;
    seen.add(norm);
    const info = DOMAIN_TAG_MAP[norm];
    out.push({ key: norm, label: info?.label || titleCase(norm), dot: DOMAIN_DOT_COLOR[info?.color] || null });
  }
  return out;
}

function DomainTagChip({ tag, style }) {
  return (
    <span className="lc-chip" style={style}>
      {tag.dot && (
        <span
          aria-hidden="true"
          style={{ width: 6, height: 6, borderRadius: '50%', background: tag.dot, flexShrink: 0 }}
        />
      )}
      {tag.label}
    </span>
  );
}

// Status chip — humanize the normalized status key to its taxonomy label so the
// uppercase CSS never renders a raw "in_committee". data-s keeps the raw key so
// the per-status color rules in index.css still apply.
function StatusChip({ status }) {
  if (!status) return null;
  const label = POLICY_STATUSES[status]?.label || status.replace(/_/g, ' ');
  return <span className="lc-status" data-s={status}>{label}</span>;
}

// Authority chip — surfaces the named enforcer (e.g. "Attorney General") in
// the metadata row so "who enforces this" is answerable without opening the
// L3 / expanded enforcement panel. Additive-only: renders nothing (Rule 1)
// when no obligation carries a recognized enforcementAuthority — never a
// guessed or "Unclassified" placeholder. Styled like StatusChip (outlined,
// no fill) so it reads as metadata, not an alarm; the full enforcement note
// still lives only in the deeper panel this chip points toward.
function AuthorityChip({ law }) {
  const authority = getEnforcementAuthority(law);
  if (!authority) return null;
  const title = law.enforcementNote ? `Enforced by ${authority} — ${law.enforcementNote}` : `Enforced by ${authority}`;
  return (
    <span
      className="inline-flex items-center gap-1"
      style={{
        fontSize: 11,
        fontWeight: 500,
        padding: '2px 8px',
        borderRadius: 4,
        border: '1px solid var(--lc-ink-200)',
        color: 'var(--lc-ink-600)',
        background: 'transparent',
        whiteSpace: 'nowrap',
      }}
      title={title}
    >
      <Gavel className="w-[10px] h-[10px]" aria-hidden="true" />
      {authority}
    </span>
  );
}

// Normalize match reasons — accept array or comma-separated string.
function getMatchReasons(law) {
  const raw = law.matchReasons || law._matchReason || law.reason;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw.filter(Boolean);
  return String(raw).split(',').map((s) => s.trim()).filter(Boolean);
}

function getTriageLabel(law) {
  // No guessed fallback: an unrecognized/absent priority is "Not yet
  // triaged," never P2's affirmative "This year" claim (honest-unknown).
  const base = TRIAGE_LABELS[law.priority] || TRIAGE_LABELS.UNCLASSIFIED;
  if (law.priority === 'P2' && law.effectiveDate) {
    const yr = new Date(law.effectiveDate + 'T00:00:00').getFullYear();
    if (!Number.isNaN(yr)) return { ...base, label: String(yr) };
  }
  return base;
}

// Provenance / freshness helpers.
//
// IMPORTANT (honesty): a row's lastUpdatedAt is the date the *record* was last
// touched — NOT evidence that a human reviewed the content. 0% of snapshot
// extractions are human-verified, so we must never present freshness as
// "Verified". A true verified state is gated on an explicit flag (law.verified /
// analystReviewed) so it lights up automatically once real review data exists.
function isHumanVerified(law) {
  return law?.verified === true || law?.analystReviewed === true || law?.analyst_reviewed === true;
}

function getUpdatedDaysAgo(law) {
  if (law.verifiedDaysAgo != null) return law.verifiedDaysAgo;
  const iso = law.verifiedAt || law.lastUpdatedAt || law.last_updated_at;
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  return Math.max(0, Math.round(ms / (1000 * 60 * 60 * 24)));
}

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// Group obligations by actorRole (case-insensitive).
function groupByRole(obligations) {
  const out = {};
  for (const ob of obligations || []) {
    const role = (ob.actorRole || '').toLowerCase() || '_unspecified';
    if (!out[role]) out[role] = [];
    out[role].push(ob);
  }
  return out;
}

// ── Provenance / trust line (shared by browse + full footers) ──────────────────
// Honest provenance: show "Analyst-verified" only when an explicit verification
// flag is set. Otherwise present an AI-extracted + freshness signal that makes no
// human-attestation claim. (0% of snapshot extractions are human-verified, and a
// record's updated_at is not evidence of review.)
function ProvenanceLine({ law }) {
  const verified = isHumanVerified(law);

  if (verified) {
    const days = getUpdatedDaysAgo(law);
    const ago = days != null ? `${days} day${days === 1 ? '' : 's'} ago` : null;
    const reviewerNote = law.analystReviewedBy ? ` by ${law.analystReviewedBy}` : '';
    return (
      <span
        className="lc-verified"
        title={`Reviewed and verified by a human analyst${reviewerNote}.`}
      >
        <span className="lc-verified-pulse" />
        {`Analyst-verified${ago ? ` · ${ago}` : ''}`}
      </span>
    );
  }

  return (
    <span
      style={{ fontSize: 11.5, color: 'var(--lc-ink-500)' }}
      title="Obligations and metadata are AI-extracted from source legislation and have not yet been reviewed by a human analyst."
    >
      Awaiting verification
    </span>
  );
}

// ── Obligation row (shared by L1 and L2) ──────────────────────────────────────

function ObligationRow({ obligation, dim = false, law = null }) {
  const { short, Icon } = categoryFor(obligation);
  // H2: first-class compliance / enforcement dates
  const compDate = obligation.complianceDate;
  const enfDate = obligation.enforcementStart;
  const deadlines = obligation.deadlines || [];
  // H3: per-obligation source provenance
  const provenance = obligation.provenance || [];
  return (
    <li className="flex gap-2.5 py-1.5 items-start">
      <Icon
        className="w-[18px] h-[18px] mt-[2px] flex-shrink-0"
        style={{ color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-500)' }}
      />
      <div className="flex-1 min-w-0">
        <div
          className="text-[11px] font-semibold uppercase mb-0.5"
          style={{ letterSpacing: '0.04em', color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-500)' }}
        >
          {short}
        </div>
        <div
          className="leading-snug"
          style={{ fontSize: 13.5, color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-800)' }}
        >
          {formatObligation(obligation)}
        </div>
        {/* H2: obligation-level deadline rows */}
        {(compDate || enfDate || deadlines.length > 0) && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5">
            {compDate && (
              <span style={{ fontSize: 11.5, color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-600)' }}>
                Comply by {fmtDate(compDate)}
              </span>
            )}
            {enfDate && (
              <span style={{ fontSize: 11.5, color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-600)' }}>
                Enforcement {fmtDate(enfDate)}
              </span>
            )}
            {deadlines.map((d, i) => (
              <span key={d.deadline_id || i} style={{ fontSize: 11.5, color: dim ? 'var(--lc-ink-500)' : 'var(--lc-ink-600)' }}>
                {d.cohort_label ? `${d.cohort_label}: ` : `${d.deadline_type}: `}{fmtDate(d.deadline_date)}
              </span>
            ))}
          </div>
        )}
        {/* H3: source provenance badge */}
        {provenance.length > 0 && (
          <div className="mt-1">
            <SourceProvenanceBadge provenance={provenance} />
          </div>
        )}
        {/* Exact-wording reveal: pass `law` to enable (active-role lists and
            the other-roles disclosure do; contexts without extraction access
            simply omit it and the row renders as before). */}
        {law && <ObligationSourceQuotes obligation={obligation} law={law} />}
      </div>
    </li>
  );
}

// ── Source-text passages (shared by the two disclosures below) ───────────────
// Lazily loads the law's extraction chunk on first request and flattens it to
// quotable passages. The chunk fetch is cached per jurisdiction inside
// extractionLoader, so many cards on one page share one network request.

function useLawPassages(law, enabled) {
  const [state, setState] = useState({ status: 'idle', passages: [] });
  const lawId = law?.id;
  const jCode = law?.stateCode || law?.jurisdiction_code;
  useEffect(() => {
    // Note: state.status must NOT be a dependency here — flipping to
    // 'loading' would rerun the effect and its cleanup would cancel the
    // fetch it just started. Reopening refires the effect; the loader's
    // per-jurisdiction cache makes the repeat fetch a no-op.
    if (!enabled || !lawId) return undefined;
    let cancelled = false;
    setState((s) => (s.status === 'loaded' ? s : { status: 'loading', passages: [] }));
    getExtractionsForLaw(lawId, jCode)
      .then((extractions) => {
        if (cancelled) return;
        setState({ status: 'loaded', passages: collectQuotablePassages(extractions) });
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'loaded', passages: [] });
      });
    return () => { cancelled = true; };
  }, [enabled, lawId, jCode]);
  return state;
}

function PassageQuote({ passage }) {
  return (
    <blockquote
      style={{
        margin: '6px 0 0',
        padding: '6px 10px',
        borderLeft: '2px solid var(--lc-ink-200)',
        fontSize: 12.5,
        lineHeight: 1.5,
        color: 'var(--lc-ink-700)',
      }}
    >
      {passage.sectionReference && (
        <span
          className="block text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '0.04em', color: 'var(--lc-ink-500)', marginBottom: 2 }}
        >
          {passage.sectionReference}
        </span>
      )}
      {passage.verbatim ? <>&ldquo;{passage.quote}&rdquo;</> : passage.text}
      {!passage.verbatim && (
        <span className="block" style={{ fontSize: 11, color: 'var(--lc-ink-500)', marginTop: 2 }}>
          AI-extracted paraphrase — no verbatim span captured
        </span>
      )}
    </blockquote>
  );
}

function SourceLink({ law, label = 'Open the full law text' }) {
  if (!law?.sourceUrl) return null;
  return (
    <a
      href={law.sourceUrl}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="inline-flex items-center gap-1"
      style={{ fontSize: 12, color: 'var(--lc-match)', textDecoration: 'underline', textUnderlineOffset: 2 }}
    >
      {label} <ExternalLink className="w-3 h-3" aria-hidden="true" />
    </a>
  );
}

// Per-obligation "exact wording" disclosure: matches the curated obligation to
// the law's extracted passages by lexical similarity (see lawSourceQuotes.js).
// Honest-unknown: when nothing plausibly matches — or the jurisdiction has no
// extraction chunk — it says so and offers the source link, never a random or
// pretend citation.
function ObligationSourceQuotes({ obligation, law }) {
  const [open, setOpen] = useState(false);
  const { status, passages } = useLawPassages(law, open);
  const matches = open && status === 'loaded' ? matchQuotesToObligation(obligation, passages) : [];

  // No passages will ever load and there's no source to link — a toggle would
  // be a dead end. Render nothing (Rule 1).
  if (!law?.extractionSummary && !law?.sourceUrl) return null;

  return (
    <details
      className="lc-other-roles"
      style={{ marginTop: 6, paddingTop: 0, borderTop: 'none' }}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary onClick={(e) => e.stopPropagation()}>
        <ChevronRight className="lc-other-roles-chev w-[10px] h-[10px]" aria-hidden="true" />
        <Quote className="w-[10px] h-[10px]" aria-hidden="true" style={{ color: 'var(--lc-ink-400)' }} />
        <span>What the law says</span>
      </summary>
      {open && status !== 'loaded' && (
        <p style={{ fontSize: 12, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>Loading source text…</p>
      )}
      {status === 'loaded' && matches.length > 0 && (
        <div>
          {matches.map((p) => (
            <PassageQuote key={p.id} passage={p} />
          ))}
          <p style={{ fontSize: 11, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>
            Best-matching AI-extracted passages, not a verified citation. <SourceLink law={law} label="Verify in the full text" />
          </p>
        </div>
      )}
      {status === 'loaded' && matches.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>
          Exact statutory wording isn&apos;t linked to this obligation yet.{' '}
          <SourceLink law={law} />
        </p>
      )}
    </details>
  );
}

// Law-level "read the law itself" disclosure: the top extracted passages for
// the whole law. This is what gives summary-only laws (no curated obligations
// yet) a path to primary-source text without leaving the card.
const LAW_TEXT_PREVIEW_COUNT = 5;

export function LawTextDisclosure({ law }) {
  const [open, setOpen] = useState(false);
  const { status, passages } = useLawPassages(law, open);
  const shown = passages.slice(0, LAW_TEXT_PREVIEW_COUNT);

  // extractionSummary is the build-time signal for "this law has extracted
  // passages" (reconciled to on-disk chunks 2026-07-03, so it never promises a
  // chunk that can't load). No passages and no source link → render nothing
  // rather than a dead-end toggle (Rule 1).
  if (!law?.extractionSummary && !law?.sourceUrl) return null;

  return (
    <details
      className="lc-other-roles"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary onClick={(e) => e.stopPropagation()}>
        <ChevronRight className="lc-other-roles-chev w-[10px] h-[10px]" aria-hidden="true" />
        <Quote className="w-[10px] h-[10px]" aria-hidden="true" style={{ color: 'var(--lc-ink-400)' }} />
        <span>Direct quotes from the law</span>
      </summary>
      {open && status !== 'loaded' && (
        <p style={{ fontSize: 12, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>Loading source text…</p>
      )}
      {status === 'loaded' && shown.length > 0 && (
        <div>
          {shown.map((p) => (
            <PassageQuote key={p.id} passage={p} />
          ))}
          <p style={{ fontSize: 11, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>
            {passages.length > shown.length && `${passages.length - shown.length} more extracted passages not shown. `}
            AI-extracted, awaiting analyst verification. <SourceLink law={law} label="Read the full text" />
          </p>
        </div>
      )}
      {status === 'loaded' && shown.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--lc-ink-500)', margin: '6px 0 0' }}>
          No extracted passages are available for this law yet.{' '}
          <SourceLink law={law} />
        </p>
      )}
    </details>
  );
}

// ── Other-roles disclosure (shared by browse, compact, full) ─────────────────
// Rule 7 (docs/specs/HONEST_UNKNOWN_RULES.md): obligations for a role the user
// hasn't selected are real regulatory content, not noise — they must collapse
// into an expandable chip, never disappear outright. `dim` on ObligationRow
// was dead code left over from the removed L2 layer; it's exactly the
// de-emphasis treatment a non-active role needs here.
function roleDisplayLabel(key, { plural = false } = {}) {
  if (key === '_unspecified') return plural ? 'other parties' : 'another party';
  const base = key.replace(/_/g, ' ');
  return plural ? `${base}s` : base;
}

function otherRoleKeys(byRole, activeKey) {
  return Object.keys(byRole).filter((k) => k !== activeKey && byRole[k]?.length);
}

// D3-2 (design audit 2026-07-13): the old CompactCard footer read
// "0 for employer + 12 for other roles" — unparseable without already
// knowing the role-toggle mechanic, and "0 for employer" reads like an
// error rather than "these 12 simply bind a different role". Names the
// other roles explicitly instead of leaving them as an unexplained bucket.
function joinLabels(labels) {
  if (labels.length <= 1) return labels[0] || '';
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

function roleCountFooterText(role, activeCount, otherKeys, byRole) {
  const roleLabel = role.replace(/_/g, ' ');
  const otherCount = otherKeys.reduce((sum, k) => sum + byRole[k].length, 0);
  const otherLabelText = joinLabels(otherKeys.map((k) => roleDisplayLabel(k, { plural: true })));

  if (activeCount > 0) {
    let text = `${activeCount} obligation${activeCount !== 1 ? 's' : ''} for you as ${roleLabel}`;
    if (otherCount > 0) text += ` · ${otherCount} more for ${otherLabelText}`;
    return text;
  }
  if (otherCount > 0) {
    return `No obligations for ${roleLabel} — ${otherCount} ${otherCount === 1 ? 'applies' : 'apply'} to ${otherLabelText}`;
  }
  return `No tracked obligations for ${roleLabel}`;
}

function OtherRolesDisclosure({ byRole, activeKey, activeHasObligations = true, law = null }) {
  const keys = otherRoleKeys(byRole, activeKey);
  if (!keys.length) return null;
  const totalCount = keys.reduce((sum, k) => sum + byRole[k].length, 0);
  const labels = keys.map((k) => roleDisplayLabel(k, { plural: true }));
  const verb = activeHasObligations ? 'Also regulates' : 'It does regulate';

  return (
    <details className="lc-other-roles">
      <summary>
        <ChevronRight className="lc-other-roles-chev w-[10px] h-[10px]" aria-hidden="true" />
        <span>
          {verb}{' '}
          <strong style={{ color: 'var(--lc-ink-700)', fontWeight: 600 }}>{labels.join(', ')}</strong>
          {' '}· {totalCount} obligation{totalCount === 1 ? '' : 's'}
        </span>
      </summary>
      {keys.map((key) => (
        <div key={key}>
          <div className="lc-other-roles-heading">For {roleDisplayLabel(key, { plural: true })}</div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {byRole[key].map((ob, i) => (
              <ObligationRow key={ob.id || i} obligation={ob} dim law={law} />
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}

// ── Browse variant — Direction A v2 paced disclosure ──────────────────────────
// L0 (default)   triage dot + name + match line
// L1 (1 click)   active-role obligations + date · status · tags
// L3 (explicit)  enforcement note + source
//
// Note: `layer` only ever takes values 0, 1, or 3 (see setLayer calls below).
// An earlier design had an L2 ("other-role obligations under role headings")
// between L1 and L3; it was removed in a later redesign but this file kept
// the L0-L3 numbering rather than renumbering, so `layer` jumps straight from
// 1 to 3. This is also why LAWCARD_A11Y_AUDIT.md's "L2 section headers use
// unnamed divs" finding no longer reproduces — the UI element it described
// doesn't exist anymore, not because it was fixed.

function BrowseCard({ law, activeRole = 'deployer', onClick = null }) {
  const [layer, setLayer] = useState(0);

  const byRole = groupByRole(law.obligations || []);
  const role = (activeRole || 'deployer').toLowerCase();
  const activeKey = byRole[role] ? role : (byRole._unspecified ? '_unspecified' : role);
  const activeObs = byRole[activeKey] || [];
  const rawMatchReasons = getMatchReasons(law);
  const withdrawn = isWithdrawn(law);
  // Rule 3: withdrawn → matchReasons collapse to "tracked for monitoring"
  const matchReasons = withdrawn ? ['Tracked for monitoring'] : rawMatchReasons;
  const triage = getTriageLabel(law);
  const showEnforcement = shouldShowEnforcement(law) && !withdrawn;
  const tbdEnforcement = isEnacted(law) && law.enforcementNote && isTBDEnforcement(law.enforcementNote);
  const effectiveCountdown = getEffectiveCountdown(law.effectiveDate);
  const isClickable = typeof onClick === 'function';

  return (
    <article
      className="lc-card overflow-hidden rounded-xl"
      style={{
        background: 'var(--lc-paper)',
        boxShadow: '0 1px 2px rgba(26,23,20,0.04), 0 0 0 1px rgba(26,23,20,0.06)',
        transition: 'box-shadow 0.2s',
      }}
    >
      {/* L0 — always visible */}
      <button
        type="button"
        onClick={() => {
          // NOTE: This button both toggles expand AND triggers onClick(law) when layer === 0.
          // Only bind onClick if you intend navigation behavior; prefer conditional routing elsewhere.
          if (layer === 0 && isClickable) {
            onClick(law);
            return;
          }
          setLayer((l) => (l === 0 ? 1 : 0));
        }}
        aria-expanded={layer > 0}
        className="w-full text-left cursor-pointer"
        style={{
          appearance: 'none',
          border: 0,
          background: 'transparent',
          padding: '16px 20px',
          font: 'inherit',
          color: 'inherit',
          borderBottom: layer > 0 ? '1px solid var(--lc-ink-150)' : '1px solid transparent',
        }}
      >
        <div className="flex items-start gap-3.5">
          <span
            className="lc-triage-dot"
            data-level={law.priority}
            role="img"
            aria-label={`Triage: ${triage.label}`}
            title={triage.sub}
            style={{ marginTop: 8 }}
          />
          <div className="flex-1 min-w-0">
            <div className="lc-meta" style={{ marginBottom: 3 }}>
              {law.identifier ? `${law.identifier} · ` : ''}{law.jurisdiction || ''}
            </div>
            <h3
              className="lc-serif"
              style={{
                margin: 0, fontSize: 19, fontWeight: 600,
                color: withdrawn ? 'var(--lc-ink-500)' : 'var(--lc-ink-900)',
                lineHeight: 1.2,
                textDecoration: withdrawn ? 'line-through' : 'none',
                textDecorationColor: withdrawn ? 'var(--lc-ink-300)' : 'inherit',
              }}
            >
              {law.name}
            </h3>
            {matchReasons.length > 0 && (
              <div
                className="flex items-center gap-1.5 mt-1.5 flex-wrap"
                style={{ fontSize: 12.5, color: 'var(--lc-ink-600)', lineHeight: 1.45 }}
              >
                <Check className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--lc-match)' }} aria-hidden="true" />
                <span style={{ color: 'var(--lc-match)', fontWeight: 600 }}>{triage.label}</span>
                <span style={{ color: 'var(--lc-ink-300)' }}>·</span>
                <span className="truncate">{matchReasons.join(', ')}</span>
              </div>
            )}
          </div>
          <div
            className="inline-flex items-center gap-1.5 flex-shrink-0"
            style={{
              fontSize: 12, fontWeight: 500,
              color: layer > 0 ? 'var(--lc-ink-700)' : 'var(--lc-ink-600)',
              padding: '6px 10px',
              borderRadius: 6,
              border: '1px solid var(--lc-ink-200)',
              background: layer > 0 ? 'var(--lc-ink-100)' : 'var(--lc-paper)',
              marginTop: 2,
            }}
          >
            {layer === 0
              ? (activeObs.length > 0
                  ? `Show ${activeObs.length} obligation${activeObs.length === 1 ? '' : 's'}`
                  : 'Show details')
              : 'Hide details'}
            <ChevronDown
              className="w-[11px] h-[11px]"
              style={{
                transform: layer > 0 ? 'rotate(180deg)' : 'none',
                transition: 'transform 0.2s',
              }}
              aria-hidden="true"
            />
          </div>
        </div>
      </button>

      {/* L1 — active role only */}
      {layer >= 1 && (
        <div style={{ padding: '14px 20px 16px' }}>
          <div
            className="flex flex-wrap items-center gap-3.5"
            style={{ marginBottom: 14, fontSize: 12, color: 'var(--lc-ink-600)' }}
          >
            {law.effectiveDate && (
              <span
                className="inline-flex items-center gap-1"
                style={{ color: 'var(--lc-ink-800)', fontWeight: 500 }}
              >
                <Calendar className="w-[13px] h-[13px]" aria-hidden="true" /> Effective {fmtDate(law.effectiveDate)}
                {effectiveCountdown && (
                  <span
                    style={{
                      marginLeft: 6,
                      fontSize: 11,
                      fontWeight: 500,
                      color: 'var(--lc-match)',
                      background: 'var(--lc-match-bg)',
                      border: '1px solid var(--lc-match-border)',
                      padding: '1px 6px',
                      borderRadius: 4,
                    }}
                  >
                    {effectiveCountdown}
                  </span>
                )}
                {/* H3: field-level provenance badge for the effective date */}
                {(law.fieldProvenance?.effective_date?.length > 0) && (
                  <SourceProvenanceBadge provenance={law.fieldProvenance.effective_date} />
                )}
              </span>
            )}
            <StatusChip status={law.status} />
            <AuthorityChip law={law} />
            {(law.domainTags || []).length > 0 && (
              <span
                style={{ width: 1, height: 14, background: 'var(--lc-ink-200)', display: 'inline-block' }}
              />
            )}
            {domainTagChips(law).map((tag) => (
              <DomainTagChip key={tag.key} tag={tag} />
            ))}
          </div>

          {getLawSummary(law) && (
            <p
              className="lc-serif"
              style={{
                margin: '0 0 14px',
                fontSize: 14.5, lineHeight: 1.55, color: 'var(--lc-ink-700)',
              }}
            >
              {getLawSummary(law)}
            </p>
          )}

          {activeObs.length > 0 ? (
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {activeObs.map((ob, i) => (
                <ObligationRow key={ob.id || i} obligation={ob} law={law} />
              ))}
            </ul>
          ) : (
            <div
              style={{ fontSize: 13, color: 'var(--lc-ink-500)', fontStyle: 'italic' }}
            >
              No {role.replace(/_/g, ' ')} obligations recorded for this law.
            </div>
          )}
          <OtherRolesDisclosure byRole={byRole} activeKey={activeKey} activeHasObligations={activeObs.length > 0} law={law} />
          <LawTextDisclosure law={law} />
        </div>
      )}

      {/* L3 trigger row */}
      {layer >= 1 && (
        <div
          className="flex flex-wrap items-center justify-between gap-3"
          style={{
            padding: '10px 20px',
            borderTop: '1px solid var(--lc-ink-150)',
            background: 'var(--lc-paper)',
          }}
        >
          <ProvenanceLine law={law} />
          {showEnforcement && layer < 3 && (
            <button
              type="button"
              onClick={() => setLayer(Math.max(layer, 3))}
              className="inline-flex items-center gap-1"
              style={{
                appearance: 'none', border: 0, background: 'none',
                color: 'var(--lc-signal)', fontWeight: 500, fontSize: 12,
                cursor: 'pointer', padding: 0, fontFamily: 'inherit',
              }}
            >
              View enforcement &amp; source
              <ChevronRight className="w-[11px] h-[11px]" aria-hidden="true" />
            </button>
          )}
          {!showEnforcement && law.sourceUrl && (
            <a
              href={law.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1"
              style={{ fontSize: 12, color: 'var(--lc-ink-700)', textDecoration: 'underline', textDecorationColor: 'var(--lc-ink-300)' }}
              onClick={(e) => e.stopPropagation()}
            >
              View source <ExternalLink className="w-[11px] h-[11px]" aria-hidden="true" />
            </a>
          )}
          {tbdEnforcement && (
            <span
              className="inline-flex items-center gap-1.5"
              style={{
                fontSize: 11,
                fontWeight: 500,
                color: 'var(--lc-signal)',
                background: 'var(--lc-signal-bg)',
                border: '1px dashed var(--lc-signal-border)',
                padding: '2px 8px',
                borderRadius: 4,
                letterSpacing: '0.04em',
                textTransform: 'uppercase',
              }}
              title={law.enforcementNote}
            >
              <Gavel className="w-[11px] h-[11px]" aria-hidden="true" />
              Pending rulemaking
            </span>
          )}
        </div>
      )}

      {/* L3 — enforcement + source */}
      {layer >= 3 && showEnforcement && (
        <div
          style={{
            padding: '12px 20px 16px',
            background: 'var(--lc-signal-bg)',
            borderTop: '1px solid var(--lc-signal-border)',
          }}
        >
          <div className="flex gap-2.5 items-start">
            <Gavel
              className="w-[14px] h-[14px] flex-shrink-0"
              style={{ color: 'var(--lc-signal)', marginTop: 3 }}
              aria-label="Enforcement signal"
            />
            <div style={{ fontSize: 13, color: 'var(--lc-ink-800)', lineHeight: 1.5 }}>
              <span
                className="mr-1.5"
                style={{
                  color: 'var(--lc-signal)', fontWeight: 600, fontSize: 11,
                  letterSpacing: '0.06em', textTransform: 'uppercase',
                }}
              >
                Enforcement
              </span>
              {law.enforcementNote}
              {getEnforcementAuthority(law) && (
                <div style={{ fontSize: 12, color: 'var(--lc-ink-600)', marginTop: 4 }}>
                  Enforced by: {getEnforcementAuthority(law)}
                </div>
              )}
            </div>
          </div>
          {law.sourceUrl && (
            <div
              className="flex items-center justify-between gap-2 flex-wrap"
              style={{
                marginTop: 10, paddingTop: 10,
                borderTop: '1px solid var(--lc-signal-border)',
                fontSize: 12, color: 'var(--lc-ink-700)',
              }}
            >
              <div className="flex items-center gap-1.5 flex-wrap">
                <span>Source register</span>
                <SourceAuthorityBadge url={law.sourceUrl} />
              </div>
              <a
                href={law.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1"
                style={{ color: 'var(--lc-ink-700)', textDecoration: 'underline', textDecorationColor: 'var(--lc-ink-300)' }}
                onClick={(e) => e.stopPropagation()}
              >
                Open <ExternalLink className="w-[11px] h-[11px]" aria-hidden="true" />
              </a>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

// ── Compact variant — QuickScan top-priority list ─────────────────────────────

function CompactCard({ law, activeRole = 'deployer', renderExpanded, coverageResult, hideSummary = false }) {
  const [open, setOpen] = useState(false);
  const role = (activeRole || 'deployer').toLowerCase();
  const triage = getTriageLabel(law);
  const matchReasons = getMatchReasons(law);
  const isP0 = law.priority === 'P0';
  const showEnforcement = shouldShowEnforcement(law);
  const tbdEnforcement = isEnacted(law) && law.enforcementNote && isTBDEnforcement(law.enforcementNote);

  const byRole = groupByRole(law.obligations || []);
  const activeKey = byRole[role] ? role : (byRole._unspecified ? '_unspecified' : role);
  const activeObs = byRole[activeKey] || [];
  const otherKeys = otherRoleKeys(byRole, activeKey);
  const categories = Array.from(
    new Set(activeObs.map((o) => categoryFor(o).short).filter(Boolean)),
  );
  const showIdentifier = law.identifier && !law.name?.includes(law.identifier);

  return (
    <article
      className="lc-card rounded-lg overflow-hidden"
      style={{
        background: 'var(--lc-paper)',
        boxShadow: '0 1px 2px rgba(26,23,20,0.04), 0 0 0 1px rgba(26,23,20,0.06)',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full text-left"
        style={{
          appearance: 'none', border: 0, background: 'transparent',
          padding: '14px 16px', font: 'inherit', color: 'inherit', cursor: 'pointer',
        }}
      >
        <div className="flex items-baseline gap-2">
          <span className="lc-meta" style={{ fontSize: 10.5 }}>
            {law.identifier || ''}
          </span>
          {law.identifier && law.jurisdiction && (
            <span style={{ color: 'var(--lc-ink-300)' }}>·</span>
          )}
          <span className="lc-meta" style={{ fontSize: 10.5 }}>
            {law.jurisdiction || ''}
          </span>
          <span
            className="ml-auto inline-flex items-center gap-1.5"
            style={{ fontSize: 12, fontWeight: 600, color: 'var(--lc-ink-800)' }}
            title={triage.sub}
          >
            <span className="lc-triage-dot" data-level={law.priority} role="img" style={{ width: 8, height: 8 }} aria-label={`Triage: ${triage.label}`} />
            {triage.label}
          </span>
        </div>
        <h4
          className="lc-serif"
          style={{
            margin: '4px 0 0', fontSize: 16, fontWeight: 600,
            color: 'var(--lc-ink-900)', lineHeight: 1.25,
          }}
        >
          {law.name}
        </h4>
        {showIdentifier && (
          <p
            className="font-mono"
            style={{ fontSize: 11, color: 'var(--lc-ink-500)', margin: '2px 0 0' }}
          >
            {law.identifier}
          </p>
        )}
        {matchReasons.length > 0 && (
          <p
            className="mt-1.5"
            style={{ fontSize: 12, color: 'var(--lc-ink-600)', lineHeight: 1.4 }}
          >
            <Check
              className="w-3 h-3 inline mr-1 -mt-px"
              style={{ color: 'var(--lc-match)' }}
            />
            <span style={{ color: 'var(--lc-match)', fontWeight: 600 }}>Why this matches</span>
            <span style={{ color: 'var(--lc-ink-300)' }}> · </span>
            <span>{matchReasons.join(', ')}</span>
          </p>
        )}
        {(open ? !hideSummary && getLawSummary(law) : summarySnippet(law)) && (
          <p
            className="lc-serif mt-1.5"
            style={{ fontSize: 13, color: 'var(--lc-ink-700)', lineHeight: 1.45 }}
          >
            {open ? getLawSummary(law) : summarySnippet(law)}
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {categories.map((c) => (
            <span key={c} className="lc-chip" style={{ fontSize: 11, padding: '1px 6px' }}>
              {c}
            </span>
          ))}
          {domainTagChips(law).slice(0, 2).map((tag) => (
            <DomainTagChip key={tag.key} tag={tag} style={{ fontSize: 11, padding: '1px 6px' }} />
          ))}
          {!law.status && <DataGapBadge type="status_unknown" />}
          <AuthorityTypeBadge policy={law} />
          <ThresholdBadge policy={law} />
          <AuthorityChip law={law} />
          <span
            className="ml-auto"
            style={{ fontSize: 11.5, color: 'var(--lc-ink-500)' }}
          >
            {roleCountFooterText(role, activeObs.length, otherKeys, byRole)}
          </span>
        </div>
        {coverageResult && (
          <div className="mt-2">
            <CoverageCard coverageResult={coverageResult} activeRole={activeRole} compact />
          </div>
        )}
        {isP0 && showEnforcement && (
          <div
            className="mt-2 flex items-start gap-1.5 rounded-lg"
            style={{
              padding: 8,
              background: 'var(--lc-signal-bg)',
              border: '1px solid var(--lc-signal-border)',
              fontSize: 12,
              color: 'var(--lc-ink-800)',
            }}
          >
            <Gavel
              className="w-3.5 h-3.5 mt-0.5 flex-shrink-0"
              style={{ color: 'var(--lc-signal)' }}
              aria-label="Enforcement signal"
            />
            <span>
              <strong style={{ color: 'var(--lc-signal)' }}>Enforcement.</strong>{' '}
              {law.enforcementNote}
              {getEnforcementAuthority(law) && (
                <span style={{ display: 'block', color: 'var(--lc-ink-600)', marginTop: 2 }}>
                  Enforced by: {getEnforcementAuthority(law)}
                </span>
              )}
            </span>
          </div>
        )}
      </button>

      {open && (
        <div
          style={{
            padding: '12px 16px 14px',
            background: 'var(--lc-ink-50)',
            borderTop: '1px solid var(--lc-ink-150)',
          }}
        >
          {/* The full summary now renders in the header above (it morphs from
              the truncated snippet on expand), so it's never repeated here.
              hideSummary is kept for renderExpanded callers that present their
              own attributed summary block and want the header text quieter. */}
          {renderExpanded && (
            <div className="mb-2">{renderExpanded(law)}</div>
          )}
          {law.is_stub ? (
            <p style={{ fontSize: 12, color: 'var(--lc-ink-500)', fontStyle: 'italic' }}>
              Limited data — obligation details in review.
            </p>
          ) : (
            <>
              {activeObs.length > 0 && (
                <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                  {activeObs.map((ob, i) => (
                    <ObligationRow key={ob.id || i} obligation={ob} law={law} />
                  ))}
                </ul>
              )}
              <OtherRolesDisclosure byRole={byRole} activeKey={activeKey} activeHasObligations={activeObs.length > 0} law={law} />
              <LawTextDisclosure law={law} />
            </>
          )}
          {!isP0 && showEnforcement && (
            <div
              className="mt-3 flex items-start gap-1.5 rounded-lg"
              style={{
                padding: 8,
                background: 'var(--lc-signal-bg)',
                border: '1px solid var(--lc-signal-border)',
                fontSize: 12,
                color: 'var(--lc-ink-800)',
              }}
            >
              <Gavel
                className="w-3.5 h-3.5 mt-0.5 flex-shrink-0"
                style={{ color: 'var(--lc-signal)' }}
                aria-label="Enforcement signal"
              />
              <span>
                <strong style={{ color: 'var(--lc-signal)' }}>Enforcement.</strong>{' '}
                {law.enforcementNote}
                {getEnforcementAuthority(law) && (
                  <span style={{ display: 'block', color: 'var(--lc-ink-600)', marginTop: 2 }}>
                    Enforced by: {getEnforcementAuthority(law)}
                  </span>
                )}
              </span>
            </div>
          )}
          {tbdEnforcement && (
            <p className="mt-3" style={{ fontSize: 12, color: 'var(--lc-ink-500)' }}>
              <strong>Enforcement:</strong> {law.enforcementNote}
            </p>
          )}
          {coverageResult && (
            <div className="mt-3">
              <CoverageCard
                coverageResult={coverageResult}
                policyName={law.name}
                activeRole={activeRole}
              />
            </div>
          )}
        </div>
      )}
    </article>
  );
}

// ── Stub variant — "On Our Radar" ─────────────────────────────────────────────

function StubCard({ law }) {
  return (
    <article
      className="lc-card rounded-xl"
      style={{
        background: 'var(--lc-paper)',
        border: '1.5px dashed var(--lc-ink-300)',
        padding: '18px 20px',
      }}
    >
      <div className="lc-meta" style={{ marginBottom: 4 }}>
        {law.identifier ? `${law.identifier} · ` : ''}{law.jurisdiction || ''}
      </div>
      <h3
        className="lc-serif"
        style={{ margin: 0, fontSize: 18, fontWeight: 600, color: 'var(--lc-ink-700)' }}
      >
        {law.name}
      </h3>
      <div className="flex items-center gap-2.5 flex-wrap" style={{ marginTop: 12 }}>
        <span
          className="lc-chip inline-flex items-center gap-1.5"
          style={{
            background: 'transparent', borderStyle: 'dashed',
            color: 'var(--lc-ink-600)',
          }}
        >
          <CircleDashed className="w-3 h-3" /> Tracking
        </span>
        {law.status && (
          <span style={{ fontSize: 11.5, color: 'var(--lc-ink-500)' }}>
            {law.status.replace(/_/g, ' ')}
          </span>
        )}
      </div>
      <p
        style={{
          marginTop: 12, fontSize: 12.5, color: 'var(--lc-ink-500)',
          lineHeight: 1.5, fontStyle: 'italic',
        }}
      >
        We&apos;re tracking this law. The text isn&apos;t finalized yet, so we don&apos;t list specific obligations.
      </p>
    </article>
  );
}

// ── Full variant — Comprehensive Audit dossier ────────────────────────────────

function FullCard({ law, activeRole = 'deployer', renderExpanded, extraBadges, coverageResult }) {
  const [open, setOpen] = useState(false);
  const role = (activeRole || 'deployer').toLowerCase();
  const triage = getTriageLabel(law);
  const matchReasons = getMatchReasons(law);
  const showEnforcement = shouldShowEnforcement(law);
  const tbdEnforcement = isEnacted(law) && law.enforcementNote && isTBDEnforcement(law.enforcementNote);
  const byRole = groupByRole(law.obligations || []);
  const activeKey = byRole[role] ? role : (byRole._unspecified ? '_unspecified' : role);
  const activeRoleObs = byRole[activeKey] || [];

  return (
    <article
      className="lc-card overflow-hidden rounded-xl"
      style={{
        background: 'var(--lc-paper)',
        boxShadow: '0 1px 2px rgba(26,23,20,0.04), 0 0 0 1px rgba(26,23,20,0.06)',
      }}
    >
      <header style={{ padding: '20px 24px 16px' }}>
        <div className="lc-meta" style={{ marginBottom: 4 }}>
          {law.identifier ? `${law.identifier} · ` : ''}{law.jurisdiction || ''}
          {law.level ? ` · ${LEVEL_LABEL[law.level] || titleCase(law.level)}` : ''}
        </div>
        <h3
          className="lc-serif"
          style={{
            margin: 0, fontSize: 24, fontWeight: 600,
            color: 'var(--lc-ink-900)', lineHeight: 1.15,
          }}
        >
          {law.name}
        </h3>
        <div className="flex items-center gap-3 flex-wrap" style={{ marginTop: 12 }}>
          <span
            className="inline-flex items-center gap-1.5"
            style={{ fontSize: 12, fontWeight: 600, color: 'var(--lc-ink-800)' }}
          >
            <span className="lc-triage-dot" data-level={law.priority} role="img" style={{ width: 8, height: 8 }} aria-label={`Triage: ${triage.label}`} />
            {triage.label}
          </span>
          {law.status
            ? <StatusChip status={law.status} />
            : <DataGapBadge type="status_unknown" />
          }
          {law.effectiveDate && (
            <span
              className="inline-flex items-center gap-1"
              style={{ fontSize: 12, color: 'var(--lc-ink-600)' }}
            >
              <Calendar className="w-[13px] h-[13px]" aria-hidden="true" /> Effective {fmtDate(law.effectiveDate)}
              {(law.fieldProvenance?.effective_date?.length > 0) && (
                <SourceProvenanceBadge provenance={law.fieldProvenance.effective_date} />
              )}
            </span>
          )}
          {domainTagChips(law).map((tag) => (
            <DomainTagChip key={tag.key} tag={tag} />
          ))}
          <AuthorityTypeBadge policy={law} />
          <ThresholdBadge policy={law} />
          <AuthorityChip law={law} />
          {extraBadges}
        </div>
        {matchReasons.length > 0 && (
          <div
            className="flex items-start gap-2.5 rounded-lg"
            style={{
              marginTop: 14, padding: '10px 12px',
              background: 'var(--lc-match-bg)',
              border: '1px solid var(--lc-match-border)',
            }}
          >
            <Check
              className="w-3.5 h-3.5 flex-shrink-0"
              style={{ color: 'var(--lc-match)', marginTop: 2 }}
            />
            <div style={{ fontSize: 12.5, color: 'var(--lc-ink-700)', lineHeight: 1.45 }}>
              <span style={{ color: 'var(--lc-match)', fontWeight: 600 }}>Why this matches</span>
              <span style={{ color: 'var(--lc-ink-500)' }}> · </span>
              {matchReasons.join(' · ')}
            </div>
          </div>
        )}
        {getLawSummary(law) && (
          <p
            className="lc-serif"
            style={{
              margin: '14px 0 0',
              fontSize: 14.5, lineHeight: 1.55, color: 'var(--lc-ink-700)',
            }}
          >
            {getLawSummary(law)}
          </p>
        )}
      </header>

      <div
        style={{
          padding: '0 24px',
          borderTop: '1px solid var(--lc-ink-200)',
          borderBottom: open ? '1px solid var(--lc-ink-200)' : 'none',
          background: 'var(--lc-ink-50)',
        }}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="w-full inline-flex items-center justify-between"
          style={{
            appearance: 'none', border: 0, background: 'transparent',
            padding: '10px 0', font: 'inherit', color: 'var(--lc-ink-800)',
            cursor: 'pointer', fontSize: 12.5, fontWeight: 600,
          }}
        >
          <span>{open ? 'Hide details' : (activeRoleObs.length > 0 ? `Show ${activeRoleObs.length} obligation${activeRoleObs.length === 1 ? '' : 's'} & sources` : 'Show details & sources')}</span>
          <ChevronDown
            className="w-3.5 h-3.5"
            style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}
            aria-hidden="true"
          />
        </button>
      </div>

      {open && (
        <div style={{ padding: '18px 24px 20px' }}>
          {law.is_stub ? (
            <p style={{ fontSize: 13, color: 'var(--lc-ink-500)', fontStyle: 'italic' }}>
              Obligation details are limited for this law. Data is being reviewed.
            </p>
          ) : (
            <>
              {activeRoleObs.length > 0 ? (
                <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                  {activeRoleObs.map((ob, i) => (
                    <ObligationRow key={ob.id || i} obligation={ob} law={law} />
                  ))}
                </ul>
              ) : law.extractionSummary ? (
                <p style={{ fontSize: 13, color: 'var(--lc-ink-500)', fontStyle: 'italic' }}>
                  No {role.replace(/_/g, ' ')} obligations recorded. Expand the extraction tabs above for raw legislative detail.
                </p>
              ) : otherRoleKeys(byRole, activeKey).length === 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <DataGapBadge type="obligations_pending" />
                  <p style={{ fontSize: 12.5, color: 'var(--lc-ink-500)', lineHeight: 1.5, margin: 0 }}>
                    Structured obligation data is still being compiled for this law.
                    The absence of listed obligations does not mean no obligations exist.
                  </p>
                </div>
              ) : null}
              <OtherRolesDisclosure byRole={byRole} activeKey={activeKey} activeHasObligations={activeRoleObs.length > 0} law={law} />
              <LawTextDisclosure law={law} />
            </>
          )}
          {showEnforcement && (
            <div
              className="rounded-lg flex gap-2.5"
              style={{
                marginTop: 12, padding: '10px 12px',
                background: 'var(--lc-signal-bg)',
                border: '1px solid var(--lc-signal-border)',
              }}
            >
              <Gavel
                className="w-3.5 h-3.5 flex-shrink-0"
                style={{ color: 'var(--lc-signal)', marginTop: 2 }}
                aria-label="Enforcement signal"
              />
              <div style={{ fontSize: 13, color: 'var(--lc-ink-700)', lineHeight: 1.5 }}>
                <span style={{ color: 'var(--lc-signal)', fontWeight: 600 }}>Enforcement.</span>{' '}
                {law.enforcementNote}
                {getEnforcementAuthority(law) && (
                  <div style={{ fontSize: 12, color: 'var(--lc-ink-600)', marginTop: 4 }}>
                    Enforced by: {getEnforcementAuthority(law)}
                  </div>
                )}
              </div>
            </div>
          )}
          {tbdEnforcement && (
            <p className="mt-3" style={{ fontSize: 12, color: 'var(--lc-ink-500)' }}>
              <strong>Enforcement:</strong> {law.enforcementNote}
            </p>
          )}
          {renderExpanded && <div className="mt-3">{renderExpanded(law)}</div>}
          {coverageResult && (
            <div className="mt-4">
              <CoverageCard
                coverageResult={coverageResult}
                policyName={law.name}
                activeRole={activeRole}
              />
            </div>
          )}
        </div>
      )}

      <footer
        className="flex items-center justify-between"
        style={{
          padding: '10px 24px',
          borderTop: '1px solid var(--lc-ink-150)',
        }}
      >
        <ProvenanceLine law={law} />
        {law.sourceUrl && (
          <a
            href={law.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1"
            style={{ fontSize: 12, color: 'var(--lc-ink-700)', textDecoration: 'underline', textDecorationColor: 'var(--lc-ink-300)' }}
          >
            Open source <ExternalLink className="w-[11px] h-[11px]" aria-hidden="true" />
          </a>
        )}
      </footer>
    </article>
  );
}

// ── Public component ──────────────────────────────────────────────────────────

/**
 * Unified law card — four variants:
 *   "compact"  QuickScan top-priority list (clickable to expand inline)
 *   "browse"   FederalPage / paced disclosure (L0 → L1 → L3; see BrowseCard comment
 *              for why the numbering skips L2)
 *   "full"     Comprehensive Audit dossier (renderExpanded for extraction tabs)
 *   "stub"     "On Our Radar" — dashed border, no obligations
 */
export default function LawCard({
  law,
  variant = 'compact',
  defaultOpen: _defaultOpen,
  renderExpanded,
  extraBadges,
  activeRole = null,
  onClick = null,
  coverageResult = null,
  hideSummary = false,
}) {
  if (law?.is_stub && variant !== 'full') {
    return <StubCard law={law} />;
  }
  if (variant === 'stub') return <StubCard law={law} />;
  if (variant === 'browse') {
    return <BrowseCard law={law} activeRole={activeRole} onClick={onClick} />;
  }
  if (variant === 'full') {
    return (
      <FullCard
        law={law}
        activeRole={activeRole}
        renderExpanded={renderExpanded}
        extraBadges={extraBadges}
        coverageResult={coverageResult}
      />
    );
  }
  return (
    <CompactCard
      law={law}
      activeRole={activeRole}
      renderExpanded={renderExpanded}
      coverageResult={coverageResult}
      hideSummary={hideSummary}
    />
  );
}
