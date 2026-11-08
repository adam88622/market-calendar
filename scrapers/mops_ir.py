"""FN-009 parse_mops_ir_html、FN-023 fetch_mops_ir：MOPS 法人說明會（t100sb02_1）。"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Callable, Literal

import requests
from bs4 import BeautifulSoup

from core.models import FetchError, FetchResult, IrRecord, ParseError
from scrapers.http import request_with_retry

logger = logging.getLogger("market_calendar.scrapers.mops_ir")

_HEADER_MARK = "召開法人說明會日期"
_ERROR_MARKS = ("頁面無法執行", "查詢過量", "FOR SECURITY REASONS")
_EMPTY_MARKS = ("查無", "無符合")
_STOCK_ID_RE = re.compile(r"^\d{4,6}$")
_DATE_RE = re.compile(r"(\d{2,4})/(\d{1,2})/(\d{1,2})")
_TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?!\d)")
_AMPM_RE = re.compile(r"上午|下午|中午|晚上|AM|PM|a\.m|p\.m", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_SUMMARY_MAX = 500

_MARKET_NAME = {"sii": "上市", "otc": "上櫃"}


def _clean(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _cell_text(cell) -> str:
    return _clean(cell.get_text(" ", strip=True))


def _parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1000:  # 民國年
        y += 1911
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _parse_time(text: str) -> str | None:
    if _AMPM_RE.search(text):
        return None
    m = _TIME_RE.search(text)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _find_data_tables(soup: BeautifulSoup) -> list:
    """含表頭字樣、且不再包含同樣表頭的內層 table。"""
    found = []
    for t in soup.find_all("table"):
        if _HEADER_MARK not in t.get_text():
            continue
        inner = [x for x in t.find_all("table") if _HEADER_MARK in x.get_text()]
        if inner:
            continue
        found.append(t)
    return found


def parse_mops_ir_html(html: str, market: Literal["sii", "otc"]) -> list[IrRecord]:
    """解析 MOPS ajax_t100sb02_1 回應。純函式。

    - 有資料表：解析（可為空）。
    - 無資料表且含「查無／無符合」：回傳 []。
    - 無資料表且含錯誤字樣或無法辨識：ParseError。
    """
    if market not in _MARKET_NAME:
        raise ValueError(f"unknown market: {market!r}")
    market_cn = _MARKET_NAME[market]
    soup = BeautifulSoup(html or "", "lxml")
    tables = _find_data_tables(soup)

    if not tables:
        text = soup.get_text(" ") if html else ""
        raw = html or ""
        for mark in _ERROR_MARKS:
            if mark in raw or mark in text:
                raise ParseError(f"MOPS error page: {mark}")
        for mark in _EMPTY_MARKS:
            if mark in text:
                return []
        raise ParseError("MOPS response has no recognizable data table")

    seen: set[tuple[str, date]] = set()
    out: list[IrRecord] = []
    for table in tables:
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) < 6:
                continue
            stock_id = _cell_text(cells[0])
            if not _STOCK_ID_RE.match(stock_id):
                continue
            date_text = _cell_text(cells[2])
            ev_date = _parse_date(date_text)
            if ev_date is None:
                logger.warning("MOPS %s: unparseable date %r, skipped", stock_id, date_text)
                continue
            key = (stock_id, ev_date)
            if key in seen:
                continue
            seen.add(key)

            summary = _cell_text(cells[5])
            if len(_DATE_RE.findall(date_text)) >= 2:
                summary = f"（日期：{date_text}）{summary}"
            summary = summary[:_SUMMARY_MAX]

            out.append(
                IrRecord(
                    stock_id=stock_id,
                    name=_cell_text(cells[1]),
                    event_date=ev_date,
                    event_time=_parse_time(_cell_text(cells[3])),
                    market=market_cn,
                    location=_cell_text(cells[4]),
                    summary=summary,
                    source="MOPS",
                )
            )
    out.sort(key=lambda r: (r.event_date, r.stock_id))
    return out


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def fetch_mops_ir(
    session: requests.Session,
    cfg,
    today: date,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> FetchResult:
    """依序抓 (當月, 下月) x markets。單一請求失敗不影響其他；本函式不丟例外。

    ok_scopes 的元素為 (市場中文, 西元年, 月)。scope_counts 為該 scope 回應中
    「event_date 落在該年月」的筆數（MOPS 的 month 參數是日期區間重疊月，
    回應含自前月跨入的列，故只計屬於該月的列，與 FN-027 對帳口徑一致）。
    """
    mc = cfg.mops
    interval = float(mc.request_interval_sec)
    deadline = clock() + float(mc.total_budget_sec)
    headers = {"User-Agent": cfg.user_agent, "Referer": mc.referer}

    months = [(today.year, today.month)]
    months.append(_next_month(*months[0]))
    scopes = [(mk, y, m) for (y, m) in months for mk in mc.markets]

    result = FetchResult(records=[])
    seen: set[tuple[str, date]] = set()
    budget_out = False

    for idx, (mk, y, m) in enumerate(scopes):
        label = f"{mk} {y}-{m:02d}"
        if not budget_out:
            remaining = deadline - clock()
            if idx > 0:
                if remaining <= interval:
                    budget_out = True
                else:
                    sleep(interval)
            elif remaining <= 0:
                budget_out = True
        if budget_out:
            msg = f"MOPS {label}: timeout, MOPS time budget ({mc.total_budget_sec}s) exhausted; skipped"
            logger.warning(msg)
            result.errors.append(msg)
            continue

        market_cn = _MARKET_NAME.get(mk, mk)
        form = {
            "encodeURIComponent": 1,
            "step": 1,
            "firstin": 1,
            "off": 1,
            "TYPEK": mk,
            "year": str(y - 1911),
            "month": f"{m:02d}",
            "co_id": "",
        }
        try:
            html = request_with_retry(
                session,
                "POST",
                mc.url,
                data=form,
                headers=headers,
                timeout=mc.timeout_sec,
                max_retries=mc.max_retries,
                backoff_sec=mc.backoff_sec,
                sleep=sleep,
                deadline=deadline,
                clock=clock,
            )
            recs = parse_mops_ir_html(html, mk)
        except (FetchError, ParseError) as e:
            msg = f"MOPS {label}: {type(e).__name__}: {e}"
            logger.warning(msg)
            result.errors.append(msg)
            continue

        key = (market_cn, y, m)
        result.ok_scopes.append(key)
        result.scope_counts[key] = sum(
            1 for r in recs if r.event_date.year == y and r.event_date.month == m
        )
        for r in recs:
            k = (r.stock_id, r.event_date)
            if k in seen:
                continue
            seen.add(k)
            result.records.append(r)

    result.records.sort(key=lambda r: (r.event_date, r.stock_id))
    return result
