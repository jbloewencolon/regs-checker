// ─────────────────────────────────────────────────────────────────────────────
// LawCard-specific constants extracted from src/data/constants.js
// Only the exports needed for LawCard rendering
// ─────────────────────────────────────────────────────────────────────────────

// ── Centralized Status Categorization ─────────────────────────────────────────
export const ENACTED_STATUSES = new Set([
  'enacted', 'effective', 'amended', 'final_rule', 'final_guidance',
  'Active', 'Enacted', 'Signed',
]);

// ── Policy Status Types ────────────────────────────────────────────────────────
export const POLICY_STATUSES = {
  enacted: { id: 'enacted', label: 'Enacted', group: 'passed', color: 'emerald' },
  effective: { id: 'effective', label: 'Effective', group: 'passed', color: 'emerald' },
  amended: { id: 'amended', label: 'Amended', group: 'passed', color: 'teal' },
  final_rule: { id: 'final_rule', label: 'Final Rule', group: 'passed', color: 'emerald' },
  final_guidance: { id: 'final_guidance', label: 'Final Guidance', group: 'passed', color: 'emerald' },
  introduced: { id: 'introduced', label: 'Introduced', group: 'pending', color: 'blue' },
  in_committee: { id: 'in_committee', label: 'In Committee', group: 'pending', color: 'blue' },
  passed_one_chamber: { id: 'passed_one_chamber', label: 'Passed One Chamber', group: 'pending', color: 'indigo' },
  pending_signature: { id: 'pending_signature', label: 'Pending Signature', group: 'pending', color: 'yellow' },
  proposed_rule: { id: 'proposed_rule', label: 'Proposed Rule', group: 'pending', color: 'blue' },
  draft_guidance: { id: 'draft_guidance', label: 'Draft Guidance', group: 'pending', color: 'blue' },
  public_consultation: { id: 'public_consultation', label: 'Public Consultation', group: 'discussed', color: 'orange' },
  under_discussion: { id: 'under_discussion', label: 'Under Discussion', group: 'discussed', color: 'orange' },
  withdrawn: { id: 'withdrawn', label: 'Withdrawn / Failed', group: 'withdrawn', color: 'gray' },
  revoked: { id: 'revoked', label: 'Revoked', group: 'withdrawn', color: 'gray' },
  vetoed: { id: 'vetoed', label: 'Vetoed', group: 'withdrawn', color: 'gray' },
  enjoined: { id: 'enjoined', label: 'Enjoined (Court-Blocked)', group: 'withdrawn', color: 'gray' },
};

// ── Domain Tags ────────────────────────────────────────────────────────────────
export const DOMAIN_TAGS = [
  { id: 'automated_decisions', label: 'Automated Decisions', color: 'orange' },
  { id: 'generative_ai', label: 'Generative AI', color: 'purple' },
  { id: 'synthetic_media', label: 'Synthetic Media', color: 'red' },
  { id: 'child_safety', label: 'Child Safety', color: 'red' },
  { id: 'health', label: 'Health', color: 'red' },
  { id: 'public_sector', label: 'Public Sector', color: 'indigo' },
  { id: 'employment', label: 'Employment', color: 'green' },
  { id: 'insurance', label: 'Insurance', color: 'emerald' },
  { id: 'transparency', label: 'Transparency / Labeling', color: 'teal' },
  { id: 'elections', label: 'Elections', color: 'blue' },
  { id: 'privacy', label: 'Privacy', color: 'blue' },
  { id: 'biometrics', label: 'Biometrics', color: 'purple' },
  { id: 'finance', label: 'Finance', color: 'emerald' },
  { id: 'education', label: 'Education', color: 'yellow' },
  { id: 'housing', label: 'Housing / Real Estate', color: 'teal' },
  { id: 'criminal_justice', label: 'Criminal Justice', color: 'red' },
  { id: 'ip_copyright', label: 'IP / Copyright', color: 'gray' },
  { id: 'surveillance', label: 'Surveillance', color: 'red' },
  { id: 'general_purpose_ai', label: 'General-Purpose AI', color: 'purple' },
  { id: 'prohibited_practice', label: 'Prohibited AI Practice', color: 'red' },
];

export const DOMAIN_TAG_MAP = Object.fromEntries(DOMAIN_TAGS.map((d) => [d.id, d]));

// ── Obligation Category Taxonomy (Phase 1 — card consolidation) ────────────────
export const OBLIGATION_CATEGORIES = [
  {
    id: 'risk_assessment',
    label: 'Risk & Impact Assessment',
    description: 'Evaluate AI systems for potential harms before and during deployment.',
    color: 'orange',
    reqTypes: ['risk_management', 'assessment'],
  },
  {
    id: 'transparency',
    label: 'Transparency & Disclosure',
    description: 'Inform users and regulators that AI is being used and how.',
    color: 'blue',
    reqTypes: ['disclosure', 'reporting'],
  },
  {
    id: 'governance',
    label: 'Governance & Documentation',
    description: 'Maintain records, enable human oversight, and designate accountability.',
    color: 'purple',
    reqTypes: ['documentation', 'human_oversight'],
  },
  {
    id: 'training',
    label: 'Training & Awareness',
    description: 'Train staff on AI use, risks, and applicable policies.',
    color: 'teal',
    reqTypes: ['training'],
  },
  {
    id: 'consumer_rights',
    label: 'Consumer Rights',
    description: 'Give affected individuals rights to appeal, opt out, or seek correction.',
    color: 'emerald',
    reqTypes: ['opt_out'],
  },
];

// Flat lookup: req_type string → category ID (for O(1) access during render)
export const OBLIGATION_CATEGORY_MAP = Object.fromEntries(
  OBLIGATION_CATEGORIES.flatMap((cat) => cat.reqTypes.map((rt) => [rt, cat.id])),
);

// ── Triage rubric (Direction A v2 — replaces "Priority" surface-level) ────────
export const TRIAGE_LABELS = {
  P0: { label: 'Act now',      sub: 'Effective and high impact' },
  P1: { label: 'This quarter', sub: 'Effective soon or moderate impact' },
  P2: { label: 'This year',    sub: 'Future-dated or narrow scope' },
  P3: { label: 'Monitor',      sub: 'Tracked, not yet actionable' },
  UNCLASSIFIED: { label: 'Not yet triaged', sub: 'Limited data available' },
};

// Short label per obligation category id
export const CATEGORY_SHORT_LABEL = {
  risk_assessment: 'Risk',
  transparency:    'Disclosure',
  governance:      'Governance',
  training:        'Training',
  consumer_rights: 'Rights',
};
