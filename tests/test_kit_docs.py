"""安裝包三份說明文件檢查。"""
import re
from pathlib import Path

import pytest

_PRJ = Path(__file__).resolve().parent.parent
# 原始專案放在 docs/kit/；安裝包把三份文件放在根目錄（V6-13：docs/kit/ 或根目錄）
KIT = _PRJ / "docs" / "kit" if (_PRJ / "docs" / "kit").is_dir() else _PRJ
DOCS = {
    "給朋友-訂閱教學.md": ["透過網址新增", "從網際網路訂閱", "加入已訂閱的行事曆", "新增行事曆訂閱"],
    "你要做的-雲端設定步驟.md": ["status.json", "Run workflow", "market-calendar", "你的帳號", "cloud-logs"],
    "安裝-Mac說明.md": ["安裝-Mac.command", "移除-Mac.command"],
}


@pytest.mark.parametrize("name", DOCS)
def test_doc_exists(name):
    p = KIT / name
    assert p.is_file()
    assert p.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("name,kws", DOCS.items())
def test_doc_keywords(name, kws):
    t = (KIT / name).read_text(encoding="utf-8")
    for kw in kws:
        assert kw in t, kw


def test_v6_checklist_keywords_overall():
    allt = "".join((KIT / n).read_text(encoding="utf-8") for n in DOCS)
    for kw in ["透過網址新增", "從網際網路訂閱", "加入已訂閱的行事曆", "新增行事曆訂閱", "status.json", "Run workflow"]:
        assert kw in allt, kw


@pytest.mark.parametrize("name", DOCS)
def test_no_personal_info(name):
    t = (KIT / name).read_text(encoding="utf-8")
    assert "/Users" + "/" not in t
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)
    assert "健康檢查" not in t
