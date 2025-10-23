import sqlite3
from . import config


def get_conn():
    """Return a sqlite3 connection to the configured DB path."""
    return sqlite3.connect(str(config.DB_PATH))

