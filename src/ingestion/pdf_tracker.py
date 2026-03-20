"""Orrick PDF parser and law seeder — replaces the web scraper.

Parses the Orrick "U.S. AI Law Tracker" PDF (downloaded manually since
Orrick blocks bot scraping) to extract structured law records.

The PDF is a table with columns:
  State/Terr | AI Scope | Relevant Law | Law Link | Effective Date |
  Key Requirements | Enforcements Penalties

This module:
1. Extracts text and hyperlinks from the PDF using pdftohtml
2. Parses the tabular structure to produce one record per law
3. Uses pdfplumber as a fallback for text extraction
4. Creates Source → DocumentFamily → DocumentVersion → IngestionJob records

The PDF should be placed at: static/Orrick-US-AI-Law-Tracker.pdf
"""

from __future__ import annotations

import html as html_mod
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

import structlog

from src.core.circuit_breaker import CircuitBreakerTripped, FailureTracker
from src.db.models import (
    DocumentFamily,
    DocumentVersion,
    IngestionJob,
    IngestionStatus,
    LegalEvent,
    LegalEventType,
    Source,
    TemporalStatus,
)

logger = structlog.get_logger()

PDF_PATH = Path("static/Orrick-US-AI-Law-Tracker.pdf")

# Map state names to two-letter codes
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


class PDFParseError(Exception):
    """Error during PDF parsing."""
    pass


def _parse_effective_date(date_str: str) -> date | None:
    """Parse effective date from various formats found in the tracker."""
    if not date_str:
        return None
    date_str = date_str.strip()
    if date_str.lower() in ("tbd", "n/a", "pending", "varies", ""):
        return None

    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                "%B %d,%Y", "%b. %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # Try "Month DD, YYYY" with extra whitespace
    cleaned = re.sub(r"\s+", " ", date_str).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    # Try to find a date-like pattern in the string
    match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", date_str)
    if match:
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                continue

    # Try "Month DD, YYYY" embedded in text
    match = re.search(r"(\w+ \d{1,2},?\s*\d{4})", date_str)
    if match:
        for fmt in ("%B %d, %Y", "%B %d,%Y", "%b %d, %Y"):
            try:
                return datetime.strptime(match.group(1).replace("  ", " "), fmt).date()
            except ValueError:
                continue

    logger.debug("unparseable_date", raw=date_str)
    return None


def _normalize_scope(ai_scope: str) -> str:
    """Normalize AI scope categories to our subject_area values."""
    scope_lower = ai_scope.lower()
    if "deepfake" in scope_lower or "csam" in scope_lower or "intimate" in scope_lower:
        return "ai_content_safety"
    if "discrim" in scope_lower or "bias" in scope_lower:
        return "ai_discrimination"
    if "transpar" in scope_lower or "disclos" in scope_lower:
        return "ai_transparency"
    if "automat" in scope_lower and "decision" in scope_lower:
        return "automated_decision_making"
    if "govern" in scope_lower:
        return "ai_governance"
    if "health" in scope_lower:
        return "ai_healthcare"
    if "insur" in scope_lower:
        return "ai_insurance"
    if "employ" in scope_lower or "hiring" in scope_lower:
        return "ai_employment"
    if "politic" in scope_lower or "election" in scope_lower:
        return "ai_political_advertising"
    if "owner" in scope_lower or "copyright" in scope_lower:
        return "ai_ownership"
    if "educat" in scope_lower:
        return "ai_education"
    if "user" in scope_lower or "bot" in scope_lower:
        return "ai_transparency"
    if "definition" in scope_lower:
        return "ai_governance"
    return "artificial_intelligence"


# ---------------------------------------------------------------------------
# PDF parsing — extract text + hyperlinks
# ---------------------------------------------------------------------------


