from datetime import date, time
from pathlib import Path

import pytest
import yaml

from core.config import load_config
from core.models import ConfigError


@pytest.fixture
def raw(fixtures_dir):
    return yaml.safe_load((fixtures_dir / "config_test.yaml").read_text(encoding="utf-8"))


def write(tmp_path, data) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_load_fixture(fixtures_dir, prj_root):
    cfg = load_config(fixtures_dir / "config_test.yaml", prj_root)
    assert cfg.paths.output_dir == prj_root / "out_test" and cfg.paths.output_dir.is_absolute()
    assert cfg.paths.data_dir.is_absolute() and cfg.paths.logs_dir.is_absolute()
    assert cfg.reminder.days_before == 1 and cfg.reminder.at == time(20, 0)
    assert cfg.taifex_holidays[0].date == date(2026, 1, 1)
    assert cfg.taifex_holidays[1].kind == "market_only"
    assert cfg.taifex_holidays[2].provisional is True
    assert cfg.taifex_holidays[0].kind == "holiday" and not cfg.taifex_holidays[0].provisional
    assert cfg.tw_settlement.weekly_wed.holiday_policy == "next"
    assert cfg.tw_ir.default_duration_minutes == 60
    assert cfg.mops.total_budget_sec == 35 and cfg.mops.markets == ("sii", "otc")
    assert set(cfg.calendars) == {
        "tw-settlement", "us-settlement", "tw-earnings", "us-earnings", "global-holidays"}
    assert cfg.us_earnings.tickers == ("NVDA", "AMD", "CRWV", "AAPL")
    assert cfg.us_earnings.group_of["CRWV"] == "Neocloud"
    assert cfg.timezone == "Asia/Taipei" and cfg.schedule.hour == 7 and cfg.log.retention_days == 30


def test_official_config_57(prj_root):
    cfg = load_config(prj_root / "config.yaml", prj_root)
    us = cfg.us_earnings
    assert len(us.watchlist) == 57 and len(set(us.tickers)) == 57
    assert us.group_of["CRWV"] == "Neocloud"
    assert us.group_of["EQIX"] == "AI基建（電網/工程/電力/機房）"
    assert us.max_workers == 6 and us.submit_stagger_sec == 0.2
    assert us.per_ticker_timeout_sec == 20 and us.total_budget_sec == 40
    assert cfg.mops.total_budget_sec == 35
    assert any(h.provisional for h in cfg.taifex_holidays)
    assert cfg.taifex_extra_holidays == ()


def test_missing_extra_holidays_ok(raw, tmp_path):
    raw.pop("taifex_extra_holidays", None)
    assert load_config(write(tmp_path, raw), tmp_path).taifex_extra_holidays == ()


def test_absolute_path_kept(raw, tmp_path):
    raw["paths"]["output_dir"] = str(tmp_path / "abs")
    assert load_config(write(tmp_path, raw), Path("/x")).paths.output_dir == tmp_path / "abs"


def test_file_missing(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml", tmp_path)


def test_yaml_syntax_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, tmp_path)


def test_missing_key(raw, tmp_path):
    del raw["mops"]
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), tmp_path)


def _mut(raw, tmp_path, fn):
    fn(raw)
    return load_config(write(tmp_path, raw), tmp_path)


@pytest.mark.parametrize("fn", [
    lambda r: r["mops"].__setitem__("request_interval_sec", 1),
    lambda r: r["tw_settlement"]["weekly_wed"].__setitem__("holiday_policy", "foo"),
    lambda r: r["reminder"].__setitem__("days_before", -1),
    lambda r: r["reminder"].__setitem__("time", "8pm"),
    lambda r: r["reminder"].__setitem__("time", "25:00"),
    lambda r: r["calendars"].pop("us-earnings"),
    lambda r: r["us_earnings"].__setitem__("watchlist", []),
    lambda r: r["us_earnings"].__setitem__("watchlist", ["NVDA", "AMD"]),
    lambda r: r["us_earnings"]["watchlist"].append({"ticker": "nvda", "group": "x"}),
    lambda r: r["us_earnings"]["watchlist"].append({"ticker": "AMZN", "group": "  "}),
    lambda r: r["us_earnings"]["watchlist"].append({"ticker": "AMZN"}),
    lambda r: r["us_earnings"]["watchlist"].append({"ticker": "BRK.B", "group": "x"}),
    lambda r: r["us_earnings"].__setitem__("max_workers", 0),
    lambda r: r["us_earnings"].__setitem__("max_workers", 9),
    lambda r: r["us_earnings"].__setitem__("per_ticker_timeout_sec", 0),
    lambda r: r["us_earnings"].__setitem__("total_budget_sec", 0),
    lambda r: r["tw_ir"].__setitem__("default_duration_minutes", 0),
    lambda r: r["mops"].__setitem__("total_budget_sec", 0),
])
def test_invalid_values(raw, tmp_path, fn):
    with pytest.raises(ConfigError):
        _mut(raw, tmp_path, fn)


def test_duplicate_ticker_message(raw, tmp_path):
    with pytest.raises(ConfigError, match="NVDA"):
        _mut(raw, tmp_path, lambda r: r["us_earnings"]["watchlist"].append({"ticker": " nvda ", "group": "x"}))


def test_old_format_message(raw, tmp_path):
    with pytest.raises(ConfigError, match="ticker"):
        _mut(raw, tmp_path, lambda r: r["us_earnings"].__setitem__("watchlist", ["NVDA"]))


