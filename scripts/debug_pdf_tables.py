"""Diagnostic: dump PDF table structure to understand extraction issues.

Usage: python scripts/debug_pdf_tables.py
"""
import pdfplumber
from pathlib import Path

pdf_path = Path("static/Orrick-US-AI-Law-Tracker.pdf")

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total pages: {len(pdf.pages)}\n")

    row_num = 0
    for pi, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        print(f"=== Page {pi + 1}: {len(tables)} table(s) ===")

        for ti, table in enumerate(tables):
            print(f"  Table {ti}: {len(table)} rows, {len(table[0]) if table else 0} cols")
            for ri, row in enumerate(table):
                row_num += 1
                # Truncate each cell for display
                cells = []
                for c in row:
                    val = (c or "").replace("\n", "\\n")
                    if len(val) > 50:
                        val = val[:50] + "..."
                    cells.append(val)
                print(f"    [{row_num:3d}] {cells}")

        if not tables:
            # Show raw text for pages without detected tables
            text = page.extract_text() or ""
            lines = text.split("\n")[:5]
            print(f"  (no tables) First lines: {lines}")

    print(f"\nTotal rows across all tables: {row_num}")
