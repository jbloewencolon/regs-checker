# IAPP US State AI Governance Legislation Tracker 2026 — Extraction QA Report

## Summary
- **Total data rows extracted:** 65
- **Sections:** LAWS SIGNED (8 rows), ACTIVE BILLS (55 rows), INACTIVE BILLS (2 rows)
- **Source date:** Tracker last updated 3 March 2026
- **Extraction method:** pdfplumber geometric word extraction with x-coordinate column assignment

## Output Columns

| Column | Description |
|---|---|
| Section | LAWS SIGNED / ACTIVE BILLS / INACTIVE BILLS |
| Jurisdiction | State or territory |
| Statute/bill | Bill number or citation |
| Scope | Scope code (A/D/F/G + combinations; * = personal-data-trained AI systems) |
| Program and documentation | Obligation column: which org types (1/2/3) must comply |
| Assessments | Obligation column: which org types (1/2/3) must comply |
| Training | Obligation column: which org types (1/2/3) must comply |
| Responsible individual | Obligation column: which org types (1/2/3) must comply |
| General notice | Obligation column: which org types (1/2/3) must comply |
| Labeling/notification | Obligation column: which org types (1/2/3) must comply |
| Explanation/incident reporting | Obligation column: which org types (1/2/3) must comply |
| Developer documentation | Obligation column: which org types (1/2/3) must comply |
| Registration | Obligation column: which org types (1/2/3) must comply |
| Third-party review | Obligation column: which org types (1/2/3) must comply |
| Opt out/appeal | Obligation column: which org types (1/2/3) must comply |
| Nondiscrimination | Obligation column: which org types (1/2/3) must comply |

## Scope Code Key

| Code | Meaning |
|---|---|
| A | All covered AI systems (without reference to specific types) |
| F | Foundation / dual-use / frontier / general-purpose models |
| D | Automated decision-making or consequential-decision systems |
| G | Generative AI / synthetic-content systems only |
| * suffix | Provisions applicable only to AI trained on personal data |

## Obligation Value Key

| Value | Meaning |
|---|---|
| 1 | Deployer organizations |
| 2 | Developer organizations |
| 3 | Distributor organizations (including integrators and importers) |

## Layout Challenges and Corrections

### 1. Vertically centred merged jurisdiction cells

In the PDF, each state's jurisdiction label occupies a single merged cell that is vertically centred across all of that state's bill rows. pdfplumber returns the label text at its visual midpoint, which falls *after* the first one or more bill rows for that state. All bills appearing between the preceding state's label and the current state's label required post-extraction jurisdiction correction.

**Corrections applied:**

- Jur corrected: 'AB 2013': '' → 'California'
- Jur corrected: 'SB 149': 'New York' → 'Utah'
- Jur corrected: 'AB 1018': 'Arizona' → 'California'
- Jur corrected: 'SB 59': 'Florida' → 'Hawaii'
- Jur corrected: 'SB 2967': 'Florida' → 'Hawaii'
- Jur corrected: 'SB 1929': 'Hawaii' → 'Illinois'
- Jur corrected: 'SB 1792': 'Hawaii' → 'Illinois'
- Jur corrected: 'SB 2203': 'Hawaii' → 'Illinois'
- Jur corrected: 'SB 2995': 'Hawaii' → 'Illinois'
- Jur corrected: 'SB 3180': 'Hawaii' → 'Illinois'
- Jur corrected: 'SB 3263': 'Hawaii' → 'Illinois'
- Jur corrected: 'LB 1083': 'Minnesota' → 'Nebraska'
- Jur corrected: 'SB 245': 'Utah' → 'Virginia'
- Jur corrected: 'HB 340': 'Virginia' → 'Vermont'
- Jur corrected: 'HB 1168': 'Vermont' → 'Washington'
- Jur corrected: 'HB 1170': 'Vermont' → 'Washington'
- Jur corrected: 'SB 6120 / HB 2157': 'Vermont' → 'Washington'
- SKIP orphan: '2157'
- Jur corrected: 'HB 28': 'Washington' → 'New Mexico'

### 2. Split bill numbers (two-chamber bills wrapping across lines)

Five bills had their second-chamber number on a separate PDF line. Reconstructed:

| Fragments | Reconstructed full name | State |
|---|---|---|
| `SB 3261 / HB` + `4705` | SB 3261 / HB 4705 | Illinois |
| `AB 6540 / SB` + `6954` | AB 6540 / SB 6954 | New York |
| `AB 8884 / SB` + `1169` | AB 8884 / SB 1169 | New York |
| `SB 6120 / HB` + `2157` | SB 6120 / HB 2157 | Washington |
| `SB 6284 / HB` + `2667` | SB 6284 / HB 2667 | Washington |

### 3. Connecticut SB 5 scope normalisation

The scope cell contained two tokens (`F,` and `G`) due to column wrapping. Normalised to `F,G`.

### 4. Alabama SB 129 — Developer documentation value `2,1`

The PDF shows `2,1` (two tokens in slightly different x-positions). Preserved as-is per source.

### 5. Illinois SB 3261 scope line ordering

The scope+obligation line (`F,G 2 2 2 2 2`) appeared *before* the continuation number (`4705`) in the PDF layout. The state machine applied the scope to the pending record and the continuation number was appended afterwards.

### 6. Page 2 excluded

Page 2 contains only the legend/glossary. No data rows were present.
