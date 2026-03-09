"""Source connectors for fetching legislative documents.

Recommendation #9: Start with 2 jurisdictions (Colorado + one federal source),
not 5. Add California as third jurisdiction after full pipeline validation.

Connectors:
  - Colorado General Assembly (SB205 and related AI legislation)
  - Federal (NIST AI RMF / Executive Orders)
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import httpx
import structlog

from src.core.config import settings
from src.db.models import IngestionJob, IngestionStatus, RawArtifact

logger = structlog.get_logger()

# Connector registry
CONNECTORS: dict[str, type["BaseConnector"]] = {}


def register_connector(connector_id: str):
    """Decorator to register a source connector."""
    def decorator(cls):
        CONNECTORS[connector_id] = cls
        return cls
    return decorator


class BaseConnector:
    """Base class for source connectors."""

    def fetch(self, url: str) -> tuple[bytes, str]:
        """Fetch content from URL. Returns (content_bytes, content_type)."""
        raise NotImplementedError


@register_connector("colorado_ga")
class ColoradoConnector(BaseConnector):
    """Connector for Colorado General Assembly legislative documents."""

    def fetch(self, url: str) -> tuple[bytes, str]:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/html").split(";")[0]
        return response.content, content_type


@register_connector("federal_nist")
class FederalNISTConnector(BaseConnector):
    """Connector for federal NIST AI RMF and executive order documents."""

    def fetch(self, url: str) -> tuple[bytes, str]:
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/html").split(";")[0]
        return response.content, content_type


@register_connector("legiscan")
class LegiScanConnector(BaseConnector):
    """Connector that fetches bill text via the LegiScan API.

    Falls back to direct URL fetch if no LegiScan doc_id is available.
    """

    def fetch(self, url: str) -> tuple[bytes, str]:
        # Try direct URL fetch first (state_link from LegiScan metadata)
        response = httpx.get(url, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "text/html").split(";")[0]
        return response.content, content_type

    def fetch_via_api(self, doc_id: int) -> tuple[bytes, str]:
        """Fetch bill text directly via LegiScan API (base64-decoded)."""
        from src.ingestion.legiscan import LegiScanClient

        client = LegiScanClient()
        return client.get_bill_text(doc_id)


def fetch_document(db, job: IngestionJob) -> RawArtifact:
    """Fetch a document and store it as a content-addressable raw artifact.

    Uses SHA-256 hashing for deduplication. Stores content in S3/MinIO.
    """
    url = job.fetch_url
    if not url:
        raise ValueError(f"No fetch URL for ingestion job {job.id}")

    # Determine connector
    source = job.document_version.family.source
    connector_id = source.connector_id or "colorado_ga"
    connector_cls = CONNECTORS.get(connector_id, ColoradoConnector)
    connector = connector_cls()

    # Fetch — use LegiScan API if doc_id available, else direct URL
    legiscan_doc_id = (job.metadata_ or {}).get("legiscan_doc_id")
    if connector_id == "legiscan" and legiscan_doc_id and isinstance(connector, LegiScanConnector):
        try:
            content_bytes, content_type = connector.fetch_via_api(legiscan_doc_id)
        except Exception:
            logger.info("legiscan_api_fallback_to_url", url=url)
            content_bytes, content_type = connector.fetch(url)
    else:
        content_bytes, content_type = connector.fetch(url)

    # Content-addressable storage
    sha256 = hashlib.sha256(content_bytes).hexdigest()

    # Check for existing artifact with same hash (dedup)
    existing = db.query(RawArtifact).filter_by(sha256_hash=sha256).first()
    if existing:
        logger.info("artifact_deduplicated", sha256=sha256[:12])
        return existing

    # Store in S3
    s3_key = f"raw/{source.jurisdiction_code}/{sha256}"
    _upload_to_s3(s3_key, content_bytes, content_type)

    # Create artifact record
    artifact = RawArtifact(
        document_version_id=job.document_version_id,
        sha256_hash=sha256,
        s3_key=s3_key,
        content_type=content_type,
        size_bytes=len(content_bytes),
        is_primary=True,
    )
    db.add(artifact)
    db.flush()

    logger.info(
        "artifact_stored",
        sha256=sha256[:12],
        size_bytes=len(content_bytes),
        content_type=content_type,
    )
    return artifact


def _upload_to_s3(key: str, content: bytes, content_type: str) -> None:
    """Upload content to S3/MinIO."""
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )

    s3.put_object(
        Bucket=settings.s3_bucket_raw,
        Key=key,
        Body=content,
        ContentType=content_type,
    )
