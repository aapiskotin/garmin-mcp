from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Sport = Literal["running", "cycling", "walking", "hiking"]
StepType = Literal["warmup", "interval", "recovery", "cooldown", "rest", "other"]
DurationType = Literal["time", "distance", "lap_button"]
TargetType = Literal[
    "none",
    "heart_rate_zone",
    "heart_rate_bpm",
    "pace_seconds_per_km",
    "power_watts",
    "cadence",
]


class StepSpec(BaseModel):
    """One executable step inside a Garmin workout block."""

    step_type: StepType
    duration_type: DurationType = "time"
    duration_value: float | None = Field(
        default=None,
        gt=0,
        description="Seconds for time, metres for distance; omit for lap_button.",
    )
    target_type: TargetType = "none"
    target_low: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Lower bound. For pace use seconds/km (250 = 4:10/km); "
            "for HR use bpm; for power use watts; for cadence use rpm/spm."
        ),
    )
    target_high: float | None = Field(
        default=None,
        gt=0,
        description="Upper bound in the same unit as target_low.",
    )
    zone_number: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Garmin heart-rate zone 1-5; only for heart_rate_zone.",
    )
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_step(self) -> StepSpec:
        if self.duration_type == "lap_button":
            if self.duration_value is not None:
                raise ValueError("duration_value must be omitted for lap_button")
        elif self.duration_value is None:
            raise ValueError("duration_value is required for time and distance")

        if self.target_type == "none":
            target_values = (self.target_low, self.target_high, self.zone_number)
            if any(value is not None for value in target_values):
                raise ValueError("none target cannot include target values or zone_number")
        elif self.target_type == "heart_rate_zone":
            if self.zone_number is None:
                raise ValueError("zone_number is required for heart_rate_zone")
            if self.target_low is not None or self.target_high is not None:
                raise ValueError("heart_rate_zone uses zone_number, not target_low/target_high")
        else:
            if self.zone_number is not None:
                raise ValueError("zone_number is only valid for heart_rate_zone")
            if self.target_low is None or self.target_high is None:
                raise ValueError(f"target_low and target_high are required for {self.target_type}")
            if self.target_low > self.target_high:
                raise ValueError("target_low must not exceed target_high")
        return self


class BlockSpec(BaseModel):
    """A sequence of steps, optionally represented as a Garmin repeat group."""

    repeat: int = Field(default=1, ge=1, le=100)
    steps: list[StepSpec] = Field(min_length=1, max_length=100)


class WorkoutSpec(BaseModel):
    """Structured workout accepted by MCP write and preview tools."""

    name: str = Field(min_length=1, max_length=80)
    sport: Sport = "running"
    description: str | None = Field(default=None, max_length=1000)
    blocks: list[BlockSpec] = Field(min_length=1, max_length=100)
