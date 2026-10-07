from __future__ import annotations

import math
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .service import DAILY
from .storage import digest, encoded, identifier, utcnow


class FeatureEngine:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def load(self, name):
        identifier(name)
        path = Path(__file__).parent / "features" / f"{name}.py"
        if not path.is_file() or name.startswith("_"):
            raise ValueError(f"Unknown feature module: {name}")
        code = path.read_bytes()
        module = types.ModuleType(f"garmin_mcp.features.{name}")
        # Local project Python is trusted; no code uploads or arbitrary paths in the API.
        exec(compile(code, str(path), "exec"), module.__dict__)
        definition = module.DEFINITION
        if definition["name"] != name or definition["entity"] not in {"activities", "days"}:
            raise ValueError("Invalid feature name/entity")
        if definition["sql_type"] not in {"REAL", "INTEGER", "TEXT"}:
            raise ValueError("Features require a scalar SQL type")
        if (
            not isinstance(definition["version"], int)
            or definition["version"] < 1
            or not isinstance(definition["lookback_days"], int)
            or not 0 <= definition["lookback_days"] <= 366
        ):
            raise ValueError("Invalid version or lookback_days")
        allowed = set(DAILY) | {"activity_list", "summary", "splits"}
        if (
            not definition["inputs"]
            or set(definition["inputs"]) - allowed
            or not isinstance(definition["unit"], str)
        ):
            raise ValueError("Invalid feature inputs/unit")
        if definition["entity"] == "activities" and definition["lookback_days"]:
            raise ValueError(
                "Activity features use same-activity inputs; day features allow lookback"
            )
        return definition, module.calculate, code

    def catalog(self):
        return [
            self.load(path.stem)[0]
            for path in sorted((Path(__file__).parent / "features").glob("*.py"))
            if not path.stem.startswith("_")
        ]

    def _inputs(self, definition, row):
        account = self.service.settings.account_id
        entity = definition["entity"]
        key = str(row["activity_id"] if entity == "activities" else row["local_date"])
        inputs, refs, missing = {}, [], []
        for source in definition["inputs"]:
            values = []
            for offset in range(definition["lookback_days"], -1, -1):
                day = str(date.fromisoformat(row["local_date"]) - timedelta(days=offset))
                if entity == "days" and source == "activity_list":
                    coverage = self.store.rows(
                        "SELECT * FROM coverage WHERE account_id=? AND source='activities' "
                        "AND start_date<=? AND end_date>=?",
                        (account, day, day),
                    )
                    # Today's request-start snapshot is not a complete calendar day.
                    complete = [
                        c
                        for c in coverage
                        if datetime.fromisoformat(c["boundary"])
                        .astimezone(ZoneInfo(self.service.settings.timezone))
                        .date()
                        > date.fromisoformat(day)
                    ]
                    if not complete:
                        missing.append(f"activity_list coverage:{day}")
                        continue
                    rows = self.store.rows(
                        "SELECT activity_id FROM activities WHERE account_id=? AND local_date=?",
                        (account, day),
                    )
                    keys = [str(r["activity_id"]) for r in rows]
                    source_entity = "activities"
                    refs.extend({"coverage": c} for c in complete)
                else:
                    source_entity = "days" if source in DAILY else "activities"
                    if entity == "days" and source_entity == "activities":
                        missing.append(f"{source}: unsupported day input; use activity_list")
                        continue
                    keys = [day if source_entity == "days" else key]
                for source_key in keys:
                    found = self.store.rows(
                        "SELECT * FROM sources WHERE account_id=? AND entity=? AND entity_id=? "
                        "AND source=?",
                        (account, source_entity, source_key, source),
                    )
                    if not found or found[0]["status"] != "ok" or not found[0]["capture_id"]:
                        missing.append(f"{source}:{source_key}")
                        continue
                    record = found[0]
                    raw = self.store.read_capture(record["capture_id"])
                    if source == "activity_list":
                        raw = next(
                            item for item in reversed(raw) if str(item["activityId"]) == source_key
                        )
                    values.append(raw)
                    refs.append(
                        {
                            "source": source,
                            "entity_id": source_key,
                            "capture_id": record["capture_id"],
                            "stale": self.service.stale(record["fetched_at"]),
                        }
                    )
            inputs[source] = values
        return inputs, refs, missing

    def _fingerprint(self, db):
        account = self.service.settings.account_id
        state = {}
        for table in ("sources", "coverage", "activities", "days"):
            state[table] = [
                dict(r)
                for r in db.execute(
                    f"SELECT * FROM {table} WHERE account_id=? ORDER BY rowid", (account,)
                )
            ]
        return digest(encoded(state))

    def recalculate(self, names: list[str], entity: str, ids: list[str], dry_run=False):
        if entity not in {"activities", "days"} or not names or not ids or len(ids) > 500:
            raise ValueError("Specify entity, feature names, and 1-500 row IDs")
        with self.service.operation_lock:
            return self._recalculate(names, entity, ids, dry_run)

    def _recalculate(self, names, entity, ids, dry_run):
        definitions = [self.load(name) for name in dict.fromkeys(names)]
        if any(d[0]["entity"] != entity for d in definitions):
            raise ValueError("Feature entity does not match request")
        key = "activity_id" if entity == "activities" else "local_date"
        with self.store.connection() as db:
            fingerprint = self._fingerprint(db)
        results = []
        for entity_id in dict.fromkeys(ids):
            if entity == "days":
                date.fromisoformat(entity_id)
            elif int(entity_id) < 1:
                raise ValueError("Activity IDs must be positive")
            rows = self.store.rows(
                f"SELECT * FROM {entity} WHERE account_id=? AND {key}=?",
                (self.service.settings.account_id, entity_id),
            )
            for definition, calculate, _ in definitions:
                result = {
                    "entity_id": str(entity_id),
                    "name": definition["name"],
                    "version": definition["version"],
                    "value": None,
                    "source_refs": [],
                }
                if not rows:
                    results.append({**result, "status": "missing_row", "missing": [entity_id]})
                    continue
                try:
                    inputs, refs, missing = self._inputs(definition, rows[0])
                    result["source_refs"] = refs
                    if missing:
                        result.update(status="missing_inputs", missing=missing)
                    elif dry_run:
                        result.update(status="ready")
                    else:
                        value = calculate(inputs)
                        kind = definition["sql_type"]
                        if value is not None:
                            valid = (
                                (kind == "REAL" and isinstance(value, (int, float)))
                                or (kind == "INTEGER" and type(value) is int)
                                or (kind == "TEXT" and isinstance(value, str))
                            )
                            if not valid or (
                                isinstance(value, (int, float)) and not math.isfinite(value)
                            ):
                                raise ValueError("Feature returned an invalid/nonfinite scalar")
                        result.update(
                            value=value, status="ok" if value is not None else "unavailable"
                        )
                except Exception as exc:
                    result.update(status="error", error=str(exc))
                results.append(result)
        if dry_run:
            return {"status": "dry_run", "results": results}
        for _definition, _, code in definitions:
            self.store.write_immutable(f"definitions/{digest(code)}.py", code)
        with self.store.transaction() as db:
            if self._fingerprint(db) != fingerprint:
                return {"status": "conflict", "error": "Inputs changed; retry recalculation"}
            for definition, _, code in definitions:
                self._install(db, definition, code)
            db.execute("INSERT INTO write_context VALUES('features')")
            for result in results:
                if result["status"] == "missing_row":
                    continue
                column = identifier(f"feature_{result['name']}")
                db.execute(
                    f'UPDATE {entity} SET "{column}"=? WHERE account_id=? AND {key}=?',
                    (result["value"], self.service.settings.account_id, result["entity_id"]),
                )
                self.store.upsert(
                    db,
                    "feature_results",
                    {
                        "account_id": self.service.settings.account_id,
                        "entity": entity,
                        "entity_id": result["entity_id"],
                        "name": result["name"],
                        "version": result["version"],
                        "status": result["status"],
                        "computed_at": utcnow(),
                        "source_refs_json": encoded(result["source_refs"]).decode(),
                        "error": result.get("error")
                        or (", ".join(result["missing"]) if result.get("missing") else None),
                    },
                    ("account_id", "entity", "entity_id", "name"),
                )
            db.execute("DELETE FROM write_context WHERE purpose='features'")
            self.store.audit(db, "recalculate", {"names": names, "entity": entity, "ids": ids})
        return {
            "status": "ok" if all(r["status"] == "ok" for r in results) else "partial",
            "results": results,
        }

    def _install(self, db, definition, code):
        name, version = definition["name"], definition["version"]
        previous = db.execute(
            "SELECT * FROM feature_definitions WHERE name=? ORDER BY version DESC", (name,)
        ).fetchone()
        if previous:
            if (
                previous["entity"] != definition["entity"]
                or previous["sql_type"] != definition["sql_type"]
            ):
                raise ValueError("Changing entity/type requires a new feature name")
            if version < previous["version"]:
                raise ValueError("Feature version downgrade is not allowed")
            if version == previous["version"]:
                if previous["code_hash"] != digest(code):
                    raise ValueError("Feature code changed without a version bump")
                return
            db.execute("UPDATE feature_results SET status='invalidated' WHERE name=?", (name,))
        table, column = definition["entity"], identifier(f"feature_{name}")
        if not previous:
            db.execute(f'ALTER TABLE {table} ADD COLUMN "{column}" {definition["sql_type"]}')
        db.execute(
            "INSERT INTO feature_definitions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                name,
                version,
                table,
                definition["sql_type"],
                definition["unit"],
                encoded(definition["inputs"]).decode(),
                definition["lookback_days"],
                digest(code),
                encoded(definition).decode(),
                utcnow(),
            ),
        )
