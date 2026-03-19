"""Extract per-bill hyperlinks from the IAPP US State AI Governance Legislation Tracker PDF.

Downloads the IAPP tracker PDF, extracts all embedded hyperlinks with their
bill number context, then matches them against the policy_navigator_urls.csv
to populate the iapp_reference_url column in document_families.

Usage:
    python -m src.scripts.extract_iapp_links [--csv PATH] [--dry-run]
    python -m src.scripts.extract_iapp_links --pdf /path/to/local.pdf  # skip download
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import structlog

from src.core.config import settings

logger = structlog.get_logger()

IAPP_PDF_URL = (
    "https://assets.contentstack.io/v3/assets/bltd4dd5b2d705252bc/"
    "blt4031f52d14548052/us_state_ai_governance_legislation_tracker.pdf"
)


# ---------------------------------------------------------------------------
# PDF link extraction
# ---------------------------------------------------------------------------


def download_pdf(url: str, dest: str) -> str:
    """Download the IAPP tracker PDF to a local path."""
    import httpx

    print(f"Downloading IAPP tracker PDF from {url[:60]}...")
    resp = httpx.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        f.write(resp.content)
    print(f"  Saved: {len(resp.content):,} bytes → {dest}")
    return dest


def extract_links_from_pdf(pdf_path: str) -> list[dict]:
    """Extract all hyperlinks from the PDF with bill number and state context.

    Returns list of dicts with keys: url, bill_number, state_hint
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    links = []

    for page_num in range(doc.page_count):
        page = doc[page_num]

        for link in page.get_links():
            if link.get("kind") != 2:  # Only URI links
                continue

            uri = link["uri"]
            rect = fitz.Rect(link["from"])

            # Get the clickable text (bill number)
            link_text = (
                page.get_text("text", clip=rect).strip().replace("\n", " ")
            )

            # Get text to the left of the link for state identification
            prefix_rect = fitz.Rect(0, rect.y0 - 2, rect.x0, rect.y1 + 2)
            prefix_text = (
                page.get_text("text", clip=prefix_rect)
                .strip()
                .replace("\n", " ")
            )

            # Clean the bill number
            bill_number = _normalize_bill_number(link_text)

            # Try to extract state from prefix
            state_hint = _extract_state_from_prefix(prefix_text)

            if bill_number or uri:
                links.append({
                    "url": uri,
                    "bill_number": bill_number,
                    "link_text_raw": link_text,
                    "state_hint": state_hint,
                })

    doc.close()
    print(f"  Extracted {len(links)} hyperlinks from {page_num + 1} pages")
    return links


def _normalize_bill_number(text: str) -> str:
    """Normalize a bill number for matching.

    E.g. 'AB 2013 G' → 'AB2013', 'SB 205 D 1,2' → 'SB205'

    PDF link text often looks like:  "SB 205 D 1,2 1,2"
    where D=status code, 1,2=category numbers. We need to extract just
    the bill prefix + number.
    """
    text = text.strip()

    # Try to extract bill number pattern: prefix + number (with optional dash)
    # e.g. "AB 2013 G 2" → "AB 2013", "SB 24-205 D 1,2" → "SB 24-205"
    match = re.match(
        r"([A-Z]{1,3})\s*(\d[\d\-]*(?:[A-Z])?)"  # AB2013 or SB24-205 or AB6453B
        r"(?:\s*/\s*([A-Z]{1,3})\s*(\d[\d\-]*))?",  # optional /SB1962
        text,
    )
    if match:
        prefix1 = match.group(1)
        number1 = match.group(2)
        result = f"{prefix1}{number1}"
        if match.group(3) and match.group(4):
            result += f"/{match.group(3)}{match.group(4)}"
        return result

    return text


def _extract_state_from_prefix(text: str) -> str | None:
    """Extract state name from text preceding the link."""
    # Look for known state names at the end of the prefix
    states = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming",
    ]
    for state in states:
        if state.lower() in text.lower():
            return state.lower()
    return None


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------


def _csv_bill_key(bill_number: str) -> str:
    """Normalize a CSV bill number for matching."""
    # Remove spaces, lowercase
    return re.sub(r"\s+", "", bill_number).upper()


def _pdf_bill_key(bill_number: str) -> str:
    """Normalize a PDF-extracted bill number for matching."""
    return re.sub(r"\s+", "", bill_number).upper()


def match_links_to_csv(
    pdf_links: list[dict],
    csv_rows: list[dict],
) -> dict[int, str]:
    """Match PDF hyperlinks to CSV rows by bill number.

    Returns mapping of law_id → iapp_reference_url.
    """
    # Build lookup: normalized bill number → list of PDF links
    pdf_by_bill: dict[str, list[dict]] = {}
    for link in pdf_links:
        if link["bill_number"]:
            key = _pdf_bill_key(link["bill_number"])
            pdf_by_bill.setdefault(key, []).append(link)

    matched: dict[int, str] = {}
    unmatched_csv: list[dict] = []

    for row in csv_rows:
        law_id = row["law_id"]
        csv_bill = row.get("bill_number", "").strip()
        if not csv_bill:
            unmatched_csv.append(row)
            continue

        key = _csv_bill_key(csv_bill)

        # Direct match
        if key in pdf_by_bill:
            matched[int(law_id)] = pdf_by_bill[key][0]["url"]
            continue

        # Try partial: "SB 24-205" in CSV might match "SB205" in PDF
        # Strip leading year prefix from CSV bill number
        alt_key = re.sub(r"^([A-Z]{1,3})(\d{2})-", r"\1", key)
        if alt_key != key and alt_key in pdf_by_bill:
            matched[int(law_id)] = pdf_by_bill[alt_key][0]["url"]
            continue

        # Try with slash variants: "AB 768/SB 1962" — try each half
        if "/" in csv_bill:
            for part in csv_bill.split("/"):
                part_key = _csv_bill_key(part.strip())
                if part_key in pdf_by_bill:
                    matched[int(law_id)] = pdf_by_bill[part_key][0]["url"]
                    break
            else:
                unmatched_csv.append(row)
            continue

        unmatched_csv.append(row)

    return matched


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------


