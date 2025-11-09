from pathlib import Path
import sys
import configparser
from typing import Any


def _resolve_config_file() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "budget.ini"
    module_dir = Path(__file__).resolve().parent
    candidate = module_dir / "budget.ini"
    if candidate.exists():
        return candidate
    return module_dir.with_name("budget.ini")


CONFIG_FILE = _resolve_config_file()
PERIOD_CHOICES = ["Monthly", "Quarterly", "Yearly", "Weekly"]

STYLE_DEFAULTS: dict[str, Any] = {
    "category_column_width": 250,
    "period_column_width": 60,
    "numeric_column_width": 90,
    "min_column_width": 10,
    "main_category_bg": "#7FB2F5",
    "diff_positive_color": "#97F18D",
    "diff_negative_color": "#F18686",
    "calculated_budget_color": "#B0B7C3",
    "ui_font_family": "Segoe UI",
    "ui_base_font_size": 10,
    "ui_bold_font_size": 10,
    "diff_font_size": 10,
    "summary_font_size": 10,
    "window_scale_ratio": 0.9,
    "chart_height": 150,
    "summary_actual_positive_color": "#CFE8FF",
    "summary_actual_negative_color": "#E7C7A6",
    "summary_budget_positive_color": "#CBF5C5",
    "summary_budget_negative_color": "#F8C4C4",
    "summary_diff_positive_color": "#F8F9FA",
    "summary_diff_negative_bg_color": "#111111",
    "summary_diff_negative_fg_color": "#FFFFFF",
    "detail_font_family": "Courier New",
    "detail_font_size": 14,
}

_STYLE_INT_KEYS = {
    "category_column_width",
    "period_column_width",
    "numeric_column_width",
    "min_column_width",
    "ui_base_font_size",
    "ui_bold_font_size",
    "diff_font_size",
    "summary_font_size",
    "chart_height",
    "detail_font_size",
}
_STYLE_FLOAT_KEYS = {"window_scale_ratio"}


def _load_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    return cfg


def _save_cfg(cfg: configparser.ConfigParser) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


def load_last_db() -> Path | None:
    cfg = _load_cfg()
    db_path = cfg.get("app", "db_path", fallback=None)
    if db_path:
        path = Path(db_path).expanduser()
        if path.exists():
            return path
    return None


def save_last_db(path: Path | None) -> None:
    cfg = _load_cfg()
    if "app" not in cfg:
        cfg["app"] = {}
    if path:
        cfg["app"]["db_path"] = str(path)
    else:
        cfg["app"].pop("db_path", None)
    _save_cfg(cfg)


def load_last_budget_year() -> str | None:
    cfg = _load_cfg()
    return cfg.get("app", "budget_year", fallback=None)


def save_last_budget_year(year: str) -> None:
    cfg = _load_cfg()
    if "app" not in cfg:
        cfg["app"] = {}
    cfg["app"]["budget_year"] = str(year)
    _save_cfg(cfg)


def load_style_settings() -> dict[str, Any]:
    cfg = _load_cfg()
    updated = False
    if "style" not in cfg:
        cfg["style"] = {}
        updated = True
    section = cfg["style"]
    settings: dict[str, Any] = {}
    for key, default in STYLE_DEFAULTS.items():
        raw_value = section.get(key)
        if raw_value is None:
            section[key] = str(default)
            raw_value = str(default)
            updated = True
        try:
            if key in _STYLE_INT_KEYS:
                settings[key] = int(float(raw_value))
            elif key in _STYLE_FLOAT_KEYS:
                settings[key] = float(raw_value)
            else:
                settings[key] = raw_value
        except (TypeError, ValueError):
            # Fallback to default on invalid values
            settings[key] = default
            section[key] = str(default)
            updated = True
    if updated:
        _save_cfg(cfg)
    return settings


# Mutable global used by db.get_conn; always reference via config.DB_PATH
DB_PATH: Path | None = load_last_db()
