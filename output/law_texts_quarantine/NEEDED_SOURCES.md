# Laws Needing Correct Source Files

These laws had wrong source text (URL row-offset in law_fulltext_report.csv).
Files marked RESOLVED have been fixed by swapping content to the correct law ID.
Files marked NEEDS SOURCE still require correct bill text placed in output/law_texts/<canonical_law_id>.txt

| canonical_law_id | Status | Notes |
|---|---|---|
| `TMP-TX-AITEXASRESPONS` | ✅ RESOLVED | TRAIGA (HB 149) text restored — copied from TMP-TX-ABUSEUSINGARTI quarantine file |
| `TMP-TX-ABUSEUSINGARTI` | ⚠️ NEEDS SOURCE | TX Financial Abuse Using AI law — correct bill URL unknown; quarantine file had TRAIGA text (now moved to AITEXASRESPONS) |
| `TMP-SC-ESTATEREALESTA` | ✅ RESOLVED | SC Real Estate AI statute restored — copied from TMP-RI-DECEPTIVEANDFR quarantine file |
| `TMP-RI-DECEPTIVEANDFR` | ⚠️ NEEDS SOURCE | RI Deceptive Synthetic Media law — quarantine file had SC statute (now moved to SC-ESTATEREALESTA) |
| `TMP-VT-AMENDMENTOFNON` | ✅ RESOLVED | VT Act 161 (intimate image amendment) restored — copied from TMP-TX-MEDIAUNLAWFULP quarantine file |
| `TMP-TX-MEDIAUNLAWFULP` | ⚠️ NEEDS SOURCE | TX Unlawful Production law — quarantine file had VT Act 161 (now moved to VT-AMENDMENTOFNON) |
| `TMP-WV-AGAINSTCHASTIT` | ✅ RESOLVED | WV SB 198 (Crimes Against Chastity) restored — copied from TMP-TX-AITEXASRESPONS quarantine file |
| `TMP-WV-CRIMESAGAINSTC` | ⚠️ NEEDS SOURCE | Second WV CSAM law — quarantine file had WY homepage (no bill text) |
| `TMP-TX-AISEXUALMATERI` | ⚠️ NEEDS SOURCE | TX AI Sexual Material law — quarantine file had WA session law |
| `TMP-TX-DECISIONTEXASD` | ⚠️ NEEDS SOURCE | TX Data Privacy Act — quarantine file had WY legislature homepage |
| `TMP-TX-TOTHECSAMSTATU` | ⚠️ NEEDS SOURCE | TX CSAM Statute amendment — quarantine file had TX SB 441 (different TX bill) |
| `TMP-TX-UNLAWFULPRODUC` | ⚠️ NEEDS SOURCE | TX Unlawful Production (2nd) — quarantine file had WA session law |
| `TMP-TN-LIKENESSVOICEA` | ⚠️ NEEDS SOURCE | TN ELVIS Act — quarantine file had TX SB 1188 (TX bill, may be legitimate TX AI law under different ID) |
| `TMP-TN-OFTENNESSEECSA` | ⚠️ NEEDS SOURCE | TN CSAM amendment — quarantine file had TX SB 815 (TX bill) |
| `TMP-TN-DECISIONTENNES` | ⚠️ NEEDS SOURCE | TN Information Protection Act — quarantine file had TX SB 2373 (TX bill, may be legitimate TX AI law) |
| `TMP-SD-ANACTTOPROHIBI` | ⚠️ NEEDS SOURCE | SD Deepfake law — quarantine file had TX SB 1621 (TX bill) |
| `TMP-SD-OFSOUTHDAKOTAS` | ⚠️ NEEDS SOURCE | SD CSAM amendment — quarantine file had TX SB 20 (TX bill) |
| `TMP-WA-AMENDMENTOFWAS` | ⚠️ NEEDS SOURCE | WA CSAM amendment (1) — quarantine file had WY homepage |
| `TMP-WA-OFWASHINGTONCS` | ⚠️ NEEDS SOURCE | WA CSAM amendment (2) — quarantine file had WY homepage |
| `TMP-NY-PRICINGNEWYORK` | ⚠️ NEEDS SOURCE | NY Algorithmic Pricing Disclosure — quarantine file had CT statute |

## Failed-fetch garbage (quarantined 2026-06-20)

