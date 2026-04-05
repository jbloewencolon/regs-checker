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

## Note on TN laws with TX bill content
The quarantine files for TMP-TN-LIKENESSVOICEA (TX SB 1188) and TMP-TN-DECISIONTENNES (TX SB 2373)
contain what may be legitimate Texas AI legislation that is not already in the database under a TX ID.
Consider reviewing these TX bills and adding them as TX law entries if they are in scope.
