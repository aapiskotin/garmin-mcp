# Personal Garmin MCP

A local Garmin training-data app with SQLite, immutable raw archives, request-driven
features, a read-only lookup UI, Swagger, and MCP. It also creates and schedules
structured workouts through **unofficial** Garmin endpoints.

> This is an experimental personal project. Garmin may change the endpoints, rate-limit
> requests, or revoke a session without notice. Do not expose the server to the internet
> without adding separate authentication.

## Important URLs and endpoints

**Bookmark [Garmin home](http://127.0.0.1:8765/home)** for one starting page linking
to the planner, saved data, database browser, and API documentation.

These links work after starting Docker Compose or the server in `streamable-http`
mode. They use the default local port, `8765`; if you change `GARMIN_MCP_PORT`, use
that port in the URLs below.

| Page or endpoint | Clickable URL | Purpose |
| --- | --- | --- |
| **Home** | [http://127.0.0.1:8765/home](http://127.0.0.1:8765/home) | All application pages and service links in one place. |
| **Training planner** | [http://127.0.0.1:8765/training](http://127.0.0.1:8765/training) | Create cycles, edit weekly plans, record execution, and review history. |
| **Lookup UI** | [http://127.0.0.1:8765/](http://127.0.0.1:8765/) | Browse saved activities and daily data. |
| **SQLite browser** | [http://127.0.0.1:8766/garmin](http://127.0.0.1:8766/garmin) | Browse all tables, filter rows, run read-only SQL, and export CSV/JSON with Datasette. |
| **Swagger API docs** | [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) | Inspect and try HTTP API requests. |
| **MCP endpoint** | [http://127.0.0.1:8765/mcp](http://127.0.0.1:8765/mcp) | Connection URL for a Streamable HTTP MCP client; not a web page. |
| OpenAPI schema | [http://127.0.0.1:8765/openapi.json](http://127.0.0.1:8765/openapi.json) | Download the HTTP API schema as JSON. |
| Local status | [http://127.0.0.1:8765/api/status](http://127.0.0.1:8765/api/status) | Read coverage, freshness, and errors. |
| Saved activities | [http://127.0.0.1:8765/api/lookup/activities](http://127.0.0.1:8765/api/lookup/activities) | Read cached activity records as JSON. |
| Saved days | [http://127.0.0.1:8765/api/lookup/days](http://127.0.0.1:8765/api/lookup/days) | Read cached daily records as JSON. |
| Feature catalog | [http://127.0.0.1:8765/api/features](http://127.0.0.1:8765/api/features) | List available feature definitions. |
| Backups | [http://127.0.0.1:8765/api/backups](http://127.0.0.1:8765/api/backups) | List complete local backups. |

The JSON links above use `GET`. For `POST` operations such as refresh,
recalculation, backup creation, restore, and Garmin actions, use
[Swagger](http://127.0.0.1:8765/docs) to supply the required parameters and body.

## Features

- stores separate activity/day records, typed features, provenance, and freshness;
- refreshes only on request, with full pagination, recent overlap, and explicit failures;
- computes requested versioned Python features from archived objects;
- provides an editable training planner plus the read-only lookup UI and Swagger alongside MCP;
- makes daily local online SQLite backups and verifies requested restores;
- verifies a saved Garmin session;
- previews Garmin JSON without making changes;
- creates workouts and optionally adds them to the calendar;
- lists the workout library and calendar;
- reads completed activities, compact summaries, and lap/split metrics;
- reads available sleep, HRV, Body Battery, Training Readiness, and Training Status;
- deletes workouts and removes workouts from the calendar;
- supports running, cycling, walking, and hiking;
- supports time, distance, and `lap button` steps;
- supports interval repeats;
- supports pace, heart-rate, power, and cadence targets;
- requires `confirm=true` for every Garmin write; local plan edits save directly.

## Training plans

The daily-use first release of [the planning specification](specs/training-plan.md)
is implemented at [Training planner](http://127.0.0.1:8765/training). Create a cycle
with a main event, goal, timezone, and availability. Weeks begin as outlines; add
sessions, explicit rest, structured workout steps, or alternatives as needed.
The planner supports additional events, training phases, travel constraints,
manual execution, feedback, and links to imported Garmin activities.

Saving a plan creates an immutable full snapshot. Drafts remain proposals until
applied. The history view compares before/after values and records the reason.
Moving a session preserves its ID; cancellation preserves the assignment.
Restoring an earlier version makes a new draft and retains later sessions as
cancelled, extending the date envelope if necessary. Recorded execution keeps
its original assignment even when the current plan changes.

Only one cycle can be active per configured account. Completed and cancelled
cycles remain readable; create a new cycle for the next goal. Dates are local
calendar dates with stored IANA timezones, not device-timezone conversions.

Weekly totals count each imported activity once. Only confirmed matches contribute
to session comparisons. If one activity is shared, explicitly allocate both
seconds and metres to every linked session; unresolved allocations produce unknown
session metrics. Linked manual records are not added again to weekly totals.
Choose manual or Garmin metrics in **Record result**; both edit history and comments
are retained. A day without an activity remains **unknown**, not skipped.
An outline week without sessions has an unknown scheduled total, not zero.

Coverage, source timestamps, missing health sources, and partial totals are visible.
Activity corrections invalidate affected matches and reviews for explicit review;
confirmed source metrics and capture references are retained in match history.
Reading any planner view never imports data or calculates features. **Refresh Garmin
data** is an explicit request to the existing activity-import service.

The first release also includes local candidate suggestions and saved review notes.
Managed publication of plan sessions to Garmin is the specification's **later
stage**, and is not implemented here. Existing workout preview/create/schedule tools
remain available with their original explicit Garmin-write confirmation. Saving,
restoring, or applying a plan never uses those tools.

### Shared HTTP and MCP operations

Every operation below uses the same `training.TrainingService`. HTTP uses
`/api/training`; request models and error responses appear in
[Swagger](http://127.0.0.1:8765/docs). MCP uses the listed names and the same parameters;
mutation payloads are passed as `body`.

| HTTP (after `/api/training`) | MCP |
| --- | --- |
| GET / POST `/cycles` | `training_list_cycles` / `training_create_cycle` |
| GET `/cycles/{cycle_id}` (`revision_id` optional) | `training_get_cycle` |
| POST `/cycles/{cycle_id}/lifecycle` | `training_lifecycle` |
| POST `/cycles/{cycle_id}/drafts` | `training_create_draft` |
| PUT `/cycles/{cycle_id}/drafts/{revision_id}` | `training_edit_draft` |
| POST `/cycles/{cycle_id}/drafts/{revision_id}/apply` | `training_apply_draft` |
| POST `/cycles/{cycle_id}/drafts/{revision_id}/discard` | `training_discard_draft` |
| GET `/cycles/{cycle_id}/compare` | `training_compare` |
| GET `/cycles/{cycle_id}/context` (`start_date`, `end_date` optional) | `training_context` |
| GET `/cycles/{cycle_id}/sessions/{session_id}/candidates` | `training_candidates` |
| PUT `/cycles/{cycle_id}/outcomes` | `training_save_outcome` |
| PUT `/cycles/{cycle_id}/matches` | `training_save_match` |
| PUT `/cycles/{cycle_id}/feedback` | `training_save_feedback` |
| PUT `/cycles/{cycle_id}/reviews` | `training_save_review` |
| GET `/cycles/{cycle_id}/history` | `training_history` |

Read the current snapshot before editing. Supply `expected_revision_id` for every
mutation of an existing cycle; draft editing/applying also needs
`expected_draft_version`. Operational records use `expected_version=0` to create,
then the returned version to edit. Always supply `author` and `reason`. Create,
apply, discard, and lifecycle commands require an `idempotency_key`: replaying the
same request returns its previous result; changing content under that key returns
`conflict`. Reload after a 409 instead of overwriting another editor's work.

Snapshot payloads contain `cycle`, `events`, `constraints`, `phases`, `weeks`, and
`sessions`. Each collection item has a stable caller-chosen `id`. Target ranges
use `distance_min_m` / `distance_max_m`, `duration_min_s` / `duration_max_s`,
pace in seconds/km, elevation in metres, and heart rate in bpm. Unknowns are null.
A session structure has `schema_version: 1`, optional free-text `instructions`, and
an optional existing `WorkoutSpec` under `workout`. For alternatives, provide
`variants` and an explicit `selected_variant_id`. Do not sum alternatives.
Constraint weekday indices are Monday=0 through Sunday=6; null means unspecified,
while an empty list means no available days.

Constraints and weekly targets optionally specify `sport`; null keeps the existing
all-sports scope. New cycles in the UI default to running availability and targets.
Weekly reports include `by_sport` and `all_sports_totals`, with running and cycling
subtypes grouped together. The main totals follow the week's target sport; manual
facts retain the sport of their assessed assignment. Cycling distance never counts
toward a running target. The Replace action cancels an assignment and creates a
linked replacement with its own sport and targets; it does not change Garmin.

Warnings do not silently override availability or volume targets: applying a
snapshot with warnings requires `override_explanation`. Other invalid references,
dates, units, or transitions return `validation_error` (422), missing records return
`not_found` (404), concurrent edits return `conflict` (409), and unavailable evidence
returns `missing_inputs` (422). Incomplete context has `status: partial` with coverage
details; `known_sum` is explicitly not a complete weekly total.

The numbered migration adds planning tables to the existing SQLite database.
Existing online backups include all plans, versions, execution, feedback, matches,
reviews, idempotency receipts, and audit history. Restore never calls Garmin.

Run the complete fixture-only suite using the patched SQLite Docker runtime:

```bash
docker build --target test -t garmin-mcp-training-test .
docker run --rm --network none garmin-mcp-training-test
```

## Installation

### Docker Compose — recommended

Local use requires Docker with Compose support. Build the image from the repository root:

```bash
docker compose build
```

The build installs dependencies strictly from `uv.lock` and compiles checksum-verified
SQLite 3.51.3, fixing the [WAL-reset bug](https://www.sqlite.org/releaselog/3_51_3.html).

Run the interactive login once. The password and MFA code are entered directly in the
container and are not stored. OAuth tokens are saved in the private
`garmin-mcp_garmin_tokens` named volume:

```bash
docker compose --profile login run --rm garmin-login
```

Start the MCP server:

```bash
docker compose up -d garmin-mcp
docker compose up -d --build datasette
docker compose ps
```

Open [Garmin home](http://127.0.0.1:8765/home) or
[Swagger API docs](http://127.0.0.1:8765/docs). See
[Important URLs and endpoints](#important-urls-and-endpoints) for all quick links.
To use a different local port:

```bash
GARMIN_MCP_PORT=8767 docker compose up -d garmin-mcp
```

### SQLite web browser

[Datasette](https://docs.datasette.io/en/stable/) runs locally at
[http://127.0.0.1:8766/garmin](http://127.0.0.1:8766/garmin). Open
[activities](http://127.0.0.1:8766/garmin/activities) or
[days](http://127.0.0.1:8766/garmin/days), use column filters, or choose
**View and edit SQL** to run SELECT queries and joins. Results can be exported
as CSV or JSON. This reads saved data and never requests Garmin downloads.

Start or rebuild it with `docker compose up -d --build datasette`; stop only the
browser with `docker compose stop datasette`. It restarts automatically with Docker.
Set `GARMIN_DB_UI_PORT` to change its port (choose a different port from the app).
Recreate the app service too after changing this port so the home page link updates:
`docker compose up -d garmin-mcp datasette`.

The browser shares `GARMIN_DATA_DIR` with the app through a read-only directory
mount, including SQLite's WAL/SHM files. It uses the same patched SQLite build
and reads the live database, without immutable mode or a copied snapshot.
The app keeps an idle SQLite connection open so WAL/SHM remain available to the
read-only browser between requests; it does not keep a read transaction open.
Reload a page after a refresh or recalculation to see newly committed records.
The app must initialize the database first; Compose waits for its health check.
The browser binds to `127.0.0.1` and cannot edit the database. It displays raw
stored columns: consult `feature_results.status` and source timestamps for
validity; the app's lookup UI additionally masks invalidated feature values.

Datasette dependencies are pinned with hashes in
`deploy/datasette/requirements.txt`. To update them after editing `requirements.in`:

```bash
uv pip compile deploy/datasette/requirements.in --python-version 3.12 \
  --generate-hashes -o deploy/datasette/requirements.txt
docker compose up -d --build datasette
```

### Service operations

View logs or stop the server:

```bash
docker compose logs -f garmin-mcp
docker compose down
```

`docker compose down` preserves the Garmin tokens. Running `docker compose down -v`
deletes the volume and its tokens.

Data persists in the host directory `./.garmin-data` (override with `GARMIN_DATA_DIR`).
Keep it on a local disk and run one app process per data directory. Startup initializes
the local schema; it does not log in to Garmin, import data, or calculate features.

Daily backups run at `03:00` Europe/Berlin, or on the next scheduler tick after that
time if the app was stopped. Configure `GARMIN_BACKUP_TIME`, `GARMIN_TIMEZONE`, or
`GARMIN_BACKUP_ENABLED=false`. Automatic backups exclude tokens. Local backups
protect against accidental changes; a second location is needed for host loss.

### Requested data and features

The first refresh requires a start date:

```bash
curl -X POST http://127.0.0.1:8765/api/refresh/activities \
  -H 'Content-Type: application/json' -d '{"start_date":"2026-10-01"}'
curl -X POST http://127.0.0.1:8765/api/refresh/days/2026-10-06 \
  -H 'Content-Type: application/json' -d '{"sources":["sleep","hrv"]}'
curl -X POST http://127.0.0.1:8765/api/features/recalculate \
  -H 'Content-Type: application/json' \
  -d '{"names":["sleep_hours"],"entity":"days","ids":["2026-10-06"]}'
```

Subsequent activity refreshes can use `{}` to continue from the successful cursor,
rechecking `GARMIN_OVERLAP_DAYS` (default 7). Pagination is bounded by 1,000 pages and
a 120-second budget checked between requests; in-flight calls have transport timeouts
and one bounded transient retry. Failed refreshes preserve completed pages without
advancing coverage. Retrying replays and deduplicates pages to tolerate moving offsets.
Explicit date ranges request older history. Detail/split and daily sources are fetched
only when named. `dry_run=true` previews work without downloads or calculations.

Feature inputs are never downloaded implicitly. Lookups report stale sources (24 hours
by default) and mask invalidated values. Corrections conservatively invalidate all
features for the account; recomputation is explicit. Built-in examples: `pace` and
`sleep_hours`. Feature and migration directories are mounted into Compose; new feature
modules load on request and new migrations apply on restart.

The [agent operations skill](skills/garmin-local-app/SKILL.md) documents paths, schema,
Python features, direct SQL repairs, migrations, errors, and restore. Raw objects are
immutable; repairs retain audit provenance.

Manual backup: POST `/api/backups` with `{}` or `{"include_tokens":true}`. List with
GET on the same route. Restore: POST `/api/backups/{backup_id}/restore` with
`{"confirm":true}`. Restore verifies checksums, integrity, references, account, and
migrations, and makes a safety backup. Saved code/configuration are recovery artifacts,
not automatically executed. Token restore is separately enabled with `restore_tokens`.

### Local installation without Docker

Python 3.12+, `uv`, and patched SQLite are required (3.51.3+ or backports 3.50.7 / 3.44.6).
Older SQLite runtimes are rejected; Docker includes the fix.

```bash
cd /path/to/garmin-mcp
uv sync --extra dev
```

## One-time login

```bash
uv run garmin-mcp-login
```

The script interactively requests an email address, password, and MFA code when required.
The password is not stored. OAuth tokens are saved to
`.garmin-tokens/garmin_tokens.json` with restricted permissions. The directory is already
included in `.gitignore`.

To store tokens somewhere else:

```bash
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp-login
```

## Local MCP over stdio

```bash
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp/.venv/bin/garmin-mcp",
      "env": {
        "GARMIN_TOKEN_DIR": "/safe/private/path/garmin-tokens"
      }
    }
  }
}
```

## Streamable HTTP for ChatGPT

```bash
export GARMIN_MCP_TRANSPORT=streamable-http
export GARMIN_MCP_HOST=127.0.0.1
export GARMIN_MCP_PORT=8765
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp
```

MCP connection URL: [http://127.0.0.1:8765/mcp](http://127.0.0.1:8765/mcp).

The app binds to loopback by default and validates Host/Origin. It has no public
authentication layer. Remote access needs a separately designed authenticated
connection; this repository does not open a public tunnel.

## Example `preview_workout` argument

```json
{
  "workout": {
    "name": "6 x 800",
    "sport": "running",
    "description": "Controlled intervals",
    "blocks": [
      {
        "steps": [
          {
            "step_type": "warmup",
            "duration_type": "time",
            "duration_value": 900
          }
        ]
      },
      {
        "repeat": 6,
        "steps": [
          {
            "step_type": "interval",
            "duration_type": "distance",
            "duration_value": 800,
            "target_type": "pace_seconds_per_km",
            "target_low": 250,
            "target_high": 260
          },
          {
            "step_type": "recovery",
            "duration_type": "time",
            "duration_value": 120
          }
        ]
      },
      {
        "steps": [
          {
            "step_type": "cooldown",
            "duration_type": "time",
            "duration_value": 600
          }
        ]
      }
    ]
  }
}
```

Pace values are specified in seconds per kilometre: `250` = 4:10/km and `260` =
4:20/km. The server converts them to the m/s values used by Garmin Connect.

After previewing, call `create_workout` with the same object, a date in `YYYY-MM-DD`
format, and `confirm=true`.

## Read-only training context

The server exposes compact read-only tools for adapting future plans to completed work:

- `list_activities` lists activities in an inclusive date range, with an optional Garmin
  activity type filter;
- `get_activity_summary` returns planning metrics for one activity;
- `get_activity_splits` returns compact lap and interval metrics;
- `get_recovery_status` aggregates available sleep, HRV, Body Battery, Training Readiness,
  and Training Status for one date.

Activity responses intentionally omit GPS coordinates and owner details. Recovery metrics
vary by Garmin device, account, and date. A missing source is returned as unavailable and
does not make the entire recovery request fail.

## Checks

```bash
docker build --target test -t personal-garmin-mcp:test .
docker run --rm --network none personal-garmin-mcp:test
# Or, with patched host SQLite:
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Tests do not contact Garmin or require credentials. Coverage includes workout
confirmation, pagination/retry, corrections, typed features, partial source failures,
concurrency, HTTP schemas, and backup/restore.

## Important limitations

- This is not an official Garmin Training API.
- Frequent logins may receive HTTP 429 responses; reuse saved tokens.
- Tokens provide access to Garmin Connect and must be protected like a password.
- If creation succeeds but scheduling fails, the tool returns `created_not_scheduled` and
  `workout_id`; the created workout remains in the library.
- Before regular use, test one simple workout in Garmin Connect and on the specific watch
  model.
