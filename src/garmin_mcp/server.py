from __future__ import annotations

import os
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
    port=int(os.getenv("GARMIN_MCP_PORT", "8000")),
    stateless_http=True,
    json_response=True,
)
_gateway = GarminGateway()

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
            "No Garmin changes were made. Ask the user to confirm, "
            "then retry with confirm=true."
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
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
