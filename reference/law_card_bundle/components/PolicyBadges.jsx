import { cn } from '../utils/cn';
import { AlertCircle } from 'lucide-react';
import { JURISDICTION_COLORS } from '../data/constants';
import { classifySourceUrl } from '../utils/sourceUrl';

export function LevelBadge({ level }) {
  if (!level) return null;
  const colors = JURISDICTION_COLORS[level];
  if (!colors)
    return <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-700">{level}</span>;
  return <span className={cn('text-xs font-semibold px-2 py-0.5 rounded-full', colors.bg, colors.text)}>{level}</span>;
}

export function MetricCard({ label, value, color = 'blue', subtitle }) {
  const colorMap = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    indigo: 'bg-indigo-50 border-indigo-200 text-indigo-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
    amber: 'bg-amber-50 border-amber-200 text-amber-700',
    red: 'bg-red-50 border-red-200 text-red-700',
    green: 'bg-green-50 border-green-200 text-green-700',
    emerald: 'bg-emerald-50 border-emerald-200 text-emerald-700',
    gray: 'bg-gray-50 border-gray-200 text-gray-600',
    orange: 'bg-orange-50 border-orange-200 text-orange-700',
  };
  const classes = colorMap[color] || colorMap.blue;
  return (
    <div className={cn('rounded-xl p-4 text-center border', classes)}>
      <p className="text-2xl font-black">{value}</p>
      <p className="text-xs text-gray-600 mt-0.5">{label}</p>
      {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
    </div>
  );
}

export function FreshnessIndicator({ date, className }) {
  if (!date) return null;
  const d = new Date(date);
  const ageMs = Date.now() - d.getTime();
  const days = Math.floor(ageMs / (24 * 60 * 60 * 1000));
  const isStale = days > 90;
  const label = days === 0 ? 'Today' : days === 1 ? '1 day ago' : `${days} days ago`;
  return (
    <span
      className={cn('text-xs', isStale ? 'text-red-500 font-medium' : 'text-gray-400', className)}
      title={`Record last updated ${d.toLocaleDateString()} — reflects data currency, not a human verification`}
    >
      {isStale ? '⚠ ' : ''}Updated {label}
    </span>
  );
}

/**
 * DataGapBadge — inline chip shown on individual law cards when key data
 * is missing. Prevents absence of data from being read as "no obligations."
 *
 * type:
 *   "status_unknown"       — law.status is null/blank
 *   "obligations_pending"  — no legacy obligations AND no extraction summary
 */
export function DataGapBadge({ type, className }) {
  const config = {
    status_unknown: {
      label: 'Status unconfirmed',
      title: 'Legislative status has not been confirmed for this law. Treat as potentially in-force.',
      // text-gray-500 on bg-gray-100 measured 4.39:1 (WCAG AA needs 4.5:1) — gray-600 passes at 6.87:1.
      cls: 'bg-gray-100 border-gray-300 text-gray-600',
    },
    obligations_pending: {
      label: 'Obligations not yet mapped',
      title: 'Structured obligation data is still being compiled. This law may impose obligations not yet listed here.',
      cls: 'bg-amber-50 border-amber-300 text-amber-700',
    },
  };
  const c = config[type];
  if (!c) return null;
  return (
    <span
      className={cn('inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-medium', c.cls, className)}
      title={c.title}
    >
      <AlertCircle className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      {c.label}
    </span>
  );
}

/**
 * StatusCoverageBadge — aggregate signal for the audit landscape view
 * when many applicable laws have unknown status.
 */
export function StatusCoverageBadge({ unknownCount, total }) {
  if (!unknownCount || total === 0) return null;
  const pct = Math.round((unknownCount / total) * 100);
  return (
    <div className="flex items-start gap-2 px-3 py-2 rounded-lg border text-xs bg-gray-50 border-gray-200 text-gray-700">
      <AlertCircle className="w-3.5 h-3.5 text-gray-400 flex-shrink-0 mt-0.5" aria-hidden="true" />
      <span>
        <strong>{unknownCount} of {total}</strong> applicable laws have unconfirmed legislative status ({pct}%).
        {' '}Review individually — unconfirmed does not mean not in force.
      </span>
    </div>
  );
}

// ── Source authority classification ──────────────────────────────────────────
// classifySourceUrl lives in utils/sourceUrl.js (MOD7-a) — one canonical
// classifier shared with the analyst-intake pages.

export function SourceAuthorityBadge({ url, className }) {
  const kind = classifySourceUrl(url);
  if (kind === 'primary' || kind === 'none') return null;
  const config = {
    placeholder: {
      label: 'Source unavailable',
      title: 'No direct legislative source URL has been recorded for this law. Verify status independently.',
      cls: 'bg-red-50 border-red-300 text-red-700',
    },
    secondary: {
      label: 'Secondary source',
      title: 'This link points to a third-party aggregator or law-firm blog, not the official legislative source.',
      cls: 'bg-amber-50 border-amber-300 text-amber-700',
    },
    unparseable: {
      label: 'Malformed URL',
      title: 'The source URL could not be parsed. Verify independently.',
      cls: 'bg-gray-100 border-gray-300 text-gray-600',
    },
  };
  const c = config[kind];
  if (!c) return null;
  return (
    <span
      className={cn('inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-medium', c.cls, className)}
      title={c.title}
    >
      <AlertCircle className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      {c.label}
    </span>
  );
}

