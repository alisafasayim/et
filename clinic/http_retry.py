"""
Klinik Sistemi — Paylaşılan HTTP Retry/Backoff Helper

Notion, Evolution, Paraşüt ve diğer upstream API'lere yapılan
HTTP isteklerini tek bir politikayla sertleştirir.

Yeniden denenir:
  - requests.ConnectionError, requests.Timeout
  - HTTP 429 (rate limit)
  - HTTP 5xx (sunucu hatası)

Yeniden denenmez:
  - HTTP 4xx (429 hariç) — kalıcı hata, retry boşa çabadır
  - JSON decode / validation hataları — caller halletmeli

Kullanım:
    from http_retry import with_retry, raise_for_retry

    @with_retry()
    def _notion_post(endpoint, payload):
        resp = requests.post(url, headers=..., json=payload, timeout=30)
        raise_for_retry(resp)
        return resp.json()
"""

import logging
import time
from typing import Callable, TypeVar

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("http_retry")

T = TypeVar("T")


class RetryableHTTPError(Exception):
    """5xx veya 429 — bir sonraki denemede başarılı olabilir."""

    def __init__(self, status_code: int, message: str, retry_after: float = 0.0) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.retry_after = retry_after


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, RetryableHTTPError):
        return True
    return False


def raise_for_retry(response: requests.Response) -> None:
    """
    response.raise_for_status() yerine kullan; 429 ve 5xx'i
    RetryableHTTPError olarak ayrıştırır.
    """
    if response.status_code == 429 or 500 <= response.status_code < 600:
        retry_after = 0.0
        ra_header = response.headers.get("Retry-After", "")
        if ra_header:
            try:
                retry_after = float(ra_header)
            except ValueError:
                retry_after = 0.0
        # 429'da Retry-After'a saygı duy (basit kontrol)
        if response.status_code == 429 and retry_after > 0:
            logger.warning(
                "429 Too Many Requests, Retry-After=%.1fs", retry_after
            )
            time.sleep(min(retry_after, 60.0))
        raise RetryableHTTPError(
            response.status_code,
            response.text[:300] if response.text else response.reason,
            retry_after,
        )
    response.raise_for_status()


def with_retry(
    max_attempts: int = 5,
    max_wait: float = 60.0,
    multiplier: float = 1.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    HTTP istemci fonksiyonları için tenacity decorator factory.

    Varsayılan: 5 deneme, exponential backoff (1s, 2s, 4s, 8s, 16s, max 60s).
    """
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=multiplier, max=max_wait),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
