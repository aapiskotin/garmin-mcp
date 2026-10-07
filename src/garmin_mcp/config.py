from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("GARMIN_DATA_DIR", ".garmin-data"))
    )
    account_id: str = field(default_factory=lambda: os.getenv("GARMIN_ACCOUNT_ID", "personal"))
    timezone: str = field(default_factory=lambda: os.getenv("GARMIN_TIMEZONE", "Europe/Berlin"))
    overlap_days: int = field(default_factory=lambda: int(os.getenv("GARMIN_OVERLAP_DAYS", "7")))
    page_size: int = 100
    max_pages: int = 1000
    refresh_timeout: float = 120
    wait_timeout: float = 130
    stale_hours: int = 24
    backup_time: str = field(default_factory=lambda: os.getenv("GARMIN_BACKUP_TIME", "03:00"))
    backup_enabled: bool = field(
        default_factory=lambda: os.getenv("GARMIN_BACKUP_ENABLED", "true").lower() == "true"
    )

    def __post_init__(self):
        ZoneInfo(self.timezone)
        time.fromisoformat(self.backup_time)
        if not self.account_id or self.overlap_days < 0:
            raise ValueError("Set an account ID and nonnegative overlap")
        if not 1 <= self.page_size <= 1000 or self.max_pages < 1:
            raise ValueError("Invalid pagination settings")

    @property
    def database(self) -> Path:
        return self.data_dir / "garmin.sqlite3"
