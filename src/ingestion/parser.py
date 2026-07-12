"""Document parser and normalizer.

Parses fetched raw artifacts into passage-level normalized source records.
Handles HTML (state legislation sites) and PDF (federal documents) formats.
"""

from __future__ import annotations

import hashlib
import re
import warnings

import structlog
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from src.db.models import IngestionJob, NormalizedSourceRecord, RawArtifact

logger = structlog.get_logger()

# Passages whose parse quality score is below this threshold get flagged for
# manual review rather than feeding the extraction pipeline directly.
_PARSE_QUALITY_REVIEW_THRESHOLD = 0.30

# Legal text markers used to estimate content quality
_LEGAL_MARKERS = re.compile(
    r"\b(?:section|§|whereas|enacted|shall|provided|pursuant|herein|thereof|"
    r"notwithstanding|jurisdiction|obligation|penalty|enforcement|compliance|"
    r"regulation|statute|subsection|paragraph)\b",
    re.I,
)

# EA2-4: engrossed/amendment bills mark deleted (no-longer-law) text with
# strikethrough and inserted (new) text with underline. BeautifulSoup's
# get_text() has no notion of this — it silently includes struck text in the
# extracted passage stream, so a repealed clause reads as if it were still
# binding law. Confirmed live in this corpus, e.g. 2025 Wisconsin Act 69
# (TMP-WI-ESTATEADVERTIS.html): the flattened text read "...client ,
# principal firm, or firm , without..." where ", principal firm," and the
# trailing "," are struck fragments with zero distinguishing signal once
# stripped of styling. Only unambiguous tag/style-level signals trigger
# removal (never a class-name heuristic alone) to avoid deleting real
# statutory text on a false positive.
_STRIKE_TAG_NAMES = ("strike", "del", "s")

# Some states (e.g. Kentucky bill PDFs, NJ Register regulatory notices) print
# deleted text wrapped in literal brackets instead of using strikethrough
# styling — this survives pdftotext/plaintext extraction as plain characters.
# Ordinary statutory citations are also bracketed but virtually always start
# with a digit ("[42 U.S.C. § 2000e-8]"), so requiring an alpha first
# character cuts most false positives; a single incidental match is also not
# enough signal (real deletion runs cluster several to a passage), hence the
# minimum count below.
_BRACKET_DELETION_PATTERN = re.compile(r"\[[A-Za-z][^\[\]]{1,60}\]")
_BRACKET_AMENDMENT_MIN_COUNT = 2


def _is_line_through_style(style: str) -> bool:
    normalized = style.replace(" ", "").lower()
    return "text-decoration:line-through" in normalized


def _is_underline_style(style: str) -> bool:
    normalized = style.replace(" ", "").lower()
    return "text-decoration:underline" in normalized


def _strip_struck_content(content_el) -> dict:
    """Remove unambiguously-marked deleted text from a parsed HTML tree in place.

    Mutates `content_el`. Returns document-level markup stats used to set
    `amendment_markup_detected` on every passage parsed from this document —
    coarse (document-, not passage-level) because passage segmentation
    happens on already-flattened text with no DOM correspondence, but honest:
    it only reports what was actually found, never a guessed offset.
    """
    struck_chars_removed = 0
    struck_found = False

    for tag in content_el.find_all(_STRIKE_TAG_NAMES):
        struck_chars_removed += len(tag.get_text())
        struck_found = True
        tag.decompose()

    for tag in content_el.find_all(style=True):
        if _is_line_through_style(tag.get("style", "")):
            struck_chars_removed += len(tag.get_text())
            struck_found = True
            tag.decompose()

    inserted_found = bool(content_el.find_all("ins"))
    if not inserted_found:
        for tag in content_el.find_all(style=True):
            if _is_underline_style(tag.get("style", "")):
                inserted_found = True
                break

    return {
        "struck_found": struck_found,
        "inserted_found": inserted_found,
        "struck_chars_removed": struck_chars_removed,
    }


