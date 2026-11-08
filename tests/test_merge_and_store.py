"""FN-027 / FN-028 累積庫測試（FN-026 merge 測試在 test_merge.py）。"""
from datetime import date, datetime, timezone

import pytest

from core.models import IrRecord, UsEarningRecord
from storage.db import init_db
from storage.ir_store import load_ir_records, upsert_ir_records
from storage.us_store import load_us_earnings, upsert_us_earnings

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 8)


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "c.db")
    yield c
    c.close()


def ir(sid, d, source="MOPS", market="上市", name="N", **kw):
    return IrRecord(stock_id=sid, name=name, event_date=d, event_time=kw.get("t"),
                    market=market, location="", summary="", source=source)


def count(conn, where="1=1"):
    return conn.execute(f"SELECT COUNT(*) FROM tw_ir_events WHERE {where}").fetchone()[0]


SC = ("上市", 2026, 11)


def test_past_event_kept(conn):
    upsert_ir_records(conn, [ir("1101", date(2026, 8, 1))], NOW, TODAY)
    upsert_ir_records(conn, [ir("2330", date(2026, 11, 13))], NOW, TODAY,
                      [("上市", 2026, 8)], {("上市", 2026, 8): 1})
    assert count(conn, "stock_id='1101'") == 1


def test_scope_success_deletes_future_missing(conn):
    upsert_ir_records(conn, [ir("2330", date(2026, 11, 13)), ir("2317", date(2026, 11, 20))],
                      NOW, TODAY)
    st = upsert_ir_records(conn, [ir("2330", date(2026, 11, 13))], NOW, TODAY,
                           [SC], {SC: 1})
    assert st["deleted"] == 1
    assert count(conn, "stock_id='2317'") == 0 and count(conn, "stock_id='2330'") == 1


def test_scope_failure_keeps(conn):
    upsert_ir_records(conn, [ir("2330", date(2026, 11, 13)), ir("2317", date(2026, 11, 20))],
                      NOW, TODAY)
    # 該 scope 失敗：不在 reconcile_scopes
    st = upsert_ir_records(conn, [ir("2330", date(2026, 11, 13))], NOW, TODAY, [], {})
    assert st["deleted"] == 0 and count(conn) == 2
    st = upsert_ir_records(conn, [ir("2330", date(2026, 11, 13))], NOW, TODAY, None, None)
    assert st["deleted"] == 0 and count(conn) == 2


def test_guard_zero_results_no_delete(conn):
    upsert_ir_records(conn, [ir("2330", date(2026, 11, 13))], NOW, TODAY)
    st = upsert_ir_records(conn, [], NOW, TODAY, [SC], {SC: 0})
    assert st["deleted"] == 0 and count(conn) == 1


def test_guard_large_ratio_no_delete(conn):
    recs = [ir(f"{1000 + i}", date(2026, 11, 10 + i)) for i in range(6)]
    upsert_ir_records(conn, recs, NOW, TODAY)
    st = upsert_ir_records(conn, recs[:2], NOW, TODAY, [SC], {SC: 2})  # 待刪4/6
    assert st["deleted"] == 0 and count(conn) == 6


def test_normal_reschedule_deletes(conn):
    recs = [ir(f"{1000 + i}", date(2026, 11, 10 + i)) for i in range(10)]
    upsert_ir_records(conn, recs, NOW, TODAY)
    st = upsert_ir_records(conn, recs[1:], NOW, TODAY, [SC], {SC: 9})
    assert st["deleted"] == 1 and count(conn) == 9


def test_small_deletes_below_three_allowed(conn):
    recs = [ir("1000", date(2026, 11, 10)), ir("1001", date(2026, 11, 11))]
    upsert_ir_records(conn, recs, NOW, TODAY)
    st = upsert_ir_records(conn, recs[:1], NOW, TODAY, [SC], {SC: 1})  # 1/2 = 50%, 非 >50%
    assert st["deleted"] == 1


def test_scope_counts_none_no_reconcile(conn):
    recs = [ir("1000", date(2026, 11, 10)), ir("1001", date(2026, 11, 11))]
    upsert_ir_records(conn, recs, NOW, TODAY)
    st = upsert_ir_records(conn, recs[:1], NOW, TODAY, [SC], None)
    assert st["deleted"] == 0 and count(conn) == 2


