import pytest

from garmin_mcp import server
from garmin_mcp.models import BlockSpec, StepSpec, WorkoutSpec


class FakeGateway:
    def __init__(self) -> None:
        self.created = []
        self.scheduled = []
        self.activity_queries = []

    def create(self, payload):
        self.created.append(payload)
        return {"workoutId": 42, "workoutName": payload["workoutName"]}

    def schedule(self, workout_id, date):
        self.scheduled.append((workout_id, date))
        return {"workoutScheduleId": 99, "date": date}

    def list_activities(self, start_date, end_date, activity_type, limit):
        self.activity_queries.append((start_date, end_date, activity_type, limit))
        return [{"activity_id": 7, "sport": "running"}]


def workout() -> WorkoutSpec:
    return WorkoutSpec(
        name="Easy",
        blocks=[
            BlockSpec(
                steps=[StepSpec(step_type="interval", duration_value=1800)]
            )
        ],
    )


def test_create_requires_confirmation(monkeypatch) -> None:
    fake = FakeGateway()
    monkeypatch.setattr(server, "_gateway", fake)
    result = server.create_workout(workout(), schedule_date="2026-08-01")
    assert result["status"] == "confirmation_required"
    assert fake.created == []
    assert fake.scheduled == []


def test_create_and_schedule(monkeypatch) -> None:
    fake = FakeGateway()
    monkeypatch.setattr(server, "_gateway", fake)
    result = server.create_workout(
        workout(),
        schedule_date="2026-08-01",
        confirm=True,
    )
    assert result["status"] == "created_and_scheduled"
    assert result["workout_id"] == 42
    assert fake.scheduled == [(42, "2026-08-01")]


def test_list_activities_normalizes_dates_and_limit(monkeypatch) -> None:
    fake = FakeGateway()
    monkeypatch.setattr(server, "_gateway", fake)

    result = server.list_activities(
        "2026-08-01",
        "2026-08-07",
        activity_type=" running ",
        limit=3,
    )

    assert result["count"] == 1
    assert result["activities"][0]["activity_id"] == 7
    assert fake.activity_queries == [("2026-08-01", "2026-08-07", "running", 3)]


def test_list_activities_rejects_reversed_range(monkeypatch) -> None:
    monkeypatch.setattr(server, "_gateway", FakeGateway())
    with pytest.raises(ValueError, match="end_date"):
        server.list_activities("2026-08-02", "2026-08-01")
