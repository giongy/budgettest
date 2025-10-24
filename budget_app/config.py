from pathlib import Path
import sys
import configparser


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


def _load_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        cfg.read(CONFIG_FILE)
    return cfg


def _save_cfg(cfg: configparser.ConfigParser) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


def load_last_db() -> Path:
    cfg = _load_cfg()
    db_path = cfg.get("app", "db_path", fallback=None)
    if db_path:
        path = Path(db_path)
        if path.exists():
            return path
    # default fallback within workspace
    return Path(r"D:\budgettest\mmex_casa.mmb")


def save_last_db(path: Path) -> None:
    cfg = _load_cfg()
    if "app" not in cfg:
        cfg["app"] = {}
    cfg["app"]["db_path"] = str(path)
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


# Mutable global used by db.get_conn; always reference via config.DB_PATH
DB_PATH: Path = load_last_db()
