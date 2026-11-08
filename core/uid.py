"""FN-014：確定性 UID。"""
from __future__ import annotations

import re

from core.models import UID_SUFFIX

_KIND_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_PART_RE = re.compile(r"^[A-Za-z0-9]+$")


def make_uid(kind: str, *key_parts: str | int) -> str:
    """回傳 f"{kind}-{key1-key2...}@market-calendar-ics"。"""
    if not key_parts:
        raise ValueError("make_uid requires at least one key part")
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ValueError(f"invalid uid kind: {kind!r}")
    parts = []
    for p in key_parts:
        s = str(p)
        if not _PART_RE.match(s):
            raise ValueError(f"invalid uid key part: {p!r}")
        parts.append(s)
    return f"{kind}-{'-'.join(parts)}{UID_SUFFIX}"
