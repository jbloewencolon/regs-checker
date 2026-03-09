"""Unit tests for the LegiScan connector and discovery module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.legiscan import (
    AI_SEARCH_TERMS,
    SUPPORTED_STATES,
    LegiScanClient,
    LegiScanError,
)


def test_supported_states_include_colorado():
    assert "CO" in SUPPORTED_STATES
    assert SUPPORTED_STATES["CO"]["name"] == "Colorado"


def test_supported_states_include_federal():
    assert "US" in SUPPORTED_STATES


def test_ai_search_terms_not_empty():
    assert len(AI_SEARCH_TERMS) > 0
    assert "artificial intelligence" in AI_SEARCH_TERMS


def test_legiscan_client_requires_api_key():
    with patch("src.ingestion.legiscan.settings") as mock_settings:
        mock_settings.legiscan_api_key = ""
        with pytest.raises(ValueError, match="LegiScan API key required"):
            LegiScanClient()


def test_legiscan_client_accepts_explicit_key():
    client = LegiScanClient(api_key="test-key-123")
    assert client.api_key == "test-key-123"


@patch("src.ingestion.legiscan.httpx.get")
def test_search_bills_parses_response(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "OK",
        "searchresult": {
            "summary": {"count": 2},
            "0": {"bill_id": 1234, "bill_number": "SB205", "title": "AI Act", "state": "CO"},
            "1": {"bill_id": 5678, "bill_number": "HB100", "title": "AI Safety", "state": "CO"},
        },
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    client = LegiScanClient(api_key="test-key")
    bills = client.search_bills("artificial intelligence", state="CO")

    assert len(bills) == 2
    assert bills[0]["bill_id"] == 1234
    assert bills[1]["bill_number"] == "HB100"


@patch("src.ingestion.legiscan.httpx.get")
def test_search_bills_handles_error(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "ERROR",
        "alert": {"message": "Invalid API key"},
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    client = LegiScanClient(api_key="bad-key")
    with pytest.raises(LegiScanError, match="Invalid API key"):
        client.search_bills("test")


@patch("src.ingestion.legiscan.httpx.get")
def test_get_bill_returns_bill_data(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "OK",
        "bill": {
            "bill_id": 1234,
            "title": "Colorado AI Act",
            "status": 6,
            "texts": [{"doc_id": 100, "type": "Enrolled"}],
            "history": [],
        },
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    client = LegiScanClient(api_key="test-key")
    bill = client.get_bill(1234)

    assert bill["bill_id"] == 1234
    assert bill["title"] == "Colorado AI Act"
    assert len(bill["texts"]) == 1