def test_ticker_normalized(raw, tmp_path):
    raw["us_earnings"]["watchlist"] = [{"ticker": " amd ", "group": " G "}]
    cfg = load_config(write(tmp_path, raw), tmp_path)
    assert cfg.us_earnings.watchlist[0].ticker == "AMD" and cfg.us_earnings.group_of == {"AMD": "G"}


# ---- V4-4 / R16
def test_official_apple_calendar(prj_root):
    cfg = load_config(prj_root / "config.yaml", prj_root)
    a = cfg.apple_calendar
    assert a.enabled is True and a.prefer_source == "iCloud" and a.delete_future_missing is True
    assert cfg.schedule.launch_mode == "direct"


def test_fixture_without_apple_section_disabled(fixtures_dir, prj_root):
    cfg = load_config(fixtures_dir / "config_test.yaml", prj_root)
    assert cfg.apple_calendar.enabled is False
    assert cfg.schedule.launch_mode == "direct"


def test_apple_prefer_source_case_insensitive(raw, tmp_path, prj_root):
    raw["apple_calendar"] = {"prefer_source": "ICLOUD"}
    cfg = load_config(write(tmp_path, raw), prj_root)
    assert cfg.apple_calendar.prefer_source == "iCloud" and cfg.apple_calendar.enabled is True
    raw["apple_calendar"] = {"prefer_source": "LOCAL", "enabled": False}
    cfg = load_config(write(tmp_path, raw), prj_root)
    assert cfg.apple_calendar.prefer_source == "local" and cfg.apple_calendar.enabled is False


@pytest.mark.parametrize("bad", [
    {"prefer_source": "exchange"},
    {"enabled": "yes"},
    {"delete_future_missing": 1},
    [1],
])
def test_apple_invalid(raw, tmp_path, prj_root, bad):
    raw["apple_calendar"] = bad
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), prj_root)


def test_launch_mode(raw, tmp_path, prj_root):
    raw["schedule"]["launch_mode"] = "open"
    assert load_config(write(tmp_path, raw), prj_root).schedule.launch_mode == "open"
    raw["schedule"]["launch_mode"] = "bogus"
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), prj_root)


# ---- schedule.times / network_check
def test_times_default_from_hour_minute(raw, tmp_path):
    cfg = load_config(write(tmp_path, raw), tmp_path)
    assert cfg.schedule.times == ((7, 30),)
    assert cfg.network_check.enabled is False


def test_times_parsed_and_preferred(raw, tmp_path):
    raw["schedule"]["times"] = ["08:30", "12:30", "19:30", "21:30"]
    cfg = load_config(write(tmp_path, raw), tmp_path)  # hour/minute 並存仍以 times 為準
    assert cfg.schedule.times == ((8, 30), (12, 30), (19, 30), (21, 30))


def test_times_only_without_hour_minute(raw, tmp_path):
    raw["schedule"].pop("hour"); raw["schedule"].pop("minute")
    raw["schedule"]["times"] = ["6:05"]
    assert load_config(write(tmp_path, raw), tmp_path).schedule.times == ((6, 5),)


@pytest.mark.parametrize("bad", [
    [], ["08:30", "08:30"], ["25:00"], ["08:60"], ["8pm"], [830], "08:30",
    [f"{h:02d}:00" for h in range(13)],
])
def test_times_invalid(raw, tmp_path, bad):
    raw["schedule"]["times"] = bad
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), tmp_path)


def test_network_check_parse(raw, tmp_path):
    raw["network_check"] = {"enabled": True, "timeout_sec": 3}
    cfg = load_config(write(tmp_path, raw), tmp_path)
    assert cfg.network_check.enabled is True and cfg.network_check.timeout_sec == 3.0
    raw["network_check"] = {"enabled": True, "timeout_sec": 0}
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), tmp_path)


def test_official_config_times(prj_root):
    cfg = load_config(prj_root / "config.yaml", prj_root)
    assert cfg.schedule.times == ((8, 30), (12, 30), (19, 30), (21, 30))
    assert cfg.network_check.enabled is True


# ---- FN-061 ----
def test_global_holidays_default(fixtures_dir, prj_root):
    from core.config import CalendarMeta
    cfg = load_config(fixtures_dir / "config_test.yaml", prj_root)
    assert cfg.calendars["global-holidays"] == CalendarMeta("global-holidays.ics", "海外休市（美日韓）")


def test_global_holidays_explicit(raw, tmp_path):
    from core.config import CalendarMeta
    raw["calendars"]["global-holidays"] = {"filename": "gh.ics", "name": "X"}
    cfg = load_config(write(tmp_path, raw), tmp_path)
    assert cfg.calendars["global-holidays"] == CalendarMeta("gh.ics", "X")


def test_global_holidays_empty_name(raw, tmp_path):
    raw["calendars"]["global-holidays"] = {"filename": "gh.ics", "name": ""}
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, raw), tmp_path)


def test_global_holidays_duplicate_filename(raw, tmp_path):
    raw["calendars"]["global-holidays"] = {"filename": "tw-settlement.ics", "name": "X"}
    with pytest.raises(ConfigError, match="duplicate filename"):
        load_config(write(tmp_path, raw), tmp_path)


def test_global_holidays_duplicate_name(raw, tmp_path):
    raw["calendars"]["global-holidays"] = {
        "filename": "gh.ics", "name": raw["calendars"]["tw-settlement"]["name"]}
    with pytest.raises(ConfigError, match="duplicate name"):
        load_config(write(tmp_path, raw), tmp_path)


def test_official_config_global_holidays(prj_root):
    cfg = load_config(prj_root / "config.yaml", prj_root)
    assert cfg.calendars["global-holidays"].name == "海外休市（美日韓）"
    assert len(cfg.calendars) == 5
