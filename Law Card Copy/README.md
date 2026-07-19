# Law Card Component Bundle

This folder contains a self-contained, portable version of the **LawCard** component system from the ai-ethics-evaluator project. It includes all necessary components, utilities, data structures, CSS, and test fixtures to integrate Law Cards into the Regs Checker pipeline or any other downstream system.

## Structure

```
Law Card Copy/
├── components/
│   ├── LawCard.jsx              # Main four-variant card component (1,512 LOC)
│   ├── lawSourceQuotes.js       # Passage collection & matching (111 LOC)
│   └── PolicyBadges.jsx         # Badge library (287 LOC)
├── services/
│   ├── extractionLoader.js      # Lazy loader for per-jurisdiction extractions (124 LOC)
│   ├── normalize-extract.js     # Tag normalization & enforcement actor detection
│   ├── textSanitize.js          # PDF boilerplate stripping & interleaving detection
│   └── [supabase.js]            # (Stub) Placeholder for Supabase client
├── data/
│   └── constants-extract.js     # Domain tags, obligation categories, triage labels
├── engines/
│   └── priority.js              # Priority derivation engine (208 LOC)
├── utils/
│   ├── cn.js                    # Simple class combiner (1 LOC)
│   └── sourceUrl.js             # Source authority classification (50 LOC)
├── css/
│   └── lawcard-tokens.css       # Design tokens & utility classes (~150 LOC)
├── fixtures/
│   └── refLaws.js               # Four reference law objects for testing (201 LOC)
└── README.md                    # This file
```

## Component Variants

The LawCard component supports four rendering variants:

1. **compact** — QuickScan top-priority list (clickable to expand inline)
2. **browse** — Direction A v2 paced disclosure (L0 → L1 → L3)
3. **full** — Comprehensive Audit dossier (with extraction tabs)
4. **stub** — "On Our Radar" (dashed border, no obligations)

## Key Design Principles

### Honest-Unknown Rendering
- Null fields render as absent, not defaulted
- No guessed values, ever
- "Not yet triaged" status uses a hollow ring, never a low-priority fill
- Missing data surfaces as explicit data-gap badges, not silent omissions

### Extraction-Chunk Contract
Each jurisdiction has a lazy-loaded extraction chunk containing:
```javascript
{
  jurisdiction, code, lawCount, extractionCount,
  byLaw: {
    [lawId]: {
      obligations, complianceMechanisms, rightsProtections,
      enforcements, definitions, ambiguities, ...
    }
  }
}
```

### Enforcement-Actor Routing
Obligation actors that are recognized enforcers (Attorney General, DOJ, etc.) are routed to `obligation.enforcementAuthority` instead of `actorRole`, so they never render as regulated parties.

### Verification Semantics
- "Analyst-verified" is gated on an explicit `law.verified` or `law.analystReviewed` flag
- `lastUpdatedAt` is NOT treated as verification (0% of snapshot extractions are human-verified)

### Role-Based Obligation Filtering
Laws display active-role obligations prominently; other-role obligations collapse into expandable "Also regulates…" disclosures. Active role is inferred from the user's profile (developer, deployer, employer, etc.).

## Data Dependencies

### From constants-extract.js
- `ENACTED_STATUSES` — Legal status categorization
- `POLICY_STATUSES` — Humanized status labels + colors
- `DOMAIN_TAG_MAP` — Canonical domain tag vocabulary
- `OBLIGATION_CATEGORY_MAP` — Obligation type → display category
- `TRIAGE_LABELS` — P0-P3 action labels ("Act now", "This quarter", etc.)

### From normalize-extract.js
- `normalizeTags()` — Normalize DB keys to engine vocabulary
- `ENFORCEMENT_ACTORS`, `isEnforcementActor()` — Recognize enforcers

### From textSanitize.js
- `looksInterleaved()` — Detect column-spliced prose (honest-unknown backstop)
- `stripSourceBoilerplate()` — Remove PDF footer noise
- `stripProvenancePrefix()` — Extract analyst-curation flags

### From extractionLoader.js
- `loadExtractionChunk()` — Per-jurisdiction lazy loader (caches per session)
- `getExtractionsForLaw()` — Fetch passages for a specific law ID
- `loadExtractionManifest()` — Check which jurisdictions have data

