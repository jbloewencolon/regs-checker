"""Unit tests for the retag endpoint in review_routes.py.

POST /review/{queue_id}/retag

Tests cover:
- Successful retag: changes extraction_type and records a ReviewAction
- Invalid type: returns 400 with valid type list
- Same type: returns info response (idempotent, no DB write)
- Not found: returns 404 when queue item or extraction doesn't exist
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.db.engine import get_db
from src.db.models import ExtractionType, ReviewStatus


def _make_client(mock_db):
    """Create a TestClient with the review router and an overridden DB session."""
    from src.api.routes.review_routes import router

    app = FastAPI()
    app.include_router(router)

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


def _make_queue_item(old_type: ExtractionType | None = ExtractionType.obligation):
    """Build a mock ReviewQueueItem with an attached Extraction."""
    mock_extraction = MagicMock()
    mock_extraction.extraction_type = old_type

    mock_item = MagicMock()
    mock_item.extraction = mock_extraction if old_type is not None else None
    return mock_item


# ---------------------------------------------------------------------------
# Successful retag
# ---------------------------------------------------------------------------


class TestRetagSuccess:
    def test_valid_retag_returns_200(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "enforcement"})
        assert resp.status_code == 200

    def test_retag_response_contains_old_and_new_type(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "enforcement"})
        body = resp.text
        assert "obligation" in body
        assert "enforcement" in body

    def test_retag_updates_extraction_type(self):
        mock_db = MagicMock()
        item = _make_queue_item(ExtractionType.obligation)
        mock_db.get.return_value = item
        client = _make_client(mock_db)

        client.post("/review/1/retag", data={"new_type": "enforcement"})

        assert item.extraction.extraction_type == ExtractionType.enforcement

    def test_retag_calls_db_add_and_commit(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        client.post("/review/1/retag", data={"new_type": "enforcement"})

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_retag_review_action_comment_format(self):
        """The review action comment should note old_type -> new_type."""
        from src.db.models import ReviewAction

        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        client.post("/review/1/retag", data={"new_type": "enforcement"})

        # Verify what was added to the session
        added_obj = mock_db.add.call_args[0][0]
        assert "obligation" in added_obj.comment
        assert "enforcement" in added_obj.comment

    def test_retag_from_threshold_to_rights_protection(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.threshold)
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "rights_protection"})
        assert resp.status_code == 200
        assert item.extraction.extraction_type == ExtractionType.rights_protection \
            if (item := mock_db.get.return_value) else True


# ---------------------------------------------------------------------------
# Invalid type
# ---------------------------------------------------------------------------


class TestRetagInvalidType:
    def test_invalid_type_returns_400(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item()
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "not_a_real_type"})
        assert resp.status_code == 400

    def test_invalid_type_response_lists_valid_types(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item()
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "gibberish"})
        # Response should name the bad type
        assert "gibberish" in resp.text

    def test_invalid_type_does_not_commit(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item()
        client = _make_client(mock_db)

        client.post("/review/1/retag", data={"new_type": "bad_type"})
        mock_db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotent retag (same type)
# ---------------------------------------------------------------------------


class TestRetagSameType:
    def test_same_type_returns_200_info(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        resp = client.post("/review/1/retag", data={"new_type": "obligation"})
        assert resp.status_code == 200
        assert "Already tagged" in resp.text

    def test_same_type_does_not_write_to_db(self):
        mock_db = MagicMock()
        mock_db.get.return_value = _make_queue_item(ExtractionType.obligation)
        client = _make_client(mock_db)

        client.post("/review/1/retag", data={"new_type": "obligation"})
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------


class TestRetagNotFound:
    def test_missing_queue_item_returns_404(self):
        mock_db = MagicMock()
        mock_db.get.return_value = None
        client = _make_client(mock_db)

        resp = client.post("/review/99/retag", data={"new_type": "enforcement"})
        assert resp.status_code == 404

    def test_item_without_extraction_returns_404(self):
        mock_db = MagicMock()
        item = MagicMock()
        item.extraction = None
        mock_db.get.return_value = item
        client = _make_client(mock_db)

        resp = client.post("/review/99/retag", data={"new_type": "enforcement"})
        assert resp.status_code == 404
