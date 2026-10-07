from __future__ import annotations

import os
import threading
from datetime import date as Date
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .builder import build_workout_payload, workout_summary
from .gateway import GarminGateway
from .models import WorkoutSpec

mcp = FastMCP(
    "Personal Garmin Connect",
    instructions=(
        "Create and schedule structured Garmin Connect workouts through unofficial endpoints. "
        "Always preview a workout and obtain explicit user confirmation before calling a write "
        "or destructive tool with confirm=true. Never request or transmit a Garmin password."
    ),
    host=os.getenv("GARMIN_MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("GARMIN_MCP_PORT", "8765")),
    stateless_http=True,
    json_response=True,
)
_gateway = GarminGateway()
_local = None
_local_lock = threading.Lock()


def local_service():
    global _local
    with _local_lock:
        if _local is None:
            from .config import Settings
            from .service import Service

            _local = Service(Settings(), _gateway)
        return _local


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)


def _require_confirmation(confirm: bool, action: str) -> dict[str, Any] | None:
    if confirm:
        return None
    return {
        "status": "confirmation_required",
        "action": action,
        "message": (
            "No Garmin changes were made. Ask the user to confirm, then retry with confirm=true."
        ),
    }


@mcp.tool(annotations=READ_ONLY)
def connection_status() -> dict[str, Any]:
    """Verify that saved Garmin tokens can authenticate."""
    return _gateway.status()


@mcp.tool(annotations=READ_ONLY)
def preview_workout(workout: WorkoutSpec) -> dict[str, Any]:
    """Validate and preview Garmin JSON without changing Garmin Connect."""
    return {
        "status": "preview",
        "summary": workout_summary(workout),
        "garmin_payload": build_workout_payload(workout),
    }


@mcp.tool(annotations=READ_ONLY)
def list_workouts(limit: int = 20) -> list[dict[str, Any]]:
    """List workout templates from the user's Garmin Connect library."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    return _gateway.list_workouts(limit)


@mcp.tool(annotations=READ_ONLY)
def list_scheduled_workouts(year: int, month: int) -> dict[str, Any]:
    """List Garmin calendar entries for a year and month."""
    if year < 2000 or not 1 <= month <= 12:
        raise ValueError("Use year >= 2000 and month 1-12")
    return _gateway.list_scheduled(year, month)


@mcp.tool(annotations=READ_ONLY)
def list_activities(
    start_date: str,
    end_date: str | None = None,
    activity_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List completed Garmin activities in an inclusive YYYY-MM-DD date range.

    Returns compact planning metrics and omits GPS coordinates and owner details.
    `activity_type` is an optional Garmin type key such as running or cycling.
    """
    start = Date.fromisoformat(start_date)
    end = Date.fromisoformat(end_date) if end_date else start
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")
    if (end - start).days + 1 > 366:
        raise ValueError("Date range must not exceed 366 days")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    normalized_type = activity_type.strip() if activity_type else None
    activities = _gateway.list_activities(
        start.isoformat(),
        end.isoformat(),
        normalized_type or None,
        limit,
    )
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "activity_type": normalized_type or None,
        "count": len(activities),
        "activities": activities,
    }


@mcp.tool(annotations=READ_ONLY)
def get_activity_summary(activity_id: int) -> dict[str, Any]:
    """Get compact summary metrics for one completed Garmin activity."""
    if activity_id < 1:
        raise ValueError("activity_id must be positive")
    return _gateway.activity_summary(activity_id)


@mcp.tool(annotations=READ_ONLY)
def get_activity_splits(activity_id: int) -> dict[str, Any]:
    """Get compact lap and interval metrics for one completed Garmin activity."""
    if activity_id < 1:
        raise ValueError("activity_id must be positive")
    return _gateway.activity_splits(activity_id)


@mcp.tool(annotations=READ_ONLY)
def get_recovery_status(date: str) -> dict[str, Any]:
    """Get available sleep, HRV, Body Battery, readiness, and training status for a date."""
    parsed_date = Date.fromisoformat(date)
    return _gateway.recovery_status(parsed_date.isoformat())


