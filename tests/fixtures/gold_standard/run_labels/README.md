# Extraction run labels — EA1 gold-set seed

Hand-checked verdicts on real extraction-run output, verified against the
committed source files in `output/law_texts/`. These labels are the seed for
the EA1 gold set that gates TA-8 (threshold/keyword retuning) and SFH-3
(confidence re-architecture): the fixtures in the parent directory define
what SHOULD be extracted; these labels record what a real run ACTUALLY
produced and what was wrong with it, giving negative examples the fixture
format alone cannot express (duplicates, fabricated quotes, misfired
normalization, hallucinated sub-structures).

## Files

- `2026-07-12_extraction_run_labels.csv` — all 37 extractions from the
  2026-07-12 run (AZ SB1462 22:30, AZ SB 1359 23:41, AR HB1877 23:43;
  first run after the NVIDIA streaming/timeout fixes). `extraction_id`
  matches the run's dashboard export CSVs.

## Verdict vocabulary

| verdict     | meaning |
|-------------|---------|
| `correct`   | Payload substantively right; quotes grounded in source. |
| `partial`   | Core content right, but with a defect listed in `error_types`. |
| `incorrect` | Substantively wrong: fabricated quote, misclassified type, etc. |
| `duplicate` | Near-identical copy of an earlier extraction for the same law (QA-4 target). |

## error_types vocabulary (semicolon-separated when multiple)

- `padded_actor` — generic actor invented to fill the schema's array
- `hallucinated_actor` — named actor not present in the definition context
- `hallucinated_framework_ref` — framework cross-contaminated from a sibling definition
- `fabricated_quote` — evidence text appears nowhere in the source document
- `truncated_quote` — verbatim quote cut short (page break, token budget)
- `reworded_quote` — content right, quote paraphrased instead of verbatim
- `misclassified_type` — wrong extraction/conflict type for the passage
- `wrong_normalization` — `*_normalized` field inconsistent with the raw phrase
- `restated_obligation` — same statutory duty emitted as two obligations
- `duplicate_overlapping_passage` — re-extraction from an overlapping passage

## Notable rows

- **id 9** (preemption, 0.895/A): the single A-tier row in the batch is the
  least trustworthy interpretation — 4/4 verified spans, wrong
  `conflict_type`. Concrete exhibit for SFH-3a: span grounding measures
  provenance, not correctness.
- **ids 22-24, 29**: fabricated quotes that the QA-1 span verifier correctly
  rejects — the model reconstructed amended-code text from training
  knowledge instead of quoting the bill.
- **ids 30-37**: the cross-passage duplicate cluster QA-4 now suppresses.
