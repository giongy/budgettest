import sqlite3
from . import config


def get_conn():
    """Return a sqlite3 connection to the configured DB path."""
    db_path = config.DB_PATH
    if not db_path:
        raise RuntimeError("Database path is not configured. Please choose a DB file.")
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    return sqlite3.connect(str(db_path))
