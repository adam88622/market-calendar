"""pytest 共用設定：把 $PRJ 加入 sys.path，提供 fixtures 路徑。"""
import sys
from pathlib import Path

import pytest

PRJ = Path(__file__).resolve().parent.parent
if str(PRJ) not in sys.path:
    sys.path.insert(0, str(PRJ))


@pytest.fixture(scope="session")
def prj_root() -> Path:
    return PRJ


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "eventkit: 需要本機 EventKit 授權的實機測試（MCI_EVENTKIT_TESTS=1）"
    )


def pytest_collection_modifyitems(config, items):
    import os

    if os.environ.get("MCI_EVENTKIT_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="eventkit tests require MCI_EVENTKIT_TESTS=1 and fullAccess")
    for item in items:
        if "eventkit" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_eventkit(request, monkeypatch):
    """離線測試絕不碰真的行事曆：除非帶有 eventkit marker，否則 EventKitAdapter.create 一律丟錯。"""
    if request.node.get_closest_marker("eventkit") is not None:
        return

    def _disabled(tz_name: str = "Asia/Taipei"):
        raise RuntimeError("EventKit disabled in tests")

    from apple.eventkit_adapter import EventKitAdapter

    monkeypatch.setattr(EventKitAdapter, "create", staticmethod(_disabled))
