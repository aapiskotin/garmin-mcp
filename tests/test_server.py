from garmin_mcp import server
from garmin_mcp.models import BlockSpec, StepSpec, WorkoutSpec


class FakeGateway:
    def __init__(self) -> None:
        self.created = []
        self.scheduled = []

    def create(self, payload):
        self.created.append(payload)
        return {"workoutId": 42, "workoutName": payload["workoutName"]}

    def schedule(self, workout_id, date):
        self.scheduled.append((workout_id, date))
        return {"workoutScheduleId": 99, "date": date}


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
