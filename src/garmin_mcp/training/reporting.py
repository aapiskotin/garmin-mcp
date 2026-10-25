"""Read a consistent local snapshot with explicit coverage and non-duplicating totals."""

from __future__ import annotations

from datetime import date, timedelta

from . import snapshots
from .matching import activity_date
from .service import fail


def rows(db, table, account, cycle_id):
    return [
        snapshots.decoded(r)
        for r in db.execute(
            f"SELECT * FROM {table} WHERE account_id=? AND cycle_id=?", (account, cycle_id)
        )
    ]


def total(values, known_empty=True):
    return {
        "value": sum(values)
        if all(v is not None for v in values) and (values or known_empty)
        else None,
        "known_sum": sum(v for v in values if v is not None),
        "missing_count": sum(v is None for v in values),
    }


def volume_totals(planned, activities, manual, detail, coverage_complete, sport=None):
    planned = [s for s in planned if snapshots.in_sport(s, sport)]
    activities = [a for a in activities if snapshots.in_sport(a, sport)]
    manual = [o for o in manual if snapshots.in_sport(o, sport)]
    scheduled = {
        field: total(
            [snapshots.targets(s)[field] for s in planned], known_empty=detail == "scheduled"
        )
        for field in ("distance_min_m", "distance_max_m", "duration_min_s", "duration_max_s")
    }
    actual = {
        metric: total([a[metric] for a in activities] + [o[metric] for o in manual])
        for metric in ("distance_m", "duration_s")
    }
    for metric in actual.values():
        if not coverage_complete:
            metric["value"] = None
        metric["complete"] = coverage_complete and metric["value"] is not None
    return {"scheduled_totals": scheduled, "actual_totals": actual}


def session_actual(session, outcome, links, all_links, activities):
    result = {
        "status": outcome["status"] if outcome else "unknown",
        "assessment_revision_id": outcome["session_revision_id"]
        if outcome
        else next(
            (
                m["session_revision_id"]
                for m in links
                if m["state"] in {"confirmed", "needs_review"}
            ),
            None,
        ),
        "source": outcome["metrics_source"] if outcome else "garmin",
        "distance_m": None,
        "duration_s": None,
        "limitations": [],
    }
    if session["kind"] == "rest":
        result["source"] = "rest_compliance"
        return result
    if outcome and outcome["metrics_source"] == "manual":
        for metric in ("duration_s", "distance_m"):
            result[metric] = outcome[metric]
        if links:
            result["limitations"].append(
                "Linked manual metrics are not added to weekly Garmin volume"
            )
        return result
    if outcome and outcome["metrics_source"] == "none":
        return result
    confirmed = [m for m in links if m["state"] == "confirmed"]
    if any(m["state"] == "needs_review" for m in links):
        result["limitations"].append("Source changed; matching needs review")
    if not confirmed:
        result["limitations"].append("No confirmed activity metrics")
        return result
    for metric in ("duration_s", "distance_m"):
        values = []
        for match in confirmed:
            activity = activities.get(match["activity_id"])
            peers = [
                m
                for m in all_links
                if m["activity_id"] == match["activity_id"]
                and m["state"] in {"confirmed", "needs_review"}
            ]
            allocated = match["allocated_" + metric]
            if len(peers) > 1 and any(m["allocated_" + metric] is None for m in peers):
                values.append(None)
                result["limitations"].append(f"Shared activity needs explicit {metric} allocations")
            elif not activity or any(m["state"] == "needs_review" for m in peers):
                values.append(None)
            else:
                values.append(allocated if allocated is not None else activity[metric])
        result[metric] = total(values)["value"]
    result["limitations"] = sorted(set(result["limitations"]))
    return result


def quality(service, db, start, end, timezone):
    account = service.account
    coverage = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM coverage WHERE account_id=? AND source='activities'", (account,)
        )
    ]
    # Coverage uses the import calendar timezone. A different plan zone requires edge coverage.
    extra = int(timezone != service.local.settings.timezone)
    required_start = date.fromisoformat(start) - timedelta(days=extra)
    required_end = date.fromisoformat(end) + timedelta(days=extra)
    missing = []
    cursor = required_start
    while cursor <= required_end:
        if not any(c["start_date"] <= cursor.isoformat() <= c["end_date"] for c in coverage):
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    sync = [dict(r) for r in db.execute("SELECT * FROM sync_state WHERE account_id=?", (account,))]
    for state in sync:
        state["stale"] = state["status"] != "ok" or service.local.stale(state["cursor"])
    sources = [
        dict(r)
        for r in db.execute(
            "SELECT entity,entity_id,source,status,fetched_at,capture_id FROM sources "
            "WHERE account_id=? AND entity='days' AND entity_id BETWEEN ? AND ?",
            (account, start, end),
        )
    ]
    for source in sources:
        source["stale"] = source["status"] != "ok" or service.local.stale(source["fetched_at"])
    daily_names = ("sleep", "hrv", "body_battery", "training_readiness", "training_status")
    missing_daily = {}
    for name in daily_names:
        available = {r["entity_id"] for r in sources if r["source"] == name and r["status"] == "ok"}
        days = []
        cursor = date.fromisoformat(start)
        while cursor <= date.fromisoformat(end):
            if cursor.isoformat() not in available:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        if days:
            missing_daily[name] = days
    return {
        "activity_coverage": coverage,
        "missing_activity_dates": missing,
        "activity_coverage_complete": not missing,
        "sync": sync,
        "daily_sources": sources,
        "missing_daily_sources": missing_daily,
        "message": "Cached sources only. Missing or stale data is not normal or zero.",
    }


