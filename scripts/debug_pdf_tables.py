"""Diagnostic: test word-level PDF extraction pipeline.

Usage: PYTHONPATH=. python scripts/debug_pdf_tables.py
"""
import sys
sys.path.insert(0, ".")

from pathlib import Path
from src.ingestion.pdf_tracker import (
    _extract_table_rows_from_pdf,
    _extract_urls_from_pdf,
    _parse_table_rows,
)

pdf_path = Path("static/Orrick-US-AI-Law-Tracker.pdf")

print("=== Extracting table rows (word-level) ===")
rows = _extract_table_rows_from_pdf(pdf_path)

if not rows:
    print("ERROR: No rows extracted!")
    exit(1)

print("Total rows: {}".format(len(rows)))
print()
print("First 15 rows:")
for i, row in enumerate(rows[:15]):
    cells = []
    for c in row:
        val = (c or "").replace("\n", "|")
        if len(val) > 35:
            val = val[:35] + "..."
        cells.append(val)
    print("  [{:3d}] {}".format(i + 1, cells))

print()
print("=== Extracting URLs ===")
all_urls = _extract_urls_from_pdf(pdf_path)
law_urls = [u for u in all_urls if "orrick.com" not in u and "mimecast" not in u]
print("Total URLs: {}, Law URLs: {}".format(len(all_urls), len(law_urls)))

print()
print("=== Parsing into records ===")
records = _parse_table_rows(rows, law_urls)
print("Total records: {}".format(len(records)))

if records:
    print()
    print("First 10 records:")
    for i, r in enumerate(records[:10]):
        print("  [{:2d}] {} | {:25s} | {:35s} | {}".format(
            i + 1, r["state_code"], r["ai_scope"][:25], r["law_name"][:35], r["effective_date"]
        ))
    if len(records) > 10:
        print()
        print("Last 3 records:")
        for r in records[-3:]:
            print("       {} | {:25s} | {:35s} | {}".format(
                r["state_code"], r["ai_scope"][:25], r["law_name"][:35], r["effective_date"]
            ))
else:
    print("ERROR: No records produced!")
