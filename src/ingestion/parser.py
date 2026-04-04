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


def parse_and_normalize(
    db,
    job: IngestionJob,
    artifact: RawArtifact,
) -> list[NormalizedSourceRecord]:
    """Parse a raw artifact into passage-level normalized records.

    Handles HTML and plain text. PDF support to be added.
    """
    content = _fetch_content_from_s3(artifact.s3_key)

    if artifact.content_type in ("text/html", "application/xhtml+xml"):
        passages = _parse_html(content)
    elif artifact.content_type in ("application/pdf",):
        passages = _parse_pdf(content)
    elif artifact.content_type in ("text/plain",):
        passages = _parse_plaintext(content)
    else:
        logger.warning("unsupported_content_type", content_type=artifact.content_type)
        passages = _parse_plaintext(content)

    records = []
    for ordinal, (section_path, text, start, end) in enumerate(passages):
        text_hash = hashlib.sha256(text.encode()).hexdigest()

        record = NormalizedSourceRecord(
            document_version_id=job.document_version_id,
            section_path=section_path,
            ordinal=ordinal,
            text_content=text,
            text_hash=text_hash,
            char_offset_start=start,
            char_offset_end=end,
        )
        db.add(record)
        records.append(record)

    db.flush()
    logger.info("parsed_document", artifact_id=artifact.id, passages=len(records))
    return records


def _make_soup(content: bytes) -> BeautifulSoup:
    """Create a BeautifulSoup instance, using the XML parser when appropriate."""
    if content.lstrip()[:100].startswith(b"<?xml"):
        try:
            return BeautifulSoup(content, "lxml-xml")
        except Exception:
            pass
    return BeautifulSoup(content, "lxml")


def _parse_html(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse HTML legislative text into passages.

    Returns list of (section_path, text, char_start, char_end).
    """
    soup = _make_soup(content)

    # Remove script and style elements
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    # Extract text from main content.
    # Use "\n\n" separator so each block element (p, div, li, h1-h6, etc.)
    # creates a double-newline paragraph break.  With "\n" the entire document
    # collapses into single-newline-separated text, making the paragraph
    # fallback splitter treat 100KB of text as a single passage.
    body = soup.find("body") or soup
    full_text = body.get_text(separator="\n\n", strip=True)

    return _segment_text(full_text)


def _parse_plaintext(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse plain text into passages."""
    text = content.decode("utf-8", errors="replace")
    return _segment_text(text)


def _segment_text(text: str) -> list[tuple[str, str, int, int]]:
    """Segment legislative text into passage-level chunks.

    Strategy:
      1. Try section-header splitting (Section X, Article Y, § Z, etc.)
      2. Fall back to paragraph splitting on double-newlines
      3. If paragraphs are too large (>5000 chars), split them further

    Returns list of (section_path, text, char_start, char_end).
    """
    # Pattern for common legislative section markers — covers:
    #   Section 1, SECTION 1, Sec. 1, SEC. 1, § 1, §1
    #   Article I, ARTICLE 1
    #   Chapter 1, CHAPTER 1, Part 1, PART 1, Title 1, TITLE 1
    #   Rule 1, RULE 1
    #   (a), (b), (1), (2), (i), (ii)
    section_pattern = re.compile(
        r"(?:^|\n\n?)"
        r"((?:Section|SECTION|Sec\.|SEC\.)\s+\d+[\w.\-]*"
        r"|§\s*\d+[\w.\-]*"
        r"|(?:Article|ARTICLE)\s+\w+"
        r"|(?:Chapter|CHAPTER|Part|PART|Title|TITLE|Rule|RULE)\s+\d+[\w.\-]*"
        r"|(?:\(\w+\))\s)"
        r"(.*?)(?=\n\n?(?:Section|SECTION|Sec\.|SEC\.|§\s*\d|Article|ARTICLE"
        r"|Chapter|CHAPTER|Part|PART|Title|TITLE|Rule|RULE|\(\w+\)\s)|\Z)",
        re.DOTALL,
    )

    matches = list(section_pattern.finditer(text))

    if not matches:
        return _split_on_paragraphs(text)

    passages = []
    for match in matches:
        section_marker = match.group(1).strip()
        passage_text = (match.group(1) + match.group(2)).strip()

        if len(passage_text) < 10:
            continue

        passages.append((
            section_marker,
            passage_text,
            match.start(),
            match.end(),
        ))

    return passages if passages else _split_on_paragraphs(text)


def _split_on_paragraphs(text: str) -> list[tuple[str, str, int, int]]:
    """Fallback paragraph splitter.

    Splits on double-newlines first, then sub-splits any oversized chunks
    on single newlines to avoid giant single-passage documents.
    """
    paragraphs = re.split(r"\n\s*\n", text)
    passages = []
    offset = 0

    for para in paragraphs:
        para = para.strip()
        if len(para) < 10:
            offset += len(para) + 2
            continue

        # Sub-split oversized paragraphs on single newlines
        if len(para) > 5000:
            sub_parts = para.split("\n")
            sub_offset = offset
            for sub in sub_parts:
                sub = sub.strip()
                if len(sub) < 10:
                    sub_offset += len(sub) + 1
                    continue
                passages.append((
                    f"Paragraph {len(passages) + 1}",
                    sub,
                    sub_offset,
                    sub_offset + len(sub),
                ))
                sub_offset += len(sub) + 1
        else:
            passages.append((
                f"Paragraph {len(passages) + 1}",
                para,
                offset,
                offset + len(para),
            ))
        offset += len(para) + 2

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
        from pdf2image import convert_from_bytes
        import pytesseract

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
    """Fetch content from S3/MinIO."""
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
