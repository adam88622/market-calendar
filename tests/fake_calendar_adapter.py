"""FN-058：FakeCalendarAdapter，在記憶體中模擬 EventKit（架構 v4 + v4.1 R11）。

語意重點（與真實 EventKit 對齊）：
- create/update/delete 只進暫存（_staged），commit() 才套用，rollback() 丟棄；fetch 只看已 commit 的狀態。
- create 時就分配 calendarItemIdentifier（fake-item-{n}）；eventIdentifier 要到 commit 之後才有，
  commit 前 identifiers(token) 回傳 (None, item_id)。
- create/update/delete 對未以 mark_managed 登記的行事曆丟 AppleCalendarError；fetch_events 不自動登記。
- fetch_events 窗口 (end - start) 必須 < 4 年（1460 天），否則 ValueError。
- create_calendar / rename_calendar 立即生效；若暫存區有未 commit 的操作，丟 AppleCalendarError
  （契約：只能在套用階段前呼叫）。
- 故障注入 fail_on：{"create"|"update"|"delete": {uid|"*"}, "commit": {"*"},
  "fetch": {calendar_identifier|"*"}, "create_calendar": {"*"}, "rename_calendar": {"*"}}。
"""
from __future__ import annotations

from datetime import date, datetime

from apple.models import (
    CAL_TYPE_CALDAV,
    CAL_TYPE_LOCAL,
    SOURCE_CALDAV,
    SOURCE_LOCAL,
    URL_SCHEME,
    AppleCalendarError,
    AuthStatus,
    CalendarInfo,
    EventPayload,
    RemoteEvent,
    SourceInfo,
)

MAX_WINDOW_DAYS = 4 * 365


def _uid_from_url(url: str | None) -> str | None:
    if url and url.startswith(URL_SCHEME) and len(url) > len(URL_SCHEME):
        return url[len(URL_SCHEME):]
    return None


def _as_date(v: date | datetime) -> date:
    return v.date() if isinstance(v, datetime) else v


