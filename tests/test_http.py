"""FN-008 request_with_retry（含 R1 deadline）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from core.models import FetchError
from scrapers.http import request_with_retry


def _resp(status=200, text="ok"):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def _session(*outcomes):
    s = MagicMock()
    s.request.side_effect = list(outcomes)
    return s


def test_success_first_try_sets_utf8():
    r = _resp(200, "你好")
    s = _session(r)
    out = request_with_retry(s, "GET", "http://x", sleep=lambda x: None)
    assert out == "你好"
    assert r.encoding == "utf-8"
    assert s.request.call_count == 1
    assert s.request.call_args.kwargs["timeout"] == 15


def test_timeout_then_success_sleeps_once():
    s = _session(requests.Timeout("t"), _resp(200, "done"))
    sleeps = []
    out = request_with_retry(s, "POST", "http://x", data={"a": 1}, backoff_sec=3, sleep=sleeps.append)
    assert out == "done"
    assert sleeps == [3]
    assert s.request.call_count == 2
    assert s.request.call_args.args[0] == "POST"
    assert s.request.call_args.kwargs["data"] == {"a": 1}


def test_all_503_exhausts_retries():
    s = _session(*[_resp(503) for _ in range(5)])
    sleeps = []
    with pytest.raises(FetchError, match="failed after 3 attempts"):
        request_with_retry(s, "GET", "http://x", max_retries=2, sleep=sleeps.append)
    assert s.request.call_count == 3
    assert len(sleeps) == 2


def test_404_no_retry():
    s = _session(_resp(404))
    sleeps = []
    with pytest.raises(FetchError):
        request_with_retry(s, "GET", "http://x", sleep=sleeps.append)
    assert s.request.call_count == 1
    assert sleeps == []


def test_connection_error_retried_then_fails():
    s = _session(*[requests.ConnectionError("boom") for _ in range(3)])
    with pytest.raises(FetchError, match="ConnectionError"):
        request_with_retry(s, "GET", "http://x", max_retries=2, sleep=lambda x: None)
    assert s.request.call_count == 3


def test_other_request_exception_not_retried():
    s = _session(requests.exceptions.InvalidURL("bad"))
    with pytest.raises(FetchError):
        request_with_retry(s, "GET", "bad", sleep=lambda x: None)
    assert s.request.call_count == 1


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def test_deadline_caps_timeout_tuple():
    clk = FakeClock(100.0)
    s = _session(_resp(200, "x"))
    request_with_retry(s, "GET", "http://x", timeout=15, deadline=103.0, clock=clk)
    assert s.request.call_args.kwargs["timeout"] == (3.0, 3.0)
    s2 = _session(_resp(200, "x"))
    request_with_retry(s2, "GET", "http://x", timeout=15, deadline=200.0, clock=clk)
    assert s2.request.call_args.kwargs["timeout"] == (5.0, 15)


def test_deadline_already_passed_never_calls_session():
    clk = FakeClock(100.0)
    s = _session(_resp(200, "x"))
    with pytest.raises(FetchError, match="budget exhausted"):
        request_with_retry(s, "GET", "http://x", deadline=100.5, clock=clk)
    assert s.request.call_count == 0


def test_deadline_reached_during_retries_stops_session_calls():
    clk = FakeClock(0.0)

    def adv(sec):
        clk.t += sec

    calls = []

    def fail(*a, **k):
        calls.append(clk.t)
        clk.t += 4.0  # 每次請求耗 4 秒
        raise requests.Timeout("slow")

    s = MagicMock()
    s.request.side_effect = fail
    # deadline=10：第 1 次耗 4s(剩6) → 6 < backoff 3+1? 否 → sleep 3 → 剩 3 → 第 2 次(3>1)耗 4s → 剩 -1
    with pytest.raises(FetchError):
        request_with_retry(s, "GET", "http://x", max_retries=5, backoff_sec=3,
                           sleep=adv, deadline=10.0, clock=clk)
    assert len(calls) == 2  # 之後預算用盡，不再呼叫 session


def test_backoff_skipped_when_budget_low():
    clk = FakeClock(0.0)
    s = _session(requests.Timeout("t"), _resp(200, "x"))
    sleeps = []
    with pytest.raises(FetchError, match="failed after 1 attempts"):
        request_with_retry(s, "GET", "http://x", max_retries=2, backoff_sec=3,
                           sleep=sleeps.append, deadline=3.5, clock=clk)
    assert sleeps == []
    assert s.request.call_count == 1
