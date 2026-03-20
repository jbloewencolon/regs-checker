"""IAPP US State AI Legislation Tracker PDF parser.

Replaces the web scraper (iapp_scraper.py) with a local PDF-based approach.
The IAPP tracker PDF should be placed at: static/IAPP_Legislation_tracker.pdf

Uses the same word-level pdfplumber extraction approach as pdf_tracker.py:
1. Detects column boundaries from the header row
2. Extracts words with bounding boxes and assigns them to columns
3. Merges continuation rows (multi-line entries)
4. Returns structured records matching the iapp_scraper output format

The output records have the same keys as iapp_scraper.scrape_tracker():
    state, state_code, bill_number, bill_title, status, normalized_status,
    ai_topic, last_action, effective_date, bill_url
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger()

IAPP_PDF_PATH = Path("static/IAPP_Legislation_tracker.pdf")

# State name to code
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Puerto Rico": "PR",
}

# Import status normalization from the existing IAPP scraper
from src.ingestion.iapp_scraper import _normalize_status, _resolve_state_code


class IAPPPDFParseError(Exception):
    """Error during IAPP PDF parsing."""


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

_NOISE_PATTERNS = re.compile(
    r"^(Page \d+ of \d+|Last updated .*|IAPP .*Tracker|"
    r"U\.S\. State AI|This tracker|For more).*$"
)


def _is_noise_word(text: str) -> bool:
    """Check if a word/phrase is page header/footer noise."""
    return bool(_NOISE_PATTERNS.match(text.strip()))


def _clean_cell(text: str) -> str:
    """Remove page footer fragments from merged cell text."""
    text = re.sub(r"\s*Page \d+ of \d+\s*", " ", text)
    text = re.sub(r"\s*Last updated [A-Za-z]+ \d{1,2},? \d{4}\s*", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Column boundary detection (same approach as pdf_tracker.py)
# ---------------------------------------------------------------------------

# Possible header keywords for the IAPP tracker
_IAPP_HEADER_ANCHORS = ["State", "Bill", "Status", "Title", "Topic", "Subject",
                         "Action", "Effective", "Date", "Scope"]


def _find_column_boundaries(first_page) -> list[float] | None:
    """Detect column x-boundaries from the header row on page 1.

    Searches for a row containing 'State' and 'Bill' (or similar header words)
    and uses word x-positions from that row to define column boundaries.
    """
    words = first_page.extract_words(keep_blank_chars=True, extra_attrs=["top", "bottom"])
    if not words:
        return None

    # Find a header anchor word — look for "State" first
    header_word = None
    for w in words:
        text = w["text"].strip()
        if text in ("State", "State/Territory", "State/Terr", "Jurisdiction"):
            header_word = w
            break

    if not header_word:
        # Try "Bill" as fallback
        for w in words:
            if w["text"].strip().startswith("Bill"):
                header_word = w
                break

    if not header_word:
        return None

    # Get all words on the same row (within 3pt vertically)
    header_y = header_word["top"]
    header_line = sorted(
        [w for w in words if abs(w["top"] - header_y) < 3],
        key=lambda w: w["x0"],
    )

    col_starts = [w["x0"] for w in header_line]
    if len(col_starts) < 3:
        return None

    logger.debug(
        "iapp_pdf_header_columns_found",
        count=len(col_starts),
        labels=[w["text"] for w in header_line],
        x_positions=col_starts,
    )

    return col_starts + [first_page.width]


# ---------------------------------------------------------------------------
# Word-level row extraction
# ---------------------------------------------------------------------------

def _extract_rows_from_page(page, col_boundaries: list[float]) -> list[list[str]]:
    """Extract structured rows from a page using known column boundaries."""
    words = page.extract_words(keep_blank_chars=True, extra_attrs=["top", "bottom"])
    if not words:
        return []

    # Filter noise
    words = [w for w in words if not _is_noise_word(w["text"])]

    # Group words into rows by y-position
    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))

    row_groups: list[list[dict]] = []
    current_row: list[dict] = []
    current_top = -999.0

    for w in words_sorted:
        if abs(w["top"] - current_top) > 3:
            if current_row:
                row_groups.append(current_row)
            current_row = [w]
            current_top = w["top"]
        else:
            current_row.append(w)

    if current_row:
        row_groups.append(current_row)

    # Convert word groups into column-based rows
    num_cols = len(col_boundaries) - 1
    result_rows: list[list[str]] = []

    for word_group in row_groups:
        cells = [""] * num_cols
        for w in sorted(word_group, key=lambda w: w["x0"]):
            col_idx = _word_to_column(w["x0"], col_boundaries)
            if col_idx < num_cols:
                if cells[col_idx]:
                    cells[col_idx] += " " + w["text"]
                else:
                    cells[col_idx] = w["text"]

        if any(c.strip() for c in cells):
            result_rows.append(cells)

    # Merge continuation rows
    merged = _merge_continuation_rows(result_rows, num_cols)
    return merged


def _word_to_column(x0: float, boundaries: list[float]) -> int:
    """Determine which column a word belongs to based on its x position."""
    for i in range(len(boundaries) - 1):
        if x0 < boundaries[i + 1]:
            return i
    return len(boundaries) - 2


def _merge_continuation_rows(rows: list[list[str]], num_cols: int) -> list[list[str]]:
    """Merge multi-line entries that span several PDF rows.

    A continuation row has empty first column(s) — append content to
    the previous row.
    """
    if not rows:
        return rows

    merged: list[list[str]] = []
    for row in rows:
        first_cell = row[0].strip() if row else ""

        # If first cell has a state name, it's a new entry
        has_state = bool(first_cell) and bool(_match_state_name(first_cell))

        if has_state:
            merged.append(list(row))
        elif merged:
            # Continuation: append to previous row
            for col_idx in range(num_cols):
                cell_text = row[col_idx].strip() if col_idx < len(row) else ""
                if cell_text:
                    if merged[-1][col_idx]:
                        merged[-1][col_idx] += " " + cell_text
                    else:
                        merged[-1][col_idx] = cell_text

    return merged


def _match_state_name(text: str) -> str:
    """Match text against known state names."""
    text = text.strip()
    if not text:
        return ""
    if text in STATE_CODES:
        return text
    first_line = text.split("\n")[0].strip()
    if first_line in STATE_CODES:
        return first_line
    for state in sorted(STATE_CODES.keys(), key=len, reverse=True):
        if text.startswith(state):
            return state
    return ""


# ---------------------------------------------------------------------------
# Column mapping detection
# ---------------------------------------------------------------------------

def _detect_column_mapping(rows: list[list[str]]) -> dict[str, int]:
    """Detect which column index corresponds to which field.

    Looks at header rows to map: state, bill_number, bill_title, status,
    ai_topic, last_action, effective_date.
    """
    mapping: dict[str, int] = {}

    for row in rows[:5]:
        for idx, cell in enumerate(row):
            cl = (cell or "").strip().lower()
            if not cl:
                continue
            if "state" in cl and "state" not in mapping:
                mapping["state"] = idx
            elif ("bill" in cl and "number" in cl) or cl == "bill #" or cl == "bill no":
                mapping["bill_number"] = idx
            elif "bill" in cl and "title" in cl:
                mapping["bill_title"] = idx
            elif cl == "bill" and "bill_number" not in mapping:
                mapping["bill_number"] = idx
            elif "status" in cl and "status" not in mapping:
                mapping["status"] = idx
            elif "topic" in cl or "subject" in cl or "scope" in cl:
                mapping["ai_topic"] = idx
            elif "action" in cl or "last" in cl:
                mapping["last_action"] = idx
            elif "effective" in cl or "date" in cl:
                mapping["effective_date"] = idx
            elif "title" in cl and "bill_title" not in mapping:
                mapping["bill_title"] = idx

    # If no header detected, use positional defaults
    # Common IAPP layout: State | Bill # | Bill Title | Status | AI Topic | Last Action | Effective Date
    if "state" not in mapping:
        mapping.setdefault("state", 0)
        mapping.setdefault("bill_number", 1)
        mapping.setdefault("bill_title", 2)
        mapping.setdefault("status", 3)
        if len(rows) > 0 and len(rows[0]) > 4:
            mapping.setdefault("ai_topic", 4)
        if len(rows) > 0 and len(rows[0]) > 5:
            mapping.setdefault("last_action", 5)
        if len(rows) > 0 and len(rows[0]) > 6:
            mapping.setdefault("effective_date", 6)

    return mapping


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_iapp_pdf(pdf_path: Path = IAPP_PDF_PATH) -> list[dict]:
    """Parse the IAPP AI Legislation Tracker PDF into structured records.

    Returns a list of dicts with keys matching iapp_scraper.scrape_tracker():
        state, state_code, bill_number, bill_title, status, normalized_status,
        ai_topic, last_action, effective_date, bill_url
    """
    if not pdf_path.exists():
        raise IAPPPDFParseError(f"IAPP PDF not found at {pdf_path}")

    logger.info("parsing_iapp_pdf", path=str(pdf_path))

    try:
        import pdfplumber
    except ImportError:
        raise IAPPPDFParseError("pdfplumber is required: pip install pdfplumber")

    # Extract URLs for bill links
    urls = _extract_urls_from_pdf(pdf_path)
    bill_urls = [u for u in urls if "iapp.org" not in u]
    logger.info("iapp_pdf_urls_extracted", total=len(urls), bill_urls=len(bill_urls))

    # Extract table rows using word-level positioning
    with pdfplumber.open(pdf_path) as pdf:
        col_boundaries = _find_column_boundaries(pdf.pages[0])
        if not col_boundaries:
            raise IAPPPDFParseError("Could not detect column boundaries in IAPP PDF")

        all_rows: list[list[str]] = []
        for page in pdf.pages:
            rows = _extract_rows_from_page(page, col_boundaries)
            all_rows.extend(rows)

    logger.info("iapp_pdf_rows_extracted", rows=len(all_rows))

    if not all_rows:
        raise IAPPPDFParseError("No table rows found in IAPP PDF")

    # Detect column mapping from headers
    col_map = _detect_column_mapping(all_rows)
    logger.info("iapp_pdf_column_mapping", mapping=col_map)

    # Parse rows into records
    records = []
    url_index = 0
    current_state = ""

    for row in all_rows:
        if len(row) < 3:
            continue

        state_cell = _clean_cell(row[col_map.get("state", 0)] if col_map.get("state", 0) < len(row) else "")

        # Skip header rows
        if state_cell.lower() in ("state", "state/territory", "jurisdiction"):
            continue

        # Track state (carries forward for continuation entries)
        if state_cell:
            matched = _match_state_name(state_cell)
            if matched:
                current_state = matched

        if not current_state:
            continue

        state_code = _resolve_state_code(current_state)
        if not state_code:
            continue

        def _get(key: str) -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(row):
                return _clean_cell(row[idx])
            return ""

        bill_number = _get("bill_number")
        bill_title = _get("bill_title")
        status = _get("status")
        ai_topic = _get("ai_topic")
        last_action = _get("last_action")
        effective_date = _get("effective_date")

        # Skip rows with no meaningful content
        if not bill_number and not bill_title and not status:
            continue

        # Match URL
        bill_url = ""
        if url_index < len(bill_urls):
            bill_url = bill_urls[url_index]
            url_index += 1

        records.append({
            "state": current_state,
            "state_code": state_code,
            "bill_number": bill_number,
            "bill_title": bill_title or bill_number,
            "status": status,
            "normalized_status": _normalize_status(status),
            "ai_topic": ai_topic,
            "last_action": last_action,
            "effective_date": effective_date,
            "bill_url": bill_url,
        })

    logger.info("iapp_pdf_parsed", total_records=len(records))
    return records


def _extract_urls_from_pdf(pdf_path: Path) -> list[str]:
    """Extract hyperlinks from the PDF using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        return []

    urls: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                if hasattr(page, "hyperlinks"):
                    for link in page.hyperlinks:
                        uri = link.get("uri", "")
                        if uri.startswith("http"):
                            urls.append(uri)
                else:
                    annots = page.page.get("/Annots")
                    if annots:
                        annots = annots.resolve() if hasattr(annots, "resolve") else annots
                        for annot in annots:
                            a = annot.resolve() if hasattr(annot, "resolve") else annot
                            action = a.get("/A")
                            if action:
                                action = action.resolve() if hasattr(action, "resolve") else action
                                uri = action.get("/URI")
                                if uri and str(uri).startswith("http"):
                                    urls.append(str(uri))
    except Exception as e:
        logger.warning("iapp_pdf_url_extraction_failed", error=str(e)[:200])

    return urls
