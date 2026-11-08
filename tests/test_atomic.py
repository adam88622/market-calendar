import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import pytest
from writer.atomic import write_atomic


def test_write_and_overwrite(tmp_path):
    p = tmp_path / "sub" / "a.ics"
    write_atomic(p, b"one")
    assert p.read_bytes() == b"one"
    write_atomic(p, b"two")
    assert p.read_bytes() == b"two"
    assert [x.name for x in p.parent.iterdir()] == ["a.ics"]


def test_replace_failure_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "a.ics"
    write_atomic(p, b"orig")

    def boom(*a, **k):
        raise OSError("x")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_atomic(p, b"new")
    assert p.read_bytes() == b"orig"
    assert not [x for x in tmp_path.iterdir() if ".tmp-" in x.name]
