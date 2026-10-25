"""Typed snapshot persistence; all writes are inside the planning service transaction."""

from __future__ import annotations

import json

from ..storage import encoded
from .models import Snapshot

TABLES = {
    "cycle": "cycle_versions",
    "events": "event_versions",
    "constraints": "constraint_versions",
    "phases": "phase_versions",
    "weeks": "week_versions",
    "sessions": "session_versions",
}
JSON_FIELDS = {
    "available_days",
    "structure",
    "variants",
    "source_capture_ids",
}


def sql_values(values):
    return {
        key + "_json" if key in JSON_FIELDS else key: encoded(value).decode()
        if key in JSON_FIELDS
        else value
        for key, value in values.items()
    }


def decoded(row):
    return {
        k[:-5] if k.endswith("_json") else k: json.loads(v)
        if k.endswith("_json") and v is not None
        else v
        for k, v in dict(row).items()
    }


def insert(db, table, values):
    db.execute(
        f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def save(db, account, cycle_id, revision_id, snapshot: Snapshot):
    keys = {"account_id": account, "cycle_id": cycle_id, "revision_id": revision_id}
    for name, table in TABLES.items():
        items = snapshot.model_dump(mode="json")[name]
        for item in [items] if name == "cycle" else items:
            insert(db, table, {**keys, **sql_values(item)})


def clear(db, account, cycle_id, revision_id):
    for table in reversed(TABLES.values()):
        db.execute(
            f"DELETE FROM {table} WHERE account_id=? AND cycle_id=? AND revision_id=?",
            (account, cycle_id, revision_id),
        )


def read(db, account, cycle_id, revision_id):
    result = {}
    for name, table in TABLES.items():
        ordering = {
            "cycle": "",
            "events": " ORDER BY role!='main',date IS NULL,date,id",
            "constraints": " ORDER BY start_date,id",
            "phases": " ORDER BY start_date,id",
            "weeks": " ORDER BY start_date,id",
            "sessions": " ORDER BY date,order_in_day,id",
        }[name]
        rows = db.execute(
            f"SELECT * FROM {table} WHERE account_id=? AND cycle_id=? AND revision_id=?" + ordering,
            (account, cycle_id, revision_id),
        ).fetchall()
        items = [
            {
                k: v
                for k, v in decoded(r).items()
                if k not in {"account_id", "cycle_id", "revision_id"}
            }
            for r in rows
        ]
        result[name] = items[0] if name == "cycle" else items
    return Snapshot.model_validate(result).model_dump(mode="json")


def diff(before, after):
    changes = []
    for group in TABLES:
        old = {"cycle": before[group]} if group == "cycle" else {x["id"]: x for x in before[group]}
        new = {"cycle": after[group]} if group == "cycle" else {x["id"]: x for x in after[group]}
        for id_ in sorted(old.keys() | new.keys()):
            if old.get(id_) != new.get(id_):
                changes.append(
                    {"entity": group, "id": id_, "before": old.get(id_), "after": new.get(id_)}
                )
    return changes


def targets(session):
    if session["selected_variant_id"]:
        return next(v for v in session["variants"] if v["id"] == session["selected_variant_id"])
    return session


def sport_group(sport):
    """Group Garmin subtypes without changing the saved source sport."""
    if sport in {"cycling", "road_biking", "mountain_biking", "indoor_cycling", "gravel_cycling"}:
        return "cycling"
    if sport in {"running", "trail_running", "treadmill_running", "track_running"}:
        return "running"
    return sport or "unknown"


def in_sport(item, sport):
    return sport is None or sport_group(item.get("sport")) == sport_group(sport)


def warnings(snapshot):
    result = []
    from datetime import date, timedelta

    for session in snapshot["sessions"]:
        if session["state"] == "cancelled" or session["kind"] == "rest":
            continue
        day = date.fromisoformat(session["date"])
        for constraint in snapshot["constraints"]:
            if not in_sport(session, constraint.get("sport")):
                continue
            if constraint["start_date"] <= session["date"] <= constraint["end_date"]:
                reasons = []
                if (
                    constraint["available_days"] is not None
                    and day.weekday() not in constraint["available_days"]
                ):
                    reasons.append("unavailable day")
                if constraint["limitations"]:
                    reasons.append(constraint["limitations"])
                budget = constraint["daily_budget_s"]
                daily = [
                    s
                    for s in snapshot["sessions"]
                    if s["date"] == session["date"]
                    and s["state"] != "cancelled"
                    and s["kind"] != "rest"
                    and in_sport(s, constraint.get("sport"))
                ]
                durations = [targets(s)["duration_max_s"] for s in daily]
                if budget is not None:
                    if any(v is None for v in durations):
                        reasons.append("daily duration budget cannot be checked")
                    elif sum(durations) > budget:
                        reasons.append("daily duration budget exceeded")
                if reasons:
                    result.append(
                        {
                            "session_id": session["id"],
                            "constraint_id": constraint["id"],
                            "message": "; ".join(reasons),
                        }
                    )
    for week in snapshot["weeks"]:
        sessions = [
            s
            for s in snapshot["sessions"]
            if s["week_id"] == week["id"] and s["state"] != "cancelled" and s["kind"] != "rest"
        ]
        if week["detail"] == "scheduled":
            scoped = [s for s in sessions if in_sport(s, week.get("sport"))]
            for metric in ("distance", "duration"):
                unit = "m" if metric == "distance" else "s"
                low, high = f"{metric}_min_{unit}", f"{metric}_max_{unit}"
                lo, hi = [targets(s)[low] for s in scoped], [targets(s)[high] for s in scoped]
                if (
                    week[low] is not None and all(v is not None for v in hi) and sum(hi) < week[low]
                ) or (
                    week[high] is not None
                    and all(v is not None for v in lo)
                    and sum(lo) > week[high]
                ):
                    result.append(
                        {"week_id": week["id"], "message": f"{metric} outside weekly target"}
                    )
        end = (date.fromisoformat(week["start_date"]) + timedelta(days=6)).isoformat()
        for constraint in snapshot["constraints"]:
            if (
                constraint["weekly_budget_s"] is not None
                and constraint["start_date"] <= end
                and constraint["end_date"] >= week["start_date"]
            ):
                durations = [
                    targets(s)["duration_max_s"]
                    for s in sessions
                    if constraint["start_date"] <= s["date"] <= constraint["end_date"]
                    and in_sport(s, constraint.get("sport"))
                ]
                if (
                    any(v is None for v in durations)
                    or sum(durations) > constraint["weekly_budget_s"]
                ):
                    result.append(
                        {
                            "week_id": week["id"],
                            "constraint_id": constraint["id"],
                            "message": "weekly duration budget exceeded or unknown",
                        }
                    )
    return result


def restore_plan(base, current):
    """Restore old assignments while retaining later additions as explicit cancellations."""
    import copy
    import uuid

    result = copy.deepcopy(base)
    ids = {s["id"] for s in result["sessions"]}
    current_weeks = {w["id"]: w for w in current["weeks"]}
    for session in current["sessions"]:
        if session["id"] in ids:
            continue
        cancelled = {**session, "state": "cancelled"}
        old_week = current_weeks[session["week_id"]]
        week = next((w for w in result["weeks"] if w["start_date"] == old_week["start_date"]), None)
        if not week:
            week = {**old_week, "id": uuid.uuid4().hex, "phase_id": None, "detail": "outline"}
            result["weeks"].append(week)
        cancelled["week_id"] = week["id"]
        result["sessions"].append(cancelled)
        # Local dates remain explicit and unchanged; the diff exposes this envelope adjustment.
        result["cycle"]["start_date"] = min(result["cycle"]["start_date"], cancelled["date"])
        result["cycle"]["end_date"] = max(result["cycle"]["end_date"], cancelled["date"])
    return result


def validate_backup(db):
    """Check snapshot invariants and JSON source references as well as SQL foreign keys."""
    import sqlite3

    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='training_cycles'"
    ).fetchone():
        return  # A pre-planner backup is migrated after restoration.
    previous_factory = db.row_factory
    db.row_factory = sqlite3.Row
    try:
        for row in db.execute("SELECT account_id,cycle_id,id FROM plan_revisions").fetchall():
            read(db, row["account_id"], row["cycle_id"], row["id"])
        invalid_current = db.execute(
            "SELECT 1 FROM training_cycles t LEFT JOIN plan_revisions r "
            "ON r.account_id=t.account_id AND r.cycle_id=t.id AND r.id=t.current_revision_id "
            "LEFT JOIN cycle_versions c ON c.account_id=t.account_id AND c.cycle_id=t.id "
            "AND c.revision_id=t.current_revision_id "
            "WHERE r.status IS NOT 'accepted' OR c.status IS NOT t.status"
        ).fetchone()
        if invalid_current:
            raise ValueError("Backup has an inconsistent current training revision")
        for table in ("plan_revisions", "activity_matches", "training_reviews"):
            for row in db.execute(f"SELECT account_id,source_capture_ids_json FROM {table}"):
                for capture in json.loads(row["source_capture_ids_json"]):
                    if not db.execute(
                        "SELECT 1 FROM captures WHERE account_id=? AND capture_id=?",
                        (row["account_id"], capture),
                    ).fetchone():
                        raise ValueError("Broken training source reference")
        missing_feedback_session = db.execute(
            "SELECT 1 FROM training_feedback f WHERE f.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM session_versions s WHERE s.account_id=f.account_id "
            "AND s.cycle_id=f.cycle_id AND s.id=f.session_id)"
        ).fetchone()
        if missing_feedback_session:
            raise ValueError("Broken training feedback session reference")
    finally:
        db.row_factory = previous_factory
