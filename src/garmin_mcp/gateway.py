from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from .normalizers import (
    normalize_activity,
    normalize_body_battery,
    normalize_hrv,
    normalize_sleep,
    normalize_splits,
    normalize_training_readiness,
    normalize_training_status,
)


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

    def list_activities(
        self,
        start_date: str,
        end_date: str,
        activity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        activities = self.client().get_activities_by_date(
            start_date,
            end_date,
            activity_type,
            "desc",
        )
        return [normalize_activity(activity) for activity in activities[:limit]]

    def activity_summary(self, activity_id: int | str) -> dict[str, Any]:
        return normalize_activity(self.client().get_activity(str(activity_id)))

    def activity_splits(self, activity_id: int | str) -> dict[str, Any]:
        return normalize_splits(self.client().get_activity_splits(str(activity_id)))

    def recovery_status(self, date: str) -> dict[str, Any]:
        """Aggregate optional recovery metrics without failing when one source is absent."""
        client = self.client()
        result: dict[str, Any] = {"date": date}
        errors: dict[str, str] = {}

        sources = {
            "sleep": (
                lambda: client.get_sleep_data(date),
                normalize_sleep,
            ),
            "hrv": (
                lambda: client.get_hrv_data(date),
                normalize_hrv,
            ),
            "body_battery": (
                lambda: client.get_body_battery(date, date),
                normalize_body_battery,
            ),
            "training_readiness": (
                lambda: client.get_training_readiness(date),
                normalize_training_readiness,
            ),
            "training_status": (
                lambda: client.get_training_status(date),
                normalize_training_status,
            ),
        }

        for name, (fetch, normalize) in sources.items():
            try:
                result[name] = normalize(fetch())
            except Exception as exc:  # Garmin endpoints can vary by device/account.
                result[name] = {"available": False}
                errors[name] = str(exc)

        if errors:
            result["errors"] = errors
        return result

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.client().upload_workout(payload)

    def schedule(self, workout_id: int | str, date: str) -> dict[str, Any]:
        return self.client().schedule_workout(workout_id, date)

    def delete(self, workout_id: int | str) -> Any:
        return self.client().delete_workout(workout_id)

    def unschedule(self, scheduled_workout_id: int | str) -> Any:
        return self.client().unschedule_workout(scheduled_workout_id)
