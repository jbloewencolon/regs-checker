"""Document parser and normalizer.

Parses fetched raw artifacts into passage-level normalized source records.
Handles HTML (state legislation sites) and PDF (federal documents) formats.
"""

from __future__ import annotations

import hashlib
import re

import structlog
from bs4 import BeautifulSoup

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


def _parse_html(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse HTML legislative text into passages.

    Returns list of (section_path, text, char_start, char_end).
    """
    soup = BeautifulSoup(content, "lxml")

    # Remove script and style elements
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    # Extract text from main content
    body = soup.find("body") or soup
    full_text = body.get_text(separator="\n", strip=True)

    return _segment_text(full_text)


def _parse_plaintext(content: bytes) -> list[tuple[str, str, int, int]]:
    """Parse plain text into passages."""
    text = content.decode("utf-8", errors="replace")
    return _segment_text(text)


def _segment_text(text: str) -> list[tuple[str, str, int, int]]:
    """Segment legislative text into passage-level chunks.

    Uses section headers and paragraph boundaries as delimiters.
    Returns list of (section_path, text, char_start, char_end).
    """
    # Pattern for common legislative section markers
    section_pattern = re.compile(
        r"(?:^|\n)"
        r"((?:Section|SECTION|Sec\.|SEC\.)\s+\d+[\w.]*"
        r"|(?:Article|ARTICLE)\s+\w+"
        r"|(?:\(\w+\))\s)"
        r"(.*?)(?=\n(?:Section|SECTION|Sec\.|SEC\.|Article|ARTICLE|\(\w+\)\s)|\Z)",
        re.DOTALL,
    )

    matches = list(section_pattern.finditer(text))

    if not matches:
        # Fallback: split on double newlines
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
    """Fallback paragraph splitter."""
    paragraphs = re.split(r"\n\s*\n", text)
    passages = []
    offset = 0

    for para in paragraphs:
        para = para.strip()
        if len(para) < 10:
            offset += len(para) + 2
            continue

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

    Uses pdfplumber for text extraction, then segments with the standard
    legislative text segmenter.
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
        if not full_text.strip():
            logger.warning("pdf_no_text_extracted")
            return []

        return _segment_text(full_text)

    except ImportError:
        logger.warning("pdfplumber_not_installed, falling back to plaintext")
        return _parse_plaintext(content)
    except Exception as e:
        logger.error("pdf_parse_error", error=str(e))
        return _parse_plaintext(content)


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
