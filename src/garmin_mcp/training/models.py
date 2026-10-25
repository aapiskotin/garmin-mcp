from __future__ import annotations

from datetime import date as Date
from datetime import time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import WorkoutSpec

ID = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]
Nonnegative = Annotated[float, Field(ge=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Goal(Model):
    goal_type: Literal["finish", "target_time", "other"] = "finish"
    target_time_s: Annotated[float, Field(gt=0)] | None = None
    goal_description: str = ""

    @model_validator(mode="after")
    def valid_goal(self):
        if self.goal_type == "target_time" and self.target_time_s is None:
            raise ValueError("target_time requires target_time_s")
        return self


class Zoned(Model):
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise ValueError("Use an IANA timezone, for example Europe/Berlin") from exc
        return value


class Period(Model):
    start_date: Date
    end_date: Date

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        return self


class Targets(Model):
    distance_min_m: Nonnegative | None = None
    distance_max_m: Nonnegative | None = None
    duration_min_s: Nonnegative | None = None
    duration_max_s: Nonnegative | None = None
    pace_min_s_per_km: Nonnegative | None = None
    pace_max_s_per_km: Nonnegative | None = None
    heart_rate_min_bpm: Nonnegative | None = None
    heart_rate_max_bpm: Nonnegative | None = None
    elevation_min_m: Nonnegative | None = None
    elevation_max_m: Nonnegative | None = None

    @model_validator(mode="after")
    def ordered_targets(self):
        for field in type(self).model_fields:
            if "_min_" in field:
                low, high = getattr(self, field), getattr(self, field.replace("_min_", "_max_"))
                if low is not None and high is not None and low > high:
                    raise ValueError(f"{field} exceeds its upper bound")
        return self


class Cycle(Goal, Zoned, Period):
    name: str = Field(min_length=1, max_length=200)
    status: Literal["draft", "active", "completed", "cancelled"] = "active"
    main_event_id: ID | None = None
    principles: str = ""


class Event(Goal, Zoned):
    id: ID
    name: str = Field(min_length=1, max_length=200)
    date: Date | None = None
    distance_m: Nonnegative | None = None
    elevation_m: Nonnegative | None = None
    role: Literal["main", "secondary", "training"] = "secondary"
    participation: Literal["considering", "planned", "registered", "completed", "cancelled"] = (
        "planned"
    )
    notes: str = ""


class Constraint(Period):
    id: ID
    name: str = Field(min_length=1, max_length=200)
    available_days: list[Annotated[int, Field(ge=0, le=6)]] | None = None
    daily_budget_s: Nonnegative | None = None
    weekly_budget_s: Nonnegative | None = None
    preferences: str = ""
    limitations: str = ""


class Phase(Period):
    id: ID
    name: str = Field(min_length=1, max_length=200)
    objective: str = ""
    explanation: str = ""


class Week(Targets):
    id: ID
    start_date: Date
    phase_id: ID | None = None
    detail: Literal["outline", "scheduled"] = "outline"
    focus: str = ""

    @field_validator("start_date")
    @classmethod
    def monday(cls, value):
        if value.weekday() != 0:
            raise ValueError("A week starts on Monday")
        return value


class Structure(Model):
    schema_version: Literal[1] = 1
    workout: WorkoutSpec | None = None
    instructions: str = ""


class Variant(Targets):
    id: ID
    name: str = Field(min_length=1)
    structure: Structure | None = None


class Session(Targets, Zoned):
    id: ID
    week_id: ID
    date: Date
    start_time: time | None = None
    order_in_day: int = Field(default=0, ge=0)
    kind: Literal["easy", "long", "intervals", "tempo", "race", "strength", "other", "rest"]
    sport: str = Field(default="running", min_length=1, max_length=100)
    purpose: str = ""
    certainty: Literal["tentative", "fixed"] = "tentative"
    state: Literal["planned", "cancelled"] = "planned"
    structure: Structure | None = None
    variants: list[Variant] = Field(default_factory=list, max_length=20)
    selected_variant_id: ID | None = None
    replaces_session_id: ID | None = None

    @model_validator(mode="after")
    def valid_session(self):
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate variant ID")
        if self.variants and self.selected_variant_id not in ids:
            raise ValueError("Explicitly select a variant")
        if not self.variants and self.selected_variant_id is not None:
            raise ValueError("Unknown selected variant")
        if self.start_time and self.start_time.tzinfo:
            raise ValueError("start_time is local; use timezone separately")
        structures = [self.structure, *(v.structure for v in self.variants)]
        if any(s and s.workout and s.workout.sport != self.sport for s in structures):
            raise ValueError("Workout sport must agree with session sport")
        if self.kind == "rest" and (
            self.variants
            or self.structure
            or any(getattr(self, f) is not None for f in Targets.model_fields)
        ):
            raise ValueError("Rest has no workout or training targets")
        return self


class Snapshot(Model):
    cycle: Cycle
    events: list[Event] = Field(default_factory=list, max_length=100)
    constraints: list[Constraint] = Field(default_factory=list, max_length=200)
    phases: list[Phase] = Field(default_factory=list, max_length=100)
    weeks: list[Week] = Field(default_factory=list, max_length=520)
    sessions: list[Session] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def valid_references(self):
        for name in ("events", "constraints", "phases", "weeks", "sessions"):
            ids = [item.id for item in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate ID in {name}")
        cycle = self.cycle
        events = {e.id: e for e in self.events}
        mains = [e for e in self.events if e.role == "main"]
        if cycle.main_event_id is not None and (
            cycle.main_event_id not in events or events[cycle.main_event_id].role != "main"
        ):
            raise ValueError("main_event_id must reference a main event")
        if len(mains) > 1 or (mains and cycle.main_event_id != mains[0].id):
            raise ValueError("The main event must match main_event_id")
        if cycle.status == "active" and (
            len(mains) != 1 or mains[0].date is None or mains[0].participation == "cancelled"
        ):
            raise ValueError("An active cycle needs exactly one dated, non-cancelled main event")
        for event in self.events:
            if event.date and not cycle.start_date <= event.date <= cycle.end_date:
                raise ValueError("Event date falls outside cycle")
        for phase in self.phases:
            if not cycle.start_date <= phase.start_date <= phase.end_date <= cycle.end_date:
                raise ValueError("Phase falls outside cycle")
        phases = {p.id: p for p in self.phases}
        starts = [w.start_date for w in self.weeks]
        if len(starts) != len(set(starts)):
            raise ValueError("Duplicate calendar week")
        weeks = {w.id: w for w in self.weeks}
        for week in self.weeks:
            end = week.start_date + timedelta(days=6)
            if week.start_date > cycle.end_date or end < cycle.start_date:
                raise ValueError("Week does not overlap cycle")
            if week.phase_id:
                phase = phases.get(week.phase_id)
                if not phase or week.start_date > phase.end_date or end < phase.start_date:
                    raise ValueError("Week must overlap its phase")
        for session in self.sessions:
            week = weeks.get(session.week_id)
            if not week or not week.start_date <= session.date <= week.start_date + timedelta(
                days=6
            ):
                raise ValueError("Session falls outside its week or references an unknown week")
            if not cycle.start_date <= session.date <= cycle.end_date:
                raise ValueError("Session falls outside cycle")
            if session.replaces_session_id == session.id:
                raise ValueError("Session cannot replace itself")
        return self


class Command(Model):
    author: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class CreateCycle(Command):
    snapshot: Snapshot
    idempotency_key: ID
    override_explanation: str | None = None
    source_capture_ids: list[ID] = Field(default_factory=list)


class RevisionCommand(Command):
    expected_revision_id: ID


class DraftCreate(RevisionCommand):
    idempotency_key: ID
    snapshot: Snapshot | None = None
    base_revision_id: ID | None = None
    source_capture_ids: list[ID] = Field(default_factory=list)


class DraftEdit(RevisionCommand):
    expected_draft_version: int = Field(ge=1)
    snapshot: Snapshot
    source_capture_ids: list[ID] = Field(default_factory=list)


class DraftAction(RevisionCommand):
    expected_draft_version: int = Field(ge=1)
    idempotency_key: ID
    override_explanation: str | None = None


class Lifecycle(RevisionCommand):
    idempotency_key: ID
    status: Literal["completed", "cancelled"]


class RecordCommand(RevisionCommand):
    expected_version: int = Field(ge=0, description="0 creates; later writes use returned version")


class Outcome(RecordCommand):
    session_id: ID
    session_revision_id: ID
    status: Literal["unknown", "completed", "partial", "skipped"] = "unknown"
    actual_date: Date
    metrics_source: Literal["manual", "garmin", "none"] = "none"
    duration_s: Nonnegative | None = None
    distance_m: Nonnegative | None = None
    deviation_reason: str = ""
    comment: str = ""

    @model_validator(mode="after")
    def valid_metrics(self):
        if self.metrics_source != "manual" and (
            self.duration_s is not None or self.distance_m is not None
        ):
            raise ValueError("Only manual outcomes accept actual metrics")
        if self.status in {"skipped", "unknown"} and (
            self.duration_s is not None or self.distance_m is not None
        ):
            raise ValueError("Unknown/skipped outcomes cannot carry training volume")
        return self


class Match(RecordCommand):
    id: ID
    session_id: ID
    session_revision_id: ID
    activity_id: int = Field(gt=0)
    state: Literal["suggested", "confirmed", "rejected", "needs_review"] = "confirmed"
    basis: str = Field(min_length=1)
    allocated_duration_s: Nonnegative | None = None
    allocated_distance_m: Nonnegative | None = None


class Feedback(RecordCommand):
    id: ID
    date: Date
    session_id: ID | None = None
    effort: int | None = Field(default=None, ge=1, le=10)
    wellbeing: str = ""
    comment: str = ""


class Review(RecordCommand, Period):
    id: ID
    plan_revision_id: ID
    proposed_revision_id: ID | None = None
    source_capture_ids: list[ID] = Field(default_factory=list)
    conclusions: str = Field(min_length=1)
    limitations: str = ""
