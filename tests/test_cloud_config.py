import yaml

from core.config import load_config

DROP = ("paths", "network_check", "apple_calendar")


def _load(p):
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if k not in DROP}


def test_cloud_config_equals_main_except_three_sections(prj_root):
    assert _load(prj_root / "config.yaml") == _load(prj_root / "config.cloud.yaml")


def test_cloud_config_values(prj_root):
    cfg = load_config(prj_root / "config.cloud.yaml", prj_root)
    assert cfg.paths.output_dir == prj_root / "site"
    assert cfg.paths.data_dir == prj_root / "cloud-data"
    assert cfg.paths.logs_dir == prj_root / "cloud-logs"
    assert cfg.network_check.enabled is False
    assert cfg.apple_calendar.enabled is False
    assert len(cfg.calendars) == 5
    assert len(cfg.us_earnings.tickers) == 57