def _extract_urls_from_pdf(pdf_path: Path) -> list[str]:
    """Extract all hyperlinks from the PDF using pdftohtml XML output.

    Falls back to pdfplumber annotation extraction when pdftohtml is not
    installed (common on Windows).
    """
    # Try pdftohtml first (fast, Linux/macOS)
    try:
        result = subprocess.run(
            ["pdftohtml", "-xml", "-stdout", str(pdf_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            urls = re.findall(r'href="(http[^"]+)"', result.stdout)
            urls = [html_mod.unescape(u) for u in urls]
            if urls:
                return urls
        else:
            logger.warning("pdftohtml_failed", stderr=result.stderr[:200])
    except FileNotFoundError:
        logger.info("pdftohtml_not_installed_trying_pdfplumber")
    except subprocess.TimeoutExpired:
        logger.warning("pdftohtml_timeout")

    # Fallback: extract hyperlink annotations via pdfplumber
    try:
        import pdfplumber

        urls: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                if not hasattr(page, "hyperlinks"):
                    # Older pdfplumber: dig into annotations
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
                else:
                    for link in page.hyperlinks:
                        uri = link.get("uri", "")
                        if uri.startswith("http"):
                            urls.append(uri)
        if urls:
            logger.info("pdf_urls_extracted_via_pdfplumber", count=len(urls))
            return urls
    except ImportError:
        logger.warning("pdfplumber_not_installed")
    except Exception as e:
        logger.warning("pdfplumber_url_extraction_failed", error=str(e)[:200])

    # Last resort: extract URLs from raw text using regex
    try:
        text = _extract_text_from_pdf(pdf_path)
        urls = re.findall(r'https?://[^\s\)\"\'<>]+', text)
        if urls:
            logger.info("pdf_urls_extracted_via_text_regex", count=len(urls))
            return urls
    except Exception as e:
        logger.warning("text_url_extraction_failed", error=str(e)[:200])

    logger.warning("pdf_url_extraction_all_methods_failed")
    return []


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract plain text from the PDF using pdftotext."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback to pdfplumber
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts)
    except ImportError:
        raise PDFParseError(
            "Neither pdftotext (poppler-utils) nor pdfplumber is available. "
            "Install one: apt-get install poppler-utils OR pip install pdfplumber"
        )


def _extract_table_rows_from_pdf(pdf_path: Path) -> list[list[str]] | None:
    """Extract table rows from the PDF using pdfplumber word-level positioning.

    pdfplumber's default extract_tables() can't detect column boundaries in
    this PDF (no grid lines), so we use extract_words() to get word bounding
    boxes, determine column boundaries from the header row, then reconstruct
    the table structure ourselves.

    Returns a list of rows, each row a list of 7 cell strings, or None if
    extraction isn't available or finds nothing.
    """
    try:
        import pdfplumber
    except ImportError:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Step 1: Find column boundaries from header row on page 1
            col_boundaries = _find_column_boundaries(pdf.pages[0])
            if not col_boundaries:
                logger.warning("pdf_could_not_detect_column_boundaries")
                return None

            logger.debug("pdf_column_boundaries", boundaries=col_boundaries)

            # Step 2: Extract rows from all pages using those boundaries
            all_rows: list[list[str]] = []
            for page in pdf.pages:
                rows = _extract_rows_from_page(page, col_boundaries)
                all_rows.extend(rows)

            return all_rows if all_rows else None

    except Exception as e:
        logger.warning("pdfplumber_table_extraction_failed", error=str(e)[:200])
        return None


# Column header keywords used to detect boundaries
_HEADER_KEYWORDS = {
    "State/Terr": "state",
    "AI Scope": "scope",
    "Relevant Law": "law",
    "Law Link": "link",
    "Effective Date": "date",
    "Key Requirements": "requirements",
    "Enforcements Penalties": "enforcement",
}


def _find_column_boundaries(first_page) -> list[float] | None:
    """Detect column x-boundaries from the header row on page 1.

    Finds the row containing "State/Terr" and uses word x-positions from
    that row to define column boundaries.

    Returns a list of x-coordinates marking the left edge of each column,
    plus the right edge of the page. Returns None if headers can't be found.
    """
    words = first_page.extract_words(keep_blank_chars=True, extra_attrs=["top", "bottom"])
    if not words:
        return None

    # Find the "State/Terr" word to locate the header row
    state_header = None
    for w in words:
        if w["text"].startswith("State/Terr"):
            state_header = w
            break

    if not state_header:
        return None

    # Get all words on the same row (within 3pt vertically)
    header_y = state_header["top"]
    header_line = sorted(
        [w for w in words if abs(w["top"] - header_y) < 3],
        key=lambda w: w["x0"],
    )

    # Each header word/phrase maps directly to a column start
    # From the PDF: "State/Terr", "AI Scope", "Relevant Law", "Law Link",
    #               "Effective Date", "Key Requirements", "Enforcements Penalties"
    col_starts = [w["x0"] for w in header_line]

    if len(col_starts) < 4:
        return None

    logger.debug(
        "pdf_header_columns_found",
        count=len(col_starts),
        labels=[w["text"] for w in header_line],
        x_positions=col_starts,
    )

    return col_starts + [first_page.width]


def _extract_rows_from_page(page, col_boundaries: list[float]) -> list[list[str]]:
    """Extract structured rows from a page using known column boundaries.

    Groups words into rows by y-position, then assigns each word to a column
    based on its x-position.
    """
    words = page.extract_words(keep_blank_chars=True, extra_attrs=["top", "bottom"])
    if not words:
        return []

    # Filter out page header/footer noise before grouping into rows
    words = [w for w in words if not _is_noise_word(w["text"])]

    # Group words into rows by y-position (words within 3pts vertically = same row)
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
            # Determine which column this word belongs to
            col_idx = _word_to_column(w["x0"], col_boundaries)
            if col_idx < num_cols:
                if cells[col_idx]:
                    cells[col_idx] += " " + w["text"]
                else:
                    cells[col_idx] = w["text"]

        # Only include rows with some content
        if any(c.strip() for c in cells):
            result_rows.append(cells)

    # Merge continuation rows: if a row has an empty state/scope but has content
    # in requirements/enforcement, it's a continuation of the previous entry
    merged = _merge_continuation_rows(result_rows, num_cols)
    return merged


def _word_to_column(x0: float, boundaries: list[float]) -> int:
    """Determine which column a word belongs to based on its x position."""
    for i in range(len(boundaries) - 1):
        if x0 < boundaries[i + 1]:
            return i
    return len(boundaries) - 2  # Last column


def _merge_continuation_rows(rows: list[list[str]], num_cols: int) -> list[list[str]]:
    """Merge multi-line entries that span several PDF rows.

    A continuation row has empty State and Scope columns but content in
    later columns — this means the previous row's requirements/enforcement
    text wraps onto additional lines.
    """
    if not rows:
        return rows

    merged: list[list[str]] = []
    for row in rows:
        state_text = row[0].strip() if len(row) > 0 else ""
        scope_text = row[1].strip() if len(row) > 1 else ""
        law_text = row[2].strip() if len(row) > 2 else ""

        # If this row has a state or scope, it's a new entry
        has_state = bool(state_text) and _match_state_name(state_text)
        has_scope = bool(scope_text)
        has_law = bool(law_text)

        if has_state or (has_scope and has_law):
            merged.append(list(row))
        elif merged:
            # Continuation: append content to previous row's cells
            for col_idx in range(num_cols):
                cell_text = row[col_idx].strip() if col_idx < len(row) else ""
                if cell_text:
                    if merged[-1][col_idx]:
                        merged[-1][col_idx] += " " + cell_text
                    else:
                        merged[-1][col_idx] = cell_text

    return merged


_NOISE_PATTERNS = re.compile(
    r"^(Page \d+ of \d+|Last updated .*|U\.S\. AI Law Tracker|"
    r"Which states have AI|This tracker summarizes|Please visit our|"
    r"For more, please).*$"
)


def _is_noise_word(text: str) -> bool:
    """Check if a word/phrase is page header/footer noise."""
    return bool(_NOISE_PATTERNS.match(text.strip()))


def _clean_cell(text: str) -> str:
    """Remove page footer fragments from merged cell text."""
    # Remove "Page X of Y" fragments
    text = re.sub(r"\s*Page \d+ of \d+\s*", " ", text)
    # Remove "Last updated ..." fragments
    text = re.sub(r"\s*Last updated [A-Za-z]+ \d{1,2},? \d{4}\s*", " ", text)
    return text.strip()


def _match_state_name(text: str) -> str:
    """Match text against known state names, handling multi-line cell content."""
    text = text.strip()
    if not text:
        return ""
    # Direct match
    if text in STATE_CODES:
        return text
    # First line of a multi-line cell
    first_line = text.split("\n")[0].strip()
    if first_line in STATE_CODES:
        return first_line
    # Partial match (state name at start of text)
    for state in sorted(STATE_CODES.keys(), key=len, reverse=True):
        if text.startswith(state):
            return state
    return ""


def _parse_table_rows(rows: list[list[str]], law_urls: list[str]) -> list[dict]:
    """Parse structured table rows (7 columns) into law records.

    Columns: 0=State, 1=AI Scope, 2=Relevant Law, 3=Law Link/Bill ID,
             4=Effective Date, 5=Key Requirements, 6=Enforcement
    """
    records = []
    url_index = 0
    current_state = ""

    for row in rows:
        if len(row) < 3:
            continue

        state_cell = _clean_cell(row[0] if row[0] else "")
        scope_cell = row[1].strip() if len(row) > 1 else ""

        # Skip header rows
        if state_cell == "State/Terr" or "AI Scope" in state_cell:
            continue

        # Detect state
        if state_cell:
            matched = _match_state_name(state_cell)
            if matched:
                current_state = matched

        if not current_state:
            continue
        state_code = STATE_CODES.get(current_state, "")
        if not state_code:
            continue

        ai_scope = _clean_cell(scope_cell)
        law_name = _clean_cell(row[2] if len(row) > 2 else "")
        bill_id = _clean_cell(row[3] if len(row) > 3 else "")
        effective_date = _clean_cell(row[4] if len(row) > 4 else "")
        key_requirements = _clean_cell(row[5] if len(row) > 5 else "")
        enforcement = _clean_cell(row[6] if len(row) > 6 else "")

        # Skip rows with no meaningful content
        if not law_name and not bill_id and not ai_scope:
            continue

        # Match a URL from extracted hyperlinks
        law_url = ""
        if url_index < len(law_urls):
            law_url = law_urls[url_index]
            url_index += 1

        records.append({
            "state": current_state,
            "state_code": state_code,
            "ai_scope": ai_scope,
            "law_name": law_name or bill_id,
            "law_url": law_url,
            "bill_id": bill_id,
            "effective_date": effective_date,
            "key_requirements": key_requirements,
            "enforcement": enforcement,
        })

    return records


def parse_tracker_pdf(pdf_path: Path = PDF_PATH) -> list[dict]:
    """Parse the Orrick AI Law Tracker PDF into structured records.

    Returns a list of dicts with keys:
        state, state_code, ai_scope, law_name, law_url, effective_date,
        key_requirements, enforcement
    """
    if not pdf_path.exists():
        raise PDFParseError(f"PDF not found at {pdf_path}")

    logger.info("parsing_tracker_pdf", path=str(pdf_path))

    # Extract all URLs from PDF hyperlinks
    all_urls = _extract_urls_from_pdf(pdf_path)
    # Filter out Orrick self-links and footer links
    law_urls = [
        u for u in all_urls
        if "orrick.com" not in u and "mimecast" not in u
    ]
    logger.info("pdf_urls_extracted", total=len(all_urls), law_urls=len(law_urls))

    # Strategy 1: Use pdfplumber word-level extraction with column detection
    table_rows = _extract_table_rows_from_pdf(pdf_path)
    if table_rows:
        logger.info("pdf_table_rows_extracted", rows=len(table_rows))
        records = _parse_table_rows(table_rows, law_urls)
        if records:
            logger.info("pdf_parsed", total_records=len(records), method="table_extraction")
            return records
        logger.warning("table_extraction_produced_no_records_falling_back_to_text")

    # Strategy 2: Fall back to line-based text parsing
    text = _extract_text_from_pdf(pdf_path)
    if not text:
        raise PDFParseError("Could not extract text from PDF")

    lines = text.split("\n")
    records = _parse_tabular_text(lines, law_urls)

    logger.info("pdf_parsed", total_records=len(records), method="text_parsing")
    return records


def _parse_tabular_text(lines: list[str], law_urls: list[str]) -> list[dict]:
    """Parse the extracted text lines into structured records.

    The PDF has a repeating pattern per law entry:
    - State name (when it changes)
    - AI Scope category
    - Law name
    - Bill identifier (Law Link column)
    - Effective date
    - Key requirements (multi-line bullet points)
    - Enforcement penalties (multi-line)

    We detect state boundaries by matching against STATE_CODES keys,
    and parse each row by identifying the structural pattern.
    """
    records = []
    url_index = 0  # Track which URL we're on

    # Build state name set for detection
    state_names = set(STATE_CODES.keys())
    # Also match state names that appear at line start
    state_pattern = re.compile(
        r"^(" + "|".join(re.escape(s) for s in sorted(state_names, key=len, reverse=True)) + r")\s*$"
    )

    current_state = ""
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines, header/footer noise
        if not line or line.startswith("U.S. AI Law Tracker") or \
           line.startswith("Which states") or \
           line.startswith("This tracker") or \
           line.startswith("Please visit") or \
           line.startswith("Last updated") or \
           line.startswith("For more,") or \
           re.match(r"^Page \d+ of \d+$", line):
            i += 1
            continue

        # Skip column headers
        if line in ("State/Terr", "AI Scope", "Relevant Law", "Law Link",
                     "Effective Date", "Key Requirements", "Enforcements Penalties"):
            i += 1
            continue

        # Detect state name
        state_match = state_pattern.match(line)
        if state_match:
            current_state = state_match.group(1)
            i += 1
            continue

        # Also check if line starts with a known state name (multi-column layout)
        for sn in state_names:
            if line == sn:
                current_state = sn
                break

        if not current_state:
            i += 1
            continue

        state_code = STATE_CODES.get(current_state, "")
        if not state_code:
            i += 1
            continue

        # Try to detect an AI Scope line (category keywords)
        if _looks_like_scope(line):
            ai_scope = line
            i += 1

            # Next: law name (may be multi-line)
            law_name, i = _read_law_name(lines, i)

            # Next: bill identifier / law link
            bill_id, i = _read_bill_id(lines, i)

            # Next: effective date
            effective_date, i = _read_effective_date(lines, i)

            # Next: key requirements (multi-line, ends at enforcement or next entry)
            key_requirements, i = _read_requirements(lines, i)

            # Next: enforcement penalties
            enforcement, i = _read_enforcement(lines, i)

            # Match a URL from the extracted hyperlinks
            law_url = ""
            if url_index < len(law_urls):
                law_url = law_urls[url_index]
                url_index += 1

            records.append({
                "state": current_state,
                "state_code": state_code,
                "ai_scope": ai_scope,
                "law_name": law_name or bill_id,
                "law_url": law_url,
                "bill_id": bill_id,
                "effective_date": effective_date,
                "key_requirements": key_requirements,
                "enforcement": enforcement,
            })
            continue

        i += 1

    return records


# Scope category keywords that appear in the "AI Scope" column
_SCOPE_KEYWORDS = [
    "AI ", "Automated", "Algorithmic", "Digital", "Deepfake",
    "CSAM", "Intimate", "Political", "Election", "Employment",
    "Hiring", "Insurance", "Healthcare", "Health", "Education",
    "Government", "Governance", "Transparency", "Disclosure",
    "Ownership", "Copyright", "Definition", "User-Facing",
    "Discrimination", "Bias", "Privacy", "Data", "Cybersecurity",
    "Impersonation", "Criminal", "Consumer", "Procurement",
    "Amendment", "General",
]


def _looks_like_scope(line: str) -> bool:
    """Check if a line looks like an AI Scope category."""
    line = line.strip()
    if len(line) > 80:
        return False
    return any(line.startswith(kw) or kw.lower() in line.lower() for kw in _SCOPE_KEYWORDS)


def _read_law_name(lines: list[str], i: int) -> tuple[str, int]:
    """Read the law name (may span multiple lines)."""
    parts = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            break
        # Stop if we hit a bill ID pattern or date
        if re.match(r"^(HB|SB|AB|HR|SR|S |H |A\d|Act No|Chapter|Public Law|P\.L\.|[A-Z]{2} [HS]B)", line):
            break
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", line):
            break
        if re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)", line):
            break
        # Stop at bullet points (key requirements)
        if line.startswith("•") or line.startswith("- "):
            break
        parts.append(line)
        i += 1
        if len(parts) >= 4:  # Law names are at most a few lines
            break
    return " ".join(parts), i


def _read_bill_id(lines: list[str], i: int) -> tuple[str, int]:
    """Read the bill identifier."""
    parts = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            break
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", line):
            break
        if re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)", line):
            break
        if line.startswith("•"):
            break
        parts.append(line)
        i += 1
        if len(parts) >= 3:
            break
    return " ".join(parts), i