export function ThresholdBadge({ policy, className }) {
  if (!policy) return null;
  const badges = [];
  if (policy.small_business_exempt) {
    badges.push({ label: 'Small biz exempt', cls: 'bg-green-50 border-green-300 text-green-700' });
  }
  if (policy.min_employees > 0) {
    badges.push({ label: `${policy.min_employees.toLocaleString()}+ employees`, cls: 'bg-blue-50 border-blue-200 text-blue-700' });
  }
  if (policy.consumer_count_trigger > 0) {
    badges.push({ label: `${policy.consumer_count_trigger.toLocaleString()}+ consumers`, cls: 'bg-purple-50 border-purple-200 text-purple-700' });
  }
  if (badges.length === 0) return null;
  return (
    <span className={cn('inline-flex flex-wrap gap-1', className)}>
      {badges.map((b) => (
        <span key={b.label} className={cn('text-xs px-1.5 py-0.5 rounded border font-medium', b.cls)}>
          {b.label}
        </span>
      ))}
    </span>
  );
}

// H4: Authority-type badge — distinguishes binding statutes from non-binding
// guidance / enforcement actions so they don't render with equal legal weight.
// Statutes/ordinances (the default binding instruments) show no badge — the
// badge only appears to FLAG a non-statute instrument that a user might
// otherwise mistake for binding law.
const AUTHORITY_BADGE_CONFIG = {
  guidance: { label: 'Guidance', cls: 'bg-amber-50 border-amber-300 text-amber-700' },
  enforcement_action: { label: 'Enforcement action', cls: 'bg-orange-50 border-orange-300 text-orange-700' },
  executive_order: { label: 'Executive order', cls: 'bg-indigo-50 border-indigo-200 text-indigo-700' },
  court_opinion: { label: 'Court opinion', cls: 'bg-slate-50 border-slate-300 text-slate-700' },
  regulation: { label: 'Regulation', cls: 'bg-blue-50 border-blue-200 text-blue-700' },
};

export function AuthorityTypeBadge({ policy, className }) {
  if (!policy) return null;
  const authority = policy.authorityType || policy.authority_type;
  const binding = policy.bindingEffect || policy.binding_effect;
  const notes = policy.legalForceNotes || policy.legal_force_notes;

  // Determine what (if anything) to flag.
  const config = authority ? AUTHORITY_BADGE_CONFIG[authority] : null;
  const isNonBinding = binding && binding !== 'binding' && binding !== 'unknown';

  // Nothing noteworthy: binding statute/ordinance → no badge.
  if (!config && !isNonBinding) return null;

  const label = config ? config.label : binding.replace(/_/g, ' ');
  const cls = config ? config.cls : 'bg-amber-50 border-amber-300 text-amber-700';
  const title = [
    authority ? `Instrument: ${authority.replace(/_/g, ' ')}` : null,
    binding ? `Legal force: ${binding.replace(/_/g, ' ')}` : null,
    notes || null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <span
      className={cn('inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-medium', cls, className)}
      title={title || undefined}
    >
      <AlertCircle className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      {label}
      {isNonBinding && config && <span className="opacity-70">· {binding.replace(/_/g, ' ')}</span>}
    </span>
  );
}

// H3: Source-provenance badge — shows citation quality for a specific field or obligation.
// Input: array of source_provenance rows (from normalizePolicy's fieldProvenance[field] or
//   obligation.provenance). Displays the most authoritative / most recent entry.
export function SourceProvenanceBadge({ provenance, className }) {
  if (!provenance || provenance.length === 0) return null;

  // Prefer verified > pending > disputed > superseded
  const RANK = { verified: 0, pending: 1, disputed: 2, superseded: 3 };
  const best = [...provenance].sort(
    (a, b) => (RANK[a.verification_status] ?? 9) - (RANK[b.verification_status] ?? 9),
  )[0];

  const STATUS_CONFIG = {
    verified: {
      label: best.section_locator || 'Sourced',
      title: [
        best.authority_type ? `Authority: ${best.authority_type.replace(/_/g, ' ')}` : null,
        best.section_locator ? best.section_locator : null,
        best.reviewer ? `Reviewed by ${best.reviewer}` : null,
      ]
        .filter(Boolean)
        .join(' · '),
      cls: 'bg-green-50 border-green-300 text-green-700',
    },
    pending: {
      label: 'Citation pending',
      title: 'A source citation exists but has not been verified by an analyst yet.',
      cls: 'bg-amber-50 border-amber-300 text-amber-700',
    },
    disputed: {
      label: 'Citation disputed',
      title: 'The source citation for this claim is disputed. Verify independently before relying on it.',
      cls: 'bg-red-50 border-red-300 text-red-700',
    },
    superseded: {
      label: 'Superseded',
      title: 'This source citation has been superseded. Check for a newer citation.',
      // text-gray-500 on bg-gray-100 measures 4.39:1 (WCAG AA needs 4.5:1) — gray-600 passes at 6.87:1.
      cls: 'bg-gray-100 border-gray-300 text-gray-600',
    },
  };

  const config = STATUS_CONFIG[best.verification_status] || STATUS_CONFIG.pending;

  return (
    <span
      className={cn('inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border font-medium', config.cls, className)}
      title={config.title}
    >
      {config.label}
    </span>
  );
}

export function ObligationCoverage({ total, withObligations }) {
  if (total === 0) return null;
  const pct = Math.round((withObligations / total) * 100);
  const isLow = pct < 50;
  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-2 rounded-lg border text-xs',
        isLow ? 'bg-amber-50 border-amber-200 text-amber-800' : 'bg-blue-50 border-blue-200 text-blue-800',
      )}
    >
      <span className="font-semibold">
        {withObligations} of {total}
      </span>
      <span>applicable laws have detailed obligations mapped ({pct}%)</span>
      {isLow && <span className="font-medium">— coverage is limited</span>}
    </div>
  );
}
