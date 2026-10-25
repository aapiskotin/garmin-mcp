"""First-release acceptance tests. All Garmin calls use local fixture data."""

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_local_app import FakeGarmin, activity

from garmin_mcp.api import create_app
from garmin_mcp.backup import Backups
from garmin_mcp.config import Settings
from garmin_mcp.service import Service
from garmin_mcp.training import models
from garmin_mcp.training.service import TrainingError, TrainingService


@pytest.fixture
def local(tmp_path):
    return Service(Settings(data_dir=tmp_path, backup_enabled=False), FakeGarmin())


@pytest.fixture
def training(local):
    return TrainingService(local)


def plan():
    return {
        "cycle": {
            "name": "Spring half",
            "start_date": "2026-01-01",
            "end_date": "2026-03-01",
            "main_event_id": "main",
            "goal_type": "target_time",
            "target_time_s": 7200,
        },
        "events": [
            {
                "id": "main",
                "name": "Half marathon",
                "date": "2026-03-01",
                "role": "main",
                "distance_m": 21097.5,
                "timezone": "Europe/Istanbul",
            },
            {"id": "practice", "name": "10K", "date": "2026-02-01", "role": "training"},
        ],
        "phases": [
            {"id": "base", "name": "Base", "start_date": "2026-01-01", "end_date": "2026-02-01"}
        ],
        "weeks": [
            {"id": "w1", "start_date": "2025-12-29", "phase_id": "base", "detail": "scheduled"},
            {"id": "w2", "start_date": "2026-01-05", "phase_id": "base", "detail": "outline"},
        ],
        "sessions": [
            {
                "id": "easy",
                "week_id": "w1",
                "date": "2026-01-02",
                "kind": "easy",
                "distance_min_m": 4000,
                "distance_max_m": 6000,
                "purpose": "Aerobic base",
            },
            {"id": "strides", "week_id": "w1", "date": "2026-01-02", "kind": "intervals"},
            {"id": "rest", "week_id": "w1", "date": "2026-01-03", "kind": "rest"},
        ],
    }


def create(training, snapshot=None, key="create"):
    return training.create_cycle(
        models.CreateCycle(
            snapshot=models.Snapshot.model_validate(snapshot or plan()),
            author="user",
            reason="Initial",
            idempotency_key=key,
        )
    )


def draft(training, created, snapshot, key="draft"):
    return training.create_draft(
        created["cycle_id"],
        models.DraftCreate(
            expected_revision_id=created["revision_id"],
            snapshot=models.Snapshot.model_validate(snapshot),
            author="user",
            reason="Move for travel",
            idempotency_key=key,
        ),
    )


def apply(training, created, proposed, **kw):
    return training.apply_draft(
        created["cycle_id"],
        proposed["revision_id"],
        models.DraftAction(
            expected_revision_id=created["revision_id"],
            expected_draft_version=proposed["version"],
            idempotency_key="apply-" + proposed["revision_id"],
            author="user",
            reason="Approved",
            **kw,
        ),
    )


def outcome(training, created, **kw):
    values = dict(
        expected_revision_id=created["revision_id"],
        expected_version=0,
        author="user",
        reason="After run",
        session_id="easy",
        session_revision_id=created["revision_id"],
        actual_date="2026-01-02",
        status="completed",
        metrics_source="manual",
        distance_m=5100,
        duration_s=1600,
    )
    values.update(kw)
    return training.save_outcome(created["cycle_id"], models.Outcome(**values))


def match(training, created, **kw):
    values = dict(
        expected_revision_id=created["revision_id"],
        expected_version=0,
        author="user",
        reason="Verified",
        id="link",
        session_id="easy",
        session_revision_id=created["revision_id"],
        activity_id=1,
        basis="Checked date, time and sport",
    )
    values.update(kw)
    return training.save_match(created["cycle_id"], models.Match(**values))


def refresh(local):
    local.refresh_activities("2025-12-29", "2026-01-11")


