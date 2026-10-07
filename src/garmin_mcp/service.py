from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future, TimeoutError
from datetime import UTC, date, datetime, timedelta
from datetime import time as Time
from zoneinfo import ZoneInfo

from . import normalizers
from .config import Settings
from .gateway import GarminGateway
from .storage import Store, encoded, utcnow

DAILY = {
    name: getattr(normalizers, f"normalize_{name}")
    for name in ("sleep", "hrv", "body_battery", "training_readiness", "training_status")
}


class Service:
    """One instance per process, shared by HTTP and MCP. No eager Garmin work."""

    def __init__(self, settings: Settings, gateway: GarminGateway | None = None):
        self.settings = settings
        self.store = Store(settings)
        self.gateway = gateway or GarminGateway()
        self._flights: dict[tuple, Future] = {}
        self._flight_lock = threading.Lock()
        self.operation_lock = threading.RLock()
        self.gateway.capture_sink = self.capture_legacy
        self.gateway.failure_sink = self.capture_failure

    def capture_failure(self, source, request, error):
        with self.operation_lock, self.store.transaction() as db:
            if source in DAILY:
                day = request["local_date"]
                db.execute(
                    "INSERT OR IGNORE INTO days(account_id,local_date,timezone) VALUES(?,?,?)",
                    (self.settings.account_id, day, self.settings.timezone),
                )
                self._source(db, "days", day, source, None, {}, "error", str(error))
                self._state(db, f"day:{day}:{source}", "error", str(error))
            elif source == "activity_list":
                self._state(db, "activities", "error", str(error))
            else:
                activity_id = request["activity_id"]
                self._source(db, "activities", activity_id, source, None, {}, "error", str(error))
                self._state(db, f"activity:{activity_id}:{source}", "error", str(error))

    def capture_legacy(self, source, raw, request):
        """Preserve existing read tools while archiving their responses before normalization."""
        with self.operation_lock:
            if source in DAILY:
                self._save_day(request["local_date"], source, raw)
                return
            capture = self.store.archive(source, raw, request)
            with self.store.transaction() as db:
                self.store.record_capture(db, capture)
                if source == "activity_list":
                    for item in raw:
                        self._activity(db, item, capture)
                    # A limited legacy listing never claims complete range coverage.
                elif source == "summary":
                    if int(raw.get("activityId", 0)) != request["activity_id"]:
                        raise ValueError("Activity ID mismatch")
                    self._activity(db, raw, capture, source)
                elif source == "splits":
                    self._source(
                        db,
                        "activities",
                        request["activity_id"],
                        source,
                        capture,
                        normalizers.normalize_splits(raw),
                    )

    def coalesce(self, key: tuple, operation):
        with self._flight_lock:
            future = self._flights.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._flights[key] = future
        if not owner:
            try:
                return {**future.result(timeout=self.settings.wait_timeout), "coalesced": True}
            except TimeoutError:
                return {"status": "busy", "error": "Refresh still in progress; retry status later"}
        try:
            if not self.operation_lock.acquire(timeout=self.settings.wait_timeout):
                result = {"status": "busy", "error": "Another local operation is in progress"}
            else:
                try:
                    result = operation()
                finally:
                    self.operation_lock.release()
            future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._flight_lock:
                del self._flights[key]

    def status(self):
        account = self.settings.account_id
        sync = self.store.rows("SELECT * FROM sync_state WHERE account_id=?", (account,))
        for state in sync:
            state["stale"] = state["status"] != "ok" or self.stale(state["cursor"])
        return {
            "account_id": account,
            "timezone": self.settings.timezone,
            "sync": sync,
            "coverage": self.store.rows("SELECT * FROM coverage WHERE account_id=?", (account,)),
            "counts": {
                table: self.store.rows(
                    f"SELECT count(*) AS n FROM {table} WHERE account_id=?", (account,)
                )[0]["n"]
                for table in ("activities", "days")
            },
            "in_progress": len(self._flights),
            "backup_enabled": self.settings.backup_enabled,
            "backup_time": self.settings.backup_time,
            "backup_status": getattr(self, "backup_status", {"status": "not_run"}),
        }

    def stale(self, timestamp):
        return timestamp is None or datetime.now(UTC) - datetime.fromisoformat(timestamp) > (
            timedelta(hours=self.settings.stale_hours)
        )

    def lookup(
        self,
        entity: str,
        start_date: str | None = None,
        end_date: str | None = None,
        activity_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        if entity not in {"activities", "days"} or not 1 <= limit <= 500 or offset < 0:
            raise ValueError("Invalid entity, limit (1-500), or offset")
        query = f"SELECT * FROM {entity} WHERE account_id=?"
        args = [self.settings.account_id]
        if start_date:
            query += " AND local_date>=?"
            args.append(date.fromisoformat(start_date).isoformat())
        if end_date:
            query += " AND local_date<=?"
            args.append(date.fromisoformat(end_date).isoformat())
        if activity_id is not None:
            if entity != "activities" or activity_id < 1:
                raise ValueError("activity_id is only valid for activities")
            query += " AND activity_id=?"
            args.append(activity_id)
        key = "activity_id" if entity == "activities" else "local_date"
        rows = self.store.rows(
            query + f" ORDER BY local_date,{key} LIMIT ? OFFSET ?", (*args, limit, offset)
        )
        for row in rows:
            row["sources"] = self.store.rows(
                "SELECT source,status,error,fetched_at,attempted_at,source_date,capture_id "
                "FROM sources WHERE account_id=? AND entity=? AND entity_id=?",
                (self.settings.account_id, entity, str(row[key])),
            )
            row["features"] = self.store.rows(
                "SELECT name,version,status,computed_at,error FROM feature_results "
                "WHERE account_id=? AND entity=? AND entity_id=?",
                (self.settings.account_id, entity, str(row[key])),
            )
            for source in row["sources"]:
                source["stale"] = source["status"] != "ok" or self.stale(source["fetched_at"])
            for feature in row["features"]:
                if feature["status"] != "ok":
                    row[f"feature_{feature['name']}"] = None
            row["stale"] = not row["sources"] or any(s["stale"] for s in row["sources"])
        return {
            "status": "cached" if rows else "missing",
            "rows": rows,
            "limit": limit,
            "offset": offset,
        }

    def _state(self, db, source, status, error=None, cursor=None, coverage_start=None):
        previous = db.execute(
            "SELECT * FROM sync_state WHERE account_id=? AND source=?",
            (self.settings.account_id, source),
        ).fetchone()
        self.store.upsert(
            db,
            "sync_state",
            {
                "account_id": self.settings.account_id,
                "source": source,
                "cursor": cursor or (previous["cursor"] if previous else None),
                "coverage_start": coverage_start
                or (previous["coverage_start"] if previous else None),
                "status": status,
                "error": error,
                "attempted_at": utcnow(),
            },
            ("account_id", "source"),
        )

    def _source(self, db, entity, entity_id, source, capture, normalized, status="ok", error=None):
        now = utcnow()
        keys = (self.settings.account_id, entity, str(entity_id), source)
        old = db.execute(
            "SELECT * FROM sources WHERE account_id=? AND entity=? AND entity_id=? AND source=?",
            keys,
        ).fetchone()
        if capture:
            self.store.record_capture(db, capture)
        self.store.upsert(
            db,
            "sources",
            {
                "account_id": keys[0],
                "entity": entity,
                "entity_id": str(entity_id),
                "source": source,
                "capture_id": capture["capture_id"]
                if capture
                else (old["capture_id"] if old else None),
                "normalized_json": encoded(normalized).decode()
                if capture
                else (old["normalized_json"] if old else None),
                "source_date": (str(normalized["date"]) if normalized.get("date") else None)
                if capture
                else (old["source_date"] if old else None),
                "status": status,
                "error": error,
                "fetched_at": capture["fetched_at"]
                if capture
                else (old["fetched_at"] if old else None),
                "attempted_at": now,
            },
            ("account_id", "entity", "entity_id", "source"),
        )

    def _activity(self, db, raw, capture, source="activity_list"):
        normalized = normalizers.normalize_activity(raw)
        activity_id = int(normalized["activity_id"])
        if activity_id < 1:
            raise ValueError("Garmin returned an invalid activity ID")
        start = normalized.get("start_time_local")
        summary = raw.get("summaryDTO") or raw
        start_utc = summary.get("startTimeGMT")
        parsed = None
        if start_utc:
            parsed = datetime.fromisoformat(start_utc)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            start_utc = parsed.astimezone(UTC).isoformat()
        if not start and parsed:
            start = parsed.astimezone(ZoneInfo(self.settings.timezone)).isoformat()
        if not start:
            raise ValueError(f"Activity {activity_id} has no start time")
        source_day = date.fromisoformat(start[:10]).isoformat()
        day = (
            str(parsed.astimezone(ZoneInfo(self.settings.timezone)).date())
            if parsed
            else source_day
        )
        existing = db.execute(
            "SELECT * FROM activities WHERE account_id=? AND activity_id=?",
            (self.settings.account_id, activity_id),
        ).fetchone()
        previous_source = db.execute(
            "SELECT normalized_json FROM sources WHERE account_id=? AND entity='activities' "
            "AND entity_id=? AND source=?",
            (self.settings.account_id, str(activity_id), source),
        ).fetchone()
        prior = json.loads(previous_source[0]) if previous_source and previous_source[0] else {}
        scalars = {k: None for k, v in prior.items() if isinstance(v, (str, int, float))}
        scalars.update({k: v for k, v in normalized.items() if isinstance(v, (str, int, float))})
        # A list summary must not overwrite richer fields from a requested detail response.
        detail = db.execute(
            "SELECT 1 FROM sources WHERE account_id=? AND entity='activities' "
            "AND entity_id=? AND source='summary'",
            (self.settings.account_id, str(activity_id)),
        ).fetchone()
        if existing and source == "activity_list" and detail:
            scalars = {
                k: v
                for k, v in scalars.items()
                if k
                in {"activity_id", "start_time_local", "name", "sport", "distance_m", "duration_s"}
            }
        values = {
            **scalars,
            "account_id": self.settings.account_id,
            "local_date": day,
            "source_local_date": source_day,
            "start_time_local": start,
            "start_time_utc": start_utc,
            "timezone": self.settings.timezone,
            "fetched_at": capture["fetched_at"],
        }
        self.store.columns(db, "activities", values)
        changed = not existing or any(
            k not in existing.keys() or existing[k] != v
            for k, v in values.items()
            if k != "fetched_at"
        )
        if changed:
            self.store.upsert(db, "activities", values, ("account_id", "activity_id"))
            if existing and any(
                k in existing.keys() and existing[k] is not None and existing[k] != v
                for k, v in values.items()
                if k != "fetched_at"
            ):
                # Previously fetched details/list inputs may describe the old activity.
                db.execute(
                    "UPDATE sources SET status='invalidated' WHERE account_id=? "
                    "AND entity='activities' AND entity_id=? AND source!=?",
                    (self.settings.account_id, str(activity_id), source),
                )
        db.execute(
            "INSERT OR IGNORE INTO days(account_id,local_date,timezone) VALUES(?,?,?)",
            (self.settings.account_id, day, self.settings.timezone),
        )
        self._source(db, "activities", activity_id, source, capture, normalized)
        return activity_id

    def refresh_activities(
        self, start_date: str | None = None, end_date: str | None = None, dry_run: bool = False
    ):
        boundary = datetime.now(UTC)
        if start_date:
            date.fromisoformat(start_date)
        if end_date:
            date.fromisoformat(end_date)
        return self.coalesce(
            ("activities", start_date, end_date, dry_run),
            lambda: self._refresh_activities(start_date, end_date, dry_run, boundary),
        )

    def _refresh_activities(self, start_date, end_date, dry_run, boundary):
        today = boundary.astimezone(ZoneInfo(self.settings.timezone)).date()
        previous = self.store.rows(
            "SELECT * FROM sync_state WHERE account_id=? AND source=?",
            (self.settings.account_id, "activities"),
        )
        cursor = previous[0]["cursor"] if previous else None
        if not start_date and not cursor:
            return {"status": "needs_start_date", "error": "First refresh requires start_date"}
        start = (
            date.fromisoformat(start_date)
            if start_date
            else (
                datetime.fromisoformat(cursor).astimezone(ZoneInfo(self.settings.timezone)).date()
                - timedelta(days=self.settings.overlap_days)
            )
        )
        if not start_date and previous[0]["coverage_start"]:
            start = max(start, date.fromisoformat(previous[0]["coverage_start"]))
        end = date.fromisoformat(end_date) if end_date else today
        if start > end or end > today:
            raise ValueError("Require start_date <= end_date <= today")
        plan = {"start_date": str(start), "end_date": str(end), "boundary": boundary.isoformat()}
        if dry_run:
            return {"status": "dry_run", **plan}
        seen = set()
        page_signatures = set()
        deadline = time.monotonic() + self.settings.refresh_timeout
        pages = 0
        try:
            for page in range(self.settings.max_pages):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Refresh time budget exceeded; retry to resume coverage")
                raw = self.gateway.activity_page(
                    str(start), str(end), page * self.settings.page_size, self.settings.page_size
                )
                capture = self.store.archive(
                    "activity_list", raw, {**plan, "offset": page * self.settings.page_size}
                )
                if not isinstance(raw, list):
                    raise ValueError("Garmin returned a non-list activity page")
                signature = tuple(item.get("activityId") for item in raw)
                if raw and signature in page_signatures:
                    raise ValueError("Garmin pagination repeated a page; coverage incomplete")
                page_signatures.add(signature)
                with self.store.transaction() as db:
                    self.store.record_capture(db, capture)
                    for item in raw:
                        summary = item.get("summaryDTO") or item
                        timestamp = summary.get("startTimeGMT") or summary.get("startTimeLocal")
                        if timestamp:
                            stamp = datetime.fromisoformat(timestamp)
                            if stamp.tzinfo is None:
                                stamp = stamp.replace(
                                    tzinfo=UTC
                                    if summary.get("startTimeGMT")
                                    else ZoneInfo(self.settings.timezone)
                                )
                            if stamp > boundary:
                                continue
                        activity_id = self._activity(db, item, capture)
                        seen.add(activity_id)
                    self._state(db, "activities", "partial")
                pages += 1
                if len(raw) < self.settings.page_size:
                    break
            else:
                raise ValueError("Pagination limit reached; coverage incomplete")
            # Historical backfills never move a recent cursor backwards or claim a gap is covered.
            coverage_end = min(
                boundary,
                datetime.combine(
                    end + timedelta(days=1), Time(), ZoneInfo(self.settings.timezone)
                ).astimezone(UTC),
            )
            advances = not cursor or start <= (
                datetime.fromisoformat(cursor).astimezone(ZoneInfo(self.settings.timezone)).date()
            )
            with self.store.transaction() as db:
                self._state(
                    db,
                    "activities",
                    "ok",
                    cursor=max(coverage_end.isoformat(), cursor or "") if advances else None,
                    coverage_start=min(str(start), previous[0]["coverage_start"] or str(start))
                    if previous
                    else str(start),
                )
                self.store.upsert(
                    db,
                    "coverage",
                    {
                        "account_id": self.settings.account_id,
                        "source": "activities",
                        "start_date": str(start),
                        "end_date": str(end),
                        "boundary": boundary.isoformat(),
                    },
                    ("account_id", "source", "start_date", "end_date"),
                )
            return {"status": "ok", "count": len(seen), "pages": pages, **plan}
        except Exception as exc:
            with self.store.transaction() as db:
                self._state(db, "activities", "partial" if pages else "error", str(exc))
            return {
                "status": "partial" if pages else "error",
                "count": len(seen),
                "pages": pages,
                "error": str(exc),
                "coverage_advanced": False,
                **plan,
            }

    def refresh_activity(self, activity_id: int, sources: list[str], dry_run=False):
        if activity_id < 1 or not sources or set(sources) - {"summary", "splits"}:
            raise ValueError("Provide a positive ID and summary/splits sources")
        return self.coalesce(
            ("activity", activity_id, tuple(sorted(set(sources))), dry_run),
            lambda: self._refresh_activity(activity_id, sources, dry_run),
        )

    def _refresh_activity(self, activity_id, sources, dry_run):
        if dry_run:
            return {"status": "dry_run", "activity_id": activity_id, "sources": sources}
        results = {}
        for source in sorted(set(sources), key=lambda s: s != "summary"):
            try:
                raw = self.gateway.raw_activity(activity_id, source)
                capture = self.store.archive(source, raw, {"activity_id": activity_id})
                with self.store.transaction() as db:
                    if source == "summary":
                        if int(raw.get("activityId", 0)) != activity_id:
                            raise ValueError("Activity ID mismatch")
                        self._activity(db, raw, capture, source)
                    else:
                        self._source(
                            db,
                            "activities",
                            activity_id,
                            source,
                            capture,
                            normalizers.normalize_splits(raw),
                        )
                    self._state(
                        db, f"activity:{activity_id}:{source}", "ok", cursor=capture["fetched_at"]
                    )
                results[source] = {"status": "ok"}
            except Exception as exc:
                with self.store.transaction() as db:
                    self._source(db, "activities", activity_id, source, None, {}, "error", str(exc))
                    self._state(db, f"activity:{activity_id}:{source}", "error", str(exc))
                results[source] = {"status": "error", "error": str(exc)}
        return {
            "status": "ok" if all(r["status"] == "ok" for r in results.values()) else "partial",
            "sources": results,
        }

    def refresh_day(self, local_date: str, sources: list[str], dry_run=False):
        day = date.fromisoformat(local_date).isoformat()
        if not sources or set(sources) - DAILY.keys():
            raise ValueError("Specify sleep/hrv/body_battery/training_readiness/training_status")
        return self.coalesce(
            ("day", day, tuple(sorted(set(sources))), dry_run),
            lambda: self._refresh_day(day, sources, dry_run),
        )

    def _save_day(self, day, source, raw):
        capture = self.store.archive(source, raw, {"local_date": day})
        normalized = DAILY[source](raw)
        status = "ok" if normalized.get("available") else "unavailable"
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT normalized_json FROM sources WHERE account_id=? "
                "AND entity='days' AND entity_id=? AND source=?",
                (self.settings.account_id, day, source),
            ).fetchone()
            # Clear disappeared scalars for this source, preserving every other source.
            prior = json.loads(old[0]) if old and old[0] else {}
            if source == "body_battery":
                prior = (prior.get("days") or [{}])[0]
            scalars = {
                f"{source}_{k}": None
                for k, v in prior.items()
                if isinstance(v, (str, int, float)) and k != "available"
            }
            scalars.update(
                {
                    f"{source}_{k}": v
                    for k, v in normalized.items()
                    if isinstance(v, (str, int, float)) and k != "available"
                }
            )
            if source == "body_battery":
                matching = next(
                    (item for item in normalized.get("days", []) if item.get("date") == day),
                    {},
                )
                scalars.update({f"{source}_{k}": v for k, v in matching.items()})
            values = {
                "account_id": self.settings.account_id,
                "local_date": day,
                "timezone": self.settings.timezone,
                "fetched_at": capture["fetched_at"],
                **scalars,
            }
            self.store.columns(db, "days", values)
            self.store.upsert(db, "days", values, ("account_id", "local_date"))
            self._source(db, "days", day, source, capture, normalized, status)
            self._state(
                db,
                f"day:{day}:{source}",
                status,
                cursor=capture["fetched_at"] if status == "ok" else None,
            )
        return status

    def _refresh_day(self, day, sources, dry_run):
        if dry_run:
            return {"status": "dry_run", "date": day, "sources": sources}
        results = {}
        for source in dict.fromkeys(sources):
            try:
                raw = self.gateway.raw_day(day, source)
                status = self._save_day(day, source, raw)
                results[source] = {"status": status}
            except Exception as exc:
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO days(account_id,local_date,timezone) VALUES(?,?,?)",
                        (self.settings.account_id, day, self.settings.timezone),
                    )
                    self._source(db, "days", day, source, None, {}, "error", str(exc))
                    self._state(db, f"day:{day}:{source}", "error", str(exc))
                results[source] = {"status": "error", "error": str(exc)}
        return {
            "status": "ok" if all(r["status"] == "ok" for r in results.values()) else "partial",
            "date": day,
            "sources": results,
        }
