"""FN-008 request_with_retry：帶 timeout、重試與時間預算（R1 deadline）的 HTTP 請求。"""
from __future__ import annotations

import logging
import time
from typing import Callable, Literal

import requests

from core.models import FetchError

logger = logging.getLogger("market_calendar.scrapers.http")

# 重試預算保留：剩餘時間 <= 此值就不再發出請求
_MIN_REMAINING_SEC = 1.0
# connect timeout 上限
_CONNECT_TIMEOUT_CAP = 5.0


def request_with_retry(
    session: requests.Session,
    method: Literal["GET", "POST"],
    url: str,
    *,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15,
    max_retries: int = 2,
    backoff_sec: float = 3,
    sleep: Callable[[float], None] = time.sleep,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """發出 HTTP 請求並回傳 UTF-8 文字。

    - 只重試連線錯誤、逾時與 5xx；4xx 直接丟 FetchError。
    - deadline 為 clock() 的絕對時間；每次嘗試前若剩餘 <= 1 秒丟
      FetchError("budget exhausted")，並把該次 timeout 限制在剩餘時間內
      （connect, read）。退避前若剩餘 < backoff_sec + 1 就不再重試。
    """
    method_u = method.upper()
    total_attempts = max(0, int(max_retries)) + 1
    last_err: str = "unknown error"
    attempts_made = 0

    for attempt in range(1, total_attempts + 1):
        if deadline is not None:
            remaining = deadline - clock()
            if remaining <= _MIN_REMAINING_SEC:
                raise FetchError(f"{method_u} {url}: budget exhausted")
            req_timeout: float | tuple[float, float] = (
                min(_CONNECT_TIMEOUT_CAP, remaining),
                min(timeout, remaining),
            )
        else:
            req_timeout = timeout

        attempts_made += 1
        retryable = True
        try:
            resp = session.request(
                method_u, url, data=data, headers=headers, timeout=req_timeout
            )
            status = resp.status_code
            if 200 <= status < 300:
                resp.encoding = "utf-8"
                return resp.text
            last_err = f"HTTP {status}"
            if status >= 500:
                retryable = True
            else:
                # 4xx（以及非預期的 1xx/3xx）不重試
                retryable = False
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            retryable = False

        logger.warning(
            "%s %s attempt %d/%d failed: %s", method_u, url, attempt, total_attempts, last_err
        )
        if not retryable:
            raise FetchError(f"{method_u} {url} failed (not retried): {last_err}")
        if attempt >= total_attempts:
            break
        if deadline is not None and (deadline - clock()) < backoff_sec + _MIN_REMAINING_SEC:
            logger.warning("%s %s: budget too low to retry, giving up", method_u, url)
            break
        sleep(backoff_sec)

    raise FetchError(
        f"{method_u} {url} failed after {attempts_made} attempts: {last_err}"
    )
