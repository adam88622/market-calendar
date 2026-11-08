import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from writer.ics_text import escape_text, fold_line


def test_escape_four():
    assert escape_text("a\\b;c,d\nE\r\nF") == "a\\\\b\\;c\\,d\\nE\\nF"


def test_escape_control_chars():
    assert escape_text("a\x00b\x07c\td") == "abc\td"


def test_short_unchanged():
    assert fold_line("SUMMARY:hi") == "SUMMARY:hi"


def test_fold_chinese_200():
    line = "DESCRIPTION:" + "法說會財報" * 40
    out = fold_line(line)
    phys = out.split("\r\n")
    assert len(phys) > 1
    for i, p in enumerate(phys):
        assert len(p.encode("utf-8")) <= 75
        p.encode("utf-8").decode("utf-8")
        if i:
            assert p.startswith(" ")
    assert out.replace("\r\n ", "") == line


def test_fold_ascii_exact_boundary():
    line = "X" * 75
    assert fold_line(line) == line
    out = fold_line("X" * 76)
    assert [len(p) for p in out.split("\r\n")] == [75, 2]
