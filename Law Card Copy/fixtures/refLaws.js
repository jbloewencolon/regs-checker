/**
 * Phase 1 reference laws — four law objects selected to span the full design
 * matrix for the card consolidation work (Phases 2–4).
 *
 * Selection rationale:
 *   REF_CO  — enacted, curated, no enforcement note, future effectiveDate (2026-06-30).
 *             Tests: deadline chip logic, null-enforcement honest-unknown rule,
 *             multi-actor (deployer + developer) collapse/expand behavior.
 *
 *   REF_CT  — effective, curated, enforcement note present, provider actor role.
 *             Tests: enforcement display on enacted laws, the unreachable-provider-actor
 *             gap (UX_HANDOFF §5 Gap 2), multi-role toggle.
 *
 *   REF_NM  — withdrawn (not enacted), curated, "TBD" enforcement note present.
 *             Tests: enforcement-gating (chip must not show for non-enacted laws),
 *             honest-unknown rule for penalty language containing "TBD".
 *
 *   REF_NY  — effective, is_stub=true, developer-only, no enforcement note.
 *             Tests: automatic stub variant routing, stub visual treatment,
 *             single-actor card, null-enforcement honest-unknown rule.
 *
 * Data extracted from src/data/snapshot.json — do not edit manually.
 * If the snapshot is regenerated, re-run: node scripts/extract_ref_laws.mjs
 */