def test_reconcile_only_mops_and_same_market(conn):
    upsert_ir_records(conn, [
        ir("1000", date(2026, 11, 10)),
        ir("2000", date(2026, 11, 11), source="Yahoo"),
        ir("3000", date(2026, 11, 12), market="上櫃"),
        ir("4000", date(2026, 12, 1)),
    ], NOW, TODAY)
    st = upsert_ir_records(conn, [ir("1000", date(2026, 11, 10))], NOW, TODAY,
                           [SC], {SC: 1})
    assert st["deleted"] == 0 and count(conn) == 4


def test_mops_over_yahoo_and_not_reverse(conn):
    d = date(2026, 11, 13)
    upsert_ir_records(conn, [ir("2330", d, source="Yahoo", name="")], NOW, TODAY)
    st = upsert_ir_records(conn, [ir("2330", d, source="MOPS", name="台積電")], NOW, TODAY)
    assert st["updated"] == 1
    st = upsert_ir_records(conn, [ir("2330", d, source="Yahoo", name="")], NOW, TODAY)
    assert st["skipped_yahoo"] == 1
    r = load_ir_records(conn, d, d)[0]
    assert r.source == "MOPS" and r.name == "台積電"


def test_first_seen_preserved(conn):
    d = date(2026, 11, 13)
    upsert_ir_records(conn, [ir("2330", d)], NOW, TODAY)
    later = datetime(2026, 11, 9, tzinfo=timezone.utc)
    upsert_ir_records(conn, [ir("2330", d)], later, TODAY)
    row = conn.execute("SELECT first_seen_at, last_seen_at FROM tw_ir_events").fetchone()
    assert row[0] == NOW.isoformat() and row[1] == later.isoformat()


def test_load_range(conn):
    upsert_ir_records(conn, [ir("1", date(2026, 11, 1)), ir("2", date(2026, 11, 10)),
                             ir("3", date(2026, 12, 1))], NOW, TODAY)
    got = load_ir_records(conn, date(2026, 11, 5), date(2026, 11, 30))
    assert [r.stock_id for r in got] == ["2"]
    assert got[0].event_date == date(2026, 11, 10)


# ---- us_store ----
def us(d, status="確認", t=None, session="盤後"):
    return UsEarningRecord(ticker="X", date_et=d, time_et=t, session=session, status=status)


def test_us_estimate_replaced_by_confirmed(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 19), "預估")], NOW, TODAY, True)
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25), "確認")], NOW, TODAY, True)
    rows = load_us_earnings(conn, ("NVDA",), date(2026, 11, 1), date(2026, 12, 31))
    assert [r.date_et for r in rows] == [date(2026, 11, 25)]
    assert rows[0].ticker == "NVDA" and rows[0].status == "確認"


def test_us_past_kept(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 8, 27), "已公布")], NOW, TODAY, True)
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, True)
    rows = load_us_earnings(conn, ("NVDA",), date(2026, 1, 1), date(2026, 12, 31))
    assert len(rows) == 2


def test_us_empty_records_no_delete(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, True)
    st = upsert_us_earnings(conn, "NVDA", [], NOW, TODAY, True)
    assert st["deleted"] == 0
    assert len(load_us_earnings(conn, ("NVDA",), date(2026, 1, 1), date(2027, 1, 1))) == 1


def test_us_only_past_records_no_delete(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, True)
    st = upsert_us_earnings(conn, "NVDA", [us(date(2026, 8, 1), "已公布")], NOW, TODAY, True)
    assert st["deleted"] == 0


def test_us_reconcile_false_keeps(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 19), "預估")], NOW, TODAY, True)
    st = upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, False)
    assert st["deleted"] == 0


def test_us_reconcile_scoped_to_ticker(conn):
    upsert_us_earnings(conn, "AAPL", [us(date(2026, 11, 19))], NOW, TODAY, True)
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, True)
    assert len(load_us_earnings(conn, ("AAPL",), date(2026, 11, 1), date(2026, 12, 31))) == 1


def test_us_watchlist_filter(conn):
    upsert_us_earnings(conn, "NVDA", [us(date(2026, 11, 25))], NOW, TODAY, True)
    upsert_us_earnings(conn, "INTC", [us(date(2026, 11, 26))], NOW, TODAY, True)
    rows = load_us_earnings(conn, ("NVDA",), date(2026, 11, 1), date(2026, 12, 31))
    assert {r.ticker for r in rows} == {"NVDA"}
    assert count_us(conn, "INTC") == 1  # 資料仍保留


def count_us(conn, t):
    return conn.execute("SELECT COUNT(*) FROM us_earnings WHERE ticker=?", (t,)).fetchone()[0]
