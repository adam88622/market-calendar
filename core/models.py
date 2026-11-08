"""FN-001：資料模型、例外、RunContext。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    import logging
    import sqlite3

    import requests

    from core.config import AppConfig

CalendarId = Literal[
    "tw-settlement", "us-settlement", "tw-earnings", "us-earnings", "global-holidays"
]
CAL_ORDER: tuple[CalendarId, ...] = (
    "tw-settlement", "us-settlement", "tw-earnings", "us-earnings", "global-holidays",
)
UID_SUFFIX = "@market-calendar-ics"

_UID_BODY_RE = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class Event:
    uid: str
    calendar: CalendarId
    title: str
    start: date | datetime
    all_day: bool
    end: date | datetime | None = None
    description: str = ""
    location: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        uid = self.uid
        if not isinstance(uid, str) or not uid.endswith(UID_SUFFIX):
            raise ValueError(f"uid must end with {UID_SUFFIX}: {uid!r}")
        body = uid[: -len(UID_SUFFIX)]
        if not _UID_BODY_RE.match(body):
            raise ValueError(f"uid contains illegal characters: {uid!r}")
        if self.all_day:
            if type(self.start) is not date:
                raise ValueError("all_day event requires start to be a plain date")
            if self.end is not None and type(self.end) is not date:
                raise ValueError("all_day event requires end to be a plain date")
        else:
            if not isinstance(self.start, datetime) or self.start.tzinfo is None:
                raise ValueError("timed event requires tz-aware datetime start")
            if self.end is not None and (
                not isinstance(self.end, datetime) or self.end.tzinfo is None
            ):
                raise ValueError("timed event requires tz-aware datetime end")


@dataclass(frozen=True)
class SettlementDate:
    original: date
    adjusted: date
    reason: str = ""


@dataclass(frozen=True)
class IrRecord:
    stock_id: str
    name: str
    event_date: date
    event_time: str | None
    market: Literal["上市", "上櫃", "未知"]
    location: str
    summary: str
    source: Literal["MOPS", "Yahoo"]


@dataclass(frozen=True)
class UsEarningRecord:
    ticker: str
    date_et: date
    time_et: str | None
    session: Literal["盤前", "盤後", "盤中", "時段未定"]
    status: Literal["確認", "預估", "已公布", "未知"]


@dataclass(frozen=True)
class EventState:
    uid: str
    sequence: int
    dtstamp: str
    content_hash: str
    changed: bool


@dataclass(frozen=True)
class WatchItem:
    ticker: str  # 大寫，只能是英數字（UID 安全）
    group: str   # 非空字串


@dataclass
class FetchResult:
    records: list
    # MOPS（R4/R9）：[(市場中文 "上市"|"上櫃", 西元年, 月)]
    ok_scopes: list[tuple] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # MOPS（R4/R9）：{(市場中文, 西元年, 月): 該次解析筆數}
    scope_counts: dict = field(default_factory=dict)


@dataclass
class RunContext:
    cfg: "AppConfig"
    prj_root: Path
    today: date
    now_utc: datetime
    range_start: date
    range_end: date
    conn: "sqlite3.Connection"
    session: "requests.Session"
    logger: "logging.Logger"
    hung_workers: int = 0  # v3 R2
    source_stats: dict = field(default_factory=dict)  # FN-068


class ConfigError(Exception):
    """設定檔錯誤。"""


class FetchError(Exception):
    """網路/HTTP 層失敗（重試用盡）。"""


class ParseError(Exception):
    """回應結構無法辨識（含 MOPS 錯誤頁）。"""


# ---- FN-060（v5）：海外休市行事曆資料模型 ----
MarketCode = Literal["us", "jp", "kr"]


@dataclass(frozen=True)
class MarketSpec:
    code: MarketCode
    label: str              # "美股" / "日股" / "韓股"（標題前綴）
    exchange: str           # 交易所全名
    mcal_name: str          # "XNYS" / "JPX" / "XKRX"（休市判斷依據）
    tz: str                 # 市場時區
    holidays_country: str   # "US" / "JP" / "KR"
    early_closes: bool      # 只有 us 是 True


MARKET_SPECS: tuple[MarketSpec, ...] = (
    MarketSpec("us", "美股", "紐約證交所 NYSE", "XNYS", "America/New_York", "US", True),
    MarketSpec("jp", "日股", "東京證交所 JPX", "JPX", "Asia/Tokyo", "JP", False),
    MarketSpec("kr", "韓股", "韓國交易所 KRX", "XKRX", "Asia/Seoul", "KR", False),
)


@dataclass(frozen=True)
class EarlyClose:
    day: date
    close_local: datetime   # tz-aware，市場時區


@dataclass(frozen=True)
class MarketClosures:
    market: MarketCode
    closed: tuple[date, ...]          # 只含週一到週五，遞增排序
    early: tuple[EarlyClose, ...]     # 遞增排序；early_closes=False 的市場為 ()


@dataclass(frozen=True)
class HolidayName:
    display: str            # 標題用名稱（最差是「休市」）
    original: str           # 來源原文（none 時為 ""）
    source: Literal["pandas_market_calendars", "holidays", "builtin", "none"]
