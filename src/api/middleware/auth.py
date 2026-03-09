"""Authentication middleware shared between /internal/ and /v1/ routes.

/internal/ routes use session-based auth (for the review UI).
/v1/ routes use API key auth (header-based).
"""

import hashlib

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.engine import get_db
from src.db.models import ApiKey

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> ApiKey:
    """Validate an API key from the X-API-Key header."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    db_key = db.scalar(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    )

    if not db_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return db_key
