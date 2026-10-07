"""Use a WAL-safe SQLite on every supported platform."""

import sqlite3


def require_patched_sqlite() -> None:
    version = sqlite3.sqlite_version_info
    if not (
        version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51) or (3, 44, 6) <= version < (3, 45)
    ):
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} lacks the WAL-reset fix. "
            "Use Docker Compose or Python linked to SQLite >=3.51.3."
        )
