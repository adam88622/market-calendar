"""FN-010 parse_yahoo_ir_html、FN-024 fetch_yahoo_ir：Yahoo 股市法說行事曆（補充來源）。"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Callable

import requests
from bs4 import BeautifulSoup

from core.models import FetchError, FetchResult, IrRecord, ParseError
from scrapers.http import request_with_retry

logger = logging.getLogger("market_calendar.scrapers.yahoo_ir")

_LINK_RE = re.compile(r"/quote/(\d{4,6})\.(TWO|TW)(?![A-Za-z0-9])")
_EXCLUDE = ("health-check", "price-valuations", "profile")
_DATE_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)")
_MAX_LEVELS = 8
_SEP = " | "
_YAHOO_BUDGET_SEC = 15.0


def parse_yahoo_ir_html(html: str) -> list[IrRecord]:
    """解析 Yahoo 法說行事曆頁。純函式；不依今天過濾。

    頁面沒有任何符合的 /quote/ 連結時丟 ParseError。
    """
    soup = BeautifulSoup(html or "", "lxml")
    seen: set[tuple[date, str]] = set()
    out: list[IrRecord] = []
    n_links = 0

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = _LINK_RE.search(href)
        if not m or any(x in href for x in _EXCLUDE):
            continue
        # 排除 /quote/2330.TW/xxx 這類子頁
        if href[m.end():].strip("/?#") and not href[m.end():].startswith(("?", "#")):
            continue
        n_links += 1
        stock_id = m.group(1)
        market = "上市" if m.group(2) == "TW" else "上櫃"

        name: str | None = None
        ev_date: date | None = None
        ev_time: str | None = None
        node = a
        for _ in range(_MAX_LEVELS):
            node = node.parent
            if node is None:
                break
            ctx = node.get_text(_SEP, strip=True)
            if not ctx:
                continue
            if name is None and stock_id in ctx:
                for part in ctx.split(_SEP):
                    p = part.strip()
                    if p and p != stock_id and stock_id not in p and ".TW" not in p and len(p) <= 15:
                        name = p
                        break
            dm = _DATE_RE.search(ctx)
            tm = _TIME_RE.search(ctx)
            if dm and tm:
                try:
                    ev_date = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
                except ValueError:
                    ev_date = None
                    break
                h, mi = int(tm.group(1)), int(tm.group(2))
                ev_time = f"{h:02d}:{mi:02d}" if h <= 23 and mi <= 59 else None
                break

        if ev_date is None:
            continue
        key = (ev_date, stock_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            IrRecord(
                stock_id=stock_id,
                name=name or "",
                event_date=ev_date,
                event_time=ev_time,
                market=market,
                location="",
                summary="",
                source="Yahoo",
            )
        )

    if n_links == 0:
        raise ParseError("Yahoo page has no /quote/ links (structure changed?)")
    out.sort(key=lambda r: (r.event_date, r.stock_id))
    return out


def fetch_yahoo_ir(
    session: requests.Session,
    cfg,
    clock: Callable[[], float] = time.monotonic,
) -> FetchResult:
    """GET Yahoo 法說行事曆並解析。本函式不丟例外。"""
    yc = cfg.yahoo
    if not getattr(yc, "enabled", True):
        return FetchResult(records=[])
    result = FetchResult(records=[])
    headers = {"User-Agent": cfg.user_agent, "Accept-Language": "zh-TW,zh;q=0.9"}
    try:
        html = request_with_retry(
            session,
            "GET",
            yc.url,
            headers=headers,
            timeout=yc.timeout_sec,
            max_retries=yc.max_retries,
            backoff_sec=getattr(yc, "backoff_sec", 3),
            deadline=clock() + _YAHOO_BUDGET_SEC,
            clock=clock,
        )
        result.records = parse_yahoo_ir_html(html)
    except (FetchError, ParseError) as e:
        msg = f"Yahoo: {type(e).__name__}: {e}"
        logger.warning(msg)
        result.errors.append(msg)
    return result
