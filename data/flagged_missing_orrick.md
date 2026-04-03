# Laws Missing Orrick Data — Flagged for Review

**Generated**: 2026-04-03
**Total laws in corpus**: 244
**Laws with Orrick data (key_requirements or enforcement_penalties)**: 185
**Laws MISSING Orrick data (auto Tier D)**: 59

## Why This Matters
The Orrick gate in `src/core/confidence.py` forces Tier D for any extraction
without Orrick reference data. These 59 laws will produce only Tier D extractions
regardless of extraction quality.

## Action Required
Add `key_requirements_raw` and/or `enforcement_penalties` to these rows in
`data/fact_laws.csv`, then re-seed and re-extract those laws only.

## Flagged Laws

| law_id | jurisdiction | bill_number | title |
|--------|-------------|-------------|-------|
| 8 | Arkansas | AB 1018 | AB 1018 |
| 9 | Arkansas | SB 258 | SB 258 |
| 10 | Arkansas | SB 468 | SB 468 |
| 25 | California | (none) | Employment and System"): Systems Housing Act, Cal. Gov. Code |
| 41 | California | AB 2013 | AB 2013 |
| 42 | California | AB 412 | AB 412 |
| 43 | California | HB 94 | HB 94 |
| 44 | California | SB 11 | SB 11 |
| 45 | California | SB 420 | SB 420 |
| 46 | California | SB 53 | SB 53 |
| 47 | California | SB 942 | SB 942 |
| 61 | Connecticut | SB 2 | SB 2 |
| 68 | Florida | HB 369 | HB 369 |
| 135 | Nevada | AB 3265 | AB 3265 |
| 136 | Nevada | AB 3356 | AB 3356 |
| 137 | Nevada | AB 3411/SB 934 | AB 3411/SB 934 |
| 54 | Colorado | SB 149 | SB 149 |
| 55 | Colorado | SB 205 | SB 205 |
| 56 | Colorado | SB 318 | SB 318 |
| 76 | Idaho | HB 127 | HB 127 |
| 77 | Idaho | HB 3506 | HB 3506 |
| 78 | Idaho | SB 1929 | SB 1929 |
| 85 | Illinois | SB 1792 | SB 1792 |
| 86 | Illinois | SB 2203 | SB 2203 |
| 91 | Iowa | HB 406 | HB 406 |
| 92 | Iowa | HB 823 | HB 823 |
| 138 | Nevada | AB 768/SB 1962 | AB 768/SB 1962 |
| 73 | Hawaii | SB 59 | SB 59 |
| 107 | Maryland | HB 1331 | HB 1331 |
| 108 | Maryland | SB 936 | SB 936 |
| 110 | Massachusetts | HB 97 | HB 97 |
| 139 | Nevada | SB199 | SB199 |
| 117 | Minnesota | SF 1886 | SF 1886 |
| 127 | Montana | SB 452 | SB 452 |
| 130 | Nebraska | LB 642 | LB 642 |
| 143 | New Hampshire | (none) | New Hampshire AI Political HB 1596 |
| 149 | New Mexico | HB 401 | HB 401 |
| 162 | New York | (none) | Untitled Law |
| 178 | Oklahoma | HB 1916 | HB 1916 |
| 164 | New York | AB 6540/SB | AB 6540/SB |
| 165 | New York | AB 6578/SB | AB 6578/SB |
| 166 | New York | AB 8884/SB | AB 8884/SB |
| 167 | New York | HB 60 | HB 60 |
| 185 | Rhode Island | HB 1709 | HB 1709 |
| 186 | Rhode Island | HB 5496 | HB 5496 |
| 187 | Rhode Island | SB 627 | SB 627 |
| 207 | Texas | HB 149 | HB 149 |
| 208 | Texas | HB 340 | HB 340 |
| 209 | Texas | SB 2966 | SB 2966 |
| 210 | Texas | SB 668 | SB 668 |
| 221 | Utah | SB 226 | SB 226 |
| 224 | Vermont | HB 2121 | HB 2121 |
| 225 | Vermont | HB 2250 | HB 2250 |
| 226 | Vermont | HB 341 | HB 341 |
| 232 | Virginia | HB 1168 | HB 1168 |
| 233 | Virginia | HB 2094 | HB 2094 |
| 234 | Virginia | HB 2554 | HB 2554 |
| 237 | Washington | HB 1170 | HB 1170 |
| 218 | Utah | (none) | or user input of a Utah user (with narrow exception). of an  |
