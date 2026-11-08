from datetime import date

from apple.models import (
    APPLE_MAP_VERSION,
    EventMapRow,
    EventPayload,
    RemoteEvent,
)
from apple.plan import DELETE_GUARD_MIN, DELETE_GUARD_RATIO, apply_delete_guard, plan_sync

TODAY = date(2026, 11, 8)
SFX = "@market-calendar-ics"


def uid(n):
    return f"tw-{n}{SFX}"


def pay(n, d=date(2026, 11, 20), h="h1"):
    return EventPayload(
        uid=uid(n), cal_id="tw-settlement", title=n, all_day=True, start=d, end=d, notes="",
        location="", url=f"market-calendar-ics://{uid(n)}", alarm_offset_sec=-14400,
        content_hash=h, start_date=d,
    )


def rem(n, d=date(2026, 11, 20), item=None, with_uid=True):
    return RemoteEvent(
        calendar_item_identifier=item or f"item-{n}", event_identifier=f"ev-{n}",
        calendar_identifier="cal", uid=uid(n) if with_uid else None, start_date=d,
    )


def row(n, d=date(2026, 11, 20), h="h1", item=None, ver=APPLE_MAP_VERSION):
    return EventMapRow(
        uid=uid(n), cal_id="tw-settlement", calendar_identifier="cal", event_identifier=f"ev-{n}",
        calendar_item_identifier=item or f"item-{n}", content_hash=h, map_version=ver,
        start_date=d, synced_at="2026-11-08T00:00:00Z",
    )


def run(desired, remote, mapped, **kw):
    kw.setdefault("delete_future_missing", True)
    return plan_sync(desired, remote, {r.uid: r for r in mapped}, TODAY, **kw)


def test_constants():
    assert DELETE_GUARD_MIN == 5 and DELETE_GUARD_RATIO == 0.5


def test_all_create():
    p = run([pay("a"), pay("b"), pay("c")], [], [])
    assert len(p.creates) == 3 and all(not c.recreated for c in p.creates)
    assert not p.updates and not p.deletes and not p.unchanged


def test_all_unchanged():
    p = run([pay("a"), pay("b")], [rem("a"), rem("b")], [row("a"), row("b")])
    assert sorted(p.unchanged) == [uid("a"), uid("b")]
    assert not (p.creates or p.updates or p.deletes or p.map_refresh or p.map_drops or p.kept_past)


def test_changed():
    p = run([pay("a", h="new"), pay("b")], [rem("a"), rem("b")], [row("a"), row("b")])
    assert [(u.payload.uid, u.reason) for u in p.updates] == [(uid("a"), "changed")]
    assert p.unchanged == [uid("b")]


def test_unmapped():
    p = run([pay("a"), pay("b")], [rem("a"), rem("b")], [])
    assert not p.creates
    assert [u.reason for u in p.updates] == ["unmapped", "unmapped"]


def test_version():
    p = run([pay("a"), pay("b")], [rem("a"), rem("b")], [row("a"), row("b")], map_version=0)
    assert [u.reason for u in p.updates] == ["version", "version"]
    assert not p.unchanged


def test_recreated_when_mapped_but_missing_remote():
    p = run([pay("a")], [], [row("a")])
    assert len(p.creates) == 1 and p.creates[0].recreated is True
    assert p.map_drops == []


def test_foreign_events_ignored():
    p = run([pay("a")], [rem("a"), rem("x", with_uid=False)], [row("a")])
    assert p.unchanged == [uid("a")] and not p.deletes and not p.kept_past


def test_missing_future_deleted_past_kept():
    fut, past = date(2026, 12, 1), date(2026, 11, 1)
    p = run([], [rem("f", fut), rem("p", past), rem("t", TODAY)], [row("f", fut), row("p", past), row("t", TODAY)])
    assert sorted(d.remote.uid for d in p.deletes) == [uid("f"), uid("t")]
    assert all(d.reason == "missing_future" for d in p.deletes)
    assert p.kept_past == [uid("p")]
    assert sorted(p.map_drops) == [uid("f"), uid("t")]


def test_delete_future_missing_false():
    fut = date(2026, 12, 1)
    p = run([], [rem("f", fut)], [row("f", fut)], delete_future_missing=False)
    assert not p.deletes and p.kept_past == [uid("f")] and p.map_drops == []


