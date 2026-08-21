import math

import pytest

from garmin_mcp.builder import build_workout_payload, estimated_duration_seconds
from garmin_mcp.models import BlockSpec, StepSpec, WorkoutSpec


def sample_workout() -> WorkoutSpec:
    return WorkoutSpec(
        name="6 x 800",
        sport="running",
        blocks=[
            BlockSpec(
                steps=[
                    StepSpec(
                        step_type="warmup",
                        duration_type="time",
                        duration_value=900,
                    )
                ]
            ),
            BlockSpec(
                repeat=6,
                steps=[
                    StepSpec(
                        step_type="interval",
                        duration_type="distance",
                        duration_value=800,
                        target_type="pace_seconds_per_km",
                        target_low=250,
                        target_high=260,
                    ),
                    StepSpec(
                        step_type="recovery",
                        duration_type="time",
                        duration_value=120,
                    ),
                ],
            ),
            BlockSpec(
                steps=[
                    StepSpec(
                        step_type="cooldown",
                        duration_type="time",
                        duration_value=600,
                    )
                ]
            ),
        ],
    )


def test_builds_repeat_and_converts_pace_to_speed() -> None:
    payload = build_workout_payload(sample_workout())
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert [step["type"] for step in steps] == [
        "ExecutableStepDTO",
        "RepeatGroupDTO",
        "ExecutableStepDTO",
    ]
    interval = steps[1]["workoutSteps"][0]
    assert interval["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert math.isclose(interval["targetValueOne"], 1000 / 260, rel_tol=1e-6)
    assert math.isclose(interval["targetValueTwo"], 4.0, rel_tol=1e-6)


def test_estimate_counts_only_timed_steps_and_repeats() -> None:
    assert estimated_duration_seconds(sample_workout()) == 900 + 6 * 120 + 600


def test_hr_zone_payload() -> None:
    spec = WorkoutSpec(
        name="Z2",
        blocks=[
            BlockSpec(
                steps=[
                    StepSpec(
                        step_type="interval",
                        duration_value=1800,
                        target_type="heart_rate_zone",
                        zone_number=2,
                    )
                ]
            )
        ],
    )
    step = build_workout_payload(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeId"] == 4
    assert step["zoneNumber"] == 2
    assert "targetValueOne" not in step


def test_rejects_invalid_target() -> None:
    with pytest.raises(ValueError, match="target_low and target_high"):
        StepSpec(
            step_type="interval",
            duration_value=60,
            target_type="power_watts",
        )