def test_persist_restart_http_mcp_and_read_only(local, training, monkeypatch):
    created = create(training)
    restarted = Service(local.settings, local.gateway)
    t = TrainingService(restarted)
    saved = t.get_cycle(created["cycle_id"])
    assert len(saved["snapshot"]["events"]) == 2
    assert saved["snapshot"]["cycle"]["timezone"] == "Europe/Berlin"
    assert saved["snapshot"]["events"][0]["timezone"] == "Europe/Istanbul"
    from mcp.server.fastmcp import FastMCP

    from garmin_mcp import server
    from garmin_mcp.training.api import add_mcp

    isolated_mcp = FastMCP("training-test", stateless_http=True, json_response=True)
    add_mcp(isolated_mcp, lambda: restarted)
    monkeypatch.setattr(server, "mcp", isolated_mcp)
    monkeypatch.setattr(server, "_local", restarted)
    with TestClient(create_app(restarted), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/training").status_code == 200
        assert client.get(f"/api/training/cycles/{created['cycle_id']}").json() == saved
        headers = {"Accept": "application/json, text/event-stream"}
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "training-tests", "version": "1"},
                },
            },
        )
        assert "result" in initialized.json()
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "training_get_cycle",
                    "arguments": {"cycle_id": created["cycle_id"]},
                },
            },
        )
        assert "structuredContent" in response.json()["result"], response.json()
        assert response.json()["result"]["structuredContent"] == saved
        feedback = {
            "id": "via-mcp",
            "date": "2026-01-02",
            "effort": 4,
            "author": "user",
            "reason": "MCP parity",
            "expected_version": 0,
            "expected_revision_id": created["revision_id"],
        }
        written = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "training_save_feedback",
                    "arguments": {"cycle_id": created["cycle_id"], "body": feedback},
                },
            },
        )
        assert written.json()["result"]["structuredContent"]["record"]["effort"] == 4
        context = client.get(f"/api/training/cycles/{created['cycle_id']}/context").json()
        assert context["revision_id"] == saved["revision"]["id"]
        assert context["feedback"][0]["effort"] == 4
        schema = client.get("/openapi.json").json()
        assert "Snapshot" in schema["components"]["schemas"]
        assert "/api/training/cycles/{cycle_id}/outcomes" in schema["paths"]
    assert local.gateway.calls == []


def test_events_moves_conflicts_idempotency_and_immutable_history(training):
    created = create(training)
    assert create(training) == created
    with pytest.raises(TrainingError, match="Idempotency"):
        create(
            training,
            {
                **plan(),
                "constraints": [
                    {
                        "id": "travel",
                        "name": "Travel",
                        "start_date": "2026-01-20",
                        "end_date": "2026-01-25",
                    }
                ],
            },
        )
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["sessions"][0]["date"] = "2026-01-04"
    snapshot["events"].append({"id": "extra", "name": "Tune up", "date": "2026-02-15"})
    proposed = draft(training, created, snapshot)
    concurrent = draft(training, created, snapshot, key="concurrent")
    compared = training.compare(
        created["cycle_id"], created["revision_id"], proposed["revision_id"]
    )
    movement = next(c for c in compared["changes"] if c["id"] == "easy")
    assert movement["before"]["date"] == "2026-01-02"
    assert movement["after"]["date"] == "2026-01-04"
    applied = apply(training, created, proposed)
    assert apply(training, created, proposed) == applied
    with pytest.raises(TrainingError, match="plan changed"):
        apply(training, created, concurrent)
    old = training.get_cycle(created["cycle_id"], created["revision_id"])
    assert old["snapshot"]["sessions"][0]["date"] == "2026-01-02"
    assert old["revision"]["status"] == "superseded"
    with pytest.raises(Exception, match="immutable"), training.store.transaction() as db:
        db.execute(
            "UPDATE session_versions SET purpose='overwrite' WHERE revision_id=?",
            (created["revision_id"],),
        )


def test_one_active_cycle_and_account_isolation(local, training):
    created = create(training)
    with pytest.raises(TrainingError, match="already active"):
        create(training, key="second")
    other = TrainingService(Service(replace(local.settings, account_id="other"), FakeGarmin()))
    with pytest.raises(TrainingError, match="not found"):
        other.get_cycle(created["cycle_id"])
    assert other.list_cycles()["cycles"] == []
    create(other)
    assert len(training.list_cycles()["cycles"]) == 1