def _read_effective_date(lines: list[str], i: int) -> tuple[str, int]:
    """Read the effective date field."""
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Check if this line contains a date
        if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", line) or \
           re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)", line) or \
           line.lower() in ("tbd", "n/a", "pending", "varies"):
            i += 1
            return line, i
        # If not a date, we've overshot
        break
    return "", i


def _read_requirements(lines: list[str], i: int) -> tuple[str, int]:
    """Read multi-line key requirements section."""
    parts = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            if parts:  # Empty line after content = end of section
                i += 1
                break
            i += 1
            continue
        # Stop if we hit a new state or scope
        if line in STATE_CODES:
            break
        if _looks_like_scope(line) and not line.startswith("•"):
            break
        # Stop at page footer
        if line.startswith("Last updated") or line.startswith("For more,") or \
           re.match(r"^Page \d+ of \d+$", line):
            i += 1
            continue
        parts.append(line)
        i += 1
    return " ".join(parts), i


def _read_enforcement(lines: list[str], i: int) -> tuple[str, int]:
    """Read enforcement/penalties section."""
    parts = []
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            if parts:
                i += 1
                break
            i += 1
            continue
        if line in STATE_CODES:
            break
        if _looks_like_scope(line) and not line.startswith("•"):
            break
        if line.startswith("Last updated") or line.startswith("For more,") or \
           re.match(r"^Page \d+ of \d+$", line):
            i += 1
            continue
        parts.append(line)
        i += 1
    return " ".join(parts), i


