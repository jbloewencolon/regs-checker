# docs/ — Active Documentation Index

Index of the active documentation directory (RC0-1, 2026-07-04). Format
mirrors `archive/README.md`. Anything classified **archive candidate** stays
here until the operator confirms — RC3-2 does the actual move, gated on this
file's classifications being confirmed.

Classification evidence: a doc is **current** when `tasks.md`'s active plans
cite it, when it's the record of completed tracked work, or when it's a
still-open operational input. Docs whose only mention in `tasks.md` is the
RC0-1 cleanup entry itself are classified from their own content and their
companion docs' status — those judgments are marked *(unconfirmed)* and need
an operator/product look before RC3-2 acts on them.

## Current — active plans and strategy

| File | Why current |
|---|---|
| `run1_unified_plan.md` | Governing plan for the Run-1 tracker-grounded quality work; `tasks.md` header links it directly |
| `engineering_strategy_v3.md` | The strategy doc that reframes the unified plan; cited in `tasks.md` header (v2 is in `archive/docs/`) |
| `remediation_plan.md` | Active remediation phases; P3 planned-not-started, P4 still open; cited throughout `tasks.md` |
| `NORMALIZATION_VOCABULARY_RATIFICATION_PLAN.md` | Phase 3 vocabulary governance; cited in `tasks.md` Phase 3a; Phase 3d ratification still open |
| `production_readiness_review.md` | 2026-07-01 full-repo audit; source review behind `tasks.md`'s Security & Data-Quality remediation phases; recent enough to still describe the system |

## Current — records of completed tracked work

| File | Why current |
|---|---|
| `phase0_completion_log.md` | Completion record, cited from `tasks.md` Phase 2 summary |
| `phase1_completion_log.md` | Completion record, cited from `tasks.md` Phase 2 summary |
| `phase2_completion_log.md` | Completion record, cited from `tasks.md` Phase 2 summary |

## Current — reference and operational inputs

| File | Why current |
|---|---|
| `data_dictionary.md` | Plain-English field/taxonomy reference for business/product/review audiences |
| `data_dictionary.pdf` | Rendered duplicate of the `.md` — regenerate on `.md` changes or it silently drifts; consider whether the repo needs both *(unconfirmed)* |
| `output_taxonomy_explained.md` | Short-form companion to `data_dictionary.md`; explains `output/` contents |
| `missing_laws_ingest_queue.csv` | Operational queue for the still-open coverage task (`tasks.md` 1d — seed 135 text-ready laws); the `text_ready` bucket is that task's worklist |

## Archive candidates — need operator/product confirmation before RC3-2 moves them

| File | Why flagged *(all unconfirmed)* |
|---|---|
| `pipeline_rebuild_plan.md` | Self-describes as "working draft v1 — gated rebuild proposal, not a committed roadmap"; its companion `taxonomy_strategy_summary.md` is already archived. Product call: is the rebuild path still on the table? |
| `taxonomy_dev_plan.md` | Working draft; both named companions are gone (`taxonomy_strategy_summary.md` archived, `data_taxonomy_analysis.md` absent from the repo). Same product call as above — it's the alternative path to the rebuild plan |
| `code_update_strategy_eng.md` | Run-1-learnings strategy whose companion (`extraction_run_corrections_eng.md`) is archived with its C-1…C-8 items tracked in `tasks.md`; likely folded into `engineering_strategy_v3.md` like the other pre-v3 strategy docs |
| `vocab_harvest_spec_eng.md` | Specifies the vocabulary harvest, which `tasks.md` Phase 3 marks done (harvest ✅, 3a ✅); remaining value is historical unless the D-1…D-4 self-improvement loop is still planned |
| `actor_taxonomy_analysis.md` | The analysis behind the actor vocabulary decision, which is locked (Phase 3a ✅ — 13 canonical codes); a completed decision's input, like other analyses already in `archive/docs/` |
| `product_review_remediation_plan.md` | 2026-06-20 response to the legal-analyst product review; not cited by any active `tasks.md` plan. Operator call: were its items absorbed into the remediation phases, or do any remain open? |
