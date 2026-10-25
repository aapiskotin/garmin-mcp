"""One typed operation catalog exposed identically over HTTP and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from . import models
from .service import invoke


class ErrorResponse(BaseModel):
    status: str
    message: str


def operations(get_service):
    def training_list_cycles() -> dict[str, Any]:
        """List locally saved training cycles. Never contacts Garmin."""
        return invoke(get_service(), "list_cycles")

    def training_create_cycle(body: models.CreateCycle) -> dict[str, Any]:
        """Save and accept an initial full plan. Local-only; idempotency key required."""
        return invoke(get_service(), "create_cycle", body=body)

    def training_get_cycle(cycle_id: str, revision_id: str | None = None) -> dict[str, Any]:
        """Read a full plan snapshot and its revision history."""
        return invoke(get_service(), "get_cycle", cycle_id=cycle_id, revision_id=revision_id)

    def training_create_draft(cycle_id: str, body: models.DraftCreate) -> dict[str, Any]:
        """Propose a full snapshot; does not change the accepted plan."""
        return invoke(get_service(), "create_draft", cycle_id=cycle_id, body=body)

    def training_edit_draft(
        cycle_id: str, revision_id: str, body: models.DraftEdit
    ) -> dict[str, Any]:
        """Edit a draft with both accepted-plan and draft-version conflict protection."""
        return invoke(
            get_service(), "edit_draft", cycle_id=cycle_id, revision_id=revision_id, body=body
        )

    def training_apply_draft(
        cycle_id: str, revision_id: str, body: models.DraftAction
    ) -> dict[str, Any]:
        """Accept a local plan atomically; a user instruction to save is sufficient."""
        return invoke(
            get_service(), "apply_draft", cycle_id=cycle_id, revision_id=revision_id, body=body
        )

    def training_discard_draft(
        cycle_id: str, revision_id: str, body: models.DraftAction
    ) -> dict[str, Any]:
        """Discard a draft while retaining its history and all execution records."""
        return invoke(
            get_service(), "discard_draft", cycle_id=cycle_id, revision_id=revision_id, body=body
        )

    def training_lifecycle(cycle_id: str, body: models.Lifecycle) -> dict[str, Any]:
        """Complete/cancel a cycle in a new revision, retaining its history."""
        return invoke(get_service(), "lifecycle", cycle_id=cycle_id, body=body)

    def training_compare(
        cycle_id: str, from_revision_id: str, to_revision_id: str
    ) -> dict[str, Any]:
        """Compare full snapshots: before, after, stable entity ID, and reason."""
        return invoke(
            get_service(),
            "compare",
            cycle_id=cycle_id,
            from_revision_id=from_revision_id,
            to_revision_id=to_revision_id,
        )

    def training_context(
        cycle_id: str, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        """Read the local calendar, plan/actual, goals, feedback, decisions, and coverage."""
        return invoke(
            get_service(), "context", cycle_id=cycle_id, start_date=start_date, end_date=end_date
        )

    def training_candidates(cycle_id: str, session_id: str) -> dict[str, Any]:
        """Explicitly find local activity candidates with reasons; never confirms or imports."""
        return invoke(get_service(), "candidates", cycle_id=cycle_id, session_id=session_id)

    def training_save_outcome(cycle_id: str, body: models.Outcome) -> dict[str, Any]:
        """Record manual execution or choose Garmin metrics, preserving the assessed assignment."""
        return invoke(get_service(), "save_outcome", cycle_id=cycle_id, body=body)

    def training_save_match(cycle_id: str, body: models.Match) -> dict[str, Any]:
        """Confirm/revise an activity link; shared activities need explicit allocations."""
        return invoke(get_service(), "save_match", cycle_id=cycle_id, body=body)

    def training_save_feedback(cycle_id: str, body: models.Feedback) -> dict[str, Any]:
        """Save effort, wellbeing, and comments with an audited revision of the record."""
        return invoke(get_service(), "save_feedback", cycle_id=cycle_id, body=body)

    def training_save_review(cycle_id: str, body: models.Review) -> dict[str, Any]:
        """Save review conclusions, source captures, limitations, and a proposed revision."""
        return invoke(get_service(), "save_review", cycle_id=cycle_id, body=body)

    def training_history(cycle_id: str) -> dict[str, Any]:
        """Read local before/after audit history, including feedback corrections."""
        return invoke(get_service(), "history", cycle_id=cycle_id)

    return [
        ("GET", "/cycles", training_list_cycles),
        ("POST", "/cycles", training_create_cycle),
        ("GET", "/cycles/{cycle_id}", training_get_cycle),
        ("POST", "/cycles/{cycle_id}/drafts", training_create_draft),
        ("PUT", "/cycles/{cycle_id}/drafts/{revision_id}", training_edit_draft),
        ("POST", "/cycles/{cycle_id}/drafts/{revision_id}/apply", training_apply_draft),
        ("POST", "/cycles/{cycle_id}/drafts/{revision_id}/discard", training_discard_draft),
        ("POST", "/cycles/{cycle_id}/lifecycle", training_lifecycle),
        ("GET", "/cycles/{cycle_id}/compare", training_compare),
        ("GET", "/cycles/{cycle_id}/context", training_context),
        ("GET", "/cycles/{cycle_id}/sessions/{session_id}/candidates", training_candidates),
        ("PUT", "/cycles/{cycle_id}/outcomes", training_save_outcome),
        ("PUT", "/cycles/{cycle_id}/matches", training_save_match),
        ("PUT", "/cycles/{cycle_id}/feedback", training_save_feedback),
        ("PUT", "/cycles/{cycle_id}/reviews", training_save_review),
        ("GET", "/cycles/{cycle_id}/history", training_history),
    ]


class TrainingResponse(JSONResponse):
    def __init__(self, content, status_code=200, **kwargs):
        if isinstance(content, dict):
            status_code = {
                "validation_error": 422,
                "not_found": 404,
                "conflict": 409,
                "missing_inputs": 422,
            }.get(content.get("status"), status_code)
        super().__init__(content, status_code=status_code, **kwargs)


def add_http(app, service):
    router = APIRouter(
        prefix="/api/training",
        tags=["Local training plans"],
        default_response_class=TrainingResponse,
        responses={code: {"model": ErrorResponse} for code in (404, 409, 422)},
    )
    for method, path, function in operations(lambda: service):
        router.add_api_route(path, function, methods=[method])
    app.include_router(router)

    @app.get("/training", response_class=HTMLResponse, include_in_schema=False)
    def training_ui():
        return (Path(__file__).parent / "training.html").read_text()


def add_mcp(mcp, get_service):
    for method, _, function in operations(get_service):
        mcp.tool(
            structured_output=True,
            annotations=ToolAnnotations(
                readOnlyHint=method == "GET",
                destructiveHint=False,
                idempotentHint=method == "GET",
                openWorldHint=False,
            ),
        )(function)