// ── REF_CO: Colorado AI Act (SB 24-205) ──────────────────────────────────────
// Enacted state law, deployer + developer obligations, no enforcement note,
// effectiveDate 2026-06-30 (near-future — deadline chip candidate).
export const REF_CO = {
  id: 48,
  identifier: 'SB 24-205',
  canonical_law_id: 'US-CO-SB24-205',
  name: 'Colorado AI Act — SB 24-205 (C.R.S.A. § 6-1-1701)',
  title: 'Colorado AI Act — SB 24-205 (C.R.S.A. § 6-1-1701)',
  level: 'state',
  jurisdiction: 'Colorado',
  stateCode: 'CO',
  status: 'enacted',
  priority: 'P2',
  is_stub: false,
  relevance_score: 3,
  government_only: false,
  effectiveDate: '2026-06-30',
  enforcementNote: null,
  coveredEntities: ['deployer', 'developer'],
  domainTags: ['generative_ai'],
  description:
    'Requires developers and deployers of high-risk AI systems to use reasonable care to protect consumers from algorithmic discrimination.',
  fullSummary:
    'Requires developers and deployers of high-risk AI systems to use reasonable care to protect consumers from algorithmic discrimination. Requires developers to address internal documentation requirements and risk management measures. Requires deployers to implement an appropriate risk management program, conduct comprehensive impact assessments, and provide consumers a right to correct information and appeal adverse decisions.',
  sourceUrl: 'https://infobytes.orrick.com/wp-content/uploads/Colorado-AI-Act-Amended.pdf',
  obligations: [
    { id: 964, type: 'risk_management', requirement_type: 'risk_management', description: 'Governance Program', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 965, type: 'risk_management', requirement_type: 'risk_management', description: '[analyst_curated] Developer must implement risk-management or reasonable-care program for high-risk AI systems before deployment and throughout lifecycle. Colorado AI Act.', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 966, type: 'assessment', requirement_type: 'assessment', description: '[analyst_curated] Deployer must complete impact assessment and maintain governance records before deployment and at regular intervals', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 967, type: 'assessment', requirement_type: 'assessment', description: 'Assessments', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 968, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 969, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 970, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 971, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 972, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 973, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 974, type: 'reporting', requirement_type: 'reporting', description: 'Explanation/Incident Reporting', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 975, type: 'reporting', requirement_type: 'reporting', description: '[analyst_curated] Developer must document and disclose material risk information to deployers/regulator promptly after discovery. 60-day cure period.', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 976, type: 'documentation', requirement_type: 'documentation', description: 'Provider Documentation', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
    { id: 977, type: 'documentation', requirement_type: 'documentation', description: 'Provider Documentation', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 48 },
    { id: 978, type: 'opt_out', requirement_type: 'opt_out', description: '[analyst_curated] Deployer must provide notice and means for review, contest, appeal, or human oversight at or before time of consequential decision', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 48 },
  ],
};

// ── REF_CT: Connecticut SB 2 ──────────────────────────────────────────────────
// Effective state law, deployer + developer + provider obligations, enforcement note.
// Key test surface: provider actor role (unreachable via QuickScan Q1, per UX_HANDOFF §5 Gap 2).
export const REF_CT = {
  id: 61,
  identifier: 'SB 2',
  canonical_law_id: 'US-CT-SB2',
  name: 'Connecticut Artificial Intelligence Act — SB 2',
  title: 'Connecticut Artificial Intelligence Act — SB 2',
  level: 'state',
  jurisdiction: 'Connecticut',
  stateCode: 'CT',
  status: 'effective',
  priority: 'P1',
  is_stub: false,
  relevance_score: 7,
  government_only: false,
  effectiveDate: null,
  enforcementNote: 'Attorney General; penalty structure TBD; no private right of action specified',
  coveredEntities: ['deployer', 'developer'],
  domainTags: ['automated_decisioning', 'generative_ai', 'synthetic_media'],
  description:
    'Comprehensive AI governance law covering high-risk AI systems. Imposes obligations on developers, deployers, and providers including risk management, impact assessment, training, transparency, and consumer rights.',
  fullSummary:
    'Large omnibus AI bill covering automated decision-making, generative AI, and synthetic media. Requires governance programs, impact assessments, staff training, responsible individual designation, consumer notice and labeling, developer risk disclosure, consumer appeal rights. Provider obligations include responsible individual designation and labeling.',
  sourceUrl: null,
  obligations: [
    { id: 648, type: 'risk_management', requirement_type: 'risk_management', description: 'Governance Program', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 649, type: 'risk_management', requirement_type: 'risk_management', description: '[analyst_curated] Developer must implement risk-management program for high-risk AI systems. Large omnibus AI bill.', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 61 },
    { id: 650, type: 'assessment', requirement_type: 'assessment', description: '[analyst_curated] Deployer must complete impact assessment and maintain governance records for high-risk AI', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 651, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 652, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 61 },
    { id: 653, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 61 },
    { id: 654, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'provider', actorTags: ['provider'], isMandatory: true, policyId: 61 },
    { id: 655, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 656, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 657, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 61 },
    { id: 658, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'provider', actorTags: ['provider'], isMandatory: true, policyId: 61 },
    { id: 873, type: 'reporting', requirement_type: 'reporting', description: '[analyst_curated] Developer must disclose material risk information to deployers/regulator promptly after discovery', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 61 },
    { id: 874, type: 'opt_out', requirement_type: 'opt_out', description: '[analyst_curated] Deployer must provide notice and means for review, contest, appeal, or human oversight', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
    { id: 875, type: 'reporting', requirement_type: 'reporting', description: '[analyst_curated] Government agency must issue public reports, inventories, or governance standards for agency AI use on recurring schedule', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 61 },
  ],
};

// ── REF_NM: New Mexico HB 60 ──────────────────────────────────────────────────
// Withdrawn law (died in committee), has obligations and a "TBD" enforcement note.
// Key test surface: enforcement-gating rule (chip must NOT render for non-enacted laws),
// honest-unknown rule for enforcement note containing "TBD".
export const REF_NM = {
  id: 167,
  identifier: 'HB 60',
  canonical_law_id: 'US-NM-HB60',
  name: 'New Mexico Artificial Intelligence Act — HB 60 (2025)',
  title: 'New Mexico Artificial Intelligence Act — HB 60 (2025)',
  level: 'state',
  jurisdiction: 'New Mexico',
  stateCode: 'NM',
  status: 'withdrawn',
  priority: 'P2',
  is_stub: false,
  relevance_score: 4,
  government_only: false,
  effectiveDate: null,
  enforcementNote: 'Attorney General; penalty structure TBD; no private right of action specified',
  coveredEntities: ['deployer', 'developer'],
  domainTags: ['automated_decisioning'],
  description:
    'Comprehensive AI governance: governance program, assessments, training, transparency, responsible individual — private sector deployers and developers. Bill died in House Judiciary Committee Feb 25, 2025.',
  fullSummary: 'AI disclosure in covered interactions; documentation; explanation on request',
  sourceUrl: 'https://www.nmlegis.gov/Legislation/Legislation?chamber=H&legtype=B&legno=60&year=25',
  obligations: [
    { id: 779, type: 'risk_management', requirement_type: 'risk_management', description: 'Governance Program', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 780, type: 'assessment', requirement_type: 'assessment', description: 'Assessments', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 781, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 782, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 167 },
    { id: 783, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 784, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 167 },
    { id: 785, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 786, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 167 },
    { id: 787, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 167 },
    { id: 788, type: 'reporting', requirement_type: 'reporting', description: 'Explanation/Incident Reporting', actorRole: 'deployer', actorTags: ['deployer'], isMandatory: true, policyId: 167 },
    { id: 789, type: 'reporting', requirement_type: 'reporting', description: 'Explanation/Incident Reporting', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 167 },
  ],
};

// ── REF_NY: New York AI Act S1169A ───────────────────────────────────────────
// Effective state law, is_stub=true, developer-only obligations, no enforcement note.
// Key test surface: automatic stub variant routing (is_stub flag drives variant, not caller),
// single-actor card (only developer obligations — no toggle alternatives), null enforcement.
// Note: probable duplicate of NY AB 8884 (law_id 166) — data team to review for merge.
export const REF_NY = {
  id: 162,
  identifier: 'S1169A',
  canonical_law_id: 'US-NY-S1169A',
  name: 'New York Artificial Intelligence Act S1169A',
  title: 'New York Artificial Intelligence Act S1169A',
  level: 'state',
  jurisdiction: 'New York',
  stateCode: 'NY',
  status: 'effective',
  priority: 'P2',
  is_stub: true,
  relevance_score: 1,
  government_only: false,
  effectiveDate: null,
  enforcementNote: null,
  coveredEntities: ['developer'],
  domainTags: ['generative_ai'],
  description:
    'Regulates high-risk AI systems; requires developer governance programs, assessments, training, responsible individual designation, general notice, and labeling. Senate companion to AB 8884.',
  fullSummary: null,
  sourceUrl: 'https://www.nysenate.gov/legislation/bills/2025/S1169/amendment/A',
  obligations: [
    { id: 758, type: 'risk_management', requirement_type: 'risk_management', description: 'Governance Program', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
    { id: 759, type: 'assessment', requirement_type: 'assessment', description: 'Assessments', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
    { id: 760, type: 'training', requirement_type: 'training', description: 'Training', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
    { id: 761, type: 'human_oversight', requirement_type: 'human_oversight', description: 'Responsible Individual', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
    { id: 762, type: 'disclosure', requirement_type: 'disclosure', description: 'General Notice', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
    { id: 763, type: 'disclosure', requirement_type: 'disclosure', description: 'Labeling/Notification', actorRole: 'developer', actorTags: ['developer'], isMandatory: true, policyId: 162 },
  ],
};

// ── Convenience array ─────────────────────────────────────────────────────────
export const REF_LAWS = [REF_CO, REF_CT, REF_NM, REF_NY];

// ── Matrix coverage summary ───────────────────────────────────────────────────
// Status:        enacted (CO), effective (CT, NY), withdrawn (NM)
// Data quality:  curated (CO, CT, NM), stub (NY)
// Actor roles:   deployer+developer (CO, NM), deployer+developer+provider (CT), developer-only (NY)
// Enforcement:   null (CO, NY), present+TBD (CT, NM)
// Effective date: future/2026-06-30 (CO), null (CT, NM, NY)