def _compute_parse_quality(text: str) -> float:
    """Estimate parse quality for a passage on a 0.0–1.0 scale.

    Two signals:
      - replacement_char_ratio: U+FFFD density (high → likely binary junk).
        Binary/garbled PDFs often produce strings like "ÿþÿþ" after a UTF-8
        decode with errors="replace".
      - legal_marker_density: how many recognised statutory keywords appear
        per 1000 characters.  Plain statutory text typically scores >= 5.

    Returns 1.0 for clean legal prose, near 0.0 for binary/OCR garbage.
    """
    if not text:
        return 0.0

    # Replacement character penalty (each � = one garbled byte)
    replacement_count = text.count("�")
    replacement_ratio = replacement_count / max(len(text), 1)
    # Score: 1.0 at 0% → 0.0 at ≥5% replacement chars
    replacement_score = max(0.0, 1.0 - replacement_ratio / 0.05)

    # Legal marker density
    marker_count = len(_LEGAL_MARKERS.findall(text))
    density = marker_count / max(len(text), 1) * 1000  # per 1000 chars
    # Score: 0.0 at 0 markers → 1.0 at ≥5 markers per 1000 chars
    density_score = min(1.0, density / 5.0)

    # Combine: replacement char quality outweighs density when egregiously bad
    return round(replacement_score * 0.60 + density_score * 0.40, 4)


def parse_and_normalize(
    db,
    job: IngestionJob,
    artifact: RawArtifact,
    content_bytes: bytes | None = None,
) -> list[NormalizedSourceRecord]:
    """Parse a raw artifact into passage-level normalized records.

    Handles HTML, plain text, and PDF.
    If content_bytes is provided, uses that instead of fetching from S3.

    Each record's metadata_ includes:
      - included_section_ids: list of all section markers merged into this passage
      - parse_quality_score: 0.0–1.0 quality estimate for the raw text
      - requires_manual_review: True when quality is below the accept threshold
      - amendment_markup_detected: True when this passage (or, for HTML
        sources, its parent document) shows engrossed/amendment-bill markup
        (EA2-4) — struck/inserted HTML styling, or a cluster of bracket-style
        deletion markers. Informational; nothing is auto-published or
        blocked on this flag.
    """
    content = content_bytes if content_bytes is not None else _fetch_content_from_s3(artifact.s3_key)

    html_markup_info: dict | None = None
    if artifact.content_type in ("text/html", "application/xhtml+xml"):
        passages, html_markup_info = _parse_html(content)
    elif artifact.content_type in ("application/pdf",):
        passages = _parse_pdf(content)
    elif artifact.content_type in ("text/plain",):
        passages = _parse_plaintext(content)
    else:
        logger.warning("unsupported_content_type", content_type=artifact.content_type)
        passages = _parse_plaintext(content)

    records = []
    for ordinal, passage_tuple in enumerate(passages):
        # Support both 4-tuple (legacy) and 5-tuple (section IDs added by RR4a)
        if len(passage_tuple) == 5:
            section_path, text, start, end, included_section_ids = passage_tuple
        else:
            section_path, text, start, end = passage_tuple
            included_section_ids = [section_path]

        text_hash = hashlib.sha256(text.encode()).hexdigest()
        parse_quality = _compute_parse_quality(text)
        bracket_markers = len(_BRACKET_DELETION_PATTERN.findall(text))

        meta: dict = {
            "included_section_ids": included_section_ids,
            "parse_quality_score": round(parse_quality, 4),
        }
        if parse_quality < _PARSE_QUALITY_REVIEW_THRESHOLD:
            meta["requires_manual_review"] = True

        markup_detected = bracket_markers >= _BRACKET_AMENDMENT_MIN_COUNT
        if html_markup_info and (
            html_markup_info["struck_found"] or html_markup_info["inserted_found"]
        ):
            markup_detected = True
        if markup_detected:
            meta["amendment_markup_detected"] = True
            if bracket_markers >= _BRACKET_AMENDMENT_MIN_COUNT:
                meta["bracket_markers_count"] = bracket_markers
            if html_markup_info and html_markup_info["struck_chars_removed"]:
                meta["struck_chars_removed"] = html_markup_info["struck_chars_removed"]

        record = NormalizedSourceRecord(
            document_version_id=job.document_version_id,
            section_path=section_path,
            ordinal=ordinal,
            text_content=text,
            text_hash=text_hash,
            char_offset_start=start,
            char_offset_end=end,
            metadata_=meta,
        )
        db.add(record)
        records.append(record)

    db.flush()
    logger.info("parsed_document", artifact_id=artifact.id, passages=len(records))
    return records