# ---------------------------------------------------------------------------
# Database seeding (kept from original orrick_scraper.py)
# ---------------------------------------------------------------------------


def seed_from_tracker(db, records: list[dict] | None = None) -> tuple[list[IngestionJob], dict]:
    """Convert tracker records into database records and create ingestion jobs.

    For each row, creates/updates:
        Source → DocumentFamily → DocumentVersion → IngestionJob

    Skips rows that already exist (matched by state_code + law_name).

    Returns:
        (jobs_created, stats_dict) where stats_dict contains:
            total_parsed, new_jobs, existing, skipped_no_url, skipped_no_state
    """
    if records is None:
        records = parse_tracker_pdf()

    jobs_created = []
    seeded_no_url = []
    skipped_no_state = []
    skipped_existing = 0

    # Circuit breaker: if most DB inserts fail, something is structurally
    # wrong (schema mismatch, constraint violation, etc.)
    tracker = FailureTracker(
        context="seed_from_tracker (DB inserts)",
        max_consecutive=5,
        max_failure_rate=0.8,
        min_items_for_rate=10,
    )

    try:
        for record in records:
            state_code = record["state_code"]
            state_name = record["state"]
            if not state_code:
                skipped_no_state.append(record.get("law_name", "unknown"))
                logger.warning("skipping_unknown_state", state=state_name, law=record.get("law_name"))
                continue

            law_url = record["law_url"]
            if not law_url:
                seeded_no_url.append(f"{state_code} - {record['law_name']}")

            try:
                job = _seed_single_law(db, record)
                if job:
                    jobs_created.append(job)
                else:
                    skipped_existing += 1
                tracker.record_success()
            except CircuitBreakerTripped:
                raise
            except Exception as e:
                logger.warning(
                    "seed_row_failed",
                    state=state_code,
                    law=record["law_name"],
                    error=str(e),
                )
                tracker.record_failure(
                    f"{state_code}/{record['law_name']}: {e}"
                )

    except CircuitBreakerTripped as cb:
        logger.error("seed_circuit_breaker", detail=str(cb))
        # Flush what we have so far
        db.flush()
        return jobs_created, {
            "total_parsed": len(records),
            "new_jobs": len(jobs_created),
            "existing": skipped_existing,
            "seeded_no_url": seeded_no_url,
            "skipped_no_state": skipped_no_state,
        }

    db.flush()
    logger.info(
        "seeding_complete",
        total_parsed=len(records),
        jobs_created=len(jobs_created),
        skipped_existing=skipped_existing,
        seeded_no_url=len(seeded_no_url),
        skipped_no_state=len(skipped_no_state),
    )
    if seeded_no_url:
        logger.warning("records_missing_urls", count=len(seeded_no_url), records=seeded_no_url[:10])

    # Attach skip info for callers (dashboard) to display
    return jobs_created, {
        "total_parsed": len(records),
        "new_jobs": len(jobs_created),
        "existing": skipped_existing,
        "seeded_no_url": seeded_no_url,
        "skipped_no_state": skipped_no_state,
    }


