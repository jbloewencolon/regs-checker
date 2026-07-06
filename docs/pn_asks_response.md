# Response: PN Pipeline Extraction Enrichment Asks (2026-07-06)

_RC extraction team response to `RC_PIPELINE_EXTRACTION_ASKS_20260706.md`
(Policy Navigator data/taxonomy review). Prepared 2026-07-06._

## Decisions taken (operator-confirmed 2026-07-06)

1. **Division of labor: RC enriches the payload only.** RC ships richer,
   structured JSONB inside the existing `synced_extractions` contract; PN's
   ingestion maps it into `obligations` / `obligation_triggers` /
   `obligation_deadlines` / `source_provenance` etc. The one-table contract
   stays stable; RC does not write to PN's evolving internal schema.
2. **Vocabulary canon: RC's ratified vocabularies + a crosswalk.** RC's
   13-code actor vocabulary and 22-code obligation-family vocabulary went
   through formal ratification (see `docs/NORMALIZATION_VOCABULARY_RATIFICATION_PLAN.md`)
   and are strictly richer than PN's 7 actor roles / 13 obligation types.
   RC will emit **both**: its canonical code and the crosswalked PN value,
   following the existing Orrick/IAPP crosswalk pattern in `data/lookups/`.
3. **Prompt-change asks are gated on the EA1 gold-set baseline.** RC's
   standing discipline (see `tasks.md`, EA plan) is that no extraction-prompt
   change ships without a measured before/after on the gold set. Everything
   deliverable *deterministically* proceeds now (it's most of the memo);
   the rest queues behind EA1.

## Coverage numbers (the query your memo couldn't run)

Run 2026-07-06 against the PN Supabase (the 502s have cleared):

| Target | Rows / coverage | Reading |
|---|---|---|
| `obligations`, `obligation_triggers`, `obligation_deadlines`, `required_artifacts`, `source_provenance` | **0 rows each** | Empty as suspected |
| `law_enforcement_details`, `law_triggering_thresholds`, `law_obligation_flags` | **0 rows each** | Purged in remediation P2; not repopulated |
| `synced_extractions` | **0 rows** | Post-P2 purge, pre-next-sync |
| `fact_laws` | 221 laws | — |
| `fact_laws.min_employees / min_revenue / consumer_count_trigger` (048) | **0 / 221** | Never backfilled |
| `fact_laws.authority_type / binding_effect / issuing_body` (056) | **0 / 221** | Confirms the AuthorityTypeBadge never fires |
| `fact_laws.small_business_exempt / private_right_of_action` (048) | 221/221 *populated* — but **all `false`** | ⚠️ See below |

Two consequences worth more than the ranking exercise:

- **The next sync is greenfield.** `synced_extractions` is empty, so any
  payload enrichment RC lands *before* the next extraction run + sync ships
  in the very first rows PN ingests — no backfill, no migration, no mixed
  payload versions. This is why RC is landing the deterministic enrichment
  now, ahead of the next run.
- **⚠️ Your two "populated" booleans are column defaults, not data.**
  All 221 rows read `small_business_exempt = false, private_right_of_action
  = false`. A default `false` on `private_right_of_action` renders as an
  affirmative legal claim ("this law has no private right of action") for
  221 laws that were never assessed. That is precisely the legal-overclaim
  class your own Ask 6 warns about, live in your schema today. Recommend PN
  make both columns nullable (null = not yet assessed) before RC starts
  feeding real values, or the real values will be indistinguishable from
  the fake ones.

## Ask-by-ask response

### Ask 1 — actor_role / enforcement_authority ✅ accepted, mostly deterministic

RC already has what this needs: a ratified 13-code actor vocabulary
(215-row alias table) in which the enforcer (`regulator`) is a distinct
code from every regulated role, plus `EnforcementInfo.enforcing_body`
already carried separately on obligation payloads. RC will emit:

- `actor_role_rc` — RC canonical code (13-value vocabulary)
- `actor_role` — crosswalked PN value (your 7-value vocabulary)
- `enforcement_authority` — from `enforcement.enforcing_body`, never merged
  with actor_role

Vocabulary note: PN's `employer`, `vendor`, `integrator` are not RC
canonical codes — per RC's ratified fork decisions, employer maps under
`deployer` (sector captured separately) and vendor under `provider`. The
crosswalk will make those mappings explicit; if PN needs employer as a
first-class role, that reopens RC's Phase 3a ratification and needs a
joint session, not a silent remap.

### Ask 2 — obligation_type ✅ accepted as a deterministic crosswalk

RC's ratified `obligation_family` vocabulary (22 codes) crosswalks onto
your 13-value taxonomy nearly 1:1 (`disclosure_to_user`→`disclosure`,
`impact_assessment`→`assessment`, `human_review`→`human_oversight`,
`opt_out_right`→`opt_out`, `training_mandate`→`training`, …). RC already
classifies obligations into families deterministically (concept-grouping
alias matching, no LLM). RC will emit `obligation_family` (RC canon) +
`obligation_type` (crosswalked PN value). Keep `modality` — agreed they're
complementary.

### Ask 3 — structured deadlines ⚠️ split: half now, half gated

Correction to the memo's premise: the flattening happens in **RC's own
sync adapter**, not (only) your app — RC extracts a structured
`TimelineInfo` object and `payload_adapter.py` collapses it to a prose
string at sync time. Landing now: the structured timeline object ships
alongside the flattened string, including `date_parse_status` so PN can
distinguish real ISO-8601 dates from unparsed prose (never do date math on
`unparsed` fields). A `deadlines[]` array derived from the parsed fields
follows in the next tranche.

**Per-cohort phasing** ("≥500 employees by 2027-01-01; others by
2028-07-01") is genuinely new extraction behavior — that's a prompt/schema
change, gated on the EA1 gold-set baseline. Queued, not refused.

### Ask 4 — trigger predicates + stable IDs ⚠️ two-thirds now, linking needs design

- **Stable ID: already shipping.** Every synced row carries
  `system_a_extraction_id` — RC's immutable extraction id. Reference that.
- **Structured predicates: accepted, deterministic.** `{trigger_type,
  trigger_operator, trigger_value}` will be parsed from RC's existing
  typed threshold fields + condition text (same parser discipline as RC's
  numeric-grounding module). Next tranche.
- **`applies_to_obligation_id` FK: needs an architecture decision**, not a
  field add. Thresholds and obligations are extracted as separate rows;
  linking them requires either a same-passage co-location heuristic, the
  concept-grouping key, or model-emitted cross-references (fragile).
  RC will propose a design rather than ship a guess.

### Ask 5 — law-level covered-entity fields ✅ accepted, deterministic rollup

RC has all five signals scattered across existing extractions (typed
threshold numerics, `private_right_of_action` on enforcement data,
exception payloads for `small_business_exempt`). RC will build a law-level
rollup emitted with the sync (shape TBD in the next tranche — likely a
law-level record type in the payload stream, since the division-of-labor
decision keeps RC out of `fact_laws` writes). Note the nullable-boolean
schema fix PN should make first (see coverage table above).

### Ask 6 — authority_type / binding_effect ⚠️ accepted in principle, not as an LLM agent

The risk is real (RC's corpus audit found regulatory notices mixed with
statutes). But this is a ~232-law, mostly-one-time classification, not a
per-run extraction task. RC will do it as seed-metadata + deterministic
heuristics + a human-review queue for the ambiguous residue. An LLM
classification pass only if that leaves too much residue — and then it's
EA1-gated like any other prompt work.

### Ask 7 — content_hash + provenance ✅ accepted, landing immediately

RC already computes and stores everything this ask wants:
`document_versions.source_hash` (SHA-256 of retrieved source content),
`retrieved_at`, section paths, and character-offset span provenance. It
just never traveled with the sync payload. A `provenance` object
(`content_hash`, `retrieved_at`, `section_locator`) is being added to
every synced payload now. `authority_type` joins it once Ask 6 lands.

### Ask 8 — ambiguity → obligation IDs ❌ premise outdated; simpler fix landing

The ambiguity agent was **retired** (RC handoff note DI-4, 2026-06-22):
ambiguity findings are now extracted as `interpretation_risks` embedded
*directly on* the obligation (and rights_protection) payload they affect.
There is nothing to fuzzy-match — the attachment is structural. The
top-level `ambiguity` rows your normalizer reads are legacy.

The real gap was on RC's side: the sync adapter was stripping
`interpretation_risks` from the payload. That's fixed in the current
tranche. **Action for PN:** retire `normalizeAmbiguity()`'s
affected-obligations matching and read `payload.interpretation_risks` on
obligation/rights rows instead (as DI-4 requested).

### Hygiene items

- **Modality:** RC already normalizes modality at parse time (a fixed map
  to `must/shall/may/should/prohibited/…`). The payload's `modality` *is*
  the normalized value. If PN's map still produces an `other` bucket from
  RC rows, send us the offending values — that's a bug report, not a
  vocabulary gap.
- **`subject_normalized`:** consistency lands with Ask 1 (same canonical
  mapping).
- **Domain tags:** RC will align tag ids with the canonical `DOMAIN_TAGS`
  set in the crosswalk tranche.

## What RC is landing, in order

| Tranche | Content | Status |
|---|---|---|
| **1 (now)** | Stop stripping already-extracted fields (`interpretation_risks`, `safe_harbor`, `consent_requirements`, `object`, structured timeline); Ask 7 provenance object; Ask 8 documentation | In progress |
| **2** | Ask 1 actor_role + crosswalk; Ask 2 obligation_type crosswalk; Ask 3a `deadlines[]` from parsed dates; Ask 4b trigger predicates; tag/modality alignment | Ready after tranche 1 |
| **3** | Ask 5 law-level rollup; Ask 6 metadata/heuristic classification + review queue | Ready after tranche 2 |
| **4 (gated)** | Ask 3b per-cohort deadline extraction; Ask 4c obligation-FK linking design; Ask 6 LLM residue pass | EA1 baseline or design ruling required |
