from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from garminconnect import Garmin


class GarminGateway:
    """Lazy, token-only connection to the unofficial Garmin Connect client."""

    def __init__(self, token_dir: str | Path | None = None) -> None:
        configured = token_dir or os.getenv("GARMIN_TOKEN_DIR", ".garmin-tokens")
        self.token_dir = Path(configured).expanduser().resolve()
        self._client: Garmin | None = None
        self._lock = threading.Lock()

    def client(self) -> Garmin:
        with self._lock:
            if self._client is not None:
                return self._client
            token_file = self.token_dir / "garmin_tokens.json"
            if not token_file.exists():
                raise RuntimeError(
                    f"No Garmin tokens at {token_file}. Run garmin-mcp-login first."
                )
            client = Garmin()
            client.login(str(self.token_dir))
            self._client = client
            return client

    def status(self) -> dict[str, Any]:
        client = self.client()
        return {
            "authenticated": True,
            "display_name": client.display_name,
            "token_dir": str(self.token_dir),
        }

    def list_workouts(self, limit: int) -> list[dict[str, Any]]:
        return self.client().get_workouts(start=0, limit=limit)

    def list_scheduled(self, year: int, month: int) -> dict[str, Any]:
        return self.client().get_scheduled_workouts(year, month)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client().upload_workout(payload)

    def schedule(self, workout_id: int | str, date: str) -> dict[str, Any]:
        return self.client().schedule_workout(workout_id, date)

    def delete(self, workout_id: int | str) -> Any:
        return self.client().delete_workout(workout_id)

    def unschedule(self, scheduled_workout_id: int | str) -> Any:
        return self.client().unschedule_workout(scheduled_workout_id)
