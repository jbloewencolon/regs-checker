# QA Report: Orrick U.S. AI Law Tracker CSV Extraction
**Source:** Orrick-US-AI-Law-Tracker.pdf  
**Last Updated (per PDF):** March 05, 2026  
**Extraction Date:** March 20, 2026

---

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total pages reviewed | 27 of 27 |
| Total data rows extracted | 190 |
| CSV file total lines (incl. header) | 191 |
| States/territories represented | 41 |
| Pages with law tracker data | Pages 1–27 |

---

## Pages Reviewed

All 27 pages were reviewed in sequential order. Every page contains law tracker table rows. No pages were skipped.

---

## Row Count by State/Territory

| State/Territory | Rows |
|----------------|------|
| Alabama | 2 |
| Arizona | 3 |
| Arkansas | 2 |
| California | 27 |
| Colorado | 5 |
| Connecticut | 4 |
| Delaware | 3 |
| Florida | 3 |
| Georgia | 2 |
| Hawaii | 2 |
| Idaho | 2 |
| Illinois | 5 |
| Indiana | 2 |
| Iowa | 2 |
| Kansas | 1 |
| Kentucky | 4 |
| Louisiana | 3 |
| Maine | 1 |
| Maryland | 4 |
| Massachusetts | 1 |
| Michigan | 2 |
| Minnesota | 4 |
| Mississippi | 1 |
| Missouri | 1 |
| Montana | 6 |
| Nebraska | 2 |
| Nevada | 4 |
| New Hampshire | 5 |
| New Jersey | 3 |
| New Mexico | 1 |
| New York | 13 |
| North Carolina | 2 |
| North Dakota | 5 |
| Oklahoma | 2 |
| Oregon | 2 |
| Pennsylvania | 2 |
| Rhode Island | 2 |
| South Carolina | 1 |
| South Dakota | 2 |
| Tennessee | 3 |
| Texas | 13 |
| Utah | 11 |
| Vermont | 2 |
| Virginia | 5 |
| Washington | 2 |
| West Virginia | 2 |
| Wisconsin | 3 |
| Wyoming | 2 |
| **TOTAL** | **190** |

---

## Uncertain or Partially Unreadable Cells

1. **California — AI Healthcare (row 15):** The "Law Link" field in the source PDF combines two citation references: `Cal. Gov. Code § Section 1339.75` and `AB 3030`. Both have been preserved as-is since the PDF presents them together in a single cell.

2. **Colorado — Colorado Privacy Act "Law Link" (row 38):** The PDF includes a note "Reprinted from Westlaw with the permission of Thomson Reuters" embedded in or near the citation `Col. Rev. Stat. § 6-1-1301 et seq.` This footnote-style notice was omitted from the Law Link field as it is a reproduction attribution, not part of the statutory citation. The same note appears for several Colorado and other Westlaw-cited statutes (Colorado AI Act, Colorado Privacy Act, Colorado Candidate Election Deepfake Disclosures Law). Rows affected: Colorado rows 2, 3, 5, 6.

3. **Maryland — AI Healthcare "Law Link" (row 82):** The citation `Md. Code Ann., Ins. §§ 15-10A-06, 15-10B05.1` may contain a minor OCR artifact (`15-10B05.1` vs. `15-10B-05.1`). Preserved exactly as shown in the PDF.

4. **Utah — AI Healthcare "Law Link" (row 171):** The source PDF shows `Utah Code § 13-72a-101` (lowercase "a"). This appears intentional based on the statutory numbering scheme and has been preserved as shown.

5. **Wyoming — Second row "AI Scope" field:** The PDF's AI Scope column for the second Wyoming entry (Amendment to Intimate Image Law, Wy. Code § 6-4-306) shows "AI CSAM" despite the law name referencing intimate images. This is exactly as printed in the PDF and has been preserved without correction.

6. **Virginia — "Law Link" for AI Intimate Images (row 177):** The source PDF shows `B 2678` as the law link, which appears to be a bill number without a house prefix (e.g., "HB" or "SB"). Preserved as shown.

---

## Rows Requiring Reconstruction Across Line Wraps or Page Breaks

The following rows span page breaks in the PDF and were reconstructed:

| Row Description | Pages Spanned |
|----------------|---------------|
| California — Automated Decision-Making (CCPA Regulations) | Pages 6–7 |
| Colorado — Colorado AI Act | Pages 7–8 (table row continues) |
| Minnesota — Automated Decision-Making (MN Consumer Data Privacy Act) | Pages 13–14 |
| Texas — AI in Government (Chapter 2054) | Pages 23–24 |
| New York — RAISE Act | Pages 19–20 |
| Utah — User-Facing AI (AI Consumer Protection Amendments) | Pages 24–25 |

All multi-line cells within pages were collapsed into single CSV cells via standard line-wrap merging.

---

## Assumptions Made

1. **Duplicate law names across scopes:** Several laws appear as separate rows because the PDF assigns them to different AI Scope categories (e.g., California AI Transparency Act appears twice — once under "AI Transparency" and once under "AI in Social Media & Online Platforms"; HB 441 in Texas appears twice under "AI in Social Media & Online Platforms" and "AI Intimate Images"). These have been preserved as separate rows, consistent with the source table structure.

2. **"Reprinted from Westlaw" attribution:** For Colorado and other states where the Law Link column contains both a statutory citation and a Westlaw attribution note, only the statutory citation was retained in the Law Link column. The attribution is a publication note, not part of the law reference.

3. **Whitespace normalization:** Internal bullet characters (•) from the Key Requirements column have been converted to sentence-level text joined with spaces, consistent with standard multi-line cell merging. The substantive content is unchanged.

4. **Italicized/footnote qualifiers:** Footnote-style qualifiers such as "Other obligations and restrictions may apply depending on the type of data processed." that appear at the bottom of certain Key Requirements cells have been included in those cells, as they are clearly part of the source row's content.

5. **Law Link values:** The PDF presents Law Link as hyperlinked text labels (e.g., "HB 168", "SB 1103") rather than full URLs. These short-form bill numbers and code citations have been extracted as-is, since the underlying hyperlink targets were not extractable from the PDF context provided.

---

## Second-Pass Verification Notes

- All 27 pages re-scanned for skipped rows: **none found**.
- All column alignments verified: no dates or law links found shifted into adjacent columns.
- Rows near page breaks double-checked: all reconstructed correctly.
- Total source rows counted visually across pages: **190**, matching CSV output.
- No duplicate rows introduced.
