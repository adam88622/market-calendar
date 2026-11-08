"""FN-064：海外休市行事曆事件建構（純函式）。"""
from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from core.dates import nth_weekday
from core.models import (
    MARKET_SPECS,
    Event,
    HolidayName,
    MarketClosures,
    MarketCode,
    MarketSpec,
)
from core.uid import make_uid

_WEEKDAY_ZH = "一二三四五六日"
_SOURCE = "pandas_market_calendars"
_NOTE = "註：未來年度為依規則推算，以交易所公告為準"
_NAME_SOURCE_LABEL = {
    "pandas_market_calendars": "pandas_market_calendars",
    "holidays": "holidays 套件",
    "builtin": "本專案內建對照",
}
_SPEC_BY_CODE: dict[str, MarketSpec] = {s.code: s for s in MARKET_SPECS}
_ORDER: dict[str, int] = {s.code: i for i, s in enumerate(MARKET_SPECS)}
_NO_NAME = HolidayName("休市", "", "none")


def us_early_close_label(d: date) -> str:
    if d.month == 11 and d.weekday() == 4:
        prev = date.fromordinal(d.toordinal() - 1)
        if prev == nth_weekday(prev.year, 11, 3, 4):
            return "感恩節翌日"
    if (d.month, d.day) == (12, 24):
        return "聖誕夜"
    if (d.month, d.day) == (7, 3):
        return "獨立紀念日前夕"
    return "提早收盤"


def _fmt_date(d: date) -> str:
    return f"{d.isoformat()}（{_WEEKDAY_ZH[d.weekday()]}）"


def _closed_event(spec: MarketSpec, d: date, hn: HolidayName) -> Event:
    if hn.source == "none":
        holiday_line = "節日：未取得名稱"
    elif hn.original and hn.original != hn.display:
        holiday_line = f"節日：{hn.display}（原文：{hn.original}）"
    else:
        holiday_line = f"節日：{hn.display}"
    src_line = f"資料來源：{_SOURCE}（{spec.mcal_name}）"
    label = _NAME_SOURCE_LABEL.get(hn.source)
    if label:
        src_line += f"；節日名稱：{label}"
    desc = "\n".join([
        f"市場：{spec.label}（{spec.exchange}）",
        f"日期：{_fmt_date(d)}",
        holiday_line,
        "當日全日休市",
        src_line,
        _NOTE,
    ])
    return Event(
        uid=make_uid(f"gh-{spec.code}-closed", d.strftime("%Y%m%d")),
        calendar="global-holidays",
        title=f"{spec.label}休市：{hn.display}",
        start=d,
        all_day=True,
        description=desc,
        source=_SOURCE,
    )


def _early_event(spec: MarketSpec, d: date, close_local, tz_local: ZoneInfo) -> Event:
    hhmm = close_local.strftime("%H:%M")
    label = us_early_close_label(d)
    tp = close_local.astimezone(tz_local)
    desc = "\n".join([
        f"市場：{spec.label}（{spec.exchange}）",
        f"日期：{_fmt_date(d)}",
        f"提早收盤：{hhmm} ET（美東）＝ 台北時間 {_fmt_date(tp.date())}{tp.strftime('%H:%M')}",
        f"說明：{label}",
        f"資料來源：{_SOURCE}（{spec.mcal_name}）",
        _NOTE,
    ])
    return Event(
        uid=make_uid(f"gh-{spec.code}-early", d.strftime("%Y%m%d")),
        calendar="global-holidays",
        title=f"{spec.label}提早收盤（{hhmm} ET）：{label}",
        start=d,
        all_day=True,
        description=desc,
        source=_SOURCE,
    )


def build_global_holiday_events(start: date, end: date,
                                closures: Sequence[MarketClosures],
                                names: Mapping[MarketCode, Mapping[date, HolidayName]],
                                tz_local: ZoneInfo) -> list[Event]:
    keyed: list[tuple[tuple[date, int, int], Event]] = []
    for mc in closures:
        spec = _SPEC_BY_CODE[mc.market]
        market_names = names.get(mc.market) or {}
        for d in mc.closed:
            if d < start or d > end or d.weekday() >= 5:
                continue
            hn = market_names.get(d, _NO_NAME)
            keyed.append(((d, _ORDER[mc.market], 0), _closed_event(spec, d, hn)))
        for ec in mc.early:
            d = ec.day
            if d < start or d > end or d.weekday() >= 5:
                continue
            keyed.append(((d, _ORDER[mc.market], 1), _early_event(spec, d, ec.close_local, tz_local)))
    keyed.sort(key=lambda kv: kv[0])
    return [ev for _k, ev in keyed]
