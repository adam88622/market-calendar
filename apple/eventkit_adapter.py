"""FN-055：EventKitAdapter（真實實作，架構 v4 + v4.1 R11/R15）。

本檔是整個專案唯一使用 EventKit / Foundation 的地方，且 import 延遲到 EventKitAdapter.create() 內；
單純 `import apple.eventkit_adapter` 不會載入 EventKit，也不會建立 EKEventStore。
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apple.mapping import uid_from_url
from apple.models import (
    AppleCalendarError,
    AuthStatus,
    CalendarInfo,
    EventPayload,
    RemoteEvent,
    SourceInfo,
)

MAX_WINDOW_DAYS = 4 * 365

_STATUS_NAMES: dict[int, AuthStatus] = {
    0: "notDetermined",
    1: "restricted",
    2: "denied",
    3: "fullAccess",
    4: "writeOnly",
}


def _status_name(value: int) -> AuthStatus:
    """EKAuthorizationStatus → AuthStatus；未知值一律視為 denied。"""
    try:
        return _STATUS_NAMES.get(int(value), "denied")
    except (TypeError, ValueError):
        return "denied"


def _err_text(err: Any) -> str:
    if err is None:
        return "unknown error"
    try:
        return str(err.localizedDescription())
    except Exception:  # noqa: BLE001
        return str(err)


class EventKitAdapter:
    """結構上符合 CalendarAdapter。請用 EventKitAdapter.create() 建立。"""

    def __init__(self, ek: Any, foundation: Any, store: Any, tz_name: str = "Asia/Taipei") -> None:
        self._ek = ek
        self._f = foundation
        self._store = store
        self._tz_name = tz_name
        self._tz = ZoneInfo(tz_name)
        self._handles: dict[str, Any] = {}  # calendarItemIdentifier → EKEvent
        self._pending: dict[str, Any] = {}  # token → EKEvent（尚未 commit 的新事件）
        self._managed: set[str] = set()
        self._dirty = 0  # 未 commit 的 create/update/delete 數

    @classmethod
    def create(cls, tz_name: str = "Asia/Taipei") -> "EventKitAdapter":
        if "pytest" in sys.modules and __import__("os").environ.get("MCI_EVENTKIT_TESTS") != "1":
            raise RuntimeError(
                "EventKitAdapter.create() is disabled under pytest; set MCI_EVENTKIT_TESTS=1 to enable"
            )
        try:
            import EventKit as ek  # noqa: PLC0415
            import Foundation as foundation  # noqa: PLC0415

            store = ek.EKEventStore.alloc().init()
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"cannot initialise EventKit: {exc}") from exc
        return cls(ek, foundation, store, tz_name)

    # ---- 授權 ----
    def authorization_status(self) -> AuthStatus:
        try:
            v = self._ek.EKEventStore.authorizationStatusForEntityType_(self._ek.EKEntityTypeEvent)
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"authorization status failed: {exc}") from exc
        return _status_name(v)

    def request_full_access(self, timeout_sec: float = 120.0) -> tuple[bool, str | None]:
        done = threading.Event()
        box: dict[str, Any] = {}

        def handler(granted: bool, error: Any) -> None:
            box["granted"] = bool(granted)
            box["error"] = None if error is None else _err_text(error)
            done.set()

        try:
            self._store.requestFullAccessToEventsWithCompletion_(handler)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        deadline = time.monotonic() + timeout_sec
        while not done.is_set():
            if time.monotonic() >= deadline:
                return False, "timeout"
            self._f.NSRunLoop.currentRunLoop().runUntilDate_(
                self._f.NSDate.dateWithTimeIntervalSinceNow_(0.2)
            )
        return box["granted"], box["error"]

    # ---- 來源 / 行事曆 ----
    def list_sources(self) -> list[SourceInfo]:
        try:
            return [
                SourceInfo(str(s.sourceIdentifier()), str(s.title()), int(s.sourceType()))
                for s in self._store.sources()
            ]
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"list_sources failed: {exc}") from exc

    def _info(self, cal: Any) -> CalendarInfo:
        src = cal.source()
        return CalendarInfo(
            identifier=str(cal.calendarIdentifier()),
            title=str(cal.title()),
            source_identifier=str(src.sourceIdentifier()) if src is not None else "",
            source_title=str(src.title()) if src is not None else "",
            source_type=int(src.sourceType()) if src is not None else -1,
            cal_type=int(cal.type()),
            writable=bool(cal.allowsContentModifications()),
        )

    def list_calendars(self) -> list[CalendarInfo]:
        try:
            cals = self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent)
            return [self._info(c) for c in cals]
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"list_calendars failed: {exc}") from exc

    def get_calendar(self, identifier: str) -> CalendarInfo | None:
        try:
            cal = self._store.calendarWithIdentifier_(identifier)
            return None if cal is None else self._info(cal)
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"get_calendar failed: {identifier}: {exc}") from exc

    def _require_no_pending(self, what: str) -> None:
        if self._dirty:
            raise AppleCalendarError(f"{what} called with uncommitted staged operations")

    def create_calendar(self, title: str, source_identifier: str) -> CalendarInfo:
        self._require_no_pending("create_calendar")
        try:
            src = next(
                (s for s in self._store.sources() if str(s.sourceIdentifier()) == source_identifier),
                None,
            )
            if src is None:
                raise AppleCalendarError(f"source not found: {source_identifier}")
            cal = self._ek.EKCalendar.calendarForEntityType_eventStore_(
                self._ek.EKEntityTypeEvent, self._store
            )
            cal.setTitle_(title)
            cal.setSource_(src)
            ok, err = self._store.saveCalendar_commit_error_(cal, True, None)
        except AppleCalendarError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"create_calendar failed: {title}: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"create_calendar failed: {title}: {_err_text(err)}")
        return self._info(cal)

    def rename_calendar(self, identifier: str, title: str) -> None:
        self._require_no_pending("rename_calendar")
        try:
            cal = self._store.calendarWithIdentifier_(identifier)
            if cal is None:
                raise AppleCalendarError(f"calendar not found: {identifier}")
            cal.setTitle_(title)
            ok, err = self._store.saveCalendar_commit_error_(cal, True, None)
        except AppleCalendarError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"rename_calendar failed: {identifier}: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"rename_calendar failed: {identifier}: {_err_text(err)}")

    def mark_managed(self, calendar_identifier: str) -> None:
        if self.get_calendar(calendar_identifier) is None:
            raise AppleCalendarError(f"calendar not found: {calendar_identifier}")
        self._managed.add(calendar_identifier)

    # ---- 日期輔助 ----
    def _local_midnight(self, d: date) -> Any:
        comps = self._f.NSDateComponents.alloc().init()
        comps.setYear_(d.year)
        comps.setMonth_(d.month)
        comps.setDay_(d.day)
        comps.setHour_(0)
        comps.setMinute_(0)
        comps.setSecond_(0)
        nsd = self._f.NSCalendar.currentCalendar().dateFromComponents_(comps)
        if nsd is None:
            raise AppleCalendarError(f"cannot build NSDate for {d}")
        return nsd

    def _local_date(self, nsdate: Any) -> date:
        unit = self._f.NSCalendarUnitYear | self._f.NSCalendarUnitMonth | self._f.NSCalendarUnitDay
        c = self._f.NSCalendar.currentCalendar().components_fromDate_(unit, nsdate)
        return date(int(c.year()), int(c.month()), int(c.day()))

    def _remote(self, ev: Any) -> RemoteEvent:
        url = ev.URL()
        uid = uid_from_url(str(url.absoluteString()) if url is not None else None)
        if ev.isAllDay():
            sd = self._local_date(ev.startDate())
        else:
            sd = datetime.fromtimestamp(ev.startDate().timeIntervalSince1970(), self._tz).date()
        eid = ev.eventIdentifier()
        return RemoteEvent(
            calendar_item_identifier=str(ev.calendarItemIdentifier()),
            event_identifier=None if eid is None else str(eid),
            calendar_identifier=str(ev.calendar().calendarIdentifier()),
            uid=uid,
            start_date=sd,
        )

    def _apply(self, ev: Any, p: EventPayload) -> None:
        f = self._f
        ev.setTitle_(p.title)
        ev.setAllDay_(bool(p.all_day))
        if p.all_day:
            ev.setTimeZone_(None)
            s = p.start.date() if isinstance(p.start, datetime) else p.start
            e = p.end.date() if isinstance(p.end, datetime) else p.end
            ev.setStartDate_(self._local_midnight(s))
            ev.setEndDate_(self._local_midnight(e))
        else:
            ev.setTimeZone_(f.NSTimeZone.timeZoneWithName_(self._tz_name))
            ev.setStartDate_(f.NSDate.dateWithTimeIntervalSince1970_(p.start.timestamp()))
            ev.setEndDate_(f.NSDate.dateWithTimeIntervalSince1970_(p.end.timestamp()))
        ev.setNotes_(p.notes or None)
        ev.setLocation_(p.location or None)
        ev.setURL_(f.NSURL.URLWithString_(p.url))
        ev.setAlarms_([self._ek.EKAlarm.alarmWithRelativeOffset_(float(p.alarm_offset_sec))])
        ev.setAvailability_(self._ek.EKEventAvailabilityFree)

    # ---- 事件 ----
    def fetch_events(self, calendar_identifier: str, start: date, end: date) -> list[RemoteEvent]:
        if (end - start).days >= MAX_WINDOW_DAYS:
            raise ValueError(f"fetch window must be < 4 years: {start}..{end}")
        try:
            cal = self._store.calendarWithIdentifier_(calendar_identifier)
            if cal is None:
                raise AppleCalendarError(f"calendar not found: {calendar_identifier}")
            pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
                self._local_midnight(start), self._local_midnight(end + timedelta(days=1)), [cal]
            )
            events = list(self._store.eventsMatchingPredicate_(pred) or [])
            out = []
            for ev in events:
                r = self._remote(ev)
                self._handles[r.calendar_item_identifier] = ev
                out.append(r)
        except AppleCalendarError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"fetch failed: {calendar_identifier}: {exc}") from exc
        out.sort(key=lambda r: (r.start_date, r.calendar_item_identifier))
        return out

    def _require_managed(self, calendar_identifier: str, what: str, uid: str | None) -> None:
        if calendar_identifier not in self._managed:
            raise AppleCalendarError(
                f"{what} failed: uid={uid} calendar {calendar_identifier} is not a managed calendar"
            )

    def create_event(self, calendar_identifier: str, payload: EventPayload) -> str:
        self._require_managed(calendar_identifier, "create", payload.uid)
        try:
            cal = self._store.calendarWithIdentifier_(calendar_identifier)
            if cal is None:
                raise AppleCalendarError(
                    f"create failed: uid={payload.uid} calendar not found {calendar_identifier}"
                )
            if not cal.allowsContentModifications():
                raise AppleCalendarError(f"create failed: uid={payload.uid} calendar not writable")
            ev = self._ek.EKEvent.eventWithEventStore_(self._store)
            ev.setCalendar_(cal)
            self._apply(ev, payload)
            ok, err = self._store.saveEvent_span_commit_error_(
                ev, self._ek.EKSpanThisEvent, False, None
            )
        except AppleCalendarError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"create failed: uid={payload.uid}: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"create failed: uid={payload.uid}: {_err_text(err)}")
        token = str(id(ev))
        self._pending[token] = ev
        self._dirty += 1
        return token

    def _handle_for(self, remote: RemoteEvent, what: str, uid: str | None) -> Any:
        ev = self._handles.get(remote.calendar_item_identifier)
        if ev is None:
            raise AppleCalendarError(
                f"{what} failed: uid={uid} item not found {remote.calendar_item_identifier}"
            )
        cal_id = str(ev.calendar().calendarIdentifier())
        if cal_id != remote.calendar_identifier:
            raise AppleCalendarError(f"{what} failed: uid={uid} calendar mismatch")
        self._require_managed(cal_id, what, uid)
        return ev

    def update_event(self, remote: RemoteEvent, payload: EventPayload) -> None:
        ev = self._handle_for(remote, "update", payload.uid)
        try:
            self._apply(ev, payload)
            ok, err = self._store.saveEvent_span_commit_error_(
                ev, self._ek.EKSpanThisEvent, False, None
            )
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"update failed: uid={payload.uid}: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"update failed: uid={payload.uid}: {_err_text(err)}")
        self._dirty += 1

    def delete_event(self, remote: RemoteEvent) -> None:
        ev = self._handle_for(remote, "delete", remote.uid)
        try:
            ok, err = self._store.removeEvent_span_commit_error_(
                ev, self._ek.EKSpanThisEvent, False, None
            )
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"delete failed: uid={remote.uid}: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"delete failed: uid={remote.uid}: {_err_text(err)}")
        self._handles.pop(remote.calendar_item_identifier, None)
        self._dirty += 1

    def commit(self) -> None:
        try:
            ok, err = self._store.commit_(None)
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"commit failed: {exc}") from exc
        if not ok:
            raise AppleCalendarError(f"commit failed: {_err_text(err)}")
        self._dirty = 0

    def rollback(self) -> None:
        try:
            self._store.reset()
        except Exception as exc:  # noqa: BLE001
            raise AppleCalendarError(f"rollback failed: {exc}") from exc
        finally:
            self._pending.clear()
            self._handles.clear()
            self._dirty = 0

    def identifiers(self, token: str) -> tuple[str | None, str]:
        ev = self._pending.get(token)
        if ev is None:
            raise AppleCalendarError(f"unknown token: {token}")
        eid = ev.eventIdentifier()
        return (None if eid is None else str(eid)), str(ev.calendarItemIdentifier())