def test_map_drops_stale_row():
    p = run([pay("a")], [rem("a")], [row("a"), row("gone")])
    assert p.map_drops == [uid("gone")]


def test_duplicates_prefer_mapped_item():
    remote = [rem("a", item="i1"), rem("a", item="i2"), rem("a", item="i3")]
    p = run([pay("a")], remote, [row("a", item="i2")])
    assert [d.remote.calendar_item_identifier for d in p.deletes] == ["i1", "i3"]
    assert all(d.reason == "duplicate" for d in p.deletes)
    assert p.unchanged == [uid("a")] and not p.map_refresh and p.map_drops == []


def test_duplicates_smallest_when_unmapped():
    p = run([pay("a")], [rem("a", item="i3"), rem("a", item="i1")], [])
    assert [d.remote.calendar_item_identifier for d in p.deletes] == ["i3"]
    assert p.updates[0].remote.calendar_item_identifier == "i1"


def test_duplicate_past_still_deleted():
    past = date(2026, 10, 1)
    p = run([pay("a", past)], [rem("a", past, item="i1"), rem("a", past, item="i2")], [row("a", past, item="i1")])
    assert [d.reason for d in p.deletes] == ["duplicate"]


def test_map_refresh():
    p = run([pay("a")], [rem("a", item="new")], [row("a", item="old")])
    assert p.unchanged == [uid("a")]
    assert [(u, r.calendar_item_identifier) for u, r in p.map_refresh] == [(uid("a"), "new")]


def test_sorting():
    past, f1, f2 = date(2026, 11, 1), date(2026, 11, 10), date(2026, 11, 12)
    p = run([pay("p", past), pay("f2", f2), pay("f1", f1), pay("f0", f1)], [], [])
    assert [c.payload.uid for c in p.creates] == [uid("f0"), uid("f1"), uid("f2"), uid("p")]


def _guard_setup(n_missing, n_kept, desired_kept=True):
    fut = date(2026, 12, 1)
    remote = [rem(f"m{i}", fut) for i in range(n_missing)] + [rem(f"k{i}", fut) for i in range(n_kept)]
    mapped = [row(f"m{i}", fut) for i in range(n_missing)] + [row(f"k{i}", fut) for i in range(n_kept)]
    desired = [pay(f"k{i}", fut) for i in range(n_kept)]
    plan = run(desired, remote, mapped)
    return plan, remote, len(desired)


def test_guard_desired_zero():
    plan, remote, dc = _guard_setup(2, 0)
    g = apply_delete_guard(plan, remote, dc, TODAY)
    assert g.guarded and not g.deletes and g.map_drops == []


def test_guard_ratio_trips():
    plan, remote, dc = _guard_setup(6, 5)  # n=6>=5, 6 > 0.5*11
    g = apply_delete_guard(plan, remote, dc, TODAY)
    assert g.guarded and not g.deletes and g.map_drops == []


def test_guard_not_tripped():
    plan, remote, dc = _guard_setup(5, 10)  # 5 >= 5 but 5 > 7.5 false
    g = apply_delete_guard(plan, remote, dc, TODAY)
    assert not g.guarded and len(g.deletes) == 5 and len(g.map_drops) == 5
    plan, remote, dc = _guard_setup(4, 0 + 1)  # n<min
    g = apply_delete_guard(plan, remote, dc, TODAY)
    assert not g.guarded and len(g.deletes) == 4


def test_guard_keeps_duplicates():
    fut = date(2026, 12, 1)
    remote = [rem("m0", fut), rem("d", fut, item="i1"), rem("d", fut, item="i2"), rem("k", fut)]
    mapped = [row("m0", fut), row("d", fut, item="i1"), row("k", fut)]
    plan = run([pay("d", fut), pay("k", fut)], remote, mapped)
    # n=1, desired>0, n<min → 不觸發；改 min_count=1、ratio=0 觸發
    g = apply_delete_guard(plan, remote, 2, TODAY, min_count=1, ratio=0.0)
    assert g.guarded
    assert [d.reason for d in g.deletes] == ["duplicate"]
    assert uid("m0") not in g.map_drops


def test_guard_no_missing_noop():
    plan = run([pay("a")], [rem("a")], [row("a")])
    g = apply_delete_guard(plan, [rem("a")], 1, TODAY)
    assert not g.guarded