def test_manual_actual_preserves_plan_and_deduplicates(local, training):
    created = create(training)
    outcome(training, created)
    first = training.context(created["cycle_id"])
    assert first["weeks"][0]["actual_totals"]["distance_m"]["known_sum"] == 5100
    assert first["weeks"][0]["actual_totals"]["distance_m"]["value"] is None
    assert first["sessions"][0]["distance_max_m"] == 6000
    refresh(local)
    match(training, created)
    linked = training.context(created["cycle_id"])
    assert linked["weeks"][0]["actual_totals"]["distance_m"]["value"] == 5000
    assert linked["sessions"][0]["actual"]["distance_m"] == 5100
    outcome(
        training,
        created,
        expected_version=1,
        metrics_source="garmin",
        duration_s=None,
        distance_m=None,
        comment="Keep my comment",
    )
    linked = training.context(created["cycle_id"])
    assert linked["sessions"][0]["actual"]["distance_m"] == 5000
    assert linked["sessions"][0]["outcome"]["comment"] == "Keep my comment"
    assert linked["weeks"][0]["actual_totals"]["distance_m"]["value"] == 5000


def test_multiple_activities_and_shared_allocations(local, training):
    created = create(training)
    local.gateway.activities = [activity(1, distance=4000), activity(2, distance=1000)]
    refresh(local)
    match(training, created)
    match(training, created, id="warmup", activity_id=2)
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["actual"]["distance_m"] == 5000
    match(
        training,
        created,
        id="shared",
        session_id="strides",
        allocated_distance_m=1000,
        allocated_duration_s=500,
    )
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["actual"]["distance_m"] is None
    assert report["weeks"][0]["actual_totals"]["distance_m"]["value"] == 5000
    with pytest.raises(TrainingError, match="Allocations exceed"):
        match(training, created, expected_version=1, allocated_distance_m=4000)
    match(
        training, created, expected_version=1, allocated_distance_m=3000, allocated_duration_s=1000
    )
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["actual"]["distance_m"] == 4000
    assert report["sessions"][1]["actual"]["distance_m"] == 1000
    assert report["weeks"][0]["actual_totals"]["duration_s"]["value"] == 3000


def test_outline_unknown_rest_and_missing_sources(training):
    created = create(training)
    outcome(
        training,
        created,
        session_id="rest",
        actual_date="2026-01-03",
        metrics_source="none",
        distance_m=None,
        duration_s=None,
    )
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["actual"]["status"] == "unknown"
    assert report["weeks"][1]["scheduled_totals"]["distance_min_m"]["value"] is None
    assert report["weeks"][0]["unique_activity_count"] == 0
    assert report["weeks"][0]["manual_fact_count"] == 0
    assert report["source_quality"]["missing_daily_sources"]["sleep"]
    days = {d["date"]: d for d in report["days"]}
    assert days["2026-01-03"]["state"] == "rest"
    assert days["2026-01-04"]["state"] == "not_planned"


def test_late_plan_correction_preserves_assessment_and_feedback(training):
    created = create(training)
    outcome(training, created)
    body = models.Feedback(
        id="note",
        expected_revision_id=created["revision_id"],
        expected_version=0,
        author="user",
        reason="After run",
        date="2026-01-02",
        session_id="easy",
        effort=6,
        wellbeing="Tired",
        comment="Original",
    )
    training.save_feedback(created["cycle_id"], body)
    training.save_feedback(
        created["cycle_id"],
        body.model_copy(
            update={
                "expected_version": 1,
                "effort": 7,
                "comment": "Corrected",
                "reason": "Correction",
            }
        ),
    )
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["sessions"][0]["distance_min_m"] = 8000
    snapshot["sessions"][0]["distance_max_m"] = 10000
    proposed = draft(training, created, snapshot)
    current = apply(training, created, proposed)
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["assessed_assignment"]["distance_min_m"] == 4000
    assert report["sessions"][0]["deviations"]["distance_m"]["below_min"] == 0
    with pytest.raises(TrainingError, match="original assignment"):
        outcome(training, current, expected_version=1)
    history = training.history(created["cycle_id"])["history"]
    corrected = [r for r in history if r["action"] == "training_training_feedback"][-1]
    assert corrected["detail"]["before"]["comment"] == "Original"
    assert corrected["detail"]["after"]["comment"] == "Corrected"