@mcp.tool(annotations=WRITE)
def create_workout(
    workout: WorkoutSpec,
    schedule_date: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a Garmin workout and optionally schedule it on YYYY-MM-DD.

    Call with confirm=false for a non-mutating confirmation response. Set confirm=true only
    after the user explicitly approves the preview and date.
    """
    pending = _require_confirmation(confirm, "create_workout")
    if pending:
        return {**pending, "preview": preview_workout(workout), "schedule_date": schedule_date}

    parsed_date: str | None = None
    if schedule_date:
        parsed_date = Date.fromisoformat(schedule_date).isoformat()

    created = _gateway.create(build_workout_payload(workout))
    workout_id = created.get("workoutId")
    result: dict[str, Any] = {
        "status": "created",
        "workout_id": workout_id,
        "created": created,
    }
    if parsed_date:
        if not workout_id:
            return {
                **result,
                "status": "created_not_scheduled",
                "schedule_error": "Garmin response contained no workoutId.",
            }
        try:
            result["scheduled"] = _gateway.schedule(workout_id, parsed_date)
            result["status"] = "created_and_scheduled"
            result["schedule_date"] = parsed_date
        except Exception as exc:
            result["status"] = "created_not_scheduled"
            result["schedule_error"] = str(exc)
    return result


@mcp.tool(annotations=DESTRUCTIVE)
def delete_workout(workout_id: int, confirm: bool = False) -> dict[str, Any]:
    """Permanently delete a workout template after explicit confirmation."""
    pending = _require_confirmation(confirm, f"delete_workout:{workout_id}")
    if pending:
        return pending
    return {
        "status": "deleted",
        "workout_id": workout_id,
        "garmin_response": _gateway.delete(workout_id),
    }


@mcp.tool(annotations=DESTRUCTIVE)
def unschedule_workout(
    scheduled_workout_id: int,
    confirm: bool = False,
) -> dict[str, Any]:
    """Remove a scheduled workout from the calendar without deleting its template."""
    pending = _require_confirmation(confirm, f"unschedule_workout:{scheduled_workout_id}")
    if pending:
        return pending
    return {
        "status": "unscheduled",
        "scheduled_workout_id": scheduled_workout_id,
        "garmin_response": _gateway.unschedule(scheduled_workout_id),
    }


def main() -> None:
    transport = os.getenv("GARMIN_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise SystemExit("GARMIN_MCP_TRANSPORT must be stdio or streamable-http")
    if transport == "streamable-http":
        import uvicorn

        from .api import create_app

        uvicorn.run(
            create_app(),
            host=os.getenv("GARMIN_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("GARMIN_MCP_PORT", "8765")),
        )
    else:
        from .backup import Backups, BackupScheduler

        with BackupScheduler(Backups(local_service())):
            mcp.run(transport="stdio")


LOCAL_READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@mcp.tool(annotations=LOCAL_READ)
def local_status() -> dict[str, Any]:
    """Read local coverage, freshness, and errors without contacting Garmin."""
    return local_service().status()


@mcp.tool(annotations=LOCAL_READ)
def lookup_records(
    entity: str,
    start_date: str | None = None,
    end_date: str | None = None,
    activity_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Read cached activities/days with source freshness and feature status."""
    return local_service().lookup(entity, start_date, end_date, activity_id, limit, offset)


@mcp.tool(annotations=LOCAL_WRITE)
def refresh_activities(
    start_date: str | None = None, end_date: str | None = None, dry_run: bool = False
) -> dict:
    """Explicitly refresh all activity pages; first call requires start_date. No details fetched."""
    return local_service().refresh_activities(start_date, end_date, dry_run)


@mcp.tool(annotations=LOCAL_WRITE)
def refresh_activity(activity_id: int, sources: list[str], dry_run: bool = False) -> dict:
    """Archive only requested summary/splits for an activity."""
    return local_service().refresh_activity(activity_id, sources, dry_run)


@mcp.tool(annotations=LOCAL_WRITE)
def refresh_day(local_date: str, sources: list[str], dry_run: bool = False) -> dict:
    """Archive only requested daily sources: sleep, hrv, body_battery, readiness/status."""
    return local_service().refresh_day(local_date, sources, dry_run)


@mcp.tool(annotations=LOCAL_READ)
def feature_catalog() -> list[dict]:
    """List available local Python feature definitions; does not compute them."""
    from .feature_engine import FeatureEngine

    return FeatureEngine(local_service()).catalog()


@mcp.tool(annotations=LOCAL_WRITE)
def recalculate_features(
    names: list[str], entity: str, ids: list[str], dry_run: bool = False
) -> dict:
    """Compute requested features/rows from archives. Report missing inputs without downloading."""
    from .feature_engine import FeatureEngine

    return FeatureEngine(local_service()).recalculate(names, entity, ids, dry_run)


@mcp.tool(annotations=LOCAL_READ)
def list_backups() -> list[dict]:
    """List complete local backups."""
    from .backup import Backups

    return Backups(local_service()).list()


@mcp.tool(annotations=LOCAL_WRITE)
def backup_local(include_tokens: bool = False) -> dict:
    """Create an online SQLite backup with raw objects, definitions, and checksums."""
    from .backup import Backups

    return Backups(local_service()).create(include_tokens)


@mcp.tool(annotations=DESTRUCTIVE)
def restore_local(backup_id: str, confirm: bool = False, restore_tokens: bool = False) -> dict:
    """Restore a verified local backup after confirmation; never replays Garmin actions."""
    from .backup import Backups

    return Backups(local_service()).restore(backup_id, confirm, restore_tokens)


if __name__ == "__main__":
    main()
