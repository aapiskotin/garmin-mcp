from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backup import Backups, BackupScheduler
from .feature_engine import FeatureEngine


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActivityRefresh(Body):
    start_date: str | None = None
    end_date: str | None = None
    dry_run: bool = False


class Sources(Body):
    sources: list[str] = Field(min_length=1, max_length=5)
    dry_run: bool = False


class Recalculation(Body):
    names: list[str] = Field(min_length=1, max_length=20)
    entity: Literal["activities", "days"]
    ids: list[str] = Field(min_length=1, max_length=500)
    dry_run: bool = False


class BackupRequest(Body):
    include_tokens: bool = False


class RestoreRequest(Body):
    confirm: bool = False
    restore_tokens: bool = False


def create_app(service=None, mount_mcp=True):
    from . import server

    service = service or server.local_service()
    backups = Backups(service)
    features = FeatureEngine(service)
    mcp_app = server.mcp.streamable_http_app() if mount_mcp else None

    @asynccontextmanager
    async def lifespan(app):
        with BackupScheduler(backups):
            if mcp_app:
                async with server.mcp.session_manager.run():
                    yield
            else:
                yield

    app = FastAPI(title="Local Garmin app", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1",
            "localhost",
            "[::1]",
            "testserver",
        ],
    )

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"status": "error", "error": "Cross-origin access denied"}, 403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_request(request, exc):
        return JSONResponse({"status": "error", "error": str(exc)}, 400)

    @app.exception_handler(Exception)
    async def operation_failed(request, exc):
        logging.getLogger(__name__).error("Local operation failed", exc_info=exc)
        return JSONResponse(
            {
                "status": "error",
                "error": type(exc).__name__,
                "message": "Local operation failed; inspect server logs",
            },
            500,
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def ui():
        return (Path(__file__).parent / "lookup.html").read_text()

    @app.get("/api/status")
    def status():
        return service.status()

    @app.get("/api/lookup/{entity}")
    def lookup(
        entity: Literal["activities", "days"],
        start_date: str | None = None,
        end_date: str | None = None,
        activity_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        return service.lookup(entity, start_date, end_date, activity_id, limit, offset)

    @app.post("/api/refresh/activities")
    def refresh_activities(body: ActivityRefresh):
        return service.refresh_activities(**body.model_dump())

    @app.post("/api/refresh/activities/{activity_id}")
    def refresh_activity(activity_id: int, body: Sources):
        return service.refresh_activity(activity_id, **body.model_dump())

    @app.post("/api/refresh/days/{local_date}")
    def refresh_day(local_date: str, body: Sources):
        return service.refresh_day(local_date, **body.model_dump())

    @app.get("/api/features")
    def feature_catalog():
        return features.catalog()

    @app.post("/api/features/recalculate")
    def recalculate(body: Recalculation):
        return features.recalculate(**body.model_dump())

    @app.get("/api/backups")
    def list_backups():
        return backups.list()

    @app.post("/api/backups")
    def backup(body: BackupRequest):
        return backups.create(**body.model_dump())

    @app.post("/api/backups/{backup_id}/restore")
    def restore(backup_id: str, body: RestoreRequest):
        return backups.restore(backup_id, **body.model_dump())

    # Existing MCP functions remain the single implementation of Garmin actions,
    # including preview and confirmation. FastAPI derives their OpenAPI schemas.
    for name in (
        "connection_status",
        "preview_workout",
        "list_workouts",
        "list_scheduled_workouts",
        "list_activities",
        "get_activity_summary",
        "get_activity_splits",
        "get_recovery_status",
        "create_workout",
        "delete_workout",
        "unschedule_workout",
    ):
        app.add_api_route(
            f"/api/garmin/{name}",
            getattr(server, name),
            methods=["POST"],
            tags=["Garmin (explicit request)"],
            name=name,
        )
    if mcp_app:
        app.mount("/", mcp_app)
    return app
