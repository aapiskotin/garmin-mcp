import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from garmin_mcp.api import create_app
from garmin_mcp.backup import Backups, BackupScheduler
from garmin_mcp.config import Settings
from garmin_mcp.feature_engine import FeatureEngine
from garmin_mcp.service import Service
from garmin_mcp.storage import Store


def activity(activity_id=1, day="2026-01-02", distance=5000):
    return {
        "activityId": activity_id,
        "activityName": "Run",
        "startTimeLocal": day + "T10:00:00",
        "startTimeGMT": day + "T09:00:00",
        "distance": distance,
        "duration": 1500,
        "ownerFullName": "PRIVATE",
        "startLatitude": 52.5,
    }


class FakeGarmin:
    def __init__(self):
        self.activities = [activity()]
        self.calls = []
        self.fail_page = None
        self.day_data = {
            "sleep": {"dailySleepDTO": {"calendarDate": "2026-01-02", "sleepTimeSeconds": 28800}},
            "hrv": {"hrvSummary": {"lastNightAvg": 42}},
            "body_battery": [],
            "training_readiness": [],
            "training_status": {},
        }

    def activity_page(self, start, end, offset, limit):
        self.calls.append(("activities", start, end, offset, limit))
        if offset == self.fail_page:
            raise RuntimeError("temporary failure")
        return copy.deepcopy(self.activities[offset : offset + limit])

    def raw_activity(self, activity_id, source):
        self.calls.append(("activity", activity_id, source))
        if source == "summary":
            return copy.deepcopy(next(a for a in self.activities if a["activityId"] == activity_id))
        return {"activityId": activity_id, "lapDTOs": [{"lapIndex": 1, "distance": 5000}]}

    def raw_day(self, day, source):
        self.calls.append(("day", day, source))
        value = self.day_data[source]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


@pytest.fixture
def service(tmp_path):
    return Service(Settings(data_dir=tmp_path, backup_enabled=False, page_size=2), FakeGarmin())


def refresh(service):
    return service.refresh_activities("2026-01-01", "2026-01-05")


def test_no_eager_work_and_dry_runs(service):
    assert service.status()["counts"] == {"activities": 0, "days": 0}
    assert service.lookup("activities")["status"] == "missing"
    assert service.refresh_activities()["status"] == "needs_start_date"
    assert service.refresh_activities("2026-01-01", dry_run=True)["status"] == "dry_run"
    service.refresh_activity(1, ["summary"], dry_run=True)
    service.refresh_day("2026-01-02", ["sleep"], dry_run=True)
    assert service.gateway.calls == []
    assert not list((service.store.root / "objects").iterdir())


def test_pagination_deduplication_and_resume(service):
    service.gateway.activities = [activity(1), activity(2), activity(2), activity(3)]
    service.gateway.fail_page = 2
    result = refresh(service)
    assert result["status"] == "partial"
    assert service.status()["sync"][0]["cursor"] is None
    assert service.status()["coverage"] == []
    assert service.status()["counts"]["activities"] == 2
    service.gateway.fail_page = None
    result = refresh(service)
    assert result["status"] == "ok"
    assert result["count"] == 3
    assert result["pages"] == 3
    assert service.status()["counts"]["activities"] == 3
    cursor = service.status()["sync"][0]["cursor"]
    refresh(service)
    assert service.status()["sync"][0]["cursor"] == cursor
    assert service.status()["counts"]["activities"] == 3
    plan = service.refresh_activities(dry_run=True)
    assert plan["start_date"] == "2026-01-01"


def test_failure_does_not_move_successful_cursor(service):
    refresh(service)
    cursor = service.status()["sync"][0]["cursor"]
    service.gateway.fail_page = 0
    assert service.refresh_activities()["status"] == "error"
    assert service.status()["sync"][0]["cursor"] == cursor
    assert service.status()["sync"][0]["status"] == "error"


def test_raw_archive_survives_normalization_failure(service):
    service.gateway.activities = [{"activityId": 42}]
    assert refresh(service)["status"] == "error"
    objects = list((service.store.root / "objects").glob("*.json"))
    assert json.loads(objects[0].read_text()) == [{"activityId": 42}]
    assert service.status()["coverage"] == []


