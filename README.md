# Personal Garmin MCP

A local Garmin training-data app with SQLite, immutable raw archives, request-driven
features, a read-only lookup UI, Swagger, and MCP. It also creates and schedules
structured workouts through **unofficial** Garmin endpoints.

> This is an experimental personal project. Garmin may change the endpoints, rate-limit
> requests, or revoke a session without notice. Do not expose the server to the internet
> without adding separate authentication.

## Important URLs and endpoints

These links work after starting Docker Compose or the server in `streamable-http`
mode. They use the default local port, `8765`; if you change `GARMIN_MCP_PORT`, use
that port in the URLs below.

| Page or endpoint | Clickable URL | Purpose |
| --- | --- | --- |
| **Lookup UI** | [http://127.0.0.1:8765/](http://127.0.0.1:8765/) | Browse saved activities and daily data. |
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
- provides a read-only lookup UI and Swagger alongside MCP;
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
- requires `confirm=true` for every change.

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
docker compose ps
```

Open the [lookup UI](http://127.0.0.1:8765/) or
[Swagger API docs](http://127.0.0.1:8765/docs). See
[Important URLs and endpoints](#important-urls-and-endpoints) for all quick links.
To use a different local port:

```bash
GARMIN_MCP_PORT=8766 docker compose up -d garmin-mcp
```

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