def load_csv(csv_path: str) -> list[dict]:
    """Load the policy_navigator_urls.csv."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            law_id = row.get("law_id", "").strip()
            if law_id:
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Database update
# ---------------------------------------------------------------------------


def update_iapp_urls(
    matches: dict[int, str],
    dry_run: bool = False,
) -> dict:
    """Update document_families.iapp_reference_url via bridge table resolution.

    Same bridge resolution as backfill_urls.py — law_id → doc_family_id.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    regs_engine = create_engine(settings.database_url)
    regs_session = sessionmaker(bind=regs_engine)()

    # Try to load bridge
    bridge: dict[int, int] = {}  # law_id → doc_family_id
    if settings.policy_navigator_url:
        try:
            pn_engine = create_engine(settings.policy_navigator_url)
            pn_session = sessionmaker(bind=pn_engine)()
            rows = pn_session.execute(
                text("SELECT system_a_doc_family_id, law_id FROM law_document_bridge")
            ).fetchall()
            bridge = {row[1]: row[0] for row in rows}
            pn_session.close()
            print(f"  Bridge loaded: {len(bridge)} mappings")
        except Exception as e:
            print(f"  Warning: Could not load bridge: {e}")

    updated = 0
    skipped_no_bridge = 0

    for law_id, iapp_url in matches.items():
        doc_family_id = bridge.get(law_id)

        if doc_family_id is None:
            # Fallback: try direct ID match (if law_id == doc_family_id)
            result = regs_session.execute(
                text("SELECT id FROM document_families WHERE id = :id"),
                {"id": law_id},
            ).scalar()
            if result:
                doc_family_id = result

        if doc_family_id is None:
            skipped_no_bridge += 1
            continue

        if dry_run:
            print(f"  [law_id={law_id}] → doc_family={doc_family_id}: {iapp_url[:70]}")
        else:
            regs_session.execute(
                text(
                    "UPDATE document_families SET iapp_reference_url = :url "
                    "WHERE id = :id"
                ),
                {"url": iapp_url, "id": doc_family_id},
            )
            updated += 1

    if not dry_run:
        regs_session.commit()

    regs_session.close()

    return {
        "matched": len(matches),
        "updated": updated,
        "skipped_no_bridge": skipped_no_bridge,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    csv_path: str,
    pdf_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Full pipeline: download PDF → extract links → match → update DB."""

    # Step 1: Get the PDF
    if pdf_path and Path(pdf_path).exists():
        print(f"Using local PDF: {pdf_path}")
    else:
        pdf_path = "/tmp/iapp_tracker.pdf"
        download_pdf(IAPP_PDF_URL, pdf_path)

    # Step 2: Extract links
    pdf_links = extract_links_from_pdf(pdf_path)

    # Step 3: Load CSV
    csv_rows = load_csv(csv_path)
    print(f"  CSV: {len(csv_rows)} rows")

    # Step 4: Match
    matches = match_links_to_csv(pdf_links, csv_rows)
    print(f"  Matched: {len(matches)}/{len(csv_rows)} CSV rows to IAPP links")

    # Step 5: Update DB
    if matches:
        print(f"\n{'DRY RUN — ' if dry_run else ''}Updating iapp_reference_url...")
        result = update_iapp_urls(matches, dry_run=dry_run)
    else:
        result = {"matched": 0, "updated": 0, "skipped_no_bridge": 0}

    # Report
    unmatched_count = len(csv_rows) - len(matches)
    print(
        f"\nIAPP link extraction complete:"
        f"\n  PDF links extracted:    {len(pdf_links)}"
        f"\n  CSV rows matched:       {len(matches)}"
        f"\n  CSV rows unmatched:     {unmatched_count}"
        f"\n  DB rows updated:        {result['updated']}"
        f"\n  Skipped (no bridge):    {result['skipped_no_bridge']}"
    )

    return {
        "pdf_links": len(pdf_links),
        **result,
        "unmatched": unmatched_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract per-bill links from IAPP tracker PDF"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="archive/policy_navigator_urls.csv",
        help="Path to the CSV file",
    )
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="Path to local IAPP PDF (skips download)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing to DB",
    )
    args = parser.parse_args()

    if not Path(args.csv).exists():
        print(f"Error: CSV not found: {args.csv}")
        sys.exit(1)

    run(csv_path=args.csv, pdf_path=args.pdf, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
