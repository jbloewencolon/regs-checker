"""Unit tests for the hardened OrrickTrackerConnector and fetch_document."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.ingestion.connector import (
    OrrickTrackerConnector,
    _ALTERNATIVE_URL_RULES,
    _BROWSER_HEADERS,
    _SSL_BYPASS_DOMAINS,
)


class TestSSLBypassDomains:
    def test_connecticut_domains(self):
        assert "cga.ct.gov" in _SSL_BYPASS_DOMAINS
        assert "www.cga.ct.gov" in _SSL_BYPASS_DOMAINS

    def test_hawaii_domains(self):
        assert "capitol.hawaii.gov" in _SSL_BYPASS_DOMAINS

    def test_ny_legislation_domain(self):
        assert "legislation.nysenate.gov" in _SSL_BYPASS_DOMAINS

    def test_mississippi_domains(self):
        assert "billstatus.ls.state.ms.us" in _SSL_BYPASS_DOMAINS


class TestAlternativeURLRules:
    def test_ny_senate_rewrite(self):
        assert "www.nysenate.gov" in _ALTERNATIVE_URL_RULES
        assert _ALTERNATIVE_URL_RULES["www.nysenate.gov"] == "legislation.nysenate.gov"

    def test_nj_rewrite(self):
        assert "pub.njleg.state.nj.us" in _ALTERNATIVE_URL_RULES

    def test_md_casetext_rewrite(self):
        assert "casetext.com" in _ALTERNATIVE_URL_RULES
        assert _ALTERNATIVE_URL_RULES["casetext.com"] == "mgaleg.maryland.gov"


class TestBrowserHeaders:
    def test_has_user_agent(self):
        assert "User-Agent" in _BROWSER_HEADERS
        assert "Mozilla" in _BROWSER_HEADERS["User-Agent"]

    def test_has_accept(self):
        assert "Accept" in _BROWSER_HEADERS


class TestOrrickTrackerConnector:
    def setup_method(self):
        self.connector = OrrickTrackerConnector()

    def test_should_verify_ssl_normal_domain(self):
        assert self.connector._should_verify_ssl("https://leg.colorado.gov/bills") is True

    def test_should_not_verify_ssl_ct(self):
        assert self.connector._should_verify_ssl("https://cga.ct.gov/2024/act/Pa/pdf") is False

    def test_should_not_verify_ssl_hawaii(self):
        assert self.connector._should_verify_ssl("https://capitol.hawaii.gov/measure") is False

    def test_should_not_verify_ssl_ny_legislation(self):
        assert self.connector._should_verify_ssl(
            "https://legislation.nysenate.gov/api/3/bills/2023/S7543"
        ) is False

    def test_should_not_verify_ssl_mississippi(self):
        assert self.connector._should_verify_ssl(
            "https://billstatus.ls.state.ms.us/2024/pdf/history/SB/SB2158.xml"
        ) is False

    def test_rewrite_url_ny_senate(self):
        original = "https://www.nysenate.gov/legislation/bills/2023/S7543"
        rewritten = self.connector._rewrite_url(original)
        assert "legislation.nysenate.gov" in rewritten
        assert "www.nysenate.gov" not in rewritten

    def test_rewrite_url_no_match(self):
        url = "https://leg.colorado.gov/bills/sb24-205"
        assert self.connector._rewrite_url(url) == url

    def test_rewrite_url_nj(self):
        original = "https://pub.njleg.state.nj.us/Bills/2024/AL24/116_.PDF"
        rewritten = self.connector._rewrite_url(original)
        assert "www.njleg.state.nj.us" in rewritten

    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/pdf; charset=utf-8"}
        mock_response.content = b"%PDF-1.4 fake content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        content, ct = self.connector.fetch("https://example.com/bill.pdf")

        assert content == b"%PDF-1.4 fake content"
        assert ct == "application/pdf"
        # Verify browser headers were sent
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"] == _BROWSER_HEADERS

    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_404_not_retried(self, mock_get):
        """404s should raise immediately without retry."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_response
        )
        mock_get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            self.connector.fetch("https://example.com/gone")

        assert mock_get.call_count == 1  # No retry

    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_410_not_retried(self, mock_get):
        """410 Gone should raise immediately without retry."""
        mock_response = MagicMock()
        mock_response.status_code = 410
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "410", request=MagicMock(), response=mock_response
        )
        mock_get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            self.connector.fetch("https://casetext.com/statute/removed")

        assert mock_get.call_count == 1

    @patch("src.ingestion.connector.time.sleep")
    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_403_retried_then_succeeds(self, mock_get, mock_sleep):
        """403 should be retried, and succeed on second attempt."""
        fail_response = MagicMock()
        fail_response.status_code = 403
        fail_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=fail_response
        )

        success_response = MagicMock()
        success_response.headers = {"content-type": "text/html"}
        success_response.content = b"<html>bill text</html>"
        success_response.raise_for_status = MagicMock()

        mock_get.side_effect = [fail_response, success_response]

        content, ct = self.connector.fetch("https://nysenate.gov/bill")

        assert content == b"<html>bill text</html>"
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(2)  # 2^(0+1) = 2

    @patch("src.ingestion.connector.time.sleep")
    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_timeout_retried(self, mock_get, mock_sleep):
        """Timeouts should be retried."""
        mock_get.side_effect = [
            httpx.TimeoutException("timed out"),
            httpx.TimeoutException("timed out again"),
            httpx.TimeoutException("still timed out"),
        ]

        with pytest.raises(httpx.TimeoutException):
            self.connector.fetch("https://slow-site.gov/bill.pdf")

        assert mock_get.call_count == 3  # 1 initial + 2 retries
        assert mock_sleep.call_count == 2

    @patch("src.ingestion.connector.httpx.get")
    def test_fetch_ssl_bypass_for_ct(self, mock_get):
        """Connecticut URLs should be fetched with verify=False."""
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = b"<html>CT law</html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        self.connector.fetch("https://cga.ct.gov/2024/act/Pa/pdf/2024PA-00020-R00SB-00002-PA.PDF")

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["verify"] is False
