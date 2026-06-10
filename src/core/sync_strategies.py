"""Supabase sync bottleneck avoidance strategies.

This module documents and implements strategies for reducing coupling to
Supabase as the sole sync target for Policy Navigator. The current architecture
has a one-directional sync (regs-checker → Supabase → Policy Navigator) that
creates several bottlenecks:

CURRENT BOTTLENECKS
-------------------
1. Schema coupling: payload_adapter.py flattens rich extraction data into
   Policy Navigator's simpler schema, losing information on every sync.
2. No conflict resolution: if Policy Navigator data diverges (manual edits,
   parallel imports), there's no merge strategy.
3. Single point of failure: Supabase outage blocks all downstream consumers.
4. Bridge row dependency: every new document family needs a manual bridge row
   in the target DB before extractions can sync.

RECOMMENDED STRATEGIES
----------------------

Strategy 1: Event-Driven Sync via Webhook/Queue
    Replace polling-based sync with an event queue (e.g., Supabase Realtime,
    or a lightweight message queue like Redis Streams / SQS).

    Benefits:
    - Decouples regs-checker from Supabase's availability
    - Enables multiple consumers (Policy Navigator, analytics dashboard, etc.)
    - Provides natural retry/dead-letter semantics

    Implementation:
    - Publish ExtractionCreated / ExtractionApproved events to a queue
    - Policy Navigator subscribes and applies changes at its own pace
    - Failed deliveries go to a dead-letter queue for inspection

Strategy 2: Versioned API Contract
    Instead of direct DB writes to Supabase, expose extractions via the
    existing /v1/ API and have Policy Navigator pull on its own schedule.

    Benefits:
    - regs-checker owns its schema; Policy Navigator owns its schema
    - API versioning prevents breaking changes
    - Standard HTTP caching reduces load

    Implementation:
    - Policy Navigator calls GET /v1/changes?since=<last_sync_timestamp>
    - Transforms responses into its own schema client-side
    - Maintains its own sync cursor / watermark

Strategy 3: Dual-Write with Reconciliation
    Write to both local PostgreSQL and Supabase, with periodic reconciliation
    to detect and resolve drift.

    Benefits:
    - Local DB is always authoritative (survives Supabase outages)
    - Reconciliation catches silent sync failures

    Implementation:
    - sync_to_supabase.py writes with idempotent upserts
    - Nightly reconciliation job compares row counts and checksums
    - Alert on divergence > threshold

Strategy 4: Export-Based Decoupling (Recommended Near-Term)
    Use CSV/JSON export as the interchange format instead of live DB sync.
    This is the simplest strategy and eliminates the bridge row problem.

    Benefits:
    - Zero runtime coupling between systems
    - Human-reviewable interchange format
    - Works with any downstream system, not just Supabase

    Implementation:
    - Scheduled export job produces versioned JSON/CSV snapshots
    - Policy Navigator imports snapshots on its own schedule
    - Bridge rows become a Policy Navigator concern, not regs-checker's

Strategy 5: GraphQL Federation (Long-Term)
    If Policy Navigator adopts a GraphQL layer (Supabase supports this),
    regs-checker can expose its data as a federated subgraph.

    Benefits:
    - Single query can span both systems
    - Schema evolution is managed per-subgraph
    - No data duplication

    Implementation:
    - Requires GraphQL adoption on both sides
    - Highest implementation cost but best long-term architecture

RECOMMENDED MIGRATION PATH
---------------------------
Phase 1 (Now):    Strategy 4 — CSV/JSON export snapshots
Phase 2 (3-6mo):  Strategy 2 — Versioned API contract
Phase 3 (6-12mo): Strategy 1 — Event-driven sync if scale demands it
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SyncStrategy(str, Enum):
    """Available sync strategies."""

    direct_db = "direct_db"           # Current: direct Supabase writes
    export_snapshot = "export_snapshot"  # Phase 1: file-based interchange
    api_pull = "api_pull"             # Phase 2: API-driven sync
    event_queue = "event_queue"       # Phase 3: message queue
    dual_write = "dual_write"         # Alternative: write-both


@dataclass
class SyncConfig:
    """Configuration for sync strategy selection."""

    strategy: SyncStrategy = SyncStrategy.direct_db
    # For export_snapshot strategy
    export_format: str = "json"  # json or csv
    export_dir: str = "export/snapshots"
    # For api_pull strategy
    api_base_url: str | None = None
    # For event_queue strategy
    queue_url: str | None = None


def get_sync_strategy_recommendation(
    extraction_count: int,
    consumer_count: int = 1,
    supabase_available: bool = True,
) -> SyncStrategy:
    """Recommend a sync strategy based on current scale and constraints.

    Args:
        extraction_count: Total extractions to sync.
        consumer_count: Number of downstream consumers.
        supabase_available: Whether Supabase is reachable.

    Returns:
        Recommended SyncStrategy.
    """
    if not supabase_available:
        return SyncStrategy.export_snapshot

    if consumer_count > 1:
        return SyncStrategy.event_queue

    if extraction_count > 100_000:
        return SyncStrategy.api_pull

    return SyncStrategy.direct_db