def test_feature_typed_value_and_correction_invalidation(service):
    refresh(service)
    engine = FeatureEngine(service)
    calls = len(service.gateway.calls)
    assert engine.recalculate(["pace"], "activities", ["1"], dry_run=True)["status"] == "dry_run"
    with service.store.connection() as db:
        assert "feature_pace" not in [r[1] for r in db.execute("PRAGMA table_info(activities)")]
    result = engine.recalculate(["pace"], "activities", ["1"])
    assert result["results"][0]["value"] == 300
    assert len(service.gateway.calls) == calls
    before = service.lookup("activities")["rows"][0]
    assert before["feature_pace"] == 300
    assert "PRIVATE" not in json.dumps(before)
    assert "startLatitude" not in json.dumps(before)
    old_refs = service.store.rows("SELECT capture_id FROM captures")
    service.gateway.activities[0] = activity(day="2026-01-03", distance=6000)
    refresh(service)
    after = service.lookup("activities")["rows"][0]
    assert after["local_date"] == "2026-01-03"
    assert after["feature_pace"] is None
    assert after["features"][0]["status"] == "invalidated"
    assert service.status()["counts"]["activities"] == 1
    assert service.status()["counts"]["days"] == 2
    assert service.store.read_capture(old_refs[0]["capture_id"])[0]["distance"] == 5000
    assert engine.recalculate(["pace"], "activities", ["1"])["results"][0]["value"] == 250


def test_daily_partial_missing_is_not_zero_and_preserves_other_sources(service):
    service.gateway.day_data["hrv"] = RuntimeError("device unavailable")
    result = service.refresh_day("2026-01-02", ["sleep", "hrv", "body_battery"])
    assert result["status"] == "partial"
    assert result["sources"]["body_battery"]["status"] == "unavailable"
    row = service.lookup("days")["rows"][0]
    assert row["sleep_total_sleep_seconds"] == 28800
    assert row["sleep_date"] == "2026-01-02"
    assert row["stale"]
    engine = FeatureEngine(service)
    assert engine.recalculate(["sleep_hours"], "days", ["2026-01-02"])["results"][0]["value"] == 8
    service.gateway.day_data["hrv"] = {"hrvSummary": {"lastNightAvg": 40}}
    service.refresh_day("2026-01-02", ["hrv"])
    row = service.lookup("days")["rows"][0]
    assert row["sleep_total_sleep_seconds"] == 28800
    assert row["hrv_last_night_average_ms"] == 40
    service.gateway.day_data["sleep"] = {}
    service.refresh_day("2026-01-02", ["sleep"])
    row = service.lookup("days")["rows"][0]
    assert row["sleep_total_sleep_seconds"] is None
    calls = len(service.gateway.calls)
    result = engine.recalculate(["sleep_hours"], "days", ["2026-01-02"])
    assert result["results"][0]["status"] == "missing_inputs"
    assert len(service.gateway.calls) == calls


def test_explicit_source_fetches_only_requested_data(service):
    service.refresh_activity(1, ["summary"])
    service.refresh_day("2026-01-02", ["sleep"])
    assert service.gateway.calls == [("activity", 1, "summary"), ("day", "2026-01-02", "sleep")]


def test_direct_sql_repair_invalidation_and_rollback(service):
    refresh(service)
    FeatureEngine(service).recalculate(["pace"], "activities", ["1"])
    with pytest.raises(RuntimeError), service.store.transaction() as db:
        db.execute("UPDATE activities SET distance_m=123")
        raise RuntimeError("abort repair")
    assert service.lookup("activities")["rows"][0]["feature_pace"] == 300
    with service.store.transaction() as db:
        service.store.audit(db, "repair", {"reason": "test", "activity_id": 1})
        db.execute("UPDATE activities SET distance_m=6000")
    assert service.lookup("activities")["rows"][0]["features"][0]["status"] == "invalidated"
    assert service.store.rows("SELECT * FROM audit_log WHERE action='repair'")


def test_backup_restore_checksums_and_no_network(service):
    refresh(service)
    FeatureEngine(service).recalculate(["pace"], "activities", ["1"])
    backups = Backups(service)
    backup = backups.create()
    calls = len(service.gateway.calls)
    with service.store.transaction() as db:
        db.execute("UPDATE activities SET distance_m=99")
    assert backups.restore(backup["backup_id"])["status"] == "confirmation_required"
    assert backups.restore(backup["backup_id"], confirm=True)["status"] == "restored"
    assert service.lookup("activities")["rows"][0]["distance_m"] == 5000
    assert service.lookup("activities")["rows"][0]["feature_pace"] == 300
    assert len(service.gateway.calls) == calls
    directory = backups.directory / backup["backup_id"]
    next((directory / "objects").glob("*.json")).write_text("corruption")
    with pytest.raises(ValueError, match="checksum"):
        backups.restore(backup["backup_id"], confirm=True)
    assert service.lookup("activities")["rows"][0]["distance_m"] == 5000


