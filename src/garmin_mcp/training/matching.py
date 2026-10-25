"""Explicit activity allocation and read-only candidate calculations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .service import fail
from .snapshots import targets


def activity_date(activity, timezone):
    if activity.get("start_time_utc"):
        stamp = datetime.fromisoformat(activity["start_time_utc"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp.astimezone(ZoneInfo(timezone)).date().isoformat()
    # Preserve source calendar context when no absolute timestamp is available.
    return activity["local_date"]


def validate_allocation(db, account, cycle_id, body, activity):
    if body.state not in {"confirmed", "needs_review"}:
        return
    others = db.execute(
        "SELECT * FROM activity_matches WHERE account_id=? AND activity_id=? "
        "AND state IN ('confirmed','needs_review') AND NOT(cycle_id=? AND id=?)",
        (account, body.activity_id, cycle_id, body.id),
    ).fetchall()
    for metric in ("duration_s", "distance_m"):
        allocation = getattr(body, "allocated_" + metric)
        known = [r["allocated_" + metric] for r in others if r["allocated_" + metric] is not None]
        if allocation is not None:
            known.append(allocation)
        if known and activity[metric] is None:
            fail("missing_inputs", f"Cannot allocate unknown activity {metric}")
        if activity[metric] is not None and sum(known) > activity[metric] + 0.000001:
            fail("validation_error", f"Allocations exceed activity {metric}; no double counting")


def candidates(db, account, session):
    if session["kind"] == "rest":
        return {"status": "ok", "candidates": []}
    rows = db.execute("SELECT * FROM activities WHERE account_id=?", (account,)).fetchall()
    result = []
    for row in rows:
        activity = dict(row)
        day = activity_date(activity, session["timezone"])
        delta = abs((date.fromisoformat(day) - date.fromisoformat(session["date"])).days)
        if delta > 1:
            continue
        basis = [f"{delta} day difference in {session['timezone']}"]
        score = 3 - delta
        if activity["sport"] == session["sport"]:
            score += 2
            basis.append("same sport")
        else:
            basis.append("sport differs or is unknown")
        target = targets(session)
        for metric, unit in (("distance", "m"), ("duration", "s")):
            value = activity[f"{metric}_{unit}"]
            low, high = target[f"{metric}_min_{unit}"], target[f"{metric}_max_{unit}"]
            if value is not None and (low is not None or high is not None):
                within = (low is None or value >= low) and (high is None or value <= high)
                basis.append(f"{metric} {'within' if within else 'outside'} target range")
                score += int(within)
        if not activity["start_time_utc"]:
            basis.append("absolute start time unavailable; date context needs review")
        result.append(
            {
                "activity_id": activity["activity_id"],
                "date": day,
                "start_time_utc": activity["start_time_utc"],
                "timezone": activity["timezone"],
                "sport": activity["sport"],
                "name": activity["name"],
                "distance_m": activity["distance_m"],
                "duration_s": activity["duration_s"],
                "basis": "; ".join(basis),
                "score": score,
            }
        )
    return {
        "status": "ok",
        "candidates": sorted(result, key=lambda r: -r["score"]),
        "message": "Candidates only; explicit confirmation is required. No links were changed.",
    }