def _make_soup(content: bytes) -> BeautifulSoup:
    """Create a BeautifulSoup instance.

    Always parse as HTML (lxml) even if the document has an <?xml> declaration.
    Legislative HTML pages from state sites commonly include XML prologues but
    are structurally HTML — the XML parser strips most content.
    """
    return BeautifulSoup(content, "lxml")


def _parse_html(content: bytes) -> tuple[list[tuple[str, str, int, int]], dict | None]:
    """Parse HTML legislative text into passages.

    Returns (passages, html_markup_info) where passages is a list of
    (section_path, text, char_start, char_end) and html_markup_info is the
    document-level dict from `_strip_struck_content` (None for the PDF-guard
    branch, which delegates to `_parse_pdf`).
    """
    # Guard: some .html files are actually PDFs (wrong extension)
    if content.lstrip()[:10].startswith(b"%PDF"):
        return _parse_pdf(content), None

    soup = _make_soup(content)

    # Remove script, style, and non-content elements
    for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        element.decompose()

    # Try to find the real content area (state legislature sites bury bill
    # text inside specific containers; everything else is navigation chrome).
    # Check selectors from most-specific to least-specific.
    content_el = None
    _CONTENT_SELECTORS = [
        "#bill",           # CA leginfo.legislature.ca.gov
        "#bill_all",       # CA leginfo (broader)
        "#billtext",       # CA leginfo (tab container)
        "#billTextContainer",
        ".bill-text",
        "#document",
        ".legislation-text",
        "#TextContent",    # Various state sites
        "article",         # Semantic HTML5
        "main",            # Semantic HTML5
        "#main-content",
        "#content_main",
        "#content",
    ]
    for selector in _CONTENT_SELECTORS:
        found = soup.select_one(selector)
        if found and len(found.get_text(strip=True)) > 500:
            content_el = found
            break

    if content_el is None:
        # Fallback: use the entire body
        content_el = soup.find("body") or soup

    # EA2-4: remove struck (deleted, no-longer-law) text before flattening —
    # must happen before get_text(), which has no concept of strikethrough
    # and would otherwise silently include it in the passage stream.
    markup_info = _strip_struck_content(content_el)

    # Extract text with double-newline separators so each block element
    # creates a paragraph break.
    full_text = content_el.get_text(separator="\n\n", strip=True)

    # Strip leading website chrome that survived tag removal.
    # Look for the bill text start marker (common patterns).
    _BILL_START_PATTERNS = [
        r"(?:Senate|Assembly|House)\s+Bill\s+No\.\s*\d+",
        r"LEGISLATIVE\s+COUNSEL",
        r"AN\s+ACT\s+(?:to|relating|concerning)",
        r"Be\s+it\s+enacted",
        r"CHAPTER\s+\d+",
        r"ENROLLED\s+(?:ACT|BILL)",
        r"(?:Section|SECTION)\s+1\b",
    ]
    for pattern in _BILL_START_PATTERNS:
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m and m.start() > 100:
            # Only trim if there's substantial leading chrome (>100 chars)
            full_text = full_text[m.start():]
            break

    return _segment_text(full_text), markup_info


