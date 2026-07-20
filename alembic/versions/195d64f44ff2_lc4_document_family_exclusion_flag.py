"""LC-4a-lite: exclude a law from future extraction/triage runs.

Adds a per-law opt-out so a re-extraction pass can skip laws an analyst has
already verified, instead of re-spending LLM calls (and re-opening review
work) on laws nobody asked to touch again. Lives on DocumentFamily (the law
itself), not on any run-scoped table — this is a durable law-level setting,
not something tied to a particular extraction run. Consumed by
src/ingestion/extractor.py's run_triage()/run_extraction() passage-selection
queries; surfaced and toggled from the Law Card dashboard
(src/api/routes/law_card_routes.py, src/api/routes/law_card_api.py).

Revision ID: 195d64f44ff2
Revises: 4457bebc03c0
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "195d64f44ff2"
down_revision = "4457bebc03c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_families",
        sa.Column(
            "excluded_from_extraction", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("document_families", sa.Column("excluded_reason", sa.Text(), nullable=True))
    # D-6-style interim free-text identity, matching ExtractionFieldEdit.editor —
    # no session/auth model exists yet to attribute this to a real user account.
    op.add_column(
        "document_families", sa.Column("excluded_by", sa.String(200), nullable=True),
    )
    op.add_column(
        "document_families", sa.Column("excluded_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_families", "excluded_at")
    op.drop_column("document_families", "excluded_by")
    op.drop_column("document_families", "excluded_reason")
    op.drop_column("document_families", "excluded_from_extraction")
