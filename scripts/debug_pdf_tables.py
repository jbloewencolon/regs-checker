"""Diagnostic: dump word positions from page 1 of the PDF.

Usage: PYTHONPATH=. python scripts/debug_pdf_tables.py
"""
import pdfplumber
from pathlib import Path

pdf_path = Path("static/Orrick-US-AI-Law-Tracker.pdf")

with pdfplumber.open(pdf_path) as pdf:
    p = pdf.pages[0]
    print("Page size: {} x {}".format(p.width, p.height))
    words = p.extract_words(keep_blank_chars=True, extra_attrs=["top", "bottom"])
    print("Total words on page 1: {}".format(len(words)))
    print()

    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))

    print("First 50 words (sorted by top, then x0):")
    for w in sorted_words[:50]:
        print("  top={:7.1f}  x0={:7.1f}  x1={:7.1f}  text={!r}".format(
            w["top"], w["x0"], w["x1"], w["text"]
        ))

    # Show unique top values to understand row structure
    tops = sorted(set(round(w["top"], 1) for w in words))
    print("\nUnique top positions (first 20): {}".format(tops[:20]))
