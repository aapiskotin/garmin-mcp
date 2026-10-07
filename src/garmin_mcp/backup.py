from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import sqlite3
from .storage import digest, encoded, sync_directory, utcnow


class Backups:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.directory = self.store.root / "backups"

    def list(self):
        result = []
        for path in sorted(self.directory.glob("*/backup.json"), reverse=True):
            try:
                manifest = json.loads(path.read_bytes())
                result.append(
                    {
                        "backup_id": path.parent.name,
                        "created_at": manifest["created_at"],
                        "includes_tokens": manifest["includes_tokens"],
                    }
                )
            except (ValueError, KeyError):
                continue
        return result

    def create(self, include_tokens=False):
        with self.service.operation_lock, self.store.lock:
            backup_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
            stage = self.directory / f".pending-{backup_id}"
            stage.mkdir(mode=0o700)
            try:
                with self.store.connection() as source:
                    target = sqlite3.connect(stage / "garmin.sqlite3")
                    try:
                        source.backup(target)
                        target.execute("PRAGMA journal_mode=DELETE")
                        self._check_db(target)
                        captures = target.execute(
                            "SELECT capture_id,object_hash FROM captures"
                        ).fetchall()
                        definitions = target.execute(
                            "SELECT code_hash FROM feature_definitions"
                        ).fetchall()
                    finally:
                        target.close()
                for capture_id, sha in captures:
                    self._copy(
                        self.store.root / "objects" / f"{sha}.json",
                        stage / "objects" / f"{sha}.json",
                    )
                    self._copy(
                        self.store.root / "manifests" / f"{capture_id}.json",
                        stage / "manifests" / f"{capture_id}.json",
                    )
                for (sha,) in definitions:
                    self._copy(
                        self.store.root / "definitions" / f"{sha}.py",
                        stage / "definitions" / f"{sha}.py",
                    )
                package = Path(__file__).parent
                for path in package.rglob("*"):
                    if path.is_file() and path.suffix in {".py", ".sql", ".html"}:
                        self._copy(path, stage / "code" / path.relative_to(package))
                project = package.parents[1]
                for name in ("pyproject.toml", "uv.lock", "Dockerfile", "compose.yaml"):
                    if (project / name).is_file():
                        self._copy(project / name, stage / "config" / name)
                if include_tokens:
                    token_dir = self.service.gateway.token_dir
                    if not token_dir.is_dir():
                        raise ValueError("Token directory does not exist")
                    for path in token_dir.rglob("*"):
                        if path.is_file():
                            self._copy(path, stage / "tokens" / path.relative_to(token_dir))
                config = {
                    "account_id": self.service.settings.account_id,
                    "timezone": self.service.settings.timezone,
                    "overlap_days": self.service.settings.overlap_days,
                    "backup_time": self.service.settings.backup_time,
                }
                (stage / "settings.json").write_bytes(encoded(config))
                files = {
                    str(p.relative_to(stage)): digest(p.read_bytes())
                    for p in sorted(stage.rglob("*"))
                    if p.is_file()
                }
                manifest = {
                    "format": 1,
                    "created_at": utcnow(),
                    "files": files,
                    "includes_tokens": include_tokens,
                    "code_id": digest(
                        encoded({k: v for k, v in files.items() if k.startswith("code/")})
                    ),
                    "config_id": digest(encoded(config)),
                }
                (stage / "backup.json").write_bytes(encoded(manifest))
                for path in stage.rglob("*"):
                    if path.is_file():
                        path.chmod(0o600)
                        with path.open("rb") as stream:
                            os.fsync(stream.fileno())
                self.verify_directory(stage)
                for path in stage.rglob("*"):
                    if path.is_dir():
                        sync_directory(path)
                sync_directory(stage)
                stage.rename(self.directory / backup_id)
                sync_directory(self.directory)
            except BaseException:
                shutil.rmtree(stage, ignore_errors=True)
                raise
        result = {"status": "ok", "backup_id": backup_id, "created_at": manifest["created_at"]}
        self.service.backup_status = result
        return result

    @staticmethod
    def _copy(source, destination):
        if source.is_symlink():
            raise ValueError(f"Refusing backup symlink: {source.name}")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)

    @staticmethod
    def _check_db(db):
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Backup database contains broken references")

    def verify_directory(self, directory):
        manifest = json.loads((directory / "backup.json").read_bytes())
        if manifest.get("format") != 1:
            raise ValueError("Unsupported backup format")
        if not {"garmin.sqlite3", "settings.json"} <= manifest["files"].keys():
            raise ValueError("Backup is missing required checksums")
        for name, sha in manifest["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe backup path")
            path = directory / relative
            if (
                not path.resolve().is_relative_to(directory.resolve())
                or path.is_symlink()
                or not path.is_file()
                or digest(path.read_bytes()) != sha
            ):
                raise ValueError(f"Backup checksum mismatch: {name}")
        db = sqlite3.connect(f"file:{directory / 'garmin.sqlite3'}?mode=ro", uri=True)
        try:
            self._check_db(db)
            for capture_id, sha, manifest_sha in db.execute(
                "SELECT capture_id,object_hash,manifest_hash FROM captures"
            ):
                object_path = f"objects/{sha}.json"
                capture_path = f"manifests/{capture_id}.json"
                if (
                    manifest["files"].get(object_path) != sha
                    or manifest["files"].get(capture_path) != manifest_sha
                ):
                    raise ValueError("Broken capture reference")
                capture = json.loads((directory / capture_path).read_bytes())
                if capture["object_hash"] != sha or capture["capture_id"] != capture_id:
                    raise ValueError("Capture manifest identity mismatch")
            for (sha,) in db.execute("SELECT code_hash FROM feature_definitions"):
                if manifest["files"].get(f"definitions/{sha}.py") != sha:
                    raise ValueError("Missing feature definition code")
            for (refs,) in db.execute("SELECT source_refs_json FROM feature_results"):
                for ref in json.loads(refs):
                    if (
                        "capture_id" in ref
                        and not db.execute(
                            "SELECT 1 FROM captures WHERE capture_id=?", (ref["capture_id"],)
                        ).fetchone()
                    ):
                        raise ValueError("Broken feature source reference")
            for name, sha in db.execute("SELECT name,sha256 FROM migrations"):
                local = Path(__file__).parent / "migrations" / name
                if not local.is_file() or digest(local.read_bytes()) != sha:
                    raise ValueError("Incompatible migration; use the backed-up application code")
        finally:
            db.close()
        return manifest

    def restore(self, backup_id: str, confirm=False, restore_tokens=False):
        if not re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{12}", backup_id):
            raise ValueError("Invalid backup ID")
        if not confirm:
            return {
                "status": "confirmation_required",
                "backup_id": backup_id,
                "message": "Restore replaces the local database. Garmin writes are never replayed.",
            }
        directory = self.directory / backup_id
        with self.service.operation_lock, self.store.lock:
            manifest = self.verify_directory(directory)
            config = json.loads((directory / "settings.json").read_bytes())
            if config["account_id"] != self.service.settings.account_id:
                raise ValueError("Backup account does not match configured account")
            if restore_tokens and not manifest["includes_tokens"]:
                raise ValueError("Backup does not contain tokens")
            # Copy immutable dependencies first. A crash cannot leave DB references dangling.
            for name in manifest["files"]:
                if name.startswith(("objects/", "manifests/", "definitions/")):
                    self.store.write_immutable(name, (directory / name).read_bytes())
            safety = self.create()
            source = sqlite3.connect(f"file:{directory / 'garmin.sqlite3'}?mode=ro", uri=True)
            try:
                with self.store.connection() as target:
                    source.backup(target)
                    target.execute("PRAGMA journal_mode=WAL")
            finally:
                source.close()
            self.store.migrate()
            if restore_tokens:
                for name in manifest["files"]:
                    if name.startswith("tokens/"):
                        self._copy(
                            directory / name,
                            self.service.gateway.token_dir / Path(name).relative_to("tokens"),
                        )
                self.service.gateway._client = None
            with self.store.transaction() as db:
                self.store.audit(db, "restore", {"backup_id": backup_id})
        return {
            "status": "restored",
            "backup_id": backup_id,
            "safety_backup_id": safety["backup_id"],
            "code_id": manifest["code_id"],
        }


class BackupScheduler:
    """The only periodic task. Never contacts Garmin or computes features."""

    def __init__(self, backups):
        self.backups = backups
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="daily-backup", daemon=True)

    def tick(self, now=None):
        settings = self.backups.service.settings
        if not settings.backup_enabled:
            return
        zone = ZoneInfo(settings.timezone)
        now = now or datetime.now(zone)
        if now.timetz().replace(tzinfo=None) < time.fromisoformat(settings.backup_time):
            return
        if any(
            datetime.fromisoformat(b["created_at"]).astimezone(zone).date() == now.date()
            for b in self.backups.list()
        ):
            return
        self.backups.create()

    def _run(self):
        while not self.stop.wait(30):
            try:
                self.tick()
            except Exception as exc:
                self.backups.service.backup_status = {"status": "error", "error": str(exc)}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=10)
