from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .db import require_patched_sqlite, sqlite3


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError(f"Invalid SQL identifier: {value}")
    return value


class Store:
    def __init__(self, settings: Settings):
        require_patched_sqlite()
        self.settings = settings
        self.root = settings.data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.lock = threading.RLock()
        for name in ("objects", "manifests", "backups", "definitions"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
        self.migrate()
        settings.database.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.settings.database, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def migrate(self):
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS migrations "
                "(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                checksum = digest(path.read_bytes())
                previous = db.execute(
                    "SELECT sha256 FROM migrations WHERE name=?", (path.name,)
                ).fetchone()
                if previous:
                    if previous[0] != checksum:
                        raise ValueError(f"Applied migration changed: {path.name}")
                    continue
                statement = ""
                for line in path.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise ValueError(f"Incomplete SQL migration: {path.name}")
                db.execute("INSERT INTO migrations VALUES(?,?,?)", (path.name, checksum, utcnow()))

    def write_immutable(self, relative: str, data: bytes):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Link a fully synced file into place; readers never see a partial object.
        temp = path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as stream:
                os.chmod(temp, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, path)
                sync_directory(path.parent)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError(f"Immutable object collision: {relative}") from None
        finally:
            temp.unlink(missing_ok=True)

    def archive(self, source: str, payload, request: dict) -> dict:
        raw = encoded(payload)
        sha = digest(raw)
        self.write_immutable(f"objects/{sha}.json", raw)
        manifest = {
            "capture_id": uuid.uuid4().hex,
            "account_id": self.settings.account_id,
            "source": source,
            "object_hash": sha,
            "fetched_at": utcnow(),
            "request": request,
            "timezone": self.settings.timezone,
        }
        manifest_bytes = encoded(manifest)
        self.write_immutable(f"manifests/{manifest['capture_id']}.json", manifest_bytes)
        return {**manifest, "manifest_hash": digest(manifest_bytes)}

    def record_capture(self, db, capture):
        db.execute(
            "INSERT OR IGNORE INTO captures VALUES(?,?,?,?,?,?)",
            tuple(
                capture[k]
                for k in (
                    "capture_id",
                    "account_id",
                    "source",
                    "object_hash",
                    "manifest_hash",
                    "fetched_at",
                )
            ),
        )

    def read_capture(self, capture_id: str):
        with self.connection() as db:
            row = db.execute(
                "SELECT object_hash FROM captures WHERE capture_id=?", (capture_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"Missing capture: {capture_id}")
        data = (self.root / "objects" / f"{row[0]}.json").read_bytes()
        if digest(data) != row[0]:
            raise ValueError("Raw object checksum mismatch")
        return json.loads(data)

    def columns(self, db, table: str, values: dict):
        """Promote normalized scalar fields to SQL columns; nested values stay in source JSON."""
        identifier(table)
        known = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        for name, value in values.items():
            identifier(name)
            if name not in known and value is not None:
                kind = (
                    "INTEGER"
                    if isinstance(value, (bool, int))
                    else ("REAL" if isinstance(value, float) else "TEXT")
                )
                db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {kind}')

    def upsert(self, db, table: str, values: dict, keys: tuple[str, ...]):
        identifier(table)
        names = [identifier(k) for k in values]
        columns = ",".join(f'"{k}"' for k in names)
        updates = ",".join(f'"{k}"=excluded."{k}"' for k in names if k not in keys)
        db.execute(
            f'INSERT INTO "{table}" ({columns}) VALUES '
            f"({','.join('?' for _ in names)}) ON CONFLICT({','.join(keys)}) "
            f"DO UPDATE SET {updates}",
            tuple(values.values()),
        )

    def audit(self, db, action: str, detail: dict):
        db.execute(
            "INSERT INTO audit_log(happened_at,account_id,action,detail_json) VALUES(?,?,?,?)",
            (utcnow(), self.settings.account_id, action, encoded(detail).decode()),
        )

    def rows(self, query: str, args=()) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute(query, args)]

    def repair(self, entity: str, entity_id: str, changes: dict, reason: str):
        """Agent-facing transactional repair with before/after provenance; never calls Garmin."""
        if entity not in {"activities", "days"} or not reason.strip() or not changes:
            raise ValueError("Provide entity, changes, and a repair reason")
        if set(changes) & {"account_id", "activity_id", "local_date" if entity == "days" else ""}:
            raise ValueError("Repairs must preserve row identity")
        key = "activity_id" if entity == "activities" else "local_date"
        with self.transaction() as db:
            previous = db.execute(
                f"SELECT * FROM {entity} WHERE account_id=? AND {key}=?",
                (self.settings.account_id, entity_id),
            ).fetchone()
            if previous is None:
                raise ValueError("Cannot repair a missing row")
            for column in changes:
                identifier(column)
                if column not in previous.keys():
                    raise ValueError(f"Unknown column: {column}")
            assignments = ",".join(f'"{column}"=?' for column in changes)
            db.execute(
                f"UPDATE {entity} SET {assignments} WHERE account_id=? AND {key}=?",
                (*changes.values(), self.settings.account_id, entity_id),
            )
            if entity == "activities" and "local_date" in changes:
                db.execute(
                    "INSERT OR IGNORE INTO days(account_id,local_date,timezone) VALUES(?,?,?)",
                    (self.settings.account_id, changes["local_date"], self.settings.timezone),
                )
            self.audit(
                db,
                "repair",
                {
                    "entity": entity,
                    "entity_id": entity_id,
                    "reason": reason,
                    "before": dict(previous),
                    "changes": changes,
                },
            )
