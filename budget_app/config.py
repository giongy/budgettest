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


def load_last_db() -> Path:
    if CONFIG_FILE.exists():
        cfg = configparser.ConfigParser()
        cfg.read(CONFIG_FILE)
        if "app" in cfg and "db_path" in cfg["app"]:
            path = Path(cfg["app"]["db_path"])
            if path.exists():
                return path
    # default fallback within workspace
    return Path(r"D:\budgettest\mmex_casa.mmb")


def save_last_db(path: Path) -> None:
    cfg = configparser.ConfigParser()
    cfg["app"] = {"db_path": str(path)}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


# Mutable global used by db.get_conn; always reference via config.DB_PATH
DB_PATH: Path = load_last_db()
