"""FN-002：load_config（以架構 v2 替換 3 + v3 R6 為準）。"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import yaml

from core.models import ConfigError, WatchItem

_CALENDAR_IDS = ("tw-settlement", "us-settlement", "tw-earnings", "us-earnings")
_OPTIONAL_CALENDARS: dict[str, "CalendarMeta"] = {}  # 於 CalendarMeta 定義後填入
_POLICIES = ("next", "previous", "skip")
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_MMDD_RE = re.compile(r"^(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class PathsConfig:
    output_dir: Path
    data_dir: Path
    logs_dir: Path


@dataclass(frozen=True)
class RangeConfig:
    months_before: int
    months_after: int
    align_to_month: bool


@dataclass(frozen=True)
class ReminderConfig:
    days_before: int
    at: time


@dataclass(frozen=True)
class CalendarMeta:
    filename: str
    name: str


_OPTIONAL_CALENDARS["global-holidays"] = CalendarMeta(
    filename="global-holidays.ics", name="海外休市（美日韓）"
)


@dataclass(frozen=True)
class WeeklySeriesConfig:
    enabled: bool
    holiday_policy: str  # next | previous | skip


@dataclass(frozen=True)
class TwSettlementConfig:
    weekly_wed: WeeklySeriesConfig
    weekly_fri: WeeklySeriesConfig


@dataclass(frozen=True)
class HolidayEntry:
    date: date
    name: str
    kind: str = "holiday"  # holiday | market_only
    provisional: bool = False


@dataclass(frozen=True)
class TwFilingConfig:
    q1: str  # "MM-DD"
    q2: str
    q3: str
    annual: str  # 次年 "MM-DD"
    monthly_revenue_day: int


@dataclass(frozen=True)
class MopsConfig:
    url: str
    referer: str
    markets: tuple[str, ...]
    request_interval_sec: float
    timeout_sec: float
    max_retries: int
    backoff_sec: float
    reconcile_future: bool
    total_budget_sec: float


@dataclass(frozen=True)
class YahooConfig:
    enabled: bool
    url: str
    timeout_sec: float
    max_retries: int


@dataclass(frozen=True)
class TwIrConfig:
    default_duration_minutes: int


@dataclass(frozen=True)
class UsEarningsConfig:
    watchlist: tuple[WatchItem, ...]
    max_workers: int
    submit_stagger_sec: float
    per_ticker_timeout_sec: float
    total_budget_sec: float
    earnings_dates_limit: int
    reconcile_future: bool

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(w.ticker for w in self.watchlist)

    @property
    def group_of(self) -> dict[str, str]:
        return {w.ticker: w.group for w in self.watchlist}


@dataclass(frozen=True)
class ScheduleConfig:
    label: str
    hour: int
    minute: int
    launch_mode: Literal["direct", "open"] = "direct"  # v4.1 R16
    times: tuple[tuple[int, int], ...] = ()  # (hour, minute) 清單；由 times 或 hour/minute 正規化


@dataclass(frozen=True)
class NetworkCheckConfig:
    enabled: bool = False
    timeout_sec: float = 4.0


@dataclass(frozen=True)
class AppleCalendarConfig:
    enabled: bool = False  # 沒有此區段時為 False（D-15）
    prefer_source: Literal["iCloud", "local"] = "iCloud"
    delete_future_missing: bool = True


@dataclass(frozen=True)
class LogConfig:
    level: str
    retention_days: int


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    user_agent: str
    paths: PathsConfig
    range: RangeConfig
    reminder: ReminderConfig
    calendars: dict[str, CalendarMeta]
    tw_settlement: TwSettlementConfig
    taifex_holidays: tuple[HolidayEntry, ...]
    taifex_extra_holidays: tuple[HolidayEntry, ...]
    tw_filing: TwFilingConfig
    mops: MopsConfig
    yahoo: YahooConfig
    tw_ir: TwIrConfig
    us_earnings: UsEarningsConfig
    schedule: ScheduleConfig
    log: LogConfig
    apple_calendar: AppleCalendarConfig = field(default_factory=AppleCalendarConfig)
    network_check: NetworkCheckConfig = field(default_factory=NetworkCheckConfig)


# ---------------------------------------------------------------- helpers
def _req(d: Any, key: str, where: str) -> Any:
    if not isinstance(d, dict):
        raise ConfigError(f"{where} must be a mapping")
    if key not in d or d[key] is None:
        raise ConfigError(f"missing required key: {where}.{key}" if where else f"missing required key: {key}")
    return d[key]


def _num(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{name} must be a number, got {v!r}")
    return v


def _int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{name} must be an integer, got {v!r}")
    return v


def _bool(v: Any, name: str) -> bool:
    if not isinstance(v, bool):
        raise ConfigError(f"{name} must be a boolean, got {v!r}")
    return v


def _str(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{name} must be a non-empty string, got {v!r}")
    return v


def _parse_hhmm(v: Any, name: str) -> time:
    if isinstance(v, int) and not isinstance(v, bool):  # YAML sexagesimal 如 20:00 → 1200
        raise ConfigError(f"{name} must be a quoted HH:MM string, got {v!r}")
    m = _HHMM_RE.match(v.strip()) if isinstance(v, str) else None
    if not m:
        raise ConfigError(f"{name} must be HH:MM, got {v!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ConfigError(f"{name} out of range: {v!r}")
    return time(h, mi)


def _parse_mmdd(v: Any, name: str) -> str:
    m = _MMDD_RE.match(v) if isinstance(v, str) else None
    if not m:
        raise ConfigError(f"{name} must be 'MM-DD', got {v!r}")
    try:
        date(2024, int(m.group(1)), int(m.group(2)))
    except ValueError as e:
        raise ConfigError(f"{name} invalid date {v!r}") from e
    return v


def _resolve_path(p: Any, prj_root: Path, name: str) -> Path:
    s = _str(p, name)
    path = Path(s).expanduser()
    if not path.is_absolute():
        path = prj_root / path
    return path


def _parse_holidays(raw: Any, name: str) -> tuple[HolidayEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{name} must be a list")
    out: list[HolidayEntry] = []
    for i, item in enumerate(raw):
        w = f"{name}[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{w} must be a mapping")
        d = _req(item, "date", w)
        if isinstance(d, datetime):
            d = d.date()
        elif isinstance(d, str):
            try:
                d = date.fromisoformat(d)
            except ValueError as e:
                raise ConfigError(f"{w}.date invalid: {d!r}") from e
        elif not isinstance(d, date):
            raise ConfigError(f"{w}.date invalid: {d!r}")
        hname = _str(_req(item, "name", w), f"{w}.name")
        kind = item.get("kind", "holiday")
        if kind not in ("holiday", "market_only"):
            raise ConfigError(f"{w}.kind must be holiday|market_only, got {kind!r}")
        prov = _bool(item.get("provisional", False), f"{w}.provisional")
        out.append(HolidayEntry(date=d, name=hname, kind=kind, provisional=prov))
    return tuple(out)


def _parse_weekly(raw: Any, name: str) -> WeeklySeriesConfig:
    enabled = _bool(_req(raw, "enabled", name), f"{name}.enabled")
    policy = _req(raw, "holiday_policy", name)
    if policy not in _POLICIES:
        raise ConfigError(f"{name}.holiday_policy must be one of {_POLICIES}, got {policy!r}")
    return WeeklySeriesConfig(enabled=enabled, holiday_policy=policy)


def _parse_watchlist(raw: Any) -> tuple[WatchItem, ...]:
    if raw is None or not isinstance(raw, list) or len(raw) == 0:
        raise ConfigError("us_earnings.watchlist must be a non-empty list")
    items: list[WatchItem] = []
    seen: set[str] = set()
    for i, it in enumerate(raw):
        if isinstance(it, str):
            raise ConfigError(
                f"us_earnings.watchlist[{i}]={it!r}: plain string list is no longer supported; "
                "use the new format {ticker: NVDA, group: AI晶片/GPU}"
            )
        if not isinstance(it, dict) or "ticker" not in it or "group" not in it:
            raise ConfigError(
                f"us_earnings.watchlist[{i}] must be a mapping with 'ticker' and 'group'"
            )
        t = it["ticker"]
        if not isinstance(t, str):
            raise ConfigError(f"us_earnings.watchlist[{i}].ticker must be a string, got {t!r}")
        t = t.strip().upper()
        if not _TICKER_RE.match(t):
            raise ConfigError(f"us_earnings.watchlist[{i}].ticker invalid: {it['ticker']!r}")
        g = it["group"]
        if not isinstance(g, str) or not g.strip():
            raise ConfigError(f"us_earnings.watchlist: group of {t} must be non-empty")
        if t in seen:
            raise ConfigError(f"us_earnings.watchlist: duplicate ticker {t}")
        seen.add(t)
        items.append(WatchItem(ticker=t, group=g.strip()))
    return tuple(items)


def _parse_apple_calendar(raw: Any) -> AppleCalendarConfig:
    if raw is None:
        return AppleCalendarConfig()
    if not isinstance(raw, dict):
        raise ConfigError("apple_calendar must be a mapping")
    enabled = _bool(raw.get("enabled", True), "apple_calendar.enabled")
    dfm = _bool(raw.get("delete_future_missing", True), "apple_calendar.delete_future_missing")
    ps = raw.get("prefer_source", "iCloud")
    if not isinstance(ps, str) or ps.strip().lower() not in ("icloud", "local"):
        raise ConfigError(f"apple_calendar.prefer_source must be iCloud|local, got {ps!r}")
    prefer = "iCloud" if ps.strip().lower() == "icloud" else "local"
    return AppleCalendarConfig(enabled=enabled, prefer_source=prefer, delete_future_missing=dfm)


# ---------------------------------------------------------------- main
def _parse_network_check(v: Any) -> NetworkCheckConfig:
    if v is None:
        return NetworkCheckConfig()
    if not isinstance(v, dict):
        raise ConfigError("network_check must be a mapping")
    enabled = _bool(v.get("enabled", True), "network_check.enabled")
    to = _num(v.get("timeout_sec", 4), "network_check.timeout_sec")
    if to <= 0:
        raise ConfigError("network_check.timeout_sec must be > 0")
    return NetworkCheckConfig(enabled=enabled, timeout_sec=float(to))


def load_config(path: Path, prj_root: Path) -> AppConfig:
    path = Path(path)
    prj_root = Path(prj_root)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML syntax error in {path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    timezone = _str(_req(raw, "timezone", ""), "timezone")
    user_agent = _str(_req(raw, "user_agent", ""), "user_agent")

    p = _req(raw, "paths", "")
    paths = PathsConfig(
        output_dir=_resolve_path(_req(p, "output_dir", "paths"), prj_root, "paths.output_dir"),
        data_dir=_resolve_path(_req(p, "data_dir", "paths"), prj_root, "paths.data_dir"),
        logs_dir=_resolve_path(_req(p, "logs_dir", "paths"), prj_root, "paths.logs_dir"),
    )

    r = _req(raw, "range", "")
    mb = _int(_req(r, "months_before", "range"), "range.months_before")
    ma = _int(_req(r, "months_after", "range"), "range.months_after")
    if mb < 0 or ma < 0:
        raise ConfigError("range.months_before/months_after must be >= 0")
    rng = RangeConfig(mb, ma, _bool(r.get("align_to_month", True), "range.align_to_month"))

    rm = _req(raw, "reminder", "")
    db = _int(_req(rm, "days_before", "reminder"), "reminder.days_before")
    if db < 0:
        raise ConfigError("reminder.days_before must be >= 0")
    reminder = ReminderConfig(days_before=db, at=_parse_hhmm(_req(rm, "time", "reminder"), "reminder.time"))

    cals_raw = _req(raw, "calendars", "")
    if not isinstance(cals_raw, dict):
        raise ConfigError("calendars must be a mapping")
    missing = [c for c in _CALENDAR_IDS if c not in cals_raw]
    if missing:
        raise ConfigError(f"calendars missing: {missing}")
    calendars: dict[str, CalendarMeta] = {
        c: CalendarMeta(
            filename=_str(_req(cals_raw[c], "filename", f"calendars.{c}"), f"calendars.{c}.filename"),
            name=_str(_req(cals_raw[c], "name", f"calendars.{c}"), f"calendars.{c}.name"),
        )
        for c in _CALENDAR_IDS
    }
    for c, default in _OPTIONAL_CALENDARS.items():
        if c in cals_raw:
            calendars[c] = CalendarMeta(
                filename=_str(_req(cals_raw[c], "filename", f"calendars.{c}"), f"calendars.{c}.filename"),
                name=_str(_req(cals_raw[c], "name", f"calendars.{c}"), f"calendars.{c}.name"),
            )
        else:
            calendars[c] = default
    fns = [m.filename for m in calendars.values()]
    if len(set(fns)) != len(fns):
        raise ConfigError(f"calendars: duplicate filename in {fns}")
    names = [m.name for m in calendars.values()]
    if len(set(names)) != len(names):
        raise ConfigError(f"calendars: duplicate name in {names}")

    ts = _req(raw, "tw_settlement", "")
    tw_settlement = TwSettlementConfig(
        weekly_wed=_parse_weekly(_req(ts, "weekly_wed", "tw_settlement"), "tw_settlement.weekly_wed"),
        weekly_fri=_parse_weekly(_req(ts, "weekly_fri", "tw_settlement"), "tw_settlement.weekly_fri"),
    )

    holidays = _parse_holidays(_req(raw, "taifex_holidays", ""), "taifex_holidays")
    extra = _parse_holidays(raw.get("taifex_extra_holidays"), "taifex_extra_holidays")

    f = _req(raw, "tw_filing", "")
    mrd = _int(_req(f, "monthly_revenue_day", "tw_filing"), "tw_filing.monthly_revenue_day")
    if not 1 <= mrd <= 28:
        raise ConfigError("tw_filing.monthly_revenue_day must be 1..28")
    tw_filing = TwFilingConfig(
        q1=_parse_mmdd(_req(f, "q1", "tw_filing"), "tw_filing.q1"),
        q2=_parse_mmdd(_req(f, "q2", "tw_filing"), "tw_filing.q2"),
        q3=_parse_mmdd(_req(f, "q3", "tw_filing"), "tw_filing.q3"),
        annual=_parse_mmdd(_req(f, "annual", "tw_filing"), "tw_filing.annual"),
        monthly_revenue_day=mrd,
    )

    m = _req(raw, "mops", "")
    interval = _num(_req(m, "request_interval_sec", "mops"), "mops.request_interval_sec")
    if interval < 2:
        raise ConfigError(f"mops.request_interval_sec must be >= 2, got {interval}")
    markets = _req(m, "markets", "mops")
    if not isinstance(markets, list) or not markets or any(x not in ("sii", "otc") for x in markets):
        raise ConfigError("mops.markets must be a non-empty list of sii|otc")
    mto = _num(_req(m, "timeout_sec", "mops"), "mops.timeout_sec")
    mbudget = _num(_req(m, "total_budget_sec", "mops"), "mops.total_budget_sec")
    mretry = _int(_req(m, "max_retries", "mops"), "mops.max_retries")
    mback = _num(_req(m, "backoff_sec", "mops"), "mops.backoff_sec")
    if mto <= 0 or mbudget <= 0:
        raise ConfigError("mops.timeout_sec/total_budget_sec must be > 0")
    if mretry < 0 or mback < 0:
        raise ConfigError("mops.max_retries/backoff_sec must be >= 0")
    mops = MopsConfig(
        url=_str(_req(m, "url", "mops"), "mops.url"),
        referer=_str(_req(m, "referer", "mops"), "mops.referer"),
        markets=tuple(markets),
        request_interval_sec=interval,
        timeout_sec=mto,
        max_retries=mretry,
        backoff_sec=mback,
        reconcile_future=_bool(m.get("reconcile_future", True), "mops.reconcile_future"),
        total_budget_sec=mbudget,
    )

    y = _req(raw, "yahoo", "")
    yto = _num(_req(y, "timeout_sec", "yahoo"), "yahoo.timeout_sec")
    yretry = _int(_req(y, "max_retries", "yahoo"), "yahoo.max_retries")
    if yto <= 0 or yretry < 0:
        raise ConfigError("yahoo.timeout_sec must be > 0 and max_retries >= 0")
    yahoo = YahooConfig(
        enabled=_bool(_req(y, "enabled", "yahoo"), "yahoo.enabled"),
        url=_str(_req(y, "url", "yahoo"), "yahoo.url"),
        timeout_sec=yto,
        max_retries=yretry,
    )

    ti = _req(raw, "tw_ir", "")
    dur = _int(_req(ti, "default_duration_minutes", "tw_ir"), "tw_ir.default_duration_minutes")
    if dur <= 0:
        raise ConfigError("tw_ir.default_duration_minutes must be > 0")
    tw_ir = TwIrConfig(default_duration_minutes=dur)

    u = _req(raw, "us_earnings", "")
    watchlist = _parse_watchlist(u.get("watchlist"))
    workers = _int(_req(u, "max_workers", "us_earnings"), "us_earnings.max_workers")
    if not 1 <= workers <= 8:
        raise ConfigError(f"us_earnings.max_workers must be 1..8, got {workers}")
    stagger = _num(_req(u, "submit_stagger_sec", "us_earnings"), "us_earnings.submit_stagger_sec")
    if stagger < 0:
        raise ConfigError("us_earnings.submit_stagger_sec must be >= 0")
    pt = _num(_req(u, "per_ticker_timeout_sec", "us_earnings"), "us_earnings.per_ticker_timeout_sec")
    tb = _num(_req(u, "total_budget_sec", "us_earnings"), "us_earnings.total_budget_sec")
    if pt <= 0 or tb <= 0:
        raise ConfigError("us_earnings.per_ticker_timeout_sec/total_budget_sec must be > 0")
    lim = _int(_req(u, "earnings_dates_limit", "us_earnings"), "us_earnings.earnings_dates_limit")
    if lim <= 0:
        raise ConfigError("us_earnings.earnings_dates_limit must be > 0")
    us = UsEarningsConfig(
        watchlist=watchlist,
        max_workers=workers,
        submit_stagger_sec=stagger,
        per_ticker_timeout_sec=pt,
        total_budget_sec=tb,
        earnings_dates_limit=lim,
        reconcile_future=_bool(u.get("reconcile_future", True), "us_earnings.reconcile_future"),
    )

    s = _req(raw, "schedule", "")
    raw_times = s.get("times")
    if raw_times is None:
        hour = _int(_req(s, "hour", "schedule"), "schedule.hour")
        minute = _int(_req(s, "minute", "schedule"), "schedule.minute")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ConfigError("schedule.hour/minute out of range")
        times = ((hour, minute),)
    else:
        if not isinstance(raw_times, list) or not 1 <= len(raw_times) <= 12:
            raise ConfigError("schedule.times must be a list of 1..12 HH:MM strings")
        parsed: list[tuple[int, int]] = []
        for i, tv in enumerate(raw_times):
            t = _parse_hhmm(tv, f"schedule.times[{i}]")
            parsed.append((t.hour, t.minute))
        if len(set(parsed)) != len(parsed):
            raise ConfigError("schedule.times must not contain duplicates")
        times = tuple(parsed)
        hour, minute = times[0]  # 與 times 並存時 hour/minute 僅為相容欄位
    lm = s.get("launch_mode", "direct")
    if lm not in ("direct", "open"):
        raise ConfigError(f"schedule.launch_mode must be direct|open, got {lm!r}")
    schedule = ScheduleConfig(
        label=_str(_req(s, "label", "schedule"), "schedule.label"),
        hour=hour,
        minute=minute,
        launch_mode=lm,
        times=times,
    )

    lg = _req(raw, "log", "")
    level = _str(_req(lg, "level", "log"), "log.level").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(f"log.level invalid: {level}")
    ret = _int(_req(lg, "retention_days", "log"), "log.retention_days")
    if ret < 0:
        raise ConfigError("log.retention_days must be >= 0")

    return AppConfig(
        timezone=timezone,
        user_agent=user_agent,
        paths=paths,
        range=rng,
        reminder=reminder,
        calendars=calendars,
        tw_settlement=tw_settlement,
        taifex_holidays=holidays,
        taifex_extra_holidays=extra,
        tw_filing=tw_filing,
        mops=mops,
        yahoo=yahoo,
        tw_ir=tw_ir,
        us_earnings=us,
        schedule=schedule,
        log=LogConfig(level=level, retention_days=ret),
        apple_calendar=_parse_apple_calendar(raw.get("apple_calendar")),
        network_check=_parse_network_check(raw.get("network_check")),
    )


def apply_platform_overrides(cfg: AppConfig, platform: str) -> tuple[AppConfig, str | None]:
    """FN-067：platform != "darwin" 且 apple 啟用 → 回傳停用 apple 的新 cfg 與 INFO 訊息；否則 (cfg, None)（同一物件）。"""
    if platform == "darwin" or not cfg.apple_calendar.enabled:
        return cfg, None
    new = dataclasses.replace(
        cfg, apple_calendar=dataclasses.replace(cfg.apple_calendar, enabled=False)
    )
    return new, f"apple calendar: disabled automatically on non-macOS platform ({platform})"
