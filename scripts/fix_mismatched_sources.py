"""Fix the two source-data quality issues identified in Phase 1 analysis.

Issue 1 — URL row-offset in law_fulltext_report.csv:
  20 .txt files in output/law_texts/ contain text from the WRONG law due
  to misaligned URL-to-canonical_law_id mapping in the fulltext report CSV.
  These are moved to output/law_texts_quarantine/ so re-ingest skips them.
  Correct source files must be obtained manually (see NEEDS_CORRECT_SOURCE
  entries created in output/law_texts_quarantine/NEEDED_SOURCES.md).

Issue 2 — Minnesota MCDPA omnibus bill:
  TMP-MN-DECISIONMINNES.txt contains the full HF4757 omnibus session law
  (9,349 lines) including cannabis regulatory articles 1-4.  The MCDPA
  (Minnesota Consumer Data Privacy Act) starts at Article 5 (line 7,849).
  This script replaces the file with the Article 5+ portion only.

Usage:
    python scripts/fix_mismatched_sources.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAW_TEXTS = ROOT / "output" / "law_texts"
QUARANTINE = ROOT / "output" / "law_texts_quarantine"

# ---------------------------------------------------------------------------
# Confirmed mismatches: canonical_law_id → what the file actually contains
# ---------------------------------------------------------------------------
MISMATCHED = {
    "TMP-TX-AITEXASRESPONS": "WV SB 198 (West Virginia CSAM bill)",
    "TMP-TX-ABUSEUSINGARTI": "TX HB 149 / TRAIGA (wrong TX bill — this is Financial Abuse law)",
    "TMP-TX-AISEXUALMATERI": "WA session law (Washington)",
    "TMP-TX-DECISIONTEXASD": "WY legislature homepage (no bill text)",
    "TMP-TX-MEDIAUNLAWFULP": "VT Act 161 (Vermont)",
    "TMP-TX-TOTHECSAMSTATU": "TX SB 441 (different TX bill)",
    "TMP-TX-UNLAWFULPRODUC": "WA session law (Washington)",
    "TMP-TN-LIKENESSVOICEA": "TX SB 1188 (Texas bill, not Tennessee ELVIS Act)",
    "TMP-TN-OFTENNESSEECSA": "TX SB 815 (Texas bill, not TN CSAM)",
    "TMP-TN-DECISIONTENNES": "TX SB 2373 (Texas bill, not TN Info Protection Act)",
    "TMP-SD-ANACTTOPROHIBI": "TX SB 1621 (Texas bill, not SD Deepfake law)",
    "TMP-SD-OFSOUTHDAKOTAS": "TX SB 20 (Texas bill, not SD CSAM)",
    "TMP-SC-ESTATEREALESTA": "TX SB 1964 (Texas bill, not SC Real Estate AI)",
    "TMP-RI-DECEPTIVEANDFR": "SC statute (South Carolina, not RI Deceptive Synthetic Media)",
    "TMP-WA-AMENDMENTOFWAS": "WY legislature homepage (no bill text)",
    "TMP-WA-OFWASHINGTONCS": "WY legislature homepage (no bill text)",
    "TMP-WV-AGAINSTCHASTIT": "WY legislature homepage (no bill text)",
    "TMP-WV-CRIMESAGAINSTC": "WY legislature homepage (no bill text)",
    "TMP-VT-AMENDMENTOFNON": "WY legislature homepage (no bill text)",
    "TMP-NY-PRICINGNEWYORK": "CT statute (Connecticut, not NY Algorithmic Pricing Disclosure)",
}

# Known correct source URLs where we have them — FILL IN as obtained
CORRECT_URLS = {
    "TMP-TX-AITEXASRESPONS": "https://capitol.texas.gov/tlodocs/89R/billtext/pdf/HB04900F.pdf",  # TX TRAIGA
    "TMP-TN-LIKENESSVOICEA": "https://advance.lexis.com/api/permalink/...",  # ELVIS Act — verify
    # Add others as known
}

# ---------------------------------------------------------------------------
# MN MCDPA omnibus — Article 5 starts at this 1-based line number
# (grep 'ARTICLE 5' output/law_texts/TMP-MN-DECISIONMINNES.txt to verify)
# ---------------------------------------------------------------------------
MN_MCDPA_ID = "TMP-MN-DECISIONMINNES"
MN_ARTICLE5_START_LINE = 7849  # 1-based; "ARTICLE 5  CONSUMER DATA POLICY"


def quarantine_mismatched(dry_run: bool) -> None:
    QUARANTINE.mkdir(parents=True, exist_ok=True)

    needed_md_lines = [
        "# Laws Needing Correct Source Files\n",
        "These laws had wrong source text (URL row-offset in law_fulltext_report.csv).\n",
        "Obtain correct bill text and place in output/law_texts/<canonical_law_id>.txt\n\n",
        "| canonical_law_id | Actual content ingested | Correct URL (if known) |\n",
        "|---|---|---|\n",
    ]

    moved = 0
    for cid, wrong_content in MISMATCHED.items():
        src = LAW_TEXTS / f"{cid}.txt"
        dst = QUARANTINE / f"{cid}.txt"
        correct_url = CORRECT_URLS.get(cid, "**UNKNOWN — needs research**")
        needed_md_lines.append(f"| `{cid}` | {wrong_content} | {correct_url} |\n")

        if not src.exists():
            print(f"  SKIP (already missing): {cid}.txt")
            continue

        if dry_run:
            print(f"  DRY RUN would move: {src.name} → quarantine/")
        else:
            shutil.move(str(src), str(dst))
            print(f"  Moved: {src.name} → output/law_texts_quarantine/")
            moved += 1

    if not dry_run:
        needed_md = QUARANTINE / "NEEDED_SOURCES.md"
        needed_md.write_text("".join(needed_md_lines), encoding="utf-8")
        print(f"\nWrote {needed_md}")
        print(f"Quarantined {moved} files. They will be skipped on re-ingest.")
    else:
        print(f"\nDRY RUN: would quarantine {sum(1 for c in MISMATCHED if (LAW_TEXTS / f'{c}.txt').exists())} files")


def fix_mn_mcdpa(dry_run: bool) -> None:
    src = LAW_TEXTS / f"{MN_MCDPA_ID}.txt"
    if not src.exists():
        print(f"  MN MCDPA file not found: {src}")
        return

    lines = src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    total_lines = len(lines)

    # Find the actual Article 5 line (in case line numbers shifted)
    article5_line = None
    for i, line in enumerate(lines):
        if "ARTICLE 5" in line and i > 7000:
            article5_line = i
            break

    if article5_line is None:
        article5_line = MN_ARTICLE5_START_LINE - 1  # fallback to known position

    mcdpa_lines = lines[article5_line:]
    print(f"  MN omnibus: {total_lines} total lines")
    print(f"  ARTICLE 5 found at line {article5_line + 1}")
    print(f"  MCDPA portion: {len(mcdpa_lines)} lines ({len(mcdpa_lines)/total_lines*100:.0f}% of bill)")

    if dry_run:
        print(f"  DRY RUN: would replace {src.name} with Article 5+ only")
        print(f"  First 3 lines of Article 5:")
        for line in mcdpa_lines[:3]:
            print(f"    {line.rstrip()}")
    else:
        # Back up the original
        backup = QUARANTINE / f"{MN_MCDPA_ID}.full_omnibus.txt"
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(backup))
        print(f"  Backed up full omnibus → output/law_texts_quarantine/{backup.name}")

        src.write_text("".join(mcdpa_lines), encoding="utf-8")
        print(f"  Replaced {src.name} with MCDPA article only ({len(mcdpa_lines)} lines)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix URL-mismatched source files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    args = parser.parse_args()

    print(f"=== Fix Mismatched Source Files {'(DRY RUN)' if args.dry_run else ''} ===\n")

    print("Step 1: Quarantine 20 wrong-URL law text files")
    quarantine_mismatched(args.dry_run)

    print("\nStep 2: Trim MN MCDPA omnibus to Article 5 only")
    fix_mn_mcdpa(args.dry_run)

    print("\nDone.")
    if not args.dry_run:
        print("\nNext steps:")
        print("  1. Review output/law_texts_quarantine/NEEDED_SOURCES.md")
        print("  2. Obtain correct .txt source files for the 20 quarantined laws")
        print("  3. Place correct files in output/law_texts/<canonical_law_id>.txt")
        print("  4. Run Phase 6: Full reset + re-seed + ingest")


if __name__ == "__main__":
    main()
