"""Unit tests for _classify_llm_error — the error-type classifier that drives
dashboard color-coding and the provider-health badge.

Distinguishes provider-level failures (auth, quota) from model/output failures
so NVIDIA quota/credit exhaustion is legible in the UI rather than looking like
generic model noise.
"""
from __future__ import annotations

import json

from src.ingestion.extractor import _classify_429_detail, _classify_llm_error


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"HTTP {status_code}")
        self.response = _FakeResponse(status_code)


class TestClassifyByStatusCode:
    def test_401_is_auth_error(self):
        assert _classify_llm_error(_FakeHTTPError(401)) == "auth_error"

    def test_403_is_auth_error(self):
        assert _classify_llm_error(_FakeHTTPError(403)) == "auth_error"

    def test_429_is_quota_error(self):
        assert _classify_llm_error(_FakeHTTPError(429)) == "quota_error"

    def test_500_falls_through_to_message(self):
        # 500 has no special status mapping → classified by message text
        assert _classify_llm_error(_FakeHTTPError(500, "internal server error")) == "llm_error"


class TestClassifyByMessage:
    def test_nvidia_429_message(self):
        err = ValueError("NVIDIA rate/quota limit hit (429). Check credits.")
        assert _classify_llm_error(err) == "quota_error"

    def test_nvidia_auth_message(self):
        err = ValueError("NVIDIA auth/entitlement error (HTTP 401) — verify NVIDIA_API_KEY")
        assert _classify_llm_error(err) == "auth_error"

    def test_quota_keyword(self):
        assert _classify_llm_error(Exception("monthly quota exceeded")) == "quota_error"

    def test_rate_limit_keyword(self):
        assert _classify_llm_error(Exception("rate limit reached, retry later")) == "quota_error"

    def test_api_key_keyword_is_auth(self):
        assert _classify_llm_error(Exception("invalid api key provided")) == "auth_error"

    def test_timeout_message(self):
        assert _classify_llm_error(Exception("Request timed out after 300s")) == "timeout_error"

    def test_json_decode_is_validation(self):
        assert _classify_llm_error(json.JSONDecodeError("Expecting value", "", 0)) == "validation_error"

    def test_validation_keyword(self):
        assert _classify_llm_error(Exception("schema validation failed for field x")) == "validation_error"

    def test_generic_error_is_llm_error(self):
        assert _classify_llm_error(Exception("model returned something weird")) == "llm_error"

    def test_accepts_plain_string(self):
        assert _classify_llm_error("429 too many requests") == "quota_error"


class TestPrecedence:
    def test_status_code_wins_over_message(self):
        # 429 status but a message that also mentions json — status takes priority
        err = _FakeHTTPError(429, "could not parse json")
        assert _classify_llm_error(err) == "quota_error"


class TestClassify429Detail:
    """NIM-0b: the finer-grained transient-vs-allowance read carried
    alongside (not instead of) the existing "quota_error" bucket, so
    dashboard color-coding and the test contract above are unaffected."""

    def test_reads_classification_attribute_when_present(self):
        err = _FakeHTTPError(429, "rate limited")
        err.nvidia_429_classification = "rate_limited_transient"
        assert _classify_429_detail(err) == "rate_limited_transient"

    def test_none_when_attribute_absent(self):
        # Plain 429 error with no classification attached (e.g. the local
        # provider, which never goes through NVIDIA's 429 path).
        err = _FakeHTTPError(429, "rate limited")
        assert _classify_429_detail(err) is None

    def test_none_for_non_429_error(self):
        err = _FakeHTTPError(401, "unauthorized")
        assert _classify_429_detail(err) is None

    def test_none_for_plain_string(self):
        assert _classify_429_detail("429 too many requests") is None

    def test_does_not_change_quota_error_bucket(self):
        # The coarse dashboard-facing classification stays "quota_error"
        # regardless of the finer-grained detail.
        err = _FakeHTTPError(429, "allowance exhausted")
        err.nvidia_429_classification = "allowance_exhausted"
        assert _classify_llm_error(err) == "quota_error"
        assert _classify_429_detail(err) == "allowance_exhausted"
