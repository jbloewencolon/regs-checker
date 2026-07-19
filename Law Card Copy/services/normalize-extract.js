// ─────────────────────────────────────────────────────────────────────────────
// LawCard-specific normalization functions extracted from src/services/normalize.js
// ─────────────────────────────────────────────────────────────────────────────

import { stripSourceBoilerplate, stripProvenancePrefix, isInternalLawIdentifier } from './textSanitize.js';

// ── Tag Normalization (compliance_tags DB keys → engine vocabulary) ──────────
export const TAG_NORMALIZE = {
  automated_decisioning: 'automated_decisions',
  employment_ai: 'employment',
  healthcare_ai: 'health',
  biometric_processing: 'biometrics',
  deepfake_generation: 'synthetic_media',
};

/** Normalize tag keys from DB (compliance_tags) to engine vocabulary. */
export function normalizeTags(tags) {
  return tags.map((t) => TAG_NORMALIZE[t] || t);
}

// ── Actor Role Normalization ─────────────────────────────────────────────────
export const ENFORCEMENT_ACTORS = new Set([
  'attorney general',
  "attorney general's office",
  'state attorney general',
  'department of justice',
  'district attorney',
  'secretary of state',
  'consumer protection division',
  'ftc',
  'federal trade commission',
  'state ag',
  'regulator',
  'court',
  'state court',
]);

export function isEnforcementActor(dbRole) {
  if (!dbRole) return false;
  return ENFORCEMENT_ACTORS.has(dbRole.toLowerCase().trim());
}
