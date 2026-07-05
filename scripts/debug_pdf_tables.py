"""Diagnostic: test PDF extraction pipeline for Orrick and IAPP PDFs.

Usage: PYTHONPATH=. python scripts/debug_pdf_tables.py [orrick|iapp]
       Defaults to testing whichever PDF exists.
"""
import sys
sys.path.insert(0, ".")

from pathlib import Path


def test_orrick():
    from src.ingestion.pdf_tracker import (
        _extract_table_rows_from_pdf,
        _extract_urls_from_pdf,
        _parse_table_rows,
    )
    pdf_path = Path("data/trackers/Orrick-US-AI-Law-Tracker.pdf")
    if not pdf_path.exists():
        print("Orrick PDF not found at {}".format(pdf_path))
        return

    print("=== ORRICK: Extracting table rows ===")
    rows = _extract_table_rows_from_pdf(pdf_path)
    if not rows:
        print("ERROR: No rows extracted!")
        return

    print("Total rows: {}".format(len(rows)))
    urls = _extract_urls_from_pdf(pdf_path)
    law_urls = [u for u in urls if "orrick.com" not in u and "mimecast" not in u]
    records = _parse_table_rows(rows, law_urls)
    print("Total records: {}\n".format(len(records)))

    for i, r in enumerate(records[:5]):
        print("  [{:2d}] {} | {:25s} | {:35s} | {}".format(
            i + 1, r["state_code"], r["ai_scope"][:25], r["law_name"][:35], r["effective_date"]))


def test_iapp():
    from src.ingestion.iapp_pdf_tracker import parse_iapp_pdf
    pdf_path = Path("data/trackers/IAPP_Legislation_tracker.pdf")
    if not pdf_path.exists():
        print("IAPP PDF not found at {}".format(pdf_path))
        return

    print("=== IAPP: Parsing PDF ===")
    records = parse_iapp_pdf(pdf_path)
    print("Total records: {}\n".format(len(records)))

    if records:
        print("First 10 records:")
        for i, r in enumerate(records[:10]):
            print("  [{:2d}] {} | {:15s} | {:30s} | {:12s} | {}".format(
                i + 1, r["state_code"],
                r["bill_number"][:15],
                r["bill_title"][:30],
                r["normalized_status"],
                r["effective_date"][:20] if r["effective_date"] else ""))

        print("\nLast 3 records:")
        for r in records[-3:]:
            print("       {} | {:15s} | {:30s} | {:12s} | {}".format(
                r["state_code"],
                r["bill_number"][:15],
                r["bill_title"][:30],
                r["normalized_status"],
                r["effective_date"][:20] if r["effective_date"] else ""))

        # Stats
        statuses = {}
        states = set()
        for r in records:
            statuses[r["normalized_status"]] = statuses.get(r["normalized_status"], 0) + 1
            states.add(r["state_code"])
        print("\nStates: {}".format(len(states)))
        print("Status distribution:")
        for s, c in sorted(statuses.items(), key=lambda x: -x[1]):
            print("  {:20s} {}".format(s, c))
    else:
        print("ERROR: No records produced!")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target == "orrick":
        test_orrick()
    elif target == "iapp":
        test_iapp()
    else:
        # Test both if they exist
        orrick_exists = Path("data/trackers/Orrick-US-AI-Law-Tracker.pdf").exists()
        iapp_exists = Path("data/trackers/IAPP_Legislation_tracker.pdf").exists()

        if orrick_exists:
            test_orrick()
            print()
        if iapp_exists:
            test_iapp()
        if not orrick_exists and not iapp_exists:
            print("No PDFs found in static/ directory.")