## CSS System

All styling uses CSS custom properties (`--lc-*` tokens) defined in `lawcard-tokens.css`. Card variants use these tokens, not Tailwind utilities, so the bundle is self-contained.

Key tokens:
- `--lc-ink-*` (900–50) — Text/border colors
- `--lc-paper` — Background
- `--lc-signal` / `--lc-signal-bg` / `--lc-signal-border` — Enforcement callouts
- `--lc-match` — Search/match highlights
- `--lc-p0`, `--lc-p1`, `--lc-p2`, `--lc-p3` — Triage colors

## Usage in Regs Checker

### 1. Import the Component
```javascript
import LawCard from './Law Card Copy/components/LawCard';
```

### 2. Pass a Law Object
```jsx
<LawCard
  law={lawData}
  variant="compact"          // or "browse", "full", "stub"
  activeRole="deployer"      // or "developer", "employer", etc.
  onClick={(law) => {...}}   // Optional: navigate on L0 click
  coverageResult={result}    // Optional: compliance assessment result
/>
```

### 3. Wire Up Extraction Data
The `extractionLoader.js` fetches passages from Supabase Storage or static files. You'll need to:
- Configure a `getSupabase()` function (in services/supabase.js) or
- Provide pre-bundled static extraction chunks at `public/data/extractions/{CODE}.json`

### 4. Style the Page
Import `lawcard-tokens.css` in your app's main CSS:
```css
@import './Law Card Copy/css/lawcard-tokens.css';
```

Or inline the tokens into your existing design system.

## Porting Notes

### What to Copy As-Is
- All files in this bundle (they're self-contained)
- The four reference laws in `fixtures/refLaws.js` (use them as smoke tests)
- The CSS design tokens (they're stable; don't modify without regenerating all four variants)

### What to Adapt
- **Supabase integration** — Replace `services/supabase.js` with your own auth/storage client
- **Extraction paths** — Adjust `BASE_PATH` and `STORAGE_BUCKET` in `extractionLoader.js` if your paths differ
- **Design tokens** — The colors/spacing are frozen to pass visual regression tests; changing them requires re-running the test suite

### What You Can Sever
- `CoverageCard` — Referenced by CompactCard/FullCard but not core to LawCard rendering; replace with your own or pass `null`
- Icons from lucide-react — Already bundled via imports; substitute your own icon library if needed

## Test Surface

Four reference laws span the design matrix:
- **REF_CO** — Enacted, future effective date, no enforcement note
- **REF_CT** — Effective, provider actor role, enforcement note present
- **REF_NM** — Withdrawn, TBD enforcement, tests enforcement-gating
- **REF_NY** — Effective stub, developer-only, single-role card

Use them to validate:
1. All four variants render
2. Role toggling works
3. Disclosure expansion/collapse works
4. Enforcement signals appear/disappear correctly
5. Stub visual treatment (dashed border)

## Deployment Checklist

- [ ] Copy all files to your Regs Checker codebase
- [ ] Wire up `services/supabase.js` or provide static extraction chunks
- [ ] Import `lawcard-tokens.css` in your app
- [ ] Test with reference laws (REF_CO, REF_CT, REF_NM, REF_NY)
- [ ] Validate rendering in all four variants
- [ ] Test role inference from your profile system
- [ ] Verify extraction passage loading (or stub with `null`)
- [ ] Run accessibility tests (ARIA labels, keyboard nav, color contrast)
- [ ] Visual regression test against bundled baselines

## Version & Maintenance

**Generated:** 2026-07-19  
**Source:** ai-ethics-evaluator commit [snapshot-at-porting-time]  
**Compatibility:** React 18+, Node 18+

This bundle is a point-in-time snapshot. To sync future updates:
1. Re-run the porting script from the main project
2. Diff against this bundle
3. Apply only the changes that don't break your customizations

## Questions?

Refer to:
- `docs/LAWCARD_CODE_INVENTORY.md` — Full inventory & coupling analysis
- `src/components/__tests__/LawCard.test.jsx` — Test patterns
- `e2e/lawcard-visual.spec.js` — Visual regression baselines
- `e2e/lawcard-a11y.spec.js` — Accessibility audit
