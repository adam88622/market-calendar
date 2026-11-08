"""FN-010 parse_yahoo_ir_html。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from core.models import ParseError
from scrapers.yahoo_ir import parse_yahoo_ir_html

FX = Path(__file__).resolve().parent / "fixtures"


def test_real_fixture():
    recs = parse_yahoo_ir_html((FX / "yahoo_earnings_call.html").read_text(encoding="utf-8"))
    assert len(recs) == 179
    assert all(r.source == "Yahoo" and r.location == "" and r.summary == "" for r in recs)
    assert all(re.fullmatch(r"\d{4,6}", r.stock_id) for r in recs)
    assert all(r.event_time is None or re.fullmatch(r"\d\d:\d\d", r.event_time) for r in recs)
    assert {r.market for r in recs} == {"上市", "上櫃"}
    r = next(r for r in recs if r.stock_id == "1303")
    assert (r.name, r.event_date, r.event_time, r.market) == ("南亞", date(2026, 11, 9), "12:00", "上市")
    assert recs == sorted(recs, key=lambda x: (x.event_date, x.stock_id))
    assert len({(r.event_date, r.stock_id) for r in recs}) == len(recs)


def _row(sid, suffix, name, d, t):
    return (
        f'<li><div><div><a href="https://tw.stock.yahoo.com/quote/{sid}.{suffix}">{name}</a>'
        f'<span>{sid}.{suffix}</span></div></div><div>{d} {t}</div><div>國內</div></li>'
    )


def test_tw_two_mixed_and_excluded_links():
    html = (
        "<html><body><ul>"
        '<li><a href="/quote/2330.TW/health-check">健診</a></li>'
        + _row("2330", "TW", "台積電", "2026/11/13", "14:00")
        + _row("6488", "TWO", "環球晶", "2026/11/14", "9:30")
        + _row("2330", "TW", "台積電", "2026/11/13", "14:00")  # 重複
        + "</ul></body></html>"
    )
    recs = parse_yahoo_ir_html(html)
    assert [(r.stock_id, r.market, r.event_date, r.event_time) for r in recs] == [
        ("2330", "上市", date(2026, 11, 13), "14:00"),
        ("6488", "上櫃", date(2026, 11, 14), "09:30"),
    ]
    assert recs[1].name == "環球晶"


def test_row_without_date_skipped():
    assert parse_yahoo_ir_html("<ul><li><a href='/quote/1101.TW'>台泥</a></li></ul>") == []


def test_name_empty_ok():
    html = "<ul><li><a href='/quote/2002.TW'>2002.TW</a> 2026/12/01 10:00</li></ul>"
    recs = parse_yahoo_ir_html(html)
    assert len(recs) == 1
    assert recs[0].stock_id == "2002" and recs[0].name == ""


def test_no_links_raises():
    with pytest.raises(ParseError):
        parse_yahoo_ir_html("<html><body><p>nothing</p></body></html>")
    with pytest.raises(ParseError):
        parse_yahoo_ir_html("")
    with pytest.raises(ParseError):  # 只有被排除的連結
        parse_yahoo_ir_html('<a href="/quote/2330.TW/health-check">x</a>')
