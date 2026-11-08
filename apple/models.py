"""FN-047：Apple 行事曆同步的資料模型（架構 v4 V4-2）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

APPLE_MAP_VERSION = 1
URL_SCHEME = "market-calendar-ics://"

AuthStatus = Literal["notDetermined", "restricted", "denied", "fullAccess", "writeOnly"]
# EKAuthorizationStatus：0 notDetermined、1 restricted、2 denied、3 fullAccess、4 writeOnly

# EKSourceType：0 Local、1 Exchange、2 CalDAV、3 MobileMe、4 Subscribed、5 Birthdays
SOURCE_LOCAL, SOURCE_CALDAV = 0, 2
# EKCalendarType：0 Local、1 CalDAV、2 Exchange、3 Subscription、4 Birthday
CAL_TYPE_LOCAL, CAL_TYPE_CALDAV = 0, 1


@dataclass(frozen=True)
class SourceInfo:
    identifier: str
    title: str  # 例："iCloud"、"On My Mac"
    source_type: int


@dataclass(frozen=True)
class CalendarInfo:
    identifier: str  # EKCalendar.calendarIdentifier
    title: str
    source_identifier: str
    source_title: str
    source_type: int
    cal_type: int
    writable: bool  # allowsContentModifications


@dataclass(frozen=True)
class EventPayload:
    """要寫進 EventKit 的完整內容（純資料）。"""

    uid: str
    cal_id: str  # CalendarId
    title: str
    all_day: bool
    start: date | datetime  # all_day → date；timed → aware datetime（Asia/Taipei）
    end: date | datetime  # all_day → 包含在內的最後一天；timed → aware datetime
    notes: str
    location: str
    url: str  # market-calendar-ics://{uid}
    alarm_offset_sec: int  # EKAlarm relativeOffset；全天事件以當天 00:00 為基準
    content_hash: str
    start_date: date  # 排序與「今天以後」判斷用


@dataclass(frozen=True)
class RemoteEvent:
    """adapter 回傳的遠端事件快照。"""

    calendar_item_identifier: str
    event_identifier: str | None
    calendar_identifier: str
    uid: str | None  # 由 URL 解析；不是本專案的事件為 None
    start_date: date


@dataclass(frozen=True)
class EventMapRow:
    uid: str
    cal_id: str
    calendar_identifier: str
    event_identifier: str | None
    calendar_item_identifier: str
    content_hash: str
    map_version: int
    start_date: date
    synced_at: str  # ISO UTC


@dataclass(frozen=True)
class CalendarMapRow:
    cal_id: str
    calendar_identifier: str
    title: str
    source_title: str
    source_type: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PlannedCreate:
    payload: EventPayload
    recreated: bool  # 對應表有、App 裡沒有（使用者手動刪除）


@dataclass(frozen=True)
class PlannedUpdate:
    payload: EventPayload
    remote: RemoteEvent
    reason: Literal["changed", "unmapped", "version"]


@dataclass(frozen=True)
class PlannedDelete:
    remote: RemoteEvent
    reason: Literal["missing_future", "duplicate"]


@dataclass
class SyncPlan:
    creates: list[PlannedCreate] = field(default_factory=list)
    updates: list[PlannedUpdate] = field(default_factory=list)
    deletes: list[PlannedDelete] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)  # uid
    map_refresh: list[tuple[str, RemoteEvent]] = field(default_factory=list)
    map_drops: list[str] = field(default_factory=list)
    kept_past: list[str] = field(default_factory=list)
    guarded: bool = False  # D-14 是否觸發


@dataclass
class SyncResult:
    cal_id: str
    calendar_identifier: str
    created: int = 0
    recreated: int = 0
    updated: int = 0
    deleted: int = 0
    duplicates_deleted: int = 0
    unchanged: int = 0
    kept_past: int = 0
    deferred: int = 0  # 因 deadline 延到下次執行
    errors: list[str] = field(default_factory=list)
    guarded: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors


class AppleCalendarError(Exception):
    """EventKit 操作失敗、找不到來源、commit 失敗。"""


class AppleAuthorizationError(AppleCalendarError):
    """未取得行事曆授權。"""