class FakeCalendarAdapter:
    def __init__(
        self,
        sources: list[SourceInfo] | None = None,
        status: AuthStatus = "fullAccess",
    ) -> None:
        self.sources: list[SourceInfo] = list(
            sources
            if sources is not None
            else [
                SourceInfo("fake-src-icloud", "iCloud", SOURCE_CALDAV),
                SourceInfo("fake-src-local", "On My Mac", SOURCE_LOCAL),
            ]
        )
        self.status: AuthStatus = status
        self.grant_on_request: bool = True
        self.calendars: dict[str, CalendarInfo] = {}
        # events[calendar_identifier][item_id] = dict(fields)
        self.events: dict[str, dict[str, dict]] = {}
        self.managed: set[str] = set()
        self.ops: list[tuple] = []
        self.fail_on: dict[str, set[str]] = {}
        self._staged: list[tuple] = []
        self._item_n = 0
        self._evt_n = 0
        self._cal_n = 0

    # ---- 內部 ----
    def _should_fail(self, kind: str, key: str) -> bool:
        s = self.fail_on.get(kind)
        return bool(s) and ("*" in s or key in s)

    def _require_managed(self, calendar_identifier: str, what: str, uid: str | None) -> None:
        if calendar_identifier not in self.calendars:
            raise AppleCalendarError(f"{what} {uid}: calendar not found {calendar_identifier}")
        if calendar_identifier not in self.managed:
            raise AppleCalendarError(
                f"{what} {uid}: calendar {calendar_identifier} is not a managed calendar"
            )

    def _new_item_id(self) -> str:
        self._item_n += 1
        return f"fake-item-{self._item_n}"

    def _fields(self, p: EventPayload) -> dict:
        return {
            "uid": p.uid,
            "title": p.title,
            "all_day": p.all_day,
            "start": p.start,
            "end": p.end,
            "notes": p.notes,
            "location": p.location,
            "url": p.url,
            "alarm_offset_sec": p.alarm_offset_sec,
            "content_hash": p.content_hash,
            "start_date": p.start_date,
            "end_date": _as_date(p.end),
        }

    def _find_item(self, item_id: str) -> tuple[str, dict] | None:
        for cal, items in self.events.items():
            if item_id in items:
                return cal, items[item_id]
        return None

    def _remote(self, cal: str, item_id: str, ev: dict) -> RemoteEvent:
        return RemoteEvent(
            calendar_item_identifier=item_id,
            event_identifier=ev["event_identifier"],
            calendar_identifier=cal,
            uid=_uid_from_url(ev["url"]),
            start_date=ev["start_date"],
        )

    # ---- CalendarAdapter ----
    def authorization_status(self) -> AuthStatus:
        return self.status

    def request_full_access(self, timeout_sec: float = 120.0) -> tuple[bool, str | None]:
        self.ops.append(("request_full_access",))
        if self.status == "fullAccess":
            return True, None
        if self.status == "notDetermined" and self.grant_on_request:
            self.status = "fullAccess"
            return True, None
        return False, f"access not granted (status={self.status})"

    def list_sources(self) -> list[SourceInfo]:
        return list(self.sources)

    def list_calendars(self) -> list[CalendarInfo]:
        return list(self.calendars.values())

    def get_calendar(self, identifier: str) -> CalendarInfo | None:
        return self.calendars.get(identifier)

    def create_calendar(self, title: str, source_identifier: str) -> CalendarInfo:
        self.ops.append(("create_calendar", title, source_identifier))
        if self._should_fail("create_calendar", "*") or self._should_fail(
            "create_calendar", title
        ):
            raise AppleCalendarError(f"create_calendar failed: {title}")
        if self._staged:
            raise AppleCalendarError("create_calendar called with uncommitted staged operations")
        src = next((s for s in self.sources if s.identifier == source_identifier), None)
        if src is None:
            raise AppleCalendarError(f"source not found: {source_identifier}")
        info = self.add_calendar(title, src)
        return info

    def rename_calendar(self, identifier: str, title: str) -> None:
        self.ops.append(("rename_calendar", identifier, title))
        if self._should_fail("rename_calendar", "*") or self._should_fail(
            "rename_calendar", identifier
        ):
            raise AppleCalendarError(f"rename_calendar failed: {identifier}")
        if self._staged:
            raise AppleCalendarError("rename_calendar called with uncommitted staged operations")
        old = self.calendars.get(identifier)
        if old is None:
            raise AppleCalendarError(f"calendar not found: {identifier}")
        self.calendars[identifier] = CalendarInfo(
            old.identifier, title, old.source_identifier, old.source_title,
            old.source_type, old.cal_type, old.writable,
        )

    def mark_managed(self, calendar_identifier: str) -> None:
        self.ops.append(("mark_managed", calendar_identifier))
        if calendar_identifier not in self.calendars:
            raise AppleCalendarError(f"calendar not found: {calendar_identifier}")
        self.managed.add(calendar_identifier)

    def fetch_events(self, calendar_identifier: str, start: date, end: date) -> list[RemoteEvent]:
        self.ops.append(("fetch", calendar_identifier))
        if (end - start).days >= MAX_WINDOW_DAYS:
            raise ValueError(f"fetch window must be < 4 years: {start}..{end}")
        if self._should_fail("fetch", calendar_identifier):
            raise AppleCalendarError(f"fetch failed: {calendar_identifier}")
        if calendar_identifier not in self.calendars:
            raise AppleCalendarError(f"calendar not found: {calendar_identifier}")
        out = []
        for item_id, ev in self.events.get(calendar_identifier, {}).items():
            if ev["start_date"] <= end and ev["end_date"] >= start:
                out.append(self._remote(calendar_identifier, item_id, ev))
        out.sort(key=lambda r: (r.start_date, r.calendar_item_identifier))
        return out

    def create_event(self, calendar_identifier: str, payload: EventPayload) -> str:
        self.ops.append(("create", calendar_identifier, payload.uid))
        if self._should_fail("create", payload.uid):
            raise AppleCalendarError(f"create failed: uid={payload.uid}")
        self._require_managed(calendar_identifier, "create", payload.uid)
        if not self.calendars[calendar_identifier].writable:
            raise AppleCalendarError(f"create failed: uid={payload.uid} calendar not writable")
        item_id = self._new_item_id()
        self._staged.append(("create", calendar_identifier, item_id, payload))
        return item_id

    def update_event(self, remote: RemoteEvent, payload: EventPayload) -> None:
        self.ops.append(("update", remote.calendar_item_identifier, payload.uid))
        if self._should_fail("update", payload.uid):
            raise AppleCalendarError(f"update failed: uid={payload.uid}")
        self._require_managed(remote.calendar_identifier, "update", payload.uid)
        found = self._find_item(remote.calendar_item_identifier)
        if found is None or found[0] != remote.calendar_identifier:
            raise AppleCalendarError(
                f"update failed: uid={payload.uid} item not found {remote.calendar_item_identifier}"
            )
        self._staged.append(("update", found[0], remote.calendar_item_identifier, payload))

    def delete_event(self, remote: RemoteEvent) -> None:
        self.ops.append(("delete", remote.calendar_item_identifier, remote.uid))
        if self._should_fail("delete", str(remote.uid)):
            raise AppleCalendarError(f"delete failed: uid={remote.uid}")
        self._require_managed(remote.calendar_identifier, "delete", remote.uid)
        found = self._find_item(remote.calendar_item_identifier)
        if found is None or found[0] != remote.calendar_identifier:
            raise AppleCalendarError(
                f"delete failed: uid={remote.uid} item not found {remote.calendar_item_identifier}"
            )
        self._staged.append(("delete", found[0], remote.calendar_item_identifier, None))

    def commit(self) -> None:
        self.ops.append(("commit",))
        if self._should_fail("commit", "*"):
            raise AppleCalendarError("commit failed")
        staged, self._staged = self._staged, []
        for kind, cal, item_id, payload in staged:
            if kind == "create":
                self._evt_n += 1
                ev = self._fields(payload)
                ev["event_identifier"] = f"fake-evt-{self._evt_n}"
                self.events.setdefault(cal, {})[item_id] = ev
            elif kind == "update":
                cur = self.events.get(cal, {}).get(item_id)
                if cur is None:  # 暫存期間被使用者刪除：略過
                    continue
                ev = self._fields(payload)
                ev["event_identifier"] = cur["event_identifier"]
                self.events[cal][item_id] = ev
            else:
                self.events.get(cal, {}).pop(item_id, None)

    def rollback(self) -> None:
        self.ops.append(("rollback",))
        self._staged = []

    def identifiers(self, token: str) -> tuple[str | None, str]:
        for kind, _cal, item_id, _p in self._staged:
            if kind == "create" and item_id == token:
                return None, item_id
        found = self._find_item(token)
        if found is None:
            raise AppleCalendarError(f"unknown token: {token}")
        return found[1]["event_identifier"], token

    # ---- 測試輔助 ----
    def add_calendar(
        self,
        title: str,
        source: SourceInfo | str | None = None,
        *,
        writable: bool = True,
        cal_type: int | None = None,
        identifier: str | None = None,
    ) -> CalendarInfo:
        """測試設定用：直接加行事曆（source 可為 SourceInfo、source identifier 或 None=第一個來源）。"""
        if source is None:
            src = self.sources[0]
        elif isinstance(source, SourceInfo):
            src = source
        else:
            src = next(s for s in self.sources if s.identifier == source)
        self._cal_n += 1
        ident = identifier or f"fake-cal-{self._cal_n}"
        if cal_type is None:
            cal_type = CAL_TYPE_LOCAL if src.source_type == SOURCE_LOCAL else CAL_TYPE_CALDAV
        info = CalendarInfo(ident, title, src.identifier, src.title, src.source_type, cal_type, writable)
        self.calendars[ident] = info
        self.events.setdefault(ident, {})
        return info

    def add_foreign_event(
        self,
        calendar_identifier: str,
        title: str = "foreign",
        start_date: date = date(2026, 9, 1),
        url: str | None = None,
        end_date: date | None = None,
    ) -> str:
        """直接寫入一筆已 commit 的事件（預設沒有本專案 URL → uid 為 None）。回傳 item_id。"""
        if calendar_identifier not in self.calendars:
            raise AppleCalendarError(f"calendar not found: {calendar_identifier}")
        item_id = self._new_item_id()
        self._evt_n += 1
        self.events.setdefault(calendar_identifier, {})[item_id] = {
            "uid": _uid_from_url(url),
            "title": title,
            "all_day": True,
            "start": start_date,
            "end": end_date or start_date,
            "notes": "",
            "location": "",
            "url": url or "",
            "alarm_offset_sec": 0,
            "content_hash": "",
            "start_date": start_date,
            "end_date": end_date or start_date,
            "event_identifier": f"fake-evt-{self._evt_n}",
        }
        return item_id

    def user_delete_event(self, uid: str) -> int:
        """模擬使用者手動刪除（已 commit 的、URL 解析出該 uid 的所有事件）；回傳刪除筆數。"""
        n = 0
        for items in self.events.values():
            for item_id in [i for i, e in items.items() if _uid_from_url(e["url"]) == uid]:
                del items[item_id]
                n += 1
        return n

    def user_delete_calendar(self, identifier: str) -> None:
        """模擬使用者刪除整個行事曆（含事件、受管登記）。"""
        self.calendars.pop(identifier, None)
        self.events.pop(identifier, None)
        self.managed.discard(identifier)

    def set_status(self, status: AuthStatus, *, grant_on_request: bool | None = None) -> None:
        self.status = status
        if grant_on_request is not None:
            self.grant_on_request = grant_on_request

    def change_item_identifier(self, uid: str) -> str:
        """模擬 iCloud 同步使 calendarItemIdentifier 改變；回傳新 id（多筆時改第一筆）。"""
        for items in self.events.values():
            for item_id, ev in list(items.items()):
                if _uid_from_url(ev["url"]) == uid:
                    new_id = self._new_item_id()
                    items[new_id] = items.pop(item_id)
                    return new_id
        raise KeyError(uid)

    def events_of(self, calendar_identifier: str) -> list[dict]:
        """已 commit 的事件欄位快照（含 calendar_item_identifier）。"""
        return [
            {**ev, "calendar_item_identifier": i}
            for i, ev in self.events.get(calendar_identifier, {}).items()
        ]

    def find_by_uid(self, uid: str) -> list[RemoteEvent]:
        return [
            self._remote(cal, i, ev)
            for cal, items in self.events.items()
            for i, ev in items.items()
            if _uid_from_url(ev["url"]) == uid
        ]

    @property
    def staged_count(self) -> int:
        return len(self._staged)

    def op_names(self) -> list[str]:
        return [o[0] for o in self.ops]