def test_travel_warning_dates_week_boundary_and_variants(training):
    created = create(training)
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["constraints"] = [
        {
            "id": "travel",
            "name": "Trip",
            "start_date": "2026-01-02",
            "end_date": "2026-01-04",
            "available_days": [],
            "limitations": "No running while travelling",
        }
    ]
    snapshot["sessions"][0]["timezone"] = "America/New_York"
    snapshot["sessions"][0]["variants"] = [
        {"id": "short", "name": "Short", "distance_min_m": 2000, "distance_max_m": 3000},
        {"id": "long", "name": "Long", "distance_min_m": 5000, "distance_max_m": 6000},
    ]
    snapshot["sessions"][0]["selected_variant_id"] = "short"
    proposed = draft(training, created, snapshot)
    with pytest.raises(TrainingError, match="Explain"):
        apply(training, created, proposed)
    apply(training, created, proposed, override_explanation="Hotel treadmill is available")
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["date"] == "2026-01-02"
    assert report["weeks"][0]["scheduled_totals"]["distance_min_m"]["known_sum"] == 2000
    snapshot["sessions"][0]["date"] = "2026-01-05"
    with pytest.raises(ValidationError, match="outside its week"):
        models.Snapshot.model_validate(snapshot)
    snapshot["sessions"][0]["week_id"] = "w2"
    models.Snapshot.model_validate(snapshot)


def test_source_correction_invalidates_matches_reviews_without_rewrite(local, training):
    created = create(training)
    refresh(local)
    match(training, created)
    training.save_review(
        created["cycle_id"],
        models.Review(
            id="review",
            start_date="2026-01-01",
            end_date="2026-01-04",
            conclusions="Good week",
            plan_revision_id=created["revision_id"],
            expected_revision_id=created["revision_id"],
            expected_version=0,
            author="agent",
            reason="Weekly review",
        ),
    )
    refresh(local)
    assert (
        training.context(created["cycle_id"])["sessions"][0]["matches"][0]["state"] == "confirmed"
    )
    local.gateway.activities = [activity(distance=6000)]
    refresh(local)
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["matches"][0]["state"] == "needs_review"
    assert report["sessions"][0]["actual"]["distance_m"] is None
    assert report["reviews"][0]["state"] == "needs_review"
    with pytest.raises(TrainingError, match="Record changed"):
        match(training, created, expected_version=1)
    match(training, created, expected_version=2)
    assert training.context(created["cycle_id"])["sessions"][0]["actual"]["distance_m"] == 6000


def test_backup_restore_training_entities_and_no_network(local, training):
    created = create(training)
    refresh(local)
    outcome(training, created)
    match(training, created)
    training.save_feedback(
        created["cycle_id"],
        models.Feedback(
            id="note",
            expected_revision_id=created["revision_id"],
            expected_version=0,
            date="2026-01-02",
            effort=5,
            comment="Persist",
            author="user",
            reason="After run",
        ),
    )
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["sessions"][0]["date"] = "2026-01-04"
    proposed = draft(training, created, snapshot)
    apply(training, created, proposed)
    before = training.context(created["cycle_id"])
    backup = Backups(local).create()
    calls = list(local.gateway.calls)
    training.lifecycle(
        created["cycle_id"],
        models.Lifecycle(
            expected_revision_id=proposed["revision_id"],
            idempotency_key="cancel",
            status="cancelled",
            author="user",
            reason="Changed mind",
        ),
    )
    Backups(local).restore(backup["backup_id"], confirm=True)
    assert training.context(created["cycle_id"]) == before
    assert local.gateway.calls == calls


