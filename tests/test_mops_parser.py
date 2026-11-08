"""FN-009 parse_mops_ir_html（離線 fixture）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.models import ParseError
from scrapers.mops_ir import parse_mops_ir_html

FX = Path(__file__).resolve().parent / "fixtures"


def _read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_sii_real_fixture():
    recs = parse_mops_ir_html(_read("mops_t100sb02_1_sii_115_11.html"), "sii")
    assert len(recs) == 170  # 173 列，去重 (stock_id, date) 後 170
    assert all(r.market == "上市" and r.source == "MOPS" for r in recs)
    r = next(r for r in recs if r.stock_id == "1102")
    assert r.name == "亞泥"
    assert r.event_date == date(2026, 11, 16)
    assert r.event_time == "14:00"
    assert r.location == "線上法說會"
    # month 參數＝召開月份：所有 event_date 都在 2026-11
    assert {(x.event_date.year, x.event_date.month) for x in recs} == {(2026, 11)}
    assert recs == sorted(recs, key=lambda x: (x.event_date, x.stock_id))
    assert all(len(x.summary) <= 500 for x in recs)


def test_otc_real_fixture():
    recs = parse_mops_ir_html(_read("mops_t100sb02_1_otc_115_11.html"), "otc")
    assert len(recs) == 69
    assert all(r.market == "上櫃" for r in recs)


def test_dec_fixture_range_dates_in_november():
    recs = parse_mops_ir_html(_read("mops_t100sb02_1_sii_115_12.html"), "sii")
    nov = [r for r in recs if r.event_date.month == 11]
    assert len(nov) == 2  # 跨月區間：起日在 11 月，出現在 12 月查詢中
    assert all(r.stock_id == "2356" for r in nov)
    assert all(r.summary.startswith("（日期：115/11/") for r in nov)
    # 重複列（台達電 12/01 09:30 兩筆）只留一筆
    assert len([r for r in recs if r.stock_id == "2308" and r.event_date == date(2026, 12, 1)]) == 1


def test_synthetic_edge_cases():
    recs = parse_mops_ir_html(_read("mops_synthetic.html"), "sii")
    by = {r.stock_id: r for r in recs}
    assert by["2330"].event_date == date(2026, 11, 16)  # 區間取第一天
    assert by["2330"].summary == "（日期：115/11/16 至 115/11/17）區間日期測試"
    assert by["2454"].event_time is None  # 空時間
    assert by["3711"].event_time is None  # 「下午2:00」
    assert by["3711"].summary == "非標準時間 含 多餘 空白"  # 空白壓縮
    assert by["2317"].event_time == "09:30"  # 9:30 補零
    assert by["1101"].event_time is None  # 25:99 非法
    assert "9999" not in by  # 日期無法解析 → 跳過
    assert "合計" not in by
    dup = [r for r in recs if r.stock_id == "2308"]
    assert len(dup) == 1 and dup[0].event_time == "09:30" and "第一筆" in dup[0].summary


def test_summary_truncated_to_500():
    long = "字" * 800
    html = (
        "<table><tr><th>公司代號</th><th>公司名稱</th><th>召開法人說明會日期</th></tr>"
        f"<tr><td>2330</td><td>台積電</td><td>115/11/16</td><td>14:00</td><td>台北</td><td>{long}</td></tr></table>"
    )
    recs = parse_mops_ir_html(html, "sii")
    assert len(recs[0].summary) == 500


def test_four_digit_year_not_shifted():
    html = (
        "<table><tr><th>召開法人說明會日期</th></tr>"
        "<tr><td>2330</td><td>台積電</td><td>2026/11/16</td><td>14:00</td><td>x</td><td>y</td></tr></table>"
    )
    assert parse_mops_ir_html(html, "sii")[0].event_date == date(2026, 11, 16)


def test_empty_returns_empty_list():
    assert parse_mops_ir_html(_read("mops_t100sb02_1_empty.html"), "sii") == []


def test_error_page_raises():
    with pytest.raises(ParseError):
        parse_mops_ir_html(_read("mops_error_page.html"), "sii")


def test_unrecognizable_raises():
    with pytest.raises(ParseError):
        parse_mops_ir_html("<html><body>hello</body></html>", "otc")
    with pytest.raises(ParseError):
        parse_mops_ir_html("", "otc")


def test_bad_market_value_error():
    with pytest.raises(ValueError):
        parse_mops_ir_html("<html></html>", "foo")  # type: ignore[arg-type]
