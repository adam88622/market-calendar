"""FN-012 RFC 5545 TEXT 跳脫與 75 octets 折行。"""
from __future__ import annotations

import re

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _CTRL_RE.sub("", s)  # 保留 \n 與 tab
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\n", "\\n")


def fold_line(line: str) -> str:
    """以 "\\r\\n " 折行；每個實體行（含續行開頭空白）≤ 75 octets，不切斷多位元組字元。"""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    limit = 75  # 第一行
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur_len + n > limit:
            parts.append("".join(cur))
            cur, cur_len, limit = [], 0, 74  # 續行有前導空白佔 1 octet
        cur.append(ch)
        cur_len += n
    if cur:
        parts.append("".join(cur))
    return "\r\n ".join(parts)