def test_parallel_draft_edits_protect_against_lost_updates(training):
    created = create(training)
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    proposed = draft(training, created, snapshot)
    body = models.DraftEdit(
        expected_revision_id=created["revision_id"],
        expected_draft_version=1,
        snapshot=models.Snapshot.model_validate(snapshot),
        author="user",
        reason="Edit",
    )

    def save():
        try:
            return training.edit_draft(created["cycle_id"], proposed["revision_id"], body)["status"]
        except TrainingError as exc:
            return exc.result["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: save(), range(2))) == ["conflict", "ok"]


def test_http_error_codes_validation_and_cross_origin(local):
    with TestClient(create_app(local, mount_mcp=False)) as client:
        response = client.post("/api/training/cycles", json={})
        assert response.status_code == 422
        assert response.json()["status"] == "validation_error"
        assert client.get("/api/training/cycles/absent").status_code == 404
        body = {
            "snapshot": plan(),
            "author": "user",
            "reason": "Initial",
            "idempotency_key": "first",
        }
        assert client.post("/api/training/cycles", json=body).status_code == 200
        body["reason"] = "Another request"
        assert client.post("/api/training/cycles", json=body).status_code == 409
        assert (
            client.post(
                "/api/training/cycles", json=body, headers={"Origin": "https://elsewhere.test"}
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["cycle"].update(main_event_id="missing"),
        lambda p: p["events"][0].update(participation="cancelled"),
        lambda p: p["weeks"][0].update(start_date="2026-01-01"),
        lambda p: p["sessions"][0].update(distance_min_m=-1),
        lambda p: p["sessions"][0].update(distance_min_m=7000),
        lambda p: p["sessions"][0].update(timezone="bad/zone"),
        lambda p: p["sessions"][0].update(distance_min_m=float("nan")),
        lambda p: p["sessions"][0].update(week_id="missing"),
        lambda p: p["cycle"].update(target_time_s=None),
    ],
)
def test_invalid_plan_inputs(mutate):
    snapshot = copy.deepcopy(plan())
    mutate(snapshot)
    with pytest.raises(ValidationError):
        models.Snapshot.model_validate(snapshot)


def test_candidates_keep_temporal_context_without_confirmation(local, training):
    created = create(training)
    local.gateway.activities = [{**activity(), "activityType": {"typeKey": "running"}}]
    refresh(local)
    calls = list(local.gateway.calls)
    candidates = training.candidates(created["cycle_id"], "easy")
    assert candidates["candidates"][0]["start_time_utc"]
    assert "same sport" in candidates["candidates"][0]["basis"]
    assert training.context(created["cycle_id"])["sessions"][0]["matches"] == []
    assert local.gateway.calls == calls


def test_cancel_retains_history_and_allows_next_active_cycle(training):
    created = create(training)
    training.lifecycle(
        created["cycle_id"],
        models.Lifecycle(
            expected_revision_id=created["revision_id"],
            idempotency_key="cancel",
            status="cancelled",
            author="user",
            reason="Next race",
        ),
    )
    create(training, key="next")
    assert len(training.list_cycles()["cycles"]) == 2


def test_snapshot_sql_scalar_types_and_fk_integrity(training):
    create(training)
    with training.store.connection() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.execute("SELECT typeof(target_time_s) FROM cycle_versions").fetchone()[0] == "real"
        )
        assert (
            db.execute(
                "SELECT typeof(distance_min_m) FROM session_versions WHERE id='easy'"
            ).fetchone()[0]
            == "real"
        )


def test_late_rest_edit_does_not_erase_original_running_fact(training):
    created = create(training)
    outcome(training, created)
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["sessions"][0]["kind"] = "rest"
    for field in models.Targets.model_fields:
        snapshot["sessions"][0][field] = None
    proposed = draft(training, created, snapshot)
    apply(training, created, proposed)
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["actual"]["distance_m"] == 5100
    assert report["sessions"][0]["assessed_assignment"]["kind"] == "easy"
    assert report["weeks"][0]["actual_totals"]["distance_m"]["known_sum"] == 5100


def test_detail_corrections_invalidate_confirmed_matches(local, training):
    created = create(training)
    refresh(local)
    local.refresh_activity(1, ["splits"])
    match(training, created)
    original = local.gateway.raw_activity

    def corrected(activity_id, source):
        value = original(activity_id, source)
        if source == "splits":
            value["lapDTOs"][0]["distance"] = 4800
        return value

    local.gateway.raw_activity = corrected
    local.refresh_activity(1, ["splits"])
    report = training.context(created["cycle_id"])
    assert report["sessions"][0]["matches"][0]["state"] == "needs_review"
    assert training.history(created["cycle_id"])["history"][-1]["detail"]["author"] == "import"


