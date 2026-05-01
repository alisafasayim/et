"""http_retry sınırları & retry kararları."""

from unittest.mock import MagicMock

import pytest
import requests

from http_retry import RetryableHTTPError, raise_for_retry, with_retry


def _mock_response(status_code: int, text: str = "", headers: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.reason = ""
    resp.raise_for_status = MagicMock(
        side_effect=requests.HTTPError(f"{status_code} error") if status_code >= 400 else None
    )
    return resp


def test_raise_for_retry_passthrough_2xx():
    resp = _mock_response(200)
    raise_for_retry(resp)  # exception fırlatmamalı


def test_raise_for_retry_429_is_retryable():
    resp = _mock_response(429, "rate limit")
    with pytest.raises(RetryableHTTPError):
        raise_for_retry(resp)


def test_raise_for_retry_500_is_retryable():
    resp = _mock_response(503, "service unavailable")
    with pytest.raises(RetryableHTTPError):
        raise_for_retry(resp)


def test_raise_for_retry_4xx_is_terminal():
    resp = _mock_response(404, "not found")
    with pytest.raises(requests.HTTPError):
        raise_for_retry(resp)


def test_with_retry_eventually_succeeds_on_transient_5xx():
    call_count = {"n": 0}

    @with_retry(max_attempts=3, max_wait=0.01, multiplier=0.001)
    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RetryableHTTPError(503, "transient")
        return "ok"

    assert flaky() == "ok"
    assert call_count["n"] == 3


def test_with_retry_does_not_retry_on_4xx():
    call_count = {"n": 0}

    @with_retry(max_attempts=3, max_wait=0.01, multiplier=0.001)
    def hard_fail():
        call_count["n"] += 1
        raise requests.HTTPError("404")

    with pytest.raises(requests.HTTPError):
        hard_fail()
    assert call_count["n"] == 1  # retry yok
