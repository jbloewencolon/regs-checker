"""Source connectors for fetching legislative documents.

Connectors:
  - Colorado General Assembly (SB205 and related AI legislation)
  - Federal (NIST AI RMF / Executive Orders)
  - Orrick Tracker (state legislature bill links — PDF and HTML)

The OrrickTrackerConnector handles the real-world messiness of fetching
from 50 different state legislature websites:
  - Browser-like User-Agent to avoid scraper detection (403s)
  - SSL verification bypass for states with expired certs (CT, HI)
  - Retries with backoff for transient failures
  - Alternative URL mapping for known-blocked domains
"""

from __future__ import annotations

import hashlib
import ssl
import time
from datetime import datetime
from urllib.parse import urlparse

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


# ---------------------------------------------------------------------------
# Browser-like headers to avoid scraper detection (fixes 403s on nysenate,
# legiscan, ncleg, etc.)
# ---------------------------------------------------------------------------
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ---------------------------------------------------------------------------
# Domains with known SSL certificate problems — skip verification
# ---------------------------------------------------------------------------
_SSL_BYPASS_DOMAINS = {
    "cga.ct.gov",                # Connecticut General Assembly
    "www.cga.ct.gov",
    "capitol.hawaii.gov",        # Hawaii State Legislature
    "www.capitol.hawaii.gov",
    "legislation.nysenate.gov",  # NY Open Legislation — drops SSL connections
    "billstatus.ls.state.ms.us", # Mississippi Bill Status System
    "index.ls.state.ms.us",      # Mississippi MLIS
}

# ---------------------------------------------------------------------------
# Alternative URLs for domains that actively block scrapers or are dead.
# Maps domain → replacement function(original_url) → new_url.
# These are public mirrors or open-data APIs for the same content.
# ---------------------------------------------------------------------------
_ALTERNATIVE_URL_RULES: dict[str, str] = {
    # NY Senate blocks non-browser requests → use Open Legislation API
    "www.nysenate.gov": "legislation.nysenate.gov",
    "nysenate.gov": "legislation.nysenate.gov",
    # NJ pub server is down → use njleg.state.nj.us mirror
    "pub.njleg.state.nj.us": "www.njleg.state.nj.us",
    # Maryland casetext removed content → official GA site
    "casetext.com": "mgaleg.maryland.gov",
    "www.casetext.com": "mgaleg.maryland.gov",
}


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


@register_connector("pdf_tracker")
@register_connector("orrick_tracker")
class OrrickTrackerConnector(BaseConnector):
    """Connector for bills discovered via the Orrick AI Law Tracker.

    Hardened for real-world state legislature sites:
      - Browser User-Agent to avoid 403 scraper detection
      - SSL bypass for states with expired certs
      - Retry with exponential backoff (2 attempts)
      - Alternative URL rewriting for known-blocked domains
    """

    max_retries: int = 2
    base_timeout: float = 60.0

    def fetch(self, url: str) -> tuple[bytes, str]:
        url = self._rewrite_url(url)
        verify = self._should_verify_ssl(url)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.get(
                    url,
                    follow_redirects=True,
                    timeout=self.base_timeout,
                    headers=_BROWSER_HEADERS,
                    verify=verify,
                )
                response.raise_for_status()
                content_type = response.headers.get(
                    "content-type", "text/html"
                ).split(";")[0]
                return response.content, content_type

            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                # Don't retry 404/410 — the resource is genuinely gone
                if status in (404, 410):
                    raise
                # Retry on 403 (scraper detection) and 5xx (server errors)
                if attempt < self.max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "fetch_retry",
                        url=url,
                        status=status,
                        attempt=attempt + 1,
                        wait=wait,
                    )
                    time.sleep(wait)
                else:
                    raise

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "fetch_retry",
                        url=url,
                        error=type(e).__name__,
                        attempt=attempt + 1,
                        wait=wait,
                    )
                    time.sleep(wait)
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    def _should_verify_ssl(self, url: str) -> bool:
        """Check if SSL verification should be skipped for this URL."""
        hostname = urlparse(url).hostname or ""
        return hostname not in _SSL_BYPASS_DOMAINS

    def _rewrite_url(self, url: str) -> str:
        """Rewrite URL if the domain has a known alternative."""
        hostname = urlparse(url).hostname or ""
        if hostname in _ALTERNATIVE_URL_RULES:
            new_host = _ALTERNATIVE_URL_RULES[hostname]
            new_url = url.replace(hostname, new_host, 1)
            logger.info("url_rewritten", original=url, rewritten=new_url)
            return new_url
        return url


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

    # Fetch
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