def test_restore_earlier_plan_retains_later_sessions_as_cancelled(training):
    created = create(training)
    snapshot = training.get_cycle(created["cycle_id"])["snapshot"]
    snapshot["sessions"].append(
        {"id": "later", "week_id": "w2", "date": "2026-01-06", "kind": "easy"}
    )
    proposed = draft(training, created, snapshot)
    current = apply(training, created, proposed)
    restored = training.create_draft(
        created["cycle_id"],
        models.DraftCreate(
            expected_revision_id=current["revision_id"],
            base_revision_id=created["revision_id"],
            idempotency_key="restore",
            author="user",
            reason="Return to baseline",
        ),
    )
    apply(training, current, restored)
    saved = training.get_cycle(created["cycle_id"])["snapshot"]
    assert next(s for s in saved["sessions"] if s["id"] == "later")["state"] == "cancelled"
    assert next(s for s in saved["sessions"] if s["id"] == "easy")["date"] == "2026-01-02"


def test_migration_adds_training_without_changing_existing_activity_rows(local, tmp_path):
    from pathlib import Path

    from garmin_mcp.storage import Store

    refresh(local)
    # Construct an old-schema DB from the original migration, then run the normal migration path.
    settings = replace(local.settings, data_dir=tmp_path / "old")
    settings.data_dir.mkdir()
    import sqlite3

    from garmin_mcp.storage import digest, utcnow

    initial = Path(__file__).parents[1] / "src/garmin_mcp/migrations/001_initial.sql"
    with sqlite3.connect(settings.database) as db:
        db.executescript(initial.read_text())
        db.execute("CREATE TABLE migrations(name TEXT PRIMARY KEY,sha256 TEXT,applied_at TEXT)")
        db.execute(
            "INSERT INTO migrations VALUES(?,?,?)",
            (initial.name, digest(initial.read_bytes()), utcnow()),
        )
        db.execute(
            "INSERT INTO activities(account_id,activity_id,local_date,timezone,fetched_at,"
            "distance_m) VALUES('personal',42,'2026-01-02','Europe/Berlin',?,5100)",
            (utcnow(),),
        )
        before = db.execute("SELECT * FROM activities").fetchall()
    store = Store(settings)
    with store.connection() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM activities")] == before
        assert db.execute("SELECT count(*) FROM training_cycles").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_week_uses_cycle_timezone_not_host_or_import_date(local, training):
    snapshot = plan()
    snapshot["cycle"]["timezone"] = "America/New_York"
    created = create(training, snapshot)
    local.gateway.activities = [
        {**activity(day="2026-01-05"), "startTimeGMT": "2026-01-04T23:30:00"}
    ]
    refresh(local)
    report = training.context(created["cycle_id"])
    assert report["activities"][0]["local_date"] == "2026-01-05"
    assert report["activities"][0]["plan_date"] == "2026-01-04"
    assert report["weeks"][0]["actual_totals"]["distance_m"]["known_sum"] == 5000
    assert report["weeks"][1]["actual_totals"]["distance_m"]["known_sum"] == 0
    assert report["weeks"][0]["actual_totals"]["distance_m"]["value"] is None


def test_backup_rejects_broken_training_source_references(local, training):
    created = create(training)
    refresh(local)
    match(training, created)
    with training.store.transaction() as db:
        db.execute("UPDATE activity_matches SET source_capture_ids_json='[\"missing\"]'")
    with pytest.raises(ValueError, match="training source"):
        Backups(local).create()


def test_wal_sidecars_remain_available_between_requests(local, training):
    from pathlib import Path

    created = create(training)
    database = local.settings.database
    assert Path(str(database) + "-wal").is_file()
    assert Path(str(database) + "-shm").is_file()
    # Reads and writes can proceed; the anchor does not hold a transaction/snapshot.
    with local.store.connection() as db:
        assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    assert training.get_cycle(created["cycle_id"])["cycle"]["id"] == created["cycle_id"]
    local.store.close()
