---
name: garmin-local-app
description: Operate and extend the local Garmin SQLite app, inspect archived training data, add versioned Python features, repair records, and request refreshes or restores. Use in the garmin-mcp checkout or with its local MCP/HTTP service.
---

Follow `specs/garmin-app.md`. Training analysis/advice belong to the agent.
Garmin retrieval and feature computation happen only on request. Existing Garmin
write tools still require an approved preview and `confirm=true`.

## Paths and access

- Host data: `$GARMIN_DATA_DIR`, default `<checkout>/.garmin-data/`.
- Container data: `/data/garmin-app/`; database: `garmin.sqlite3`.
- Immutable objects: `objects/<sha256>.json`, canonical raw response JSON.
- Captures: `manifests/<capture_id>.json`, with account, source, object hash,
  request boundaries/parameters, timezone, and UTC fetched timestamp.
- Tokens: separate `garmin_tokens` Compose volume at `/data/garmin-tokens`.
- Features: `src/garmin_mcp/features/*.py`; migrations:
  `src/garmin_mcp/migrations/*.sql`. Compose mounts both directories into the app.
- Used definition code: `definitions/<code_hash>.py` in the data directory.
- Backups: `backups/<backup_id>/`, with `backup.json` checksums and code/config IDs.
- Local endpoints: `http://127.0.0.1:8765/mcp`, `/docs`, `/openapi.json`, `/`.
  The UI only reads cached records. Run one server process per data directory.

Use the Docker runtime for DB operations if host Python lacks patched SQLite.
The image builds verified SQLite 3.51.3; the app rejects unpatched WAL versions.
Run Python with `docker compose exec garmin-mcp python`; import
`garmin_mcp.server.local_service` for access to `service.store`.
Never expose credentials in features, responses, or raw-object metadata.

## Schema and freshness

Read `src/garmin_mcp/migrations/001_initial.sql` for metadata tables and keys.
`activities` uses `(account_id, activity_id)`; `days` uses
`(account_id, local_date)`. Inspect `PRAGMA table_info` for source scalar columns
and `feature_<name>` columns added after initialization.
Activity `local_date` uses the calendar timezone when UTC time is available;
`source_local_date` and `start_time_local` preserve Garmin's original context.

`sources` records per-source status, capture, normalized nested metadata, original
source date, fetched time, and attempted time. `captures` retains superseded
responses. `coverage` records completed activity ranges; `sync_state` holds
cursors and latest errors. `feature_results` records versions, source references,
computed times, and statuses. Only `status=ok` feature values are usable; cached
SQL values can remain after invalidation. Lookup masks invalidated feature values.

`local_status` and `lookup_records` never contact Garmin. Freshness defaults to
24 hours. Missing/unavailable is not zero. Corrections preserve Garmin IDs and
conservatively invalidate all features for that account, covering affected days
and lookbacks. Recalculation remains explicit.

## Requested operations

MCP and HTTP share services:

| MCP | HTTP |
| --- | --- |
| `local_status`, `lookup_records` | GET `/api/status`, `/api/lookup/{entity}` |
| `refresh_activities` | POST `/api/refresh/activities` |
| `refresh_activity` | POST `/api/refresh/activities/{id}` |
| `refresh_day` | POST `/api/refresh/days/{YYYY-MM-DD}` |
| `feature_catalog`, `recalculate_features` | GET `/api/features`, POST `/api/features/recalculate` |
| `list_backups`, `backup_local` | GET/POST `/api/backups` |
| `restore_local` | POST `/api/backups/{id}/restore` |

The first activity refresh needs an explicit `start_date`. Later refreshes begin
at the successful cursor minus the overlap (default seven days), through a fixed
request-start boundary. Historical backfills require explicit dates. Pagination
failure retains completed pages without advancing coverage. Retry the request to
resume by replaying/deduplicating pages; offsets are not trusted across requests
because uploads can reorder Garmin pages.

Pass only requested sources. Activity sources: `summary`, `splits`. Daily sources:
`sleep`, `hrv`, `body_battery`, `training_readiness`, `training_status`. Dates use
the configured calendar zone (Europe/Berlin by default); original Garmin sleep
dates are retained as source metadata.

`dry_run=true` plans retrieval or checks feature input readiness without downloads,
calculations, or feature schema changes. Handle `needs_start_date`, `partial`,
`error`, `busy`, `missing_inputs`, `unavailable`, and `conflict` explicitly. Inspect
coverage before training claims. Decide whether to retry, request missing inputs,
use stale data with disclosure, or defer advice.

## Add or revise a feature

1. Create a trusted local module like `features/sleep_hours.py`. `DEFINITION`
   declares name, positive integer version, entity, scalar SQL type
   (`REAL`/`INTEGER`/`TEXT`), unit, source inputs, lookback days, and description.
   Implement `calculate(inputs)` returning one finite scalar or `None`.
2. Inputs map source names to raw responses, oldest first. Activity features use
   the selected activity; day features use the date and declared lookback.
   `activity_list` as a day input requires complete activity coverage and includes
   activities in that interval. Covered empty days can have an empty list.
   Missing sources are reported before calculation.
3. Test fixture archives and missing/boundary cases. Modules must be pure local
   calculations: no network or DB writes. The API does not accept code uploads.
4. Increment the version for every code change. Keep name/entity/type stable;
   use a new name for incompatible outputs. Request only needed features and IDs.
5. Recalculation atomically installs the typed column/version, stores immutable
   code, and writes results/provenance. Input changes during computation cause a
   conflict. Results from older definitions are invalidated.

For general schema changes, add a numbered SQL migration; never edit an applied
migration. Restart the service to apply it atomically. Preserve unrelated columns
and source references.

## Direct repairs

Prefer `Store.repair(entity, id, changes, reason)`: it logs before/after provenance
in a transaction and triggers invalidation. For custom SQL use
`Store.transaction()` and `Store.audit()` together. Never disable triggers or leave
`write_context` populated. Features compute from archives, so scalar-only repairs
do not change calculation inputs. Correct source inputs by archiving a new object
and updating source references transactionally; keep the old capture.

## Backup and restore

The only scheduled work is a daily local backup at `GARMIN_BACKUP_TIME` in the
calendar timezone (default 03:00). `GARMIN_BACKUP_ENABLED=false` disables it.
Backups include an online SQLite snapshot, referenced captures/objects, definitions,
project code/migrations, and nonsecret config IDs. Manual backups can include
tokens with `include_tokens=true`; copies use private permissions.

Restore requires `confirm=true` because it replaces the DB. It verifies checksums,
integrity, account, migrations, and references, and makes a safety backup. Token
restore is separately opt-in. It never executes backed-up code or replays Garmin
writes. Saved code/configuration are recovery artifacts; rebuild compatible code
before restoring an incompatible migration set. Local backups do not cover host loss.