def context(service, db, cycle, snapshot, start_date, end_date):
    start = str(date.fromisoformat(start_date)) if start_date else snapshot["cycle"]["start_date"]
    end = str(date.fromisoformat(end_date)) if end_date else snapshot["cycle"]["end_date"]
    if end < start or (date.fromisoformat(end) - date.fromisoformat(start)).days > 3660:
        fail("validation_error", "Use an ordered range of at most 3661 days")
    account, cycle_id = service.account, cycle["id"]
    outcomes = {r["session_id"]: r for r in rows(db, "session_outcomes", account, cycle_id)}
    # Manual facts retain the sport of their assessed assignment after plan edits.
    for row in db.execute(
        "SELECT o.session_id,s.sport FROM session_outcomes o JOIN session_versions s "
        "ON s.account_id=o.account_id AND s.cycle_id=o.cycle_id "
        "AND s.revision_id=o.session_revision_id AND s.id=o.session_id "
        "WHERE o.account_id=? AND o.cycle_id=?",
        (account, cycle_id),
    ):
        outcomes[row["session_id"]]["sport"] = row["sport"]
    matches = rows(db, "activity_matches", account, cycle_id)
    all_matches = [
        snapshots.decoded(r)
        for r in db.execute("SELECT * FROM activity_matches WHERE account_id=?", (account,))
    ]
    feedback = rows(db, "training_feedback", account, cycle_id)
    reviews = rows(db, "training_reviews", account, cycle_id)
    all_activities = {
        r["activity_id"]: dict(r)
        for r in db.execute(
            "SELECT activity_id,local_date,start_time_utc,start_time_local,timezone,name,sport,"
            "duration_s,distance_m,fetched_at FROM activities WHERE account_id=?",
            (account,),
        )
    }
    zone = snapshot["cycle"]["timezone"]
    for activity in all_activities.values():
        activity["plan_date"] = activity_date(activity, zone)
        activity["stale"] = service.local.stale(activity["fetched_at"])
    activity_sources = db.execute(
        "SELECT entity_id,source,status,fetched_at,capture_id FROM sources "
        "WHERE account_id=? AND entity='activities'",
        (account,),
    ).fetchall()
    for activity in all_activities.values():
        activity["sources"] = []
    for row in activity_sources:
        if row["entity_id"].isdigit() and int(row["entity_id"]) in all_activities:
            source = dict(row)
            source["stale"] = source["status"] != "ok" or service.local.stale(source["fetched_at"])
            all_activities[int(row["entity_id"])]["sources"].append(source)
    selected_sessions = [s for s in snapshot["sessions"] if start <= s["date"] <= end]
    enriched = []
    for session in selected_sessions:
        links = [m for m in matches if m["session_id"] == session["id"]]
        outcome = outcomes.get(session["id"])
        assessment = None
        assessed_revision = (
            outcome["session_revision_id"]
            if outcome
            else next(
                (
                    m["session_revision_id"]
                    for m in links
                    if m["state"] in {"confirmed", "needs_review"}
                ),
                None,
            )
        )
        if assessed_revision:
            row = db.execute(
                "SELECT * FROM session_versions WHERE account_id=? AND cycle_id=? "
                "AND revision_id=? AND id=?",
                (account, cycle_id, assessed_revision, session["id"]),
            ).fetchone()
            assessment = snapshots.decoded(row) if row else None
        actual = session_actual(assessment or session, outcome, links, all_matches, all_activities)
        target = snapshots.targets(assessment or session)
        deviations = {}
        for metric, unit in (("distance", "m"), ("duration", "s")):
            value = actual[f"{metric}_{unit}"]
            low, high = target[f"{metric}_min_{unit}"], target[f"{metric}_max_{unit}"]
            deviations[f"{metric}_{unit}"] = (
                None
                if value is None
                else {
                    "below_min": max(0, low - value) if low is not None else None,
                    "above_max": max(0, value - high) if high is not None else None,
                }
            )
        enriched.append(
            {
                **session,
                "actual": actual,
                "outcome": outcome,
                "matches": links,
                "assessed_assignment": assessment,
                "deviations": deviations,
                "feedback": [f for f in feedback if f["session_id"] == session["id"]],
            }
        )
    activity_rows = [a for a in all_activities.values() if start <= a["plan_date"] <= end]
    confirmed_ids = {m["activity_id"] for m in all_matches if m["state"] == "confirmed"}
    linked_sessions = {
        m["session_id"] for m in matches if m["state"] in {"confirmed", "needs_review"}
    }
    rest_ids = {
        row[0]
        for row in db.execute(
            "SELECT o.session_id FROM session_outcomes o JOIN session_versions s "
            "ON s.account_id=o.account_id AND s.cycle_id=o.cycle_id "
            "AND s.revision_id=o.session_revision_id AND s.id=o.session_id "
            "WHERE o.account_id=? AND o.cycle_id=? AND s.kind='rest'",
            (account, cycle_id),
        )
    }
    manual = [
        o
        for o in outcomes.values()
        if o["metrics_source"] == "manual"
        and o["status"] in {"completed", "partial"}
        and o["session_id"] not in linked_sessions
        and o["session_id"] not in rest_ids
    ]
    weeks = []
    for week in snapshot["weeks"]:
        week_end = (date.fromisoformat(week["start_date"]) + timedelta(days=6)).isoformat()
        if week["start_date"] > end or week_end < start:
            continue
        planned = [
            s
            for s in snapshot["sessions"]
            if s["week_id"] == week["id"] and s["state"] != "cancelled" and s["kind"] != "rest"
        ]
        actual_activities = [
            a for a in all_activities.values() if week["start_date"] <= a["plan_date"] <= week_end
        ]
        manual_week = [o for o in manual if week["start_date"] <= o["actual_date"] <= week_end]
        coverage = quality(service, db, week["start_date"], week_end, zone)

        sports = {
            snapshots.sport_group(x.get("sport"))
            for x in [*planned, *actual_activities, *manual_week]
        }
        if week.get("sport"):
            sports.add(snapshots.sport_group(week["sport"]))
        totals = {
            sport: volume_totals(
                planned,
                actual_activities,
                manual_week,
                week["detail"],
                coverage["activity_coverage_complete"],
                sport,
            )
            for sport in sports | {None}
        }
        weeks.append(
            {
                **week,
                **totals[snapshots.sport_group(week["sport"]) if week.get("sport") else None],
                "by_sport": {sport: totals[sport] for sport in sorted(sports)},
                "all_sports_totals": totals[None],
                "unique_activity_count": len(actual_activities),
                "manual_fact_count": len(manual_week),
                "coverage": coverage,
            }
        )
    days = []
    cursor = date.fromisoformat(start)
    while cursor <= date.fromisoformat(end):
        day = cursor.isoformat()
        sessions = [s for s in enriched if s["date"] == day]
        active = [s for s in sessions if s["state"] != "cancelled"]
        state = (
            "rest"
            if active and all(s["kind"] == "rest" for s in active)
            else ("planned" if active else "not_planned")
        )
        days.append({"date": day, "state": state, "session_ids": [s["id"] for s in sessions]})
        cursor += timedelta(days=1)
    revision = service.revision_row(db, cycle_id, cycle["current_revision_id"])
    daily_data = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM days WHERE account_id=? AND local_date BETWEEN ? AND ?",
            (account, start, end),
        )
    ]
    for day in daily_data:
        day["features"] = [
            snapshots.decoded(r)
            for r in db.execute(
                "SELECT name,status,computed_at,source_refs_json FROM feature_results "
                "WHERE account_id=? AND entity='days' AND entity_id=?",
                (account, day["local_date"]),
            )
        ]
        for feature in day["features"]:
            if feature["status"] != "ok":
                day["feature_" + feature["name"]] = None
    source_quality = quality(service, db, start, end, zone)
    for day in daily_data:
        day["sources"] = [
            s for s in source_quality["daily_sources"] if s["entity_id"] == day["local_date"]
        ]
        day["stale"] = not day["sources"] or any(s["stale"] for s in day["sources"])
    return {
        "status": "ok" if source_quality["activity_coverage_complete"] else "partial",
        "cycle_id": cycle_id,
        "revision_id": cycle["current_revision_id"],
        "cycle": snapshot["cycle"],
        "events": snapshot["events"],
        "phases": snapshot["phases"],
        "constraints": snapshot["constraints"],
        "start_date": start,
        "end_date": end,
        "days": days,
        "weeks": weeks,
        "sessions": enriched,
        "activities": activity_rows,
        "unplanned_activities": [a for a in activity_rows if a["activity_id"] not in confirmed_ids],
        "manual_facts": [o for o in manual if start <= o["actual_date"] <= end],
        "feedback": [f for f in feedback if start <= f["date"] <= end],
        "reviews": [r for r in reviews if r["start_date"] <= end and r["end_date"] >= start],
        "decision": revision,
        "source_quality": source_quality,
        "daily_data": daily_data,
        "warnings": snapshots.warnings(snapshot),
    }
