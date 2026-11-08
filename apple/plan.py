"""FN-051：plan_sync、apply_delete_guard（純函式 diff，架構 v4 V4-5）。"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from apple.models import (
    APPLE_MAP_VERSION,
    EventMapRow,
    EventPayload,
    PlannedCreate,
    PlannedDelete,
    PlannedUpdate,
    RemoteEvent,
    SyncPlan,
)

DELETE_GUARD_MIN = 5
DELETE_GUARD_RATIO = 0.5


def _dedupe(
    remote: list[RemoteEvent], mapped: dict[str, EventMapRow]
) -> tuple[dict[str, RemoteEvent], list[RemoteEvent]]:
    groups: dict[str, list[RemoteEvent]] = {}
    for r in remote:
        if r.uid is not None:
            groups.setdefault(r.uid, []).append(r)
    kept: dict[str, RemoteEvent] = {}
    dups: list[RemoteEvent] = []
    for uid, items in groups.items():
        m = mapped.get(uid)
        pick = None
        if m is not None:
            pick = next(
                (r for r in sorted(items, key=lambda x: x.calendar_item_identifier)
                 if r.calendar_item_identifier == m.calendar_item_identifier),
                None,
            )
        if pick is None:
            pick = min(items, key=lambda x: x.calendar_item_identifier)
        kept[uid] = pick
        seen_pick = False
        for r in items:
            if r is pick and not seen_pick:
                seen_pick = True
                continue
            dups.append(r)
    return kept, dups


def plan_sync(
    desired: list[EventPayload],
    remote: list[RemoteEvent],
    mapped: dict[str, EventMapRow],
    today: date,
    *,
    delete_future_missing: bool,
    map_version: int = APPLE_MAP_VERSION,
) -> SyncPlan:
    plan = SyncPlan()
    remote_by_uid, dups = _dedupe(remote, mapped)
    desired_uids = {p.uid for p in desired}

    for r in dups:
        plan.deletes.append(PlannedDelete(remote=r, reason="duplicate"))

    for p in desired:
        r = remote_by_uid.get(p.uid)
        m = mapped.get(p.uid)
        if r is None:
            plan.creates.append(PlannedCreate(payload=p, recreated=m is not None))
        elif m is None:
            plan.updates.append(PlannedUpdate(payload=p, remote=r, reason="unmapped"))
        elif m.map_version != map_version:
            plan.updates.append(PlannedUpdate(payload=p, remote=r, reason="version"))
        elif m.content_hash != p.content_hash:
            plan.updates.append(PlannedUpdate(payload=p, remote=r, reason="changed"))
        else:
            plan.unchanged.append(p.uid)
            if r.calendar_item_identifier != m.calendar_item_identifier:
                plan.map_refresh.append((p.uid, r))

    deleted_uids: list[str] = []
    for uid, r in remote_by_uid.items():
        if uid in desired_uids:
            continue
        if r.start_date >= today and delete_future_missing:
            plan.deletes.append(PlannedDelete(remote=r, reason="missing_future"))
            deleted_uids.append(uid)
        else:
            plan.kept_past.append(uid)

    drops = {u for u in mapped if u not in desired_uids and u not in remote_by_uid}
    drops.update(deleted_uids)
    plan.map_drops = sorted(drops)
    plan.kept_past.sort()

    plan.creates.sort(key=lambda c: (c.payload.start_date < today, c.payload.start_date, c.payload.uid))
    plan.updates.sort(key=lambda u: (u.payload.start_date, u.payload.uid))
    plan.deletes.sort(key=lambda d: (d.remote.start_date, d.remote.uid or "", d.remote.calendar_item_identifier))
    return plan


def apply_delete_guard(
    plan: SyncPlan,
    remote: list[RemoteEvent],
    desired_count: int,
    today: date,
    min_count: int = DELETE_GUARD_MIN,
    ratio: float = DELETE_GUARD_RATIO,
) -> SyncPlan:
    kept, _ = _dedupe(remote, {})
    future_owned = sum(1 for r in kept.values() if r.start_date >= today)
    missing = [d for d in plan.deletes if d.reason == "missing_future"]
    n = len(missing)
    trip = (desired_count == 0 and n > 0) or (n >= min_count and n > ratio * future_owned)
    if not trip:
        return plan
    gone = {d.remote.uid for d in missing}
    plan.deletes = [d for d in plan.deletes if d.reason != "missing_future"]
    plan.map_drops = [u for u in plan.map_drops if u not in gone]
    plan.guarded = True
    return plan
