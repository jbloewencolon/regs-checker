# Authentication & Trust Boundary — Regs Checker API

**Status:** RR3d documentation (2026-07-06)

This document describes the current authentication posture of the Regs Checker
API and the intended deployment trust boundary.

## Current Architecture

The application exposes three distinct route groups:

### 1. `/dashboard/` — Pipeline Dashboard (No Authentication)

- **Purpose:** Real-time extraction pipeline monitoring and control
- **Access:** Unauthenticated (no X-API-Key required)
- **Capabilities:** 
  - View extraction progress, statistics, and breakdowns
  - Trigger pipeline steps (triage, extraction, verification)
  - Access review queue (read-only via HTML)
  - Download exports (low-confidence extractions, etc.)
  - Manage triage and pipeline state

**Data Exposed:**
- Full extraction payloads and confidence scores
- Evidence spans and source passages
- Document metadata and jurisdiction information
- Pipeline execution state and statistics

### 2. `/internal/` — Review & Control API (No Authentication)

- **Purpose:** Human-in-the-loop quality assurance and pipeline control
- **Access:** Unauthenticated (no X-API-Key required)
- **Capabilities:**
  - JSON API for review workflow (list queue, approve/reject extractions)
  - Create/update review actions with immutable audit logs
  - Trigger verification runs, concept grouping, and re-extraction
  - Access extraction metadata and payload details
  - Receive analysis and diagnostics

**Data Exposed:**
- Complete extraction records with full payloads
- Review queue items and decisions
- Extraction attempt history and pipeline events
- Confidence breakdowns and verification results

### 3. `/v1/` — Product API (API Key Required)

- **Purpose:** External access to finalized, published extractions
- **Access:** Requires valid X-API-Key header
- **Capabilities:**
  - Query synced extractions intended for Policy Navigator
  - Paginated retrieval of extraction batches
  - Filtered queries by jurisdiction, law, extraction type

**Data Exposed:**
- Published extractions only (synced_extractions table)
- Subset of fields (payload_summary, confidence tier, review status for transparency)
- No internal pipeline state

## Intended Deployment Boundary

### Current Use Case: Localhost Analyst Tool ✅

The absence of authentication on `/dashboard/` and `/internal/` is **appropriate**
and **intentional** for the current deployment pattern:

- **Target Users:** RC (regulatory compliance) team members running locally via
  `python start.py` on their own machine per CLAUDE.md
- **Network Isolation:** Bound to `127.0.0.1` (localhost only) by default
- **Trust Model:** All users accessing the tool are trusted team members with
  full access to the underlying database
- **Risk Profile:** No external exposure; only internal pipeline state

### Future Deployment: Requires Authentication ⚠️

If the application is ever deployed:
- **Behind a shared proxy or LAN:** Implement session-based auth for `/dashboard/`
  and `/internal/` (e.g., HTTP Basic Auth, OAuth2, or similar)
- **On the public internet:** Implement strong authentication + authorization,
  rate limiting, and audit logging for all route groups
- **With restricted scopes:** Add role-based access control (e.g., analyst can
  review, admin can trigger runs, viewer can only query `/v1/`)

## Security Considerations

### No Authentication Required
- **Dashboard + Internal:** These are intended for **internal use only** with
  high trust (direct team access to source databases)
- **Assumption:** Network is private (localhost or corporate LAN with firewall)
- **NOT suitable for:** Untrusted users, shared hosting, public internet

### API Key Auth (Required for /v1/)
- **Strength:** SHA256-hashed keys in database, per-request validation
- **Model:** Prevents casual unauthorized access; suitable for partner integration
- **Not suitable for:** High-security requirements (consider OAuth2 + TLS mutual
  auth for sensitive deployments)

### Audit & Accountability
- **Dashboard actions:** Logged via pipeline_events table (operator runs, step
  triggers)
- **Review actions:** Logged via ReviewAction table (who approved/rejected what)
- **API access:** Implicit via API key (X-API-Key), but not per-user unless auth
  layer is added

## Recommendations (If Deployed Beyond Localhost)

1. **Add authentication layer** before any external deployment:
   - Session-based (cookies + CSRF protection) for browser UI (`/dashboard/`)
   - Bearer token (OAuth2 or similar) for `/internal/` API
   - Rotate and audit API keys for `/v1/`

2. **Implement authorization:**
   - Role-based access control (analyst, admin, viewer, editor)
   - Scope limits for each role (e.g., analysts can't trigger full extraction runs)
   - Data visibility filters (e.g., only users' own jurisdiction extractions)

3. **Enable audit logging:**
   - Per-user action tracking (who did what, when)
   - Immutable audit trail for compliance
   - Regular audit reviews

4. **Add operational security:**
   - Rate limiting (prevent brute-force key enumeration on `/v1/`)
   - TLS/HTTPS (protect keys in transit)
   - Secrets management for API keys (rotate periodically)

## Code References

- **Route registration:** `src/api/app.py` (lines 123–130)
  - Dashboard: no `dependencies=` parameter (unauthenticated)
  - Internal: no `dependencies=` parameter (unauthenticated)
  - V1: `dependencies=[Depends(verify_api_key)]` (API key required)

- **Auth middleware:** `src/api/middleware/auth.py`
  - `verify_api_key()`: validates X-API-Key header against db.api_keys.key_hash

- **Data models:** 
  - `src/db/models.ApiKey` — API key storage (hashed)
  - `src/db.models.ReviewAction` — Immutable review audit trail
  - `src/db.models.PipelineEvent` — Extraction pipeline event log
