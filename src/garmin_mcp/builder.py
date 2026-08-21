from __future__ import annotations

from typing import Any

from .models import StepSpec, WorkoutSpec

SPORT_TYPES: dict[str, dict[str, Any]] = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "walking": {"sportTypeId": 17, "sportTypeKey": "walking", "displayOrder": 17},
    "hiking": {"sportTypeId": 18, "sportTypeKey": "hiking", "displayOrder": 18},
}

STEP_TYPES: dict[str, dict[str, Any]] = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
    "other": {"stepTypeId": 7, "stepTypeKey": "other", "displayOrder": 7},
}

END_CONDITIONS: dict[str, dict[str, Any]] = {
    "lap_button": {
        "conditionTypeId": 1,
        "conditionTypeKey": "lap.button",
        "displayOrder": 1,
        "displayable": True,
    },
    "time": {
        "conditionTypeId": 2,
        "conditionTypeKey": "time",
        "displayOrder": 2,
        "displayable": True,
    },
    "distance": {
        "conditionTypeId": 3,
        "conditionTypeKey": "distance",
        "displayOrder": 3,
        "displayable": True,
    },
}

NO_TARGET = {
    "workoutTargetTypeId": 1,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}

TARGET_TYPES: dict[str, dict[str, Any]] = {
    "power_watts": {
        "workoutTargetTypeId": 2,
        "workoutTargetTypeKey": "power.zone",
        "displayOrder": 2,
    },
    "cadence": {
        "workoutTargetTypeId": 3,
        "workoutTargetTypeKey": "cadence",
        "displayOrder": 3,
    },
    "heart_rate_zone": {
        "workoutTargetTypeId": 4,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    },
    "heart_rate_bpm": {
        "workoutTargetTypeId": 4,
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    },
    "pace_seconds_per_km": {
        "workoutTargetTypeId": 6,
        "workoutTargetTypeKey": "pace.zone",
        "displayOrder": 6,
    },
}


def _target_fields(step: StepSpec) -> dict[str, Any]:
    if step.target_type == "none":
        return {"targetType": NO_TARGET}
    if step.target_type == "heart_rate_zone":
        return {
            "targetType": TARGET_TYPES[step.target_type],
            "zoneNumber": step.zone_number,
        }

    low = float(step.target_low)
    high = float(step.target_high)
    if step.target_type == "pace_seconds_per_km":
        # Garmin Connect's unofficial JSON expects pace targets as speed in m/s.
        values = sorted((1000.0 / low, 1000.0 / high))
        low, high = values[0], values[1]

    return {
        "targetType": TARGET_TYPES[step.target_type],
        "targetValueOne": round(low, 6),
        "targetValueTwo": round(high, 6),
    }


def _executable_step(step: StepSpec, order: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": STEP_TYPES[step.step_type],
        "endCondition": END_CONDITIONS[step.duration_type],
        **_target_fields(step),
    }
    if step.duration_value is not None:
        payload["endConditionValue"] = float(step.duration_value)
    if step.notes:
        payload["description"] = step.notes
    return payload


def _repeat_group(steps: list[StepSpec], repeat: int, order: int) -> dict[str, Any]:
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": order,
        "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
        "numberOfIterations": repeat,
        "workoutSteps": [_executable_step(step, index) for index, step in enumerate(steps, 1)],
        "endCondition": {
            "conditionTypeId": 7,
            "conditionTypeKey": "iterations",
            "displayOrder": 7,
            "displayable": False,
        },
        "endConditionValue": float(repeat),
        "smartRepeat": False,
    }


def estimated_duration_seconds(spec: WorkoutSpec) -> int:
    total = 0.0
    for block in spec.blocks:
        for step in block.steps:
            if step.duration_type == "time" and step.duration_value is not None:
                total += step.duration_value * block.repeat
    return round(total)


def build_workout_payload(spec: WorkoutSpec) -> dict[str, Any]:
    sport = SPORT_TYPES[spec.sport]
    workout_steps: list[dict[str, Any]] = []
    order = 1

    for block in spec.blocks:
        if block.repeat == 1:
            for step in block.steps:
                workout_steps.append(_executable_step(step, order))
                order += 1
        else:
            workout_steps.append(_repeat_group(block.steps, block.repeat, order))
            order += 1

    payload: dict[str, Any] = {
        "workoutName": spec.name,
        "sportType": sport,
        "estimatedDurationInSecs": estimated_duration_seconds(spec),
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": sport,
                "workoutSteps": workout_steps,
            }
        ],
    }
    if spec.description:
        payload["description"] = spec.description
    return payload


def workout_summary(spec: WorkoutSpec) -> dict[str, Any]:
    executable_steps = sum(len(block.steps) * block.repeat for block in spec.blocks)
    return {
        "name": spec.name,
        "sport": spec.sport,
        "estimated_duration_seconds": estimated_duration_seconds(spec),
        "executable_steps": executable_steps,
        "repeat_blocks": sum(block.repeat > 1 for block in spec.blocks),
    }