These files contained no usable bill text — either empty bytes, a JavaScript-disabled error,
or a search-portal landing page.  Quarantine prevents them from re-entering the extraction
pipeline until correct text is supplied.

| canonical_law_id | Status | Quarantine reason |
|---|---|---|
| `TMP-NV-AIFORSCHOOLCOU` | ⚠️ NEEDS SOURCE | 8 bytes of form-feed chars — fetch returned empty |
| `TMP-NV-AIGENERALAICHA` | ⚠️ NEEDS SOURCE | 8 bytes of form-feed chars — fetch returned empty |
| `TMP-NV-FORMENTALANDBE` | ⚠️ NEEDS SOURCE | 8 bytes of form-feed chars — fetch returned empty |
| `TMP-IN-AMENDMENTOFIND` | ⚠️ NEEDS SOURCE | 71 bytes — "You need to enable JavaScript" JS-gated page |
| `TMP-TX-UNLAWFULDISTRI` | ⚠️ NEEDS SOURCE | 219 bytes — "Rocket NXT" search-portal landing page (no bill text) |
| `TMP-WY-EXPLOITATIONOF` | ⚠️ NEEDS SOURCE | 219 bytes — same Rocket NXT portal page as TX-UNLAWFULDISTRI |
| `TMP-WY-TOINTIMATEIMAG` | ⚠️ NEEDS SOURCE | 219 bytes — same Rocket NXT portal page as TX-UNLAWFULDISTRI |

## Mislabeled cross-jurisdiction content (quarantined 2026-06-20)

This file contained real bill text, but for the wrong jurisdiction/bill.

| canonical_law_id | Status | Quarantine reason |
|---|---|---|
| `TMP-TX-OFTEXASCSAMLAW` | ⚠️ NEEDS SOURCE | File contained **West Virginia SB 198** text (23 KB) — identical to TMP-WV-AGAINSTCHASTIT. The correct TX SB 198 "Amendment of Texas CSAM Laws" text was never fetched. Any extractions attributed to this TX law were derived from WV statute. |

## Same-bill duplicates requiring review (detected 2026-06-20)

These law pairs share byte-identical source text — the same bill was ingested under two
different TMP-IDs.  The files remain in law_texts/ but these pairs need an analyst to
confirm whether they represent genuinely distinct law entries or should be merged/deduplicated.

| TMP-ID (copy 1) | TMP-ID (copy 2) | Identified bill |
|---|---|---|
| `TMP-CO-PREVENTINGUNAU` | `TMP-CO-UNAUTHORIZEDSB` | Colorado SB 25-288 |
| `TMP-KY-AIELECTIONEERI` | `TMP-KY-GOVERNMENTUSEO` | Kentucky 25RS SB 4 |
| `TMP-KY-AMENDMENTTOINT` | `TMP-KY-TOCSAMLAWHB207` | Kentucky HB 207 |
| `TMP-LA-DEEPFAKELAW14L` | `TMP-LA-LOUISIANADEEPF` | Louisiana deepfake law |
| `TMP-NC-NORTHCAROLINAI` | `TMP-NC-OFNORTHCAROLIN` | North Carolina AI law |
| `TMP-ND-DAKOTAHARASSME` | `TMP-ND-DAKOTASTALKING` | North Dakota stalking/harassment |
| `TMP-PA-AMENDMENTOFPEN` | `TMP-PA-OFPENNSYLVANIA` | Pennsylvania amendment |
| `TMP-UT-AIARTIFICIALIN` | `TMP-UT-INTELLIGENCECO` | Utah AI law |
| `TMP-UT-APPLICATIONSRE` | `TMP-UT-ORUSERINPUTOFA` | Utah applications law |
| `TMP-UT-ARTIFICIALPORN` | `TMP-UT-PORNOGRAPHICIM` | Utah AI pornography law |
| `TMP-IL-AITHEWELLNESSA` | `TMP-IL-WELLNESSANDOVE` | Illinois wellness/oversight |
| `TMP-CA-EMPLOYMENTANDS` | `TMP-CA-EMPLOYMENTREGU` | California employment regulation |

## Note on TN laws with TX bill content
The quarantine files for TMP-TN-LIKENESSVOICEA (TX SB 1188) and TMP-TN-DECISIONTENNES (TX SB 2373)
contain what may be legitimate Texas AI legislation that is not already in the database under a TX ID.
Consider reviewing these TX bills and adding them as TX law entries if they are in scope.

