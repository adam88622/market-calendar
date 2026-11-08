"""FN-063：海外市場節日名稱查詢與中文化。

休市與否不在此判斷（見 core/market_closures.py）；這裡只負責「名稱」。
名稱來源鏈：pandas_market_calendars（僅美股）→ holidays 套件 → 內建 builtin → 「休市」。
"""
from __future__ import annotations

import importlib
import re
from datetime import date
from types import ModuleType
from typing import Callable, Iterable

from core.holidays import _clean_holiday_name
from core.models import HolidayName, MarketSpec

_NONE_DISPLAY = "休市"


def _import_holidays() -> ModuleType:
    return importlib.import_module("holidays")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


# 順序即優先權：juneteenth 必須先於 independence
_US_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("newyear",), "元旦"),
    (("martinlutherking",), "馬丁·路德·金恩紀念日"),
    (("washington", "president"), "總統日"),
    (("goodfriday",), "耶穌受難日"),
    (("memorial",), "陣亡將士紀念日"),
    (("juneteenth",), "六月節（解放日）"),
    (("independence",), "獨立紀念日"),
    (("labor",), "勞動節"),
    (("thanksgiving",), "感恩節"),
    (("christmas",), "聖誕節"),
    (("mourning", "funeral"), "國殤日（臨時休市）"),
    (("election",), "選舉日"),
)

_KR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("chuseok",), "中秋（秋夕）"),
    (("koreannewyear", "seollal", "lunarnewyear"), "農曆春節"),
    (("newyearsday",), "元旦"),
    (("independencemovement",), "三一節"),
    (("children",), "兒童節"),
    (("buddha",), "佛誕日"),
    (("memorial",), "顯忠日"),
    (("liberation",), "光復節"),
    (("nationalfoundation",), "開天節"),
    (("hangul",), "韓文日"),
    (("christmas",), "聖誕節"),
    (("labor", "workers"), "勞動節"),
    (("election",), "選舉日"),
    (("armedforces",), "國軍日"),
    (("temporary",), "臨時公休日"),
)

_KR_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:alternative|substitute) holiday for (.+)$", re.I), "補休"),
    (re.compile(r"^the day preceding (.+)$", re.I), "前一日"),
    (re.compile(r"^the second day of (.+)$", re.I), "翌日"),
)


def translate_us(name_en: str) -> str | None:
    key = _norm(name_en)
    for words, zh in _US_RULES:
        if any(w in key for w in words):
            return zh + "（補假）" if "observed" in key else zh
    return None


def translate_kr(name_en: str) -> str | None:
    text = name_en.strip()
    for pat, suffix in _KR_PREFIXES:
        m = pat.match(text)
        if m:
            inner = translate_kr(m.group(1))
            return None if inner is None else inner + suffix
    key = _norm(text)
    for words, zh in _KR_RULES:
        if any(w in key for w in words):
            return zh
    return None


def _join_unique(items: list[str]) -> str:
    seen: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.append(it)
    return "、".join(seen)


def _from_names(originals: list[str], displays: list[str], source: str) -> HolidayName:
    return HolidayName(
        display=_join_unique(displays),
        original="; ".join(dict.fromkeys(o for o in originals if o)),
        source=source,  # type: ignore[arg-type]
    )


def _builtin(code: str, d: date) -> str | None:
    md = (d.month, d.day)
    if code == "jp":
        if md == (12, 31):
            return "年末休市"
        if md in ((1, 2), (1, 3)):
            return "年始休市"
    elif code == "kr":
        if md == (12, 31):
            return "年末休市"
        if md == (5, 1):
            return "勞動節"
    return None


def _us_pmc_names(days: list[date], mcal_module: ModuleType | None) -> dict[date, str]:
    try:
        mcal = mcal_module or importlib.import_module("pandas_market_calendars")
        cal = mcal.get_calendar("NYSE")
        series = cal.regular_holidays.holidays(
            start=min(days).isoformat(), end=max(days).isoformat(), return_name=True
        )
        return {ts.date(): _clean_holiday_name(str(nm)) for ts, nm in series.items()}
    except Exception:  # 名稱只是補充資訊，安靜改走下一層
        return {}


def lookup_holiday_names(spec: MarketSpec, days: Iterable[date], *,
                         importer: Callable[[], ModuleType] = _import_holidays,
                         mcal_module: ModuleType | None = None
                         ) -> tuple[dict[date, HolidayName], list[str]]:
    day_list = sorted(set(days))
    warnings: list[str] = []
    result: dict[date, HolidayName] = {}
    if not day_list:
        return result, warnings
    code = spec.code
    years = sorted({d.year for d in day_list})

    hol: ModuleType | None = None
    try:
        hol = importer()
    except Exception as e:
        warnings.append(f"holidays package unavailable: {e}")

    pmc_names = _us_pmc_names(day_list, mcal_module) if code == "us" else {}

    table = None
    if hol is not None:
        try:
            if code == "us":
                table = hol.financial_holidays("NYSE", years=years)
            elif code == "jp":
                table = hol.country_holidays(spec.holidays_country, years=years, language="ja")
            elif code == "kr":
                base = hol.country_holidays(spec.holidays_country)
                lang = "en_US" if "en_US" in (getattr(base, "supported_languages", ()) or ()) else None
                table = hol.country_holidays(spec.holidays_country, years=years, language=lang)
        except Exception as e:
            warnings.append(f"holidays package unavailable: {e}")
            table = None

    def table_list(d: date) -> list[str]:
        if table is None:
            return []
        try:
            return [str(x) for x in table.get_list(d)]
        except Exception as e:
            warnings.append(f"holidays package unavailable: {e}")
            return []

    for d in day_list:
        hn: HolidayName | None = None
        if code == "us":
            raw = pmc_names.get(d)
            if raw:
                hn = _from_names([raw], [translate_us(raw) or raw], "pandas_market_calendars")
            else:
                raws = table_list(d)
                if raws:
                    hn = _from_names(raws, [translate_us(r) or r for r in raws], "holidays")
        elif code == "jp":
            raws = table_list(d)
            if raws:
                hn = _from_names(raws, raws, "holidays")
        elif code == "kr":
            raws = table_list(d)
            if raws:
                hn = _from_names(raws, [translate_kr(r) or r for r in raws], "holidays")
        if hn is None:
            b = _builtin(code, d)
            if b:
                hn = HolidayName(display=b, original="", source="builtin")
        if hn is None:
            hn = HolidayName(display=_NONE_DISPLAY, original="", source="none")
        result[d] = hn
    return result, warnings