def _seed_single_law(db, record: dict) -> IngestionJob | None:
    """Create Source/DocumentFamily/DocumentVersion/IngestionJob for one tracker row."""
    state_code = record["state_code"]
    state_name = record["state"]
    law_name = record["law_name"]
    law_url = record["law_url"]
    ai_scope = record["ai_scope"]
    effective_date = _parse_effective_date(record["effective_date"])

    # --- Source ---
    source = db.query(Source).filter_by(
        jurisdiction_code=state_code, connector_id="pdf_tracker"
    ).first()
    if not source:
        # Also check for legacy orrick_tracker connector and migrate it
        source = db.query(Source).filter_by(
            jurisdiction_code=state_code, connector_id="orrick_tracker"
        ).first()
        if source:
            source.connector_id = "pdf_tracker"
            source.base_url = ""
            db.flush()

    if not source:
        source = Source(
            jurisdiction_code=state_code,
            jurisdiction_name=state_name,
            source_type="state_statute",
            base_url="",
            connector_id="pdf_tracker",
            metadata_={"source": "orrick_pdf_tracker"},
        )
        db.add(source)
        db.flush()

    # --- DocumentFamily (one per unique law name within a state) ---
    family = db.query(DocumentFamily).filter_by(
        source_id=source.id, short_cite=law_name
    ).first()
    if not family:
        family = DocumentFamily(
            source_id=source.id,
            canonical_title=f"{state_name} - {law_name}",
            short_cite=law_name,
            subject_area=_normalize_scope(ai_scope),
            metadata_={
                "ai_scope": ai_scope,
                "key_requirements": record["key_requirements"],
                "enforcement": record["enforcement"],
                "pdf_last_parsed": datetime.utcnow().isoformat(),
            },
        )
        db.add(family)
        db.flush()
    else:
        # Refresh metadata if changed
        new_meta = {
            "ai_scope": ai_scope,
            "key_requirements": record["key_requirements"],
            "enforcement": record["enforcement"],
            "pdf_last_parsed": datetime.utcnow().isoformat(),
        }
        old_meta = family.metadata_ or {}
        if (
            old_meta.get("key_requirements") != new_meta["key_requirements"]
            or old_meta.get("enforcement") != new_meta["enforcement"]
            or old_meta.get("ai_scope") != new_meta["ai_scope"]
        ):
            family.metadata_ = {**old_meta, **new_meta}
            family.subject_area = _normalize_scope(ai_scope)
            logger.info(
                "metadata_refreshed",
                state=state_code,
                law=law_name,
                changed_fields=[
                    k for k in ("key_requirements", "enforcement", "ai_scope")
                    if old_meta.get(k) != new_meta[k]
                ],
            )
            db.flush()

    # --- DocumentVersion ---
    version_label = "Current"
    existing_version = db.query(DocumentVersion).filter_by(
        family_id=family.id, version_label=version_label
    ).first()
    if existing_version:
        logger.debug(
            "version_exists",
            state=state_code,
            law=law_name,
            version=version_label,
        )
        return None

    temporal_status = TemporalStatus.active if effective_date else TemporalStatus.enacted
    if effective_date and effective_date > date.today():
        temporal_status = TemporalStatus.future_effective

    version = DocumentVersion(
        family_id=family.id,
        version_label=version_label,
        temporal_status=temporal_status,
        effective_date=effective_date,
        metadata_={
            "law_url": law_url,
            "ai_scope": ai_scope,
        },
    )
    db.add(version)
    db.flush()

    # --- LegalEvent ---
    if effective_date:
        db.add(LegalEvent(
            document_version_id=version.id,
            event_type=LegalEventType.effective,
            event_date=effective_date,
            description=f"{law_name} effective date",
            authority=state_name,
        ))

    # --- IngestionJob ---
    # If no URL was extracted from the PDF, mark the job as needing manual
    # upload so it appears in the Failed Documents tab immediately.
    if law_url:
        job_status = IngestionStatus.pending
        error_msg = None
    else:
        job_status = IngestionStatus.failed
        error_msg = (
            "No URL found in tracker PDF for this law. "
            "Upload the document manually or edit the fetch URL."
        )

    job = IngestionJob(
        document_version_id=version.id,
        status=job_status,
        fetch_url=law_url or None,
        error_message=error_msg,
        metadata_={
            "ai_scope": ai_scope,
            "source": "pdf_tracker",
        },
    )
    db.add(job)
    db.flush()

    logger.info(
        "law_seeded",
        state=state_code,
        law=law_name,
        effective_date=str(effective_date),
        url=(law_url[:80] if law_url else "(no URL)"),
        job_id=job.id,
        needs_manual_upload=not bool(law_url),
    )
    return job
