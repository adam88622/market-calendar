"""連網檢查：任一網址可連線即視為在線。"""
from __future__ import annotations

import requests


def is_online(urls: tuple[str, ...], timeout_sec: float = 4.0, session=None) -> bool:
    """對每個網址先 HEAD、失敗再 GET；任一取得 HTTP 回應即回傳 True。

    只要能收到任何 HTTP 回應（含 403/405 等）即代表網路可達；僅連線層例外視為失敗。
    """
    own = session is None
    sess = session if session is not None else requests.Session()
    try:
        for url in urls:
            for method in ("head", "get"):
                try:
                    resp = getattr(sess, method)(url, timeout=timeout_sec, allow_redirects=True)
                    close = getattr(resp, "close", None)
                    if close:
                        close()
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False
    finally:
        if own:
            sess.close()
