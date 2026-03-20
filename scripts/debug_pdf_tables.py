"""Diagnostic: test the word-level column extraction pipeline.

Usage: python scripts/debug_pdf_tables.py
"""
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

print(f"Total rows: {len(rows)}\n")
print("First 20 rows:")
for i, row in enumerate(rows[:20]):
    cells = []
    for c in row:
        val = (c or "").replace("\n", "|")
        if len(val) > 40:
            val = val[:40] + "..."
        cells.append(val)
    print(f"  [{i+1:3d}] {cells}")

print(f"\n=== Extracting URLs ===")
all_urls = _extract_urls_from_pdf(pdf_path)
law_urls = [u for u in all_urls if "orrick.com" not in u and "mimecast" not in u]
print(f"Total URLs: {len(all_urls)}, Law URLs: {len(law_urls)}")

print(f"\n=== Parsing into records ===")
records = _parse_table_rows(rows, law_urls)
print(f"Total records: {len(records)}\n")

if records:
    print("First 10 records:")
    for i, r in enumerate(records[:10]):
        print(f"  [{i+1}] {r['state_code']} | {r['ai_scope'][:30]:30s} | {r['law_name'][:40]:40s} | {r['effective_date']}")
    print(f"\nLast 3 records:")
    for r in records[-3:]:
        print(f"       {r['state_code']} | {r['ai_scope'][:30]:30s} | {r['law_name'][:40]:40s} | {r['effective_date']}")
else:
    print("ERROR: No records produced!")