def test_concurrent_identical_refreshes_coalesce(service):
    entered, release = threading.Event(), threading.Event()
    original = service.gateway.activity_page

    def slow(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    service.gateway.activity_page = slow
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(refresh, service)
        assert entered.wait(3)
        second_started = threading.Event()
        future = next(iter(service._flights.values()))
        original_result = future.result

        def waiting(*args, **kwargs):
            second_started.set()
            return original_result(*args, **kwargs)

        future.result = waiting
        second = pool.submit(refresh, service)
        assert second_started.wait(3)
        release.set()
        assert first.result()["status"] == "ok"
        assert second.result()["coalesced"] is True
    assert len(service.gateway.calls) == 1


def test_migration_tampering_rejected(service, monkeypatch, tmp_path):
    with service.store.transaction() as db:
        db.execute("UPDATE migrations SET sha256='wrong'")
    with pytest.raises(ValueError, match="migration changed"):
        Store(service.settings)


def test_http_lookup_swagger_and_confirmation(service, monkeypatch):
    from garmin_mcp import server

    monkeypatch.setattr(server, "_gateway", service.gateway)
    with TestClient(create_app(service, mount_mcp=False)) as client:
        assert client.get("/").status_code == 200
        schema = client.get("/openapi.json").json()
        assert "/api/refresh/activities" in schema["paths"]
        assert "/api/garmin/create_workout" in schema["paths"]
        assert client.get("/docs").status_code == 200
        assert client.get("/api/status").json()["counts"]["activities"] == 0
        assert service.gateway.calls == []
        response = client.post(
            "/api/refresh/activities", json={"start_date": "2026-01-01", "end_date": "2026-01-05"}
        )
        assert response.json()["status"] == "ok"
        assert client.get("/api/lookup/activities").json()["rows"][0]["activity_id"] == 1
        assert (
            client.post("/api/garmin/delete_workout?workout_id=1").json()["status"]
            == "confirmation_required"
        )
        assert (
            client.post("/api/refresh/activities", json={"start_date": "oops"}).status_code == 400
        )
        assert (
            client.get("/api/status", headers={"Origin": "https://other.test"}).status_code == 403
        )


def test_scheduler_once_daily_local_only(service):
    settings = Settings(
        data_dir=service.settings.data_dir, backup_enabled=True, backup_time="00:00"
    )
    service.settings = settings
    scheduler = BackupScheduler(Backups(service))
    now = datetime.now(ZoneInfo(settings.timezone))
    scheduler.tick(now)
    scheduler.tick(now)
    assert len(scheduler.backups.list()) == 1
    assert service.gateway.calls == []


def test_mcp_transport_shares_local_service(service, monkeypatch):
    from garmin_mcp import server

    monkeypatch.setattr(server, "_local", service)
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(create_app(service), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200
        assert "result" in response.json()
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "local_status", "arguments": {}},
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["counts"]["activities"] == 0
        assert service.gateway.calls == []


def test_definition_versioning_atomicity_and_validation(service, monkeypatch):
    refresh(service)
    engine = FeatureEngine(service)
    definition, calculate, code = engine.load("pace")
    engine.recalculate(["pace"], "activities", ["1"])
    monkeypatch.setattr(engine, "load", lambda name: (definition, calculate, code + b"\n# changed"))
    with pytest.raises(ValueError, match="version bump"):
        engine.recalculate(["pace"], "activities", ["1"])
    assert service.lookup("activities")["rows"][0]["feature_pace"] == 300
    definition = {**definition, "version": 2}
    result = engine.recalculate(["pace"], "activities", ["1"])
    assert result["results"][0]["version"] == 2
    assert len(service.store.rows("SELECT * FROM feature_definitions")) == 2
    monkeypatch.setattr(
        engine,
        "load",
        lambda name: ({**definition, "version": 3}, lambda inputs: float("nan"), code + b"\n# nan"),
    )
    result = engine.recalculate(["pace"], "activities", ["1"])
    assert result["results"][0]["status"] == "error"
    assert service.lookup("activities")["rows"][0]["feature_pace"] is None


def test_lookback_missing_coverage_and_covered_zero(service, monkeypatch):
    service.refresh_day("2026-01-02", ["sleep"])
    engine = FeatureEngine(service)
    definition = {
        "name": "distance_2d",
        "version": 1,
        "entity": "days",
        "sql_type": "REAL",
        "unit": "m",
        "inputs": ["activity_list"],
        "lookback_days": 1,
    }
    monkeypatch.setattr(
        engine,
        "load",
        lambda name: (
            definition,
            lambda inputs: sum(a["distance"] for a in inputs["activity_list"]),
            b"# test",
        ),
    )
    calls = len(service.gateway.calls)
    result = engine.recalculate(["distance_2d"], "days", ["2026-01-02"])
    assert result["results"][0]["status"] == "missing_inputs"
    assert len(service.gateway.calls) == calls
    service.gateway.activities = []
    refresh(service)
    result = engine.recalculate(["distance_2d"], "days", ["2026-01-02"])
    assert result["results"][0]["value"] == 0
    assert result["results"][0]["status"] == "ok"


def test_correction_invalidates_old_detail_sources(service):
    refresh(service)
    service.refresh_activity(1, ["summary", "splits"])
    service.gateway.activities[0] = activity(distance=7000)
    refresh(service)
    sources = {
        row["source"]: row["status"] for row in service.lookup("activities")["rows"][0]["sources"]
    }
    assert sources == {"activity_list": "ok", "summary": "invalidated", "splits": "invalidated"}
    # Even if the caller lists splits first, the corrected summary is fetched first.
    service.refresh_activity(1, ["splits", "summary"])
    assert service.gateway.calls[-2:] == [("activity", 1, "summary"), ("activity", 1, "splits")]


def test_missing_body_battery_clears_previous_scalars(service):
    service.gateway.day_data["body_battery"] = [
        {"date": "2026-01-02", "charged": 40, "drained": 30}
    ]
    service.refresh_day("2026-01-02", ["body_battery"])
    assert service.lookup("days")["rows"][0]["body_battery_charged"] == 40
    service.gateway.day_data["body_battery"] = []
    service.refresh_day("2026-01-02", ["body_battery"])
    assert service.lookup("days")["rows"][0]["body_battery_charged"] is None


def test_request_boundary_filters_future_activities(service):
    today = datetime.now(ZoneInfo(service.settings.timezone)).date().isoformat()
    service.gateway.activities = [
        activity(),
        {**activity(2, today), "startTimeGMT": "2999-01-01T00:00:00"},
    ]
    result = service.refresh_activities("2026-01-01")
    assert result["count"] == 1
    assert service.status()["counts"]["activities"] == 1


def test_repeated_page_and_page_limit_do_not_claim_coverage(service):
    service.gateway.activities = [activity(1), activity(2)]
    original = service.gateway.activity_page
    service.gateway.activity_page = lambda start, end, offset, limit: original(start, end, 0, limit)
    assert refresh(service)["status"] == "partial"
    assert service.status()["coverage"] == []


def test_existing_gateway_reads_archive_without_claiming_full_coverage(tmp_path, monkeypatch):
    from garmin_mcp.gateway import GarminGateway

    class Client:
        garmin_connect_activities = "/activities"

        def connectapi(self, path, params):
            return [activity()]

        def get_activity(self, activity_id):
            return activity()

    gateway = GarminGateway(tmp_path / "tokens")
    monkeypatch.setattr(gateway, "client", lambda: Client())
    service = Service(Settings(data_dir=tmp_path / "data", backup_enabled=False), gateway)
    gateway.list_activities("2026-01-01", "2026-01-05")
    gateway.activity_summary(1)
    assert service.status()["counts"]["activities"] == 1
    assert service.status()["coverage"] == []
    assert len(service.store.rows("SELECT * FROM captures")) == 2
    assert service.lookup("activities")["rows"][0]["sources"][0]["status"] == "ok"


def test_calendar_timezone_preserves_source_date(service):
    service.gateway.activities = [
        {**activity(day="2026-01-02"), "startTimeGMT": "2026-01-01T22:30:00"}
    ]
    refresh(service)
    row = service.lookup("activities")["rows"][0]
    assert row["local_date"] == "2026-01-01"
    assert row["source_local_date"] == "2026-01-02"
    assert row["start_time_utc"] == "2026-01-01T22:30:00+00:00"
