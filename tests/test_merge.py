from datetime import date

from core.models import IrRecord
from processors.tw_earnings import merge_ir_records


def rec(sid, d, src, name="台積電"):
    return IrRecord(sid, name, d, None, "上市", "", "", src)


def test_merge_basic():
    m = [rec("2330", date(2026, 11, 13), "MOPS")]
    y = [rec("2330", date(2026, 11, 13), "Yahoo"), rec("2454", date(2026, 11, 20), "Yahoo", "")]
    out = merge_ir_records(m, y)
    assert len(out) == 2
    assert out[0].stock_id == "2330" and out[0].source == "MOPS"
    assert out[1].stock_id == "2454" and out[1].source == "Yahoo"


def test_empty():
    assert merge_ir_records([], []) == []


def test_name_backfill_and_sorted():
    m = [rec("2330", date(2026, 11, 13), "MOPS")]
    y = [rec("2330", date(2026, 12, 1), "Yahoo", "")]
    out = merge_ir_records(m, y)
    assert [r.event_date for r in out] == [date(2026, 11, 13), date(2026, 12, 1)]
    assert out[1].name == "台積電" and out[1].source == "Yahoo"