def _parse_plaintext(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse plain text into passages.

    Strips web-sourced markdown headers (Title:, URL Source:, Markdown Content:)
    and leading navigation chrome when present.
    """
    text = content.decode("utf-8", errors="replace")

    # Strip web-sourced markdown wrapper (produced by URL fetcher)
    if text.startswith("Title:"):
        # Remove "Title: ...\n\nURL Source: ...\n\nMarkdown Content:\n" header
        marker = "Markdown Content:"
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx + len(marker):].lstrip("\n")

    # Strip markdown navigation chrome (links, menus, image references)
    # Look for the actual legislative content start
    _BILL_START_PATTERNS = [
        r"(?:Senate|Assembly|House)\s+Bill\s+No\.\s*\d+",
        r"LEGISLATIVE\s+COUNSEL",
        r"AN\s+ACT\s+(?:to|relating|concerning)",
        r"Be\s+it\s+enacted",
        r"CHAPTER\s+\d+",
        r"ENROLLED\s+(?:ACT|BILL)",
        r"(?:Section|SECTION)\s+1\b",
        r"Bill\s+(?:Text|Start)",
    ]
    for pattern in _BILL_START_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m and m.start() > 200:
            text = text[m.start():]
            break

    return _segment_text(text)


def _splice_marker_only_stubs(
    raw: list[tuple[str, str, int, int]],
) -> list[tuple[str, str, int, int]]:
    """Merge section markers with an empty captured body into their successor.

    An empty body (passage_text == marker) means section_pattern's lookahead
    stopped immediately because the next token was itself a marker match —
    the real content that should belong to this section got split off under
    a different label. See the call site in _segment_text for the concrete
    "SECTION 7. Chapter 272..." example this fixes.
    """
    fixed: list[tuple[str, str, int, int]] = []
    i = 0
    n = len(raw)
    while i < n:
        marker, ptext, start, end = raw[i]
        text_parts = [ptext]
        combined_end = end
        j = i
        while text_parts[-1].strip() == raw[j][0].strip() and j + 1 < n:
            j += 1
            _, next_ptext, _, next_end = raw[j]
            text_parts.append(next_ptext)
            combined_end = next_end
        fixed.append((marker, "\n\n".join(text_parts), start, combined_end))
        i = j + 1
    return fixed


def _segment_text(text: str) -> list[tuple[str, str, int, int]]:
    """Segment legislative text into passage-level chunks.

    Strategy:
      0. If text has few/no newlines, insert them before section markers
      1. Try section-header splitting (Section X, Article Y, § Z, etc.)
      2. Fall back to paragraph splitting on double-newlines
      3. If paragraphs are too large (>15000 chars), split them further

    Returns list of (section_path, text, char_start, char_end).
    """
    # Pre-process: if the text is mostly one long line (common with HTML-extracted
    # law_texts/*.txt files), insert double-newlines before section markers so
    # the section regex can match them.
    newline_count = text.count("\n")
    if newline_count < len(text) / 2000:  # Very few newlines relative to length
        text = re.sub(
            r"(?<=[.;)\]])\s+(?=(?:Section|SECTION|Sec\.|SEC\.)\s+\d"
            r"|§\s*\d"
            r"|(?:Article|ARTICLE)\s+[IVXLCDM\d]"
            r"|(?:Chapter|CHAPTER|Part|PART|Title|TITLE|Rule|RULE)\s+\d)",
            "\n\n",
            text,
        )

    # Pattern for top-level legislative section markers — covers:
    #   Section 1, SECTION 1, Sec. 1, SEC. 1, § 1, §1
    #   Article I, ARTICLE 1
    #   Chapter 1, CHAPTER 1, Part 1, PART 1, Title 1, TITLE 1
    #   Rule 1, RULE 1
    # NOTE: Sub-section markers like (a), (b), (1), (i) are intentionally
    # excluded — they appear dozens of times per bill and create tiny
    # fragments that waste triage/extraction tokens.
    section_pattern = re.compile(
        r"(?:^|\n\n?)"
        r"((?:Section|SECTION|Sec\.|SEC\.)\s+\d+[\w.\-]*"
        r"|§\s*\d+[\w.\-]*"
        r"|(?:Article|ARTICLE)\s+\w+"
        r"|(?:Chapter|CHAPTER|Part|PART|Title|TITLE|Rule|RULE)\s+\d+[\w.\-]*)"
        r"(.*?)(?=\n\n?(?:Section|SECTION|Sec\.|SEC\.|§\s*\d|Article|ARTICLE"
        r"|Chapter|CHAPTER|Part|PART|Title|TITLE|Rule|RULE)|\Z)",
        re.DOTALL,
    )

    matches = list(section_pattern.finditer(text))

    if not matches:
        return _split_on_paragraphs(text)

    raw_passages = []
    for match in matches:
        section_marker = match.group(1).strip()
        passage_text = (match.group(1) + match.group(2)).strip()

        if len(passage_text) < 10:
            continue

        raw_passages.append((
            section_marker,
            passage_text,
            match.start(),
            match.end(),
        ))

    if not raw_passages:
        return _split_on_paragraphs(text)

    # Fix a mislabeling bug: a section marker whose captured body is empty
    # (passage_text == the bare marker itself) means the lookahead in
    # section_pattern stopped immediately because the very next token was
    # ALSO a marker match — typically a cross-reference to the code being
    # amended, e.g. "SECTION 7. Chapter 272 of the General Laws is hereby
    # amended by..." is one continuous clause, but "Chapter 272" matches the
    # same pattern as a top-level marker, so section_pattern split it off as
    # its own chunk. Left alone, this produces an empty "SECTION 7." stub
    # AND mislabels SECTION 7's real content as belonging to "Chapter 272"
    # instead — the section number and its own body get separated. Splice
    # marker-only entries into their immediate successor (handles chains of
    # back-to-back empty markers too) so real content stays attributed to
    # the section that actually contains it.
    raw_passages = _splice_marker_only_stubs(raw_passages)

    # Merge adjacent small section passages into larger chunks.
    # PDF-extracted text often splits on every "Section X" marker,
    # producing hundreds of tiny fragments (< 200 chars each).
    # RR4a: track included_section_ids (all markers merged into this chunk).
    # RR4b: use the actual raw end offset of the last merged section instead of
    #        start + len(merged_text), which was wrong for multi-section merges.
    TARGET_SECTION_CHARS = 3000
    merged = []
    chunk_parts: list[str] = []
    chunk_markers: list[str] = []  # RR4a: all section IDs in this chunk
    chunk_marker = raw_passages[0][0]
    chunk_start = raw_passages[0][2]
    chunk_end = raw_passages[0][3]   # RR4b: track actual raw end offset
    chunk_len = 0

    for marker, ptext, start, end in raw_passages:
        if chunk_parts and chunk_len + len(ptext) > TARGET_SECTION_CHARS:
            merged_text = "\n\n".join(chunk_parts)
            merged.append((chunk_marker, merged_text, chunk_start, chunk_end, list(chunk_markers)))
            chunk_parts = []
            chunk_markers = []
            chunk_marker = marker
            chunk_start = start
            chunk_end = end
            chunk_len = 0

        chunk_parts.append(ptext)
        chunk_markers.append(marker)
        chunk_end = end   # extend to the end of the latest section
        chunk_len += len(ptext)

    if chunk_parts:
        merged_text = "\n\n".join(chunk_parts)
        merged.append((chunk_marker, merged_text, chunk_start, chunk_end, list(chunk_markers)))

    return merged


def _split_on_paragraphs(text: str) -> list[tuple[str, str, int, int]]:
    """Fallback paragraph splitter.

    Splits on double-newlines, then merges adjacent small paragraphs into
    chunks of TARGET_CHUNK_CHARS to avoid thousands of tiny passages from
    PDF-extracted text (which has double-newlines at every page/column break).
    """
    TARGET_CHUNK_CHARS = 3000  # Target size per merged passage
    MAX_CHUNK_CHARS = 15000   # Hard cap — never exceed this

    paragraphs = re.split(r"\n\s*\n", text)
    # First pass: collect non-trivial paragraphs with offsets
    raw_parts: list[tuple[str, int]] = []  # (text, offset)
    offset = 0
    for para in paragraphs:
        para = para.strip()
        if len(para) >= 10:
            raw_parts.append((para, offset))
        offset += len(para) + 2

    if not raw_parts:
        return []

    # Second pass: merge small adjacent paragraphs into TARGET_CHUNK_CHARS chunks
    # RR4a: 5-tuple format (label, text, start, end, included_section_ids)
    passages = []
    chunk_parts: list[str] = []
    chunk_start = raw_parts[0][1]
    chunk_end = raw_parts[0][1]
    chunk_len = 0

    def _flush_chunk(label_n: int) -> None:
        label = f"Paragraph {label_n}"
        merged = "\n\n".join(chunk_parts)
        passages.append((label, merged, chunk_start, chunk_end, [label]))

    for para_text, para_offset in raw_parts:
        para_end = para_offset + len(para_text)
        # Would adding this paragraph exceed the target?
        if chunk_parts and chunk_len + len(para_text) > TARGET_CHUNK_CHARS:
            _flush_chunk(len(passages) + 1)
            chunk_parts = []
            chunk_start = para_offset
            chunk_end = para_offset
            chunk_len = 0

        # Sub-split oversized single paragraphs, then merge into TARGET chunks
        if len(para_text) > MAX_CHUNK_CHARS:
            # Flush any pending chunk first
            if chunk_parts:
                _flush_chunk(len(passages) + 1)
                chunk_parts = []
                chunk_len = 0

            sub_parts = para_text.split("\n")
            sub_chunk: list[str] = []
            sub_chunk_start = para_offset
            sub_chunk_end = para_offset
            sub_chunk_len = 0
            sub_offset = para_offset
            for sub in sub_parts:
                sub = sub.strip()
                if len(sub) < 10:
                    sub_offset += len(sub) + 1
                    continue
                sub_end = sub_offset + len(sub)
                if sub_chunk and sub_chunk_len + len(sub) > TARGET_CHUNK_CHARS:
                    label = f"Paragraph {len(passages) + 1}"
                    merged = "\n".join(sub_chunk)
                    passages.append((label, merged, sub_chunk_start, sub_chunk_end, [label]))
                    sub_chunk = []
                    sub_chunk_start = sub_offset
                    sub_chunk_end = sub_offset
                    sub_chunk_len = 0
                sub_chunk.append(sub)
                sub_chunk_end = sub_end
                sub_chunk_len += len(sub)
                sub_offset += len(sub) + 1
            if sub_chunk:
                label = f"Paragraph {len(passages) + 1}"
                merged = "\n".join(sub_chunk)
                passages.append((label, merged, sub_chunk_start, sub_chunk_end, [label]))
            chunk_start = sub_offset
            chunk_end = sub_offset
        else:
            chunk_parts.append(para_text)
            chunk_end = para_end
            chunk_len += len(para_text)

    # Flush remaining
    if chunk_parts:
        _flush_chunk(len(passages) + 1)

    return passages


def _parse_pdf(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse PDF content into passages.

    Uses pdfplumber for text extraction. If the PDF is scanned (no text layer),
    falls back to OCR via pytesseract + pdf2image.
    """
    try:
        import io

        import pdfplumber

        text_parts = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

        full_text = "\n\n".join(text_parts)

        # If pdfplumber got no text, the PDF is likely scanned — try OCR
        if not full_text.strip():
            logger.info("pdf_no_text_layer, attempting OCR")
            return _parse_pdf_ocr(content)

        return _segment_text(full_text)

    except ImportError:
        logger.warning("pdfplumber_not_installed, falling back to plaintext")
        return _parse_plaintext(content)
    except Exception as e:
        logger.error("pdf_parse_error", error=str(e))
        return _parse_plaintext(content)


def _parse_pdf_ocr(content: bytes) -> list[tuple[str, str, int, int]]:
    """OCR fallback for scanned PDFs with no text layer.

    Uses pdf2image to render pages, then pytesseract for OCR.
    Requires system packages: tesseract-ocr, poppler-utils.
    """
    try:
        import pytesseract
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(content, dpi=300)
        text_parts = []
        for image in images:
            page_text = pytesseract.image_to_string(image, lang="eng")
            if page_text and page_text.strip():
                text_parts.append(page_text)

        full_text = "\n\n".join(text_parts)
        if not full_text.strip():
            logger.warning("pdf_ocr_no_text_extracted")
            return []

        logger.info("pdf_ocr_success", pages=len(images), chars=len(full_text))
        return _segment_text(full_text)

    except ImportError as e:
        logger.warning(
            "pdf_ocr_deps_missing",
            error=str(e),
            hint="Install: pip install pdf2image pytesseract; "
            "System: apt-get install tesseract-ocr poppler-utils",
        )
        return []
    except Exception as e:
        logger.error("pdf_ocr_failed", error=str(e))
        return []


def extract_text_sample(artifact, max_chars: int = 4000) -> str:
    """Extract a text sample from a raw artifact for classification.

    This is a lightweight extraction used by the pipeline's Discovery Agent
    to classify content before full parsing. Returns the first `max_chars`
    characters of extracted text.
    """
    content = _fetch_content_from_s3(artifact.s3_key)

    if artifact.content_type in ("text/html", "application/xhtml+xml"):
        soup = _make_soup(content)
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()
        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)
    elif artifact.content_type == "application/pdf":
        try:
            import io

            import pdfplumber

            text_parts = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages[:3]:  # First 3 pages only
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            text = "\n\n".join(text_parts)
        except Exception:
            text = content.decode("utf-8", errors="replace")
    else:
        text = content.decode("utf-8", errors="replace")

    return text[:max_chars]


def _fetch_content_from_s3(s3_key: str) -> bytes:
    """Fetch content from S3/MinIO, or read directly for local:// keys."""
    if s3_key.startswith("local://"):
        from pathlib import Path
        return Path(s3_key[len("local://"):]).read_bytes()

    import boto3

    from src.core.config import settings

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )

    response = s3.get_object(Bucket=settings.s3_bucket_raw, Key=s3_key)
    return response["Body"].read()
