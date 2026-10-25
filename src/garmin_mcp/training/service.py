from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager

from pydantic import ValidationError

from ..storage import digest, encoded, utcnow
from . import models, snapshots


class TrainingError(Exception):
    def __init__(self, status, message, **detail):
        self.result = {"status": status, "message": message, **detail}
        super().__init__(message)


def fail(status, message, **detail):
    raise TrainingError(status, message, **detail)


class TrainingService:
    def __init__(self, local):
        self.local = local
        self.store = local.store
        self.account = local.settings.account_id

    @contextmanager
    def write(self):
        try:
            with self.local.operation_lock, self.store.transaction() as db:
                yield db
        except sqlite3.IntegrityError as exc:
            fail("conflict", "A plan reference or uniqueness constraint changed", detail=str(exc))

    @contextmanager
    def read(self):
        with self.local.operation_lock, self.store.connection() as db:
            db.execute("BEGIN")
            yield db

    def cycle_row(self, db, cycle_id, expected=None):
        row = db.execute(
            "SELECT * FROM training_cycles WHERE account_id=? AND id=?", (self.account, cycle_id)
        ).fetchone()
        if not row:
            fail("not_found", "Training cycle not found")
        if expected is not None and row["current_revision_id"] != expected:
            fail(
                "conflict",
                "The plan changed; reload before saving",
                current_revision_id=row["current_revision_id"],
            )
        return dict(row)

    def revision_row(self, db, cycle_id, revision_id):
        row = db.execute(
            "SELECT * FROM plan_revisions WHERE account_id=? AND cycle_id=? AND id=?",
            (self.account, cycle_id, revision_id),
        ).fetchone()
        if not row:
            fail("not_found", "Plan revision not found")
        return snapshots.decoded(row)

    def replay(self, db, action, scope, body):
        request_hash = digest(
            encoded({"action": action, "scope": scope, "body": body.model_dump(mode="json")})
        )
        previous = db.execute(
            "SELECT * FROM training_commands WHERE account_id=? AND key=?",
            (self.account, body.idempotency_key),
        ).fetchone()
        if previous:
            if previous["request_hash"] != request_hash:
                fail("conflict", "Idempotency key was already used with different content")
            return request_hash, json.loads(previous["result_json"])
        return request_hash, None

    def remember(self, db, body, request_hash, result):
        snapshots.insert(
            db,
            "training_commands",
            {
                "account_id": self.account,
                "key": body.idempotency_key,
                "request_hash": request_hash,
                "result_json": encoded(result).decode(),
                "created_at": utcnow(),
            },
        )
        return result

    def audit(self, db, action, cycle_id, body, **detail):
        self.store.audit(
            db,
            "training_" + action,
            {
                "cycle_id": cycle_id,
                "author": body.author,
                "reason": body.reason,
                **detail,
            },
        )

    def source_refs(self, db, refs):
        for capture in refs:
            if not db.execute(
                "SELECT 1 FROM captures WHERE account_id=? AND capture_id=?",
                (self.account, capture),
            ).fetchone():
                fail(
                    "missing_inputs",
                    "Source capture is unavailable for this account",
                    capture_id=capture,
                )

    def normalize(self, snapshot):
        result = snapshot.model_dump(mode="json")
        zone = result["cycle"]["timezone"] or self.local.settings.timezone
        result["cycle"]["timezone"] = zone
        for row in result["events"] + result["sessions"]:
            row["timezone"] = row["timezone"] or zone
        return models.Snapshot.model_validate(result)

    def validate_change(self, db, cycle_id, snapshot, current=None):
        data = snapshot.model_dump(mode="json")
        if (
            data["cycle"]["status"] == "active"
            and db.execute(
                "SELECT 1 FROM training_cycles WHERE account_id=? AND status='active' AND id!=?",
                (self.account, cycle_id),
            ).fetchone()
        ):
            fail("conflict", "Another training cycle is already active")
        if current:
            previous = snapshots.read(db, self.account, cycle_id, current)
            if (
                previous["cycle"]["status"] in {"completed", "cancelled"}
                and data["cycle"]["status"] != previous["cycle"]["status"]
            ):
                fail("validation_error", "A finished cycle cannot be reopened; create a new cycle")
            if previous["cycle"]["status"] == "active" and data["cycle"]["status"] == "draft":
                fail("validation_error", "An active cycle cannot become a draft cycle")
            old_ids = {s["id"] for s in previous["sessions"]}
            new_ids = {s["id"] for s in data["sessions"]}
            if old_ids - new_ids:
                fail(
                    "validation_error",
                    "Keep removed sessions in the snapshot as cancelled",
                    session_ids=sorted(old_ids - new_ids),
                )
        for session in data["sessions"]:
            replaces = session["replaces_session_id"]
            if (
                replaces
                and not db.execute(
                    "SELECT 1 FROM session_versions s JOIN plan_revisions r "
                    "ON r.account_id=s.account_id AND r.cycle_id=s.cycle_id AND r.id=s.revision_id "
                    "WHERE s.account_id=? AND s.cycle_id=? AND s.id=? "
                    "AND r.status IN ('accepted','superseded')",
                    (self.account, cycle_id, replaces),
                ).fetchone()
            ):
                fail("validation_error", "Replacement must reference an existing accepted session")
        return snapshots.warnings(data)

    def new_revision(self, db, cycle_id, snapshot, body, parent, base, refs):
        revision_id = uuid.uuid4().hex
        number = db.execute(
            "SELECT coalesce(max(number),0)+1 FROM plan_revisions "
            "WHERE account_id=? AND cycle_id=?",
            (self.account, cycle_id),
        ).fetchone()[0]
        self.source_refs(db, refs)
        snapshots.insert(
            db,
            "plan_revisions",
            {
                "account_id": self.account,
                "cycle_id": cycle_id,
                "id": revision_id,
                "number": number,
                "parent_revision_id": parent,
                "base_revision_id": base,
                "status": "draft",
                "version": 1,
                "author": body.author,
                "reason": body.reason,
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "source_capture_ids_json": encoded(refs).decode(),
            },
        )
        snapshots.save(db, self.account, cycle_id, revision_id, snapshot)
        return revision_id

    def accept(self, db, cycle_id, revision_id, body):
        row = self.revision_row(db, cycle_id, revision_id)
        snapshot = models.Snapshot.model_validate(
            snapshots.read(db, self.account, cycle_id, revision_id)
        )
        warnings = self.validate_change(db, cycle_id, snapshot, row["parent_revision_id"])
        explanation = getattr(body, "override_explanation", None)
        if warnings and not (explanation and explanation.strip()):
            fail(
                "validation_error",
                "Explain why this plan overrides its warnings",
                warnings=warnings,
            )
        if row["parent_revision_id"]:
            db.execute(
                "UPDATE plan_revisions SET status='superseded' "
                "WHERE account_id=? AND cycle_id=? AND id=?",
                (self.account, cycle_id, row["parent_revision_id"]),
            )
        db.execute(
            "UPDATE plan_revisions SET status='accepted', accepted_at=?, updated_at=?, "
            "override_explanation=? WHERE account_id=? AND cycle_id=? AND id=?",
            (utcnow(), utcnow(), explanation, self.account, cycle_id, revision_id),
        )
        db.execute(
            "UPDATE training_cycles SET current_revision_id=?,status=? WHERE account_id=? AND id=?",
            (revision_id, snapshot.cycle.status, self.account, cycle_id),
        )
        self.audit(
            db,
            "accept",
            cycle_id,
            body,
            revision_id=revision_id,
            parent_revision_id=row["parent_revision_id"],
            warnings=warnings,
            override_explanation=explanation,
        )
        return {
            "status": "ok",
            "cycle_id": cycle_id,
            "revision_id": revision_id,
            "warnings": warnings,
        }

    def create_cycle(self, body: models.CreateCycle):
        with self.write() as db:
            fingerprint, replay = self.replay(db, "create_cycle", None, body)
            if replay:
                return replay
            cycle_id = uuid.uuid4().hex
            snapshot = self.normalize(body.snapshot)
            self.validate_change(db, cycle_id, snapshot)
            snapshots.insert(
                db,
                "training_cycles",
                {
                    "account_id": self.account,
                    "id": cycle_id,
                    "current_revision_id": None,
                    "status": snapshot.cycle.status,
                    "created_at": utcnow(),
                },
            )
            revision = self.new_revision(
                db, cycle_id, snapshot, body, None, None, body.source_capture_ids
            )
            result = self.accept(db, cycle_id, revision, body)
            return self.remember(db, body, fingerprint, result)

    def list_cycles(self):
        with self.read() as db:
            rows = db.execute(
                "SELECT t.*,c.name,c.start_date,c.end_date,c.timezone FROM training_cycles t "
                "JOIN cycle_versions c ON c.account_id=t.account_id AND c.cycle_id=t.id "
                "AND c.revision_id=t.current_revision_id WHERE t.account_id=? "
                "ORDER BY t.created_at",
                (self.account,),
            ).fetchall()
            return {
                "status": "ok",
                "cycles": [dict(row) for row in rows],
                "timezone": self.local.settings.timezone,
            }

    def get_cycle(self, cycle_id, revision_id=None):
        with self.read() as db:
            cycle = self.cycle_row(db, cycle_id)
            revision_id = revision_id or cycle["current_revision_id"]
            revision = self.revision_row(db, cycle_id, revision_id)
            revisions = db.execute(
                "SELECT * FROM plan_revisions WHERE account_id=? AND cycle_id=? ORDER BY number",
                (self.account, cycle_id),
            ).fetchall()
            snapshot = snapshots.read(db, self.account, cycle_id, revision_id)
            return {
                "status": "ok",
                "cycle": cycle,
                "revision": revision,
                "revisions": [snapshots.decoded(r) for r in revisions],
                "snapshot": snapshot,
                "warnings": snapshots.warnings(snapshot),
            }

    def create_draft(self, cycle_id, body: models.DraftCreate):
        with self.write() as db:
            fingerprint, replay = self.replay(db, "create_draft", cycle_id, body)
            if replay:
                return replay
            self.cycle_row(db, cycle_id, body.expected_revision_id)
            base = body.base_revision_id or body.expected_revision_id
            self.revision_row(db, cycle_id, base)
            if body.snapshot is not None:
                snapshot = self.normalize(body.snapshot)
            else:
                data = snapshots.read(db, self.account, cycle_id, base)
                if base != body.expected_revision_id:
                    data = snapshots.restore_plan(
                        data, snapshots.read(db, self.account, cycle_id, body.expected_revision_id)
                    )
                snapshot = self.normalize(models.Snapshot.model_validate(data))
            warnings = self.validate_change(db, cycle_id, snapshot, body.expected_revision_id)
            revision = self.new_revision(
                db,
                cycle_id,
                snapshot,
                body,
                body.expected_revision_id,
                base,
                body.source_capture_ids,
            )
            self.audit(db, "draft_create", cycle_id, body, revision_id=revision)
            return self.remember(
                db,
                body,
                fingerprint,
                {
                    "status": "ok",
                    "cycle_id": cycle_id,
                    "revision_id": revision,
                    "version": 1,
                    "warnings": warnings,
                },
            )

    def draft_row(self, db, cycle_id, revision_id, body):
        self.cycle_row(db, cycle_id, body.expected_revision_id)
        row = self.revision_row(db, cycle_id, revision_id)
        if row["status"] != "draft" or row["version"] != body.expected_draft_version:
            fail("conflict", "Draft changed or is no longer editable")
        if row["parent_revision_id"] != body.expected_revision_id:
            fail(
                "conflict", "Draft is based on an older plan; create a new draft from current plan"
            )
        return row

    def edit_draft(self, cycle_id, revision_id, body: models.DraftEdit):
        with self.write() as db:
            row = self.draft_row(db, cycle_id, revision_id, body)
            snapshot = self.normalize(body.snapshot)
            warnings = self.validate_change(db, cycle_id, snapshot, body.expected_revision_id)
            self.source_refs(db, body.source_capture_ids)
            before = snapshots.read(db, self.account, cycle_id, revision_id)
            snapshots.clear(db, self.account, cycle_id, revision_id)
            snapshots.save(db, self.account, cycle_id, revision_id, snapshot)
            db.execute(
                "UPDATE plan_revisions SET version=version+1,author=?,reason=?,updated_at=?,"
                "source_capture_ids_json=? WHERE account_id=? AND cycle_id=? AND id=?",
                (
                    body.author,
                    body.reason,
                    utcnow(),
                    encoded(body.source_capture_ids).decode(),
                    self.account,
                    cycle_id,
                    revision_id,
                ),
            )
            self.audit(
                db,
                "draft_edit",
                cycle_id,
                body,
                revision_id=revision_id,
                before=before,
                after=snapshot.model_dump(mode="json"),
            )
            return {
                "status": "ok",
                "revision_id": revision_id,
                "version": row["version"] + 1,
                "warnings": warnings,
            }

    def apply_draft(self, cycle_id, revision_id, body: models.DraftAction):
        with self.write() as db:
            fingerprint, replay = self.replay(db, "apply_draft", [cycle_id, revision_id], body)
            if replay:
                return replay
            self.draft_row(db, cycle_id, revision_id, body)
            result = self.accept(db, cycle_id, revision_id, body)
            return self.remember(db, body, fingerprint, result)

    def discard_draft(self, cycle_id, revision_id, body: models.DraftAction):
        with self.write() as db:
            fingerprint, replay = self.replay(db, "discard_draft", [cycle_id, revision_id], body)
            if replay:
                return replay
            # Stale drafts can still be discarded against the current cycle revision.
            self.cycle_row(db, cycle_id, body.expected_revision_id)
            row = self.revision_row(db, cycle_id, revision_id)
            if row["status"] != "draft" or row["version"] != body.expected_draft_version:
                fail("conflict", "Draft changed or is no longer editable")
            db.execute(
                "UPDATE plan_revisions SET status='discarded',updated_at=? "
                "WHERE account_id=? AND cycle_id=? AND id=?",
                (utcnow(), self.account, cycle_id, revision_id),
            )
            self.audit(db, "draft_discard", cycle_id, body, revision_id=revision_id)
            return self.remember(
                db, body, fingerprint, {"status": "ok", "revision_id": revision_id}
            )

    def lifecycle(self, cycle_id, body: models.Lifecycle):
        with self.write() as db:
            fingerprint, replay = self.replay(db, "lifecycle", cycle_id, body)
            if replay:
                return replay
            self.cycle_row(db, cycle_id, body.expected_revision_id)
            snapshot = snapshots.read(db, self.account, cycle_id, body.expected_revision_id)
            snapshot["cycle"]["status"] = body.status
            model = models.Snapshot.model_validate(snapshot)
            revision = self.new_revision(
                db, cycle_id, model, body, body.expected_revision_id, body.expected_revision_id, []
            )
            action = models.DraftAction(
                **body.model_dump(exclude={"status"}),
                expected_draft_version=1,
                override_explanation=body.reason,
            )
            result = self.accept(db, cycle_id, revision, action)
            return self.remember(db, body, fingerprint, result)

    def compare(self, cycle_id, from_revision_id, to_revision_id):
        with self.read() as db:
            self.cycle_row(db, cycle_id)
            for revision in (from_revision_id, to_revision_id):
                self.revision_row(db, cycle_id, revision)
            before = snapshots.read(db, self.account, cycle_id, from_revision_id)
            after = snapshots.read(db, self.account, cycle_id, to_revision_id)
            return {
                "status": "ok",
                "from_revision_id": from_revision_id,
                "to_revision_id": to_revision_id,
                "changes": snapshots.diff(before, after),
                "reason": self.revision_row(db, cycle_id, to_revision_id)["reason"],
            }

    def assessed_session(self, db, cycle_id, revision, session_id):
        row = self.revision_row(db, cycle_id, revision)
        if row["status"] not in {"accepted", "superseded"}:
            fail("validation_error", "Execution must reference an accepted assignment")
        session = db.execute(
            "SELECT * FROM session_versions WHERE account_id=? AND cycle_id=? "
            "AND revision_id=? AND id=?",
            (self.account, cycle_id, revision, session_id),
        ).fetchone()
        if not session:
            fail("not_found", "Session does not belong to this cycle/revision")
        # Once execution is recorded, later plan edits cannot move its assessment basis.
        refs = db.execute(
            "SELECT session_revision_id FROM session_outcomes WHERE account_id=? AND cycle_id=? "
            "AND session_id=? UNION SELECT session_revision_id FROM activity_matches "
            "WHERE account_id=? AND cycle_id=? AND session_id=? "
            "AND state IN ('confirmed','needs_review')",
            (self.account, cycle_id, session_id, self.account, cycle_id, session_id),
        ).fetchall()
        if any(r[0] != revision for r in refs):
            fail("conflict", "Keep the original assignment revision used for execution")
        return snapshots.decoded(session)

    def record(self, db, table, cycle_id, body, key, extra=None):
        self.cycle_row(db, cycle_id, body.expected_revision_id)
        values = body.model_dump(mode="json")
        id_ = values[key]
        previous = db.execute(
            f"SELECT * FROM {table} WHERE account_id=? AND cycle_id=? AND {key}=?",
            (self.account, cycle_id, id_),
        ).fetchone()
        if (previous["version"] if previous else 0) != body.expected_version:
            fail("conflict", "Record changed; reload before saving")
        if previous:
            for identity in ("session_id", "session_revision_id", "activity_id"):
                if identity in values and previous[identity] != values[identity]:
                    fail(
                        "validation_error",
                        "Record identity cannot change; reject the old link first",
                    )
        values.pop("expected_revision_id")
        values.pop("expected_version")
        values.update(
            account_id=self.account,
            cycle_id=cycle_id,
            version=body.expected_version + 1,
            updated_at=utcnow(),
            **(extra or {}),
        )
        self.store.upsert(db, table, snapshots.sql_values(values), ("account_id", "cycle_id", key))
        self.audit(
            db,
            table,
            cycle_id,
            body,
            before=snapshots.decoded(previous) if previous else None,
            after=values,
        )
        return {"status": "ok", "record": values}

    def save_outcome(self, cycle_id, body: models.Outcome):
        with self.write() as db:
            session = self.assessed_session(db, cycle_id, body.session_revision_id, body.session_id)
            if session["kind"] == "rest" and body.metrics_source != "none":
                fail("validation_error", "Rest is a compliance mark without activity metrics")
            links = db.execute(
                "SELECT 1 FROM activity_matches WHERE account_id=? AND cycle_id=? "
                "AND session_id=? AND state IN ('confirmed','needs_review')",
                (self.account, cycle_id, body.session_id),
            ).fetchone()
            if body.metrics_source == "garmin" and not links:
                fail("missing_inputs", "Link an imported activity before choosing Garmin metrics")
            return self.record(db, "session_outcomes", cycle_id, body, "session_id")

    def save_match(self, cycle_id, body: models.Match):
        from .matching import validate_allocation

        with self.write() as db:
            self.cycle_row(db, cycle_id, body.expected_revision_id)
            session = self.assessed_session(db, cycle_id, body.session_revision_id, body.session_id)
            if session["kind"] == "rest":
                fail("validation_error", "Rest cannot be matched to an activity")
            activity = db.execute(
                "SELECT * FROM activities WHERE account_id=? AND activity_id=?",
                (self.account, body.activity_id),
            ).fetchone()
            if not activity:
                fail("not_found", "Activity not found for this account")
            validate_allocation(db, self.account, cycle_id, body, dict(activity))
            evidence = {
                "source_" + key: activity[key]
                for key in ("distance_m", "duration_s", "start_time_utc", "local_date", "timezone")
            }
            evidence["source_capture_ids"] = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT capture_id FROM sources WHERE account_id=? "
                    "AND entity='activities' AND entity_id=? AND capture_id IS NOT NULL",
                    (self.account, str(body.activity_id)),
                )
            ]
            return self.record(db, "activity_matches", cycle_id, body, "id", evidence)

    def save_feedback(self, cycle_id, body: models.Feedback):
        with self.write() as db:
            self.cycle_row(db, cycle_id, body.expected_revision_id)
            if (
                body.session_id
                and not db.execute(
                    "SELECT 1 FROM session_versions WHERE account_id=? AND cycle_id=? "
                    "AND revision_id=? AND id=?",
                    (self.account, cycle_id, body.expected_revision_id, body.session_id),
                ).fetchone()
            ):
                fail("not_found", "Feedback session not found in this cycle")
            return self.record(db, "training_feedback", cycle_id, body, "id")

    def save_review(self, cycle_id, body: models.Review):
        with self.write() as db:
            reviewed = self.revision_row(db, cycle_id, body.plan_revision_id)
            if reviewed["status"] not in {"accepted", "superseded"}:
                fail("validation_error", "Review an accepted plan; link a draft as the proposal")
            if body.proposed_revision_id:
                self.revision_row(db, cycle_id, body.proposed_revision_id)
            self.source_refs(db, body.source_capture_ids)
            return self.record(db, "training_reviews", cycle_id, body, "id", {"state": "current"})

    def context(self, cycle_id, start_date=None, end_date=None):
        from .reporting import context

        with self.read() as db:
            cycle = self.cycle_row(db, cycle_id)
            snapshot = snapshots.read(db, self.account, cycle_id, cycle["current_revision_id"])
            return context(self, db, cycle, snapshot, start_date, end_date)

    def candidates(self, cycle_id, session_id):
        from .matching import candidates

        with self.read() as db:
            cycle = self.cycle_row(db, cycle_id)
            snapshot = snapshots.read(db, self.account, cycle_id, cycle["current_revision_id"])
            session = next((s for s in snapshot["sessions"] if s["id"] == session_id), None)
            if not session:
                fail("not_found", "Session not found")
            return candidates(db, self.account, session)

    def history(self, cycle_id):
        with self.read() as db:
            self.cycle_row(db, cycle_id)
            rows = db.execute(
                "SELECT * FROM audit_log WHERE account_id=? AND action LIKE 'training_%' "
                "AND json_extract(detail_json,'$.cycle_id')=? ORDER BY id",
                (self.account, cycle_id),
            ).fetchall()
            return {"status": "ok", "history": [snapshots.decoded(r) for r in rows]}


def invoke(service, operation, **kwargs):
    """HTTP and MCP receive the same domain result, including structured errors."""
    try:
        return getattr(TrainingService(service), operation)(**kwargs)
    except TrainingError as exc:
        return exc.result
    except (ValidationError, ValueError) as exc:
        return {"status": "validation_error", "message": str(exc)}
