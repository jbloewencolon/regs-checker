// Canonical source-authority classification (MOD7-a).
//
// One classifier, used by both the Shield law cards (SourceAuthorityBadge in
// components/PolicyBadges.jsx) and the analyst-intake queue/form (via
// sourceUrlStatus in pages/analyst-intake/intakeHelpers.js). Previously there
// were two independent same-named `classifySourceUrl` implementations — a
// string-returning host-based one here-in-PolicyBadges and an object-returning
// substring-matching one in intakeHelpers — that disagreed on real data (e.g.
// `media.orrick.com`: the intake matcher flagged it, the host matcher did not).
// This is the single reconciled source of truth.

// Exact hosts (after stripping a leading `www.`) that are third-party
// aggregators / trackers / law-firm blogs, not the official legislative source.
const SECONDARY_HOSTS = new Set([
  'iapp.org', //        IAPP tracker — placeholder citation to replace w/ official
  'legiscan.com', //    tracking aggregator, not the official legislature
  'trackerr.io',
  'statetracker.ai',
]);

// Whole domain families that are secondary regardless of subdomain. Orrick (a
// law firm) republishes law PDFs under several subdomains — infobytes.orrick.com
// (×11 in the dataset), media.orrick.com, … — none of which is the official
// legislature, so match the family rather than enumerating every subdomain.
const SECONDARY_DOMAIN_SUFFIXES = ['orrick.com'];

/**
 * Classify a law's source URL by authority.
 * @returns {'none'|'placeholder'|'secondary'|'primary'|'unparseable'}
 *   - none:        no URL recorded
 *   - placeholder: an explicit stand-in ("(...)", "tbd", "N/A") — no real link
 *   - secondary:   a real link, but to an aggregator/blog, not the official source
 *   - unparseable: a non-empty value that isn't a valid URL
 *   - primary:     a parseable link to a source treated as authoritative
 */
export function classifySourceUrl(url) {
  if (!url) return 'none';
  const s = url.trim();
  if (s.startsWith('(') || s === 'tbd' || s === 'TBD' || s === 'N/A') return 'placeholder';
  try {
    const host = new URL(s).hostname.replace(/^www\./, '');
    const isSecondary =
      SECONDARY_HOSTS.has(host) ||
      SECONDARY_DOMAIN_SUFFIXES.some((d) => host === d || host.endsWith('.' + d));
    if (isSecondary) return 'secondary';
  } catch {
    return 'unparseable';
  }
  return 'primary';
}
