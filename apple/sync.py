"""FN-053 sync_calendar、FN-054 sync_apple_calendars（架構 v4 V4-5 + v4.1 R11/R15-9）。

只透過 CalendarAdapter 操作行事曆；EventKit 由 EventKitAdapter.create() 延遲載入。
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Callable

from apple.adapter import CalendarAdapter
from apple.calendars import ensure_calendar
from apple.eventkit_adapter import EventKitAdapter
from apple.mapping import build_payloads
from apple.models import (
    APPLE_MAP_VERSION,
    AppleCalendarError,
    EventMapRow,
    EventPayload,
    RemoteEvent,
    SyncResult,
)
from apple.plan import apply_delete_guard, plan_sync
from core.config import AppConfig
from core.models import CAL_ORDER, CalendarId, Event, RunContext
from storage.apple_map_store import (
    apply_event_map_changes,
    get_calendar_map,
    load_event_map,
    purge_stale_event_map,
)



def sync_calendar(
    adapter: CalendarAdapter,
    conn: sqlite3.Connection,
    cfg: AppConfig,
    cal_id: CalendarId,
    events: list[Event],
    *,
    today: date,
    now_utc: datetime,
    range_start: date,
    range_end: date,
    logger: logging.Logger,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    exclude_ids: frozenset[str] = frozenset(),
) -> SyncResult:
    # 1. 時間窗（往後多留一年；不超過 predicate 的 4 年上限）
    window = (
        range_start,
        min(
            max(range_end, today) + timedelta(days=366),
            range_start + timedelta(days=4 * 365 - 2),
        ),
    )
    # 2. 行事曆（R15-9：create/rename_calendar 會立即 commit，只能在套用階段前呼叫）
    cal = ensure_calendar(
        adapter, conn, cal_id, cfg.calendars[cal_id].name, cfg.apple_calendar.prefer_source,
        window, now_utc, logger, exclude_ids,
    )
    # R11：確定行事曆後、fetch 前明確登記受管
    adapter.mark_managed(cal.identifier)
    # 3.
    n = purge_stale_event_map(conn, cal_id, cal.identifier)
    if n > 0:
        logger.info("apple %s: calendar changed; purged %d stale map rows", cal_id, n)
    # 4. 5.
    payloads = build_payloads(events, cfg)
    by_uid: dict[str, EventPayload] = {p.uid: p for p in payloads}
    remote = adapter.fetch_events(cal.identifier, *window)
    mapped = load_event_map(conn, cal_id)
    # 6.
    plan = plan_sync(
        payloads, remote, mapped, today,
        delete_future_missing=cfg.apple_calendar.delete_future_missing,
    )
    n_missing = sum(1 for d in plan.deletes if d.reason == "missing_future")
    plan = apply_delete_guard(plan, remote, len(payloads), today)
    if plan.guarded:
        logger.warning(
            "apple %s: delete guard tripped; %d future events missing from source were NOT deleted "
            "(delete the whole calendar in the Calendar app to rebuild it if intended)",
            cal_id, n_missing,
        )

    # 7. 套用：deletes → updates → creates
    res = SyncResult(cal_id=cal_id, calendar_identifier=cal.identifier)
    res.guarded = plan.guarded
    ops: list[tuple[str, object]] = (
        [("delete", d) for d in plan.deletes]
        + [("update", u) for u in plan.updates]
        + [("create", c) for c in plan.creates]
    )
    upserts_src: list[tuple[EventPayload, RemoteEvent | None, str | None]] = []
    not_done_delete_uids: set[str] = set()
    recreated_uids: list[str] = []
    for i, (kind, item) in enumerate(ops):
        if deadline is not None and clock() >= deadline:
            res.deferred = len(ops) - i
            for k2, it2 in ops[i:]:
                if k2 == "delete" and it2.reason == "missing_future" and it2.remote.uid:  # type: ignore[attr-defined]
                    not_done_delete_uids.add(it2.remote.uid)  # type: ignore[attr-defined]
            break
        try:
            if kind == "delete":
                adapter.delete_event(item.remote)  # type: ignore[attr-defined]
                res.deleted += 1
                if item.reason == "duplicate":  # type: ignore[attr-defined]
                    res.duplicates_deleted += 1
            elif kind == "update":
                adapter.update_event(item.remote, item.payload)  # type: ignore[attr-defined]
                res.updated += 1
                upserts_src.append((item.payload, item.remote, None))  # type: ignore[attr-defined]
            else:
                token = adapter.create_event(cal.identifier, item.payload)  # type: ignore[attr-defined]
                res.created += 1
                if item.recreated:  # type: ignore[attr-defined]
                    res.recreated += 1
                    recreated_uids.append(item.payload.uid)  # type: ignore[attr-defined]
                upserts_src.append((item.payload, None, token))  # type: ignore[attr-defined]
        except AppleCalendarError as e:
            if kind == "delete":
                uid = item.remote.uid  # type: ignore[attr-defined]
                if item.reason == "missing_future" and uid:  # type: ignore[attr-defined]
                    not_done_delete_uids.add(uid)
                label = uid or item.remote.calendar_item_identifier  # type: ignore[attr-defined]
            else:
                label = item.payload.uid  # type: ignore[attr-defined]
            res.errors.append(f"{label}: {e}")
            logger.error("apple %s: %s %s failed: %s", cal_id, kind, label, e)

    # 8. commit（失敗 → rollback 並往外丟，對應表不動）
    try:
        adapter.commit()
    except AppleCalendarError:
        adapter.rollback()
        raise

    # 9. 對應表
    synced_at = now_utc.isoformat()
    upserts: list[EventMapRow] = []

    def _row(p: EventPayload, evt_id: str | None, item_id: str) -> EventMapRow:
        return EventMapRow(
            uid=p.uid, cal_id=cal_id, calendar_identifier=cal.identifier,
            event_identifier=evt_id, calendar_item_identifier=item_id,
            content_hash=p.content_hash, map_version=APPLE_MAP_VERSION,
            start_date=p.start_date, synced_at=synced_at,
        )

    for p, rem, token in upserts_src:
        if rem is not None:
            upserts.append(_row(p, rem.event_identifier, rem.calendar_item_identifier))
        else:
            try:
                evt_id, item_id = adapter.identifiers(token)  # type: ignore[arg-type]
            except AppleCalendarError as e:
                logger.warning("apple %s: identifiers unavailable for %s: %s", cal_id, p.uid, e)
                continue
            upserts.append(_row(p, evt_id, item_id))
    for uid, rem in plan.map_refresh:
        p = by_uid.get(uid)
        if p is not None:
            upserts.append(_row(p, rem.event_identifier, rem.calendar_item_identifier))
    drops = [u for u in plan.map_drops if u not in not_done_delete_uids]
    apply_event_map_changes(conn, cal_id, cal.identifier, upserts, drops)

    res.unchanged = len(plan.unchanged)
    res.kept_past = len(plan.kept_past)

    # 10. 日誌
    logger.info(
        "apple %s: created %d (recreated %d), updated %d, deleted %d (dup %d), unchanged %d, "
        "kept-past %d, deferred %d, errors %d [%s]",
        cal_id, res.created, res.recreated, res.updated, res.deleted, res.duplicates_deleted,
        res.unchanged, res.kept_past, res.deferred, len(res.errors), cal.source_title,
    )
    if res.recreated > 0:
        logger.info("apple %s: recreated uids: %s", cal_id, ", ".join(recreated_uids[:10]))
    if res.deferred > 0:
        logger.warning(
            "apple %s: %d operations deferred to next run (time budget)", cal_id, res.deferred
        )
    return res


def sync_apple_calendars(
    ctx: RunContext,
    produced: dict[CalendarId, list[Event]],
    adapter: CalendarAdapter | None = None,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    adapter_factory: Callable[[], CalendarAdapter] | None = None,
) -> list[str]:
    logger = ctx.logger
    if adapter is None:
        try:
            factory = adapter_factory or (lambda: EventKitAdapter.create(ctx.cfg.timezone))
            adapter = factory()
        except Exception:  # noqa: BLE001  ImportError、建立 store 失敗等
            logger.exception("apple calendar: EventKit unavailable")
            return ["apple:init"]

    try:
        status = adapter.authorization_status()
    except Exception:  # noqa: BLE001
        logger.exception("apple calendar: cannot read authorization status")
        return ["apple:init"]
    logger.info("apple calendar: authorization status=%s", status)
    if status != "fullAccess":
        from scheduler.app_bundle import default_app_path

        app = default_app_path(ctx.prj_root)
        logger.error(
            "apple calendar: not authorized (status=%s); run 'open \"%s\" --args --authorize' "
            "(or '.venv/bin/python main.py --authorize' in Terminal). "
            "F-008 skipped; .ics files are unaffected",
            status, app,
        )
        return ["apple:authorization"]

    failures: list[str] = []
    for cal_id in CAL_ORDER:
        if cal_id not in produced:
            logger.info("apple %s: not produced this run; skipped", cal_id)
            continue
        if deadline is not None and clock() >= deadline:
            logger.warning("apple %s: skipped (time budget exhausted)", cal_id)
            continue
        try:
            exclude = set()
            for other in CAL_ORDER:
                if other == cal_id:
                    continue
                row = get_calendar_map(ctx.conn, other)
                if row is not None:
                    exclude.add(row.calendar_identifier)
            res = sync_calendar(
                adapter, ctx.conn, ctx.cfg, cal_id, produced[cal_id],
                today=ctx.today, now_utc=ctx.now_utc,
                range_start=ctx.range_start, range_end=ctx.range_end, logger=logger,
                deadline=deadline, clock=clock, exclude_ids=frozenset(exclude),
            )
            if not res.ok:
                failures.append(f"apple:{cal_id}")
        except Exception:  # noqa: BLE001
            logger.exception(f"apple {cal_id} sync failed")
            failures.append(f"apple:{cal_id}")
    return failures
