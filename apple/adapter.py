"""FN-048：CalendarAdapter Protocol（架構 v4 V4-5 + v4.1 R11）。

契約（Fake 與真實實作都必須遵守）：
- fetch_events 的時間窗為 [start 00:00, end+1 00:00)（當地時間），只回傳指定行事曆的事件；
  end - start 必須小於 4 年，超過時丟 ValueError。fetch_events 不會自動登記受管行事曆。
- create_event / update_event / delete_event 只接受已以 mark_managed 登記的行事曆，
  否則丟 AppleCalendarError（D-11 第二道防護）。
- 單筆 create / update / delete 失敗丟 AppleCalendarError，訊息包含 uid。
- create_calendar / rename_calendar 會立即 commit，只能在套用階段前呼叫。
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from apple.models import (
    AuthStatus,
    CalendarInfo,
    EventPayload,
    RemoteEvent,
    SourceInfo,
)


@runtime_checkable
class CalendarAdapter(Protocol):
    def authorization_status(self) -> AuthStatus: ...

    def request_full_access(self, timeout_sec: float = 120.0) -> tuple[bool, str | None]: ...

    def list_sources(self) -> list[SourceInfo]: ...

    def list_calendars(self) -> list[CalendarInfo]: ...  # 只列 event 類型的行事曆

    def get_calendar(self, identifier: str) -> CalendarInfo | None: ...

    def create_calendar(self, title: str, source_identifier: str) -> CalendarInfo: ...  # 立即 commit

    def rename_calendar(self, identifier: str, title: str) -> None: ...  # 立即 commit

    def mark_managed(self, calendar_identifier: str) -> None: ...  # R11：登記受管行事曆

    def fetch_events(self, calendar_identifier: str, start: date, end: date) -> list[RemoteEvent]: ...

    def create_event(self, calendar_identifier: str, payload: EventPayload) -> str: ...  # token，commit=NO

    def update_event(self, remote: RemoteEvent, payload: EventPayload) -> None: ...  # commit=NO

    def delete_event(self, remote: RemoteEvent) -> None: ...  # commit=NO

    def commit(self) -> None: ...  # 失敗丟 AppleCalendarError

    def rollback(self) -> None: ...  # 丟棄所有未 commit 的變更

    def identifiers(self, token: str) -> tuple[str | None, str]: ...  # (eventIdentifier, calendarItemIdentifier)
