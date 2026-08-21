# Personal Garmin MCP

A local MCP server for creating and scheduling structured workouts in Garmin Connect
through **unofficial** Garmin endpoints.

> This is an experimental personal project. Garmin may change the endpoints, rate-limit
> requests, or revoke a session without notice. Do not expose the server to the internet
> without adding separate authentication.

## Features

- verifies a saved Garmin session;
- previews Garmin JSON without making changes;
- creates workouts and optionally adds them to the calendar;
- lists the workout library and calendar;
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

The build installs Python dependencies with `uv` strictly from `uv.lock`.

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

The endpoint is available at `http://127.0.0.1:8000/mcp`. To use a different local port:

```bash
GARMIN_MCP_PORT=8765 docker compose up -d garmin-mcp
```

View logs or stop the server:

```bash
docker compose logs -f garmin-mcp
docker compose down
```

`docker compose down` preserves the Garmin tokens. Running `docker compose down -v`
deletes the volume and its tokens.

### Local installation without Docker

Python 3.12+ and `uv` are required.

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
export GARMIN_MCP_PORT=8000
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp
```

Endpoint: `http://127.0.0.1:8000/mcp`.

ChatGPT must be able to reach the endpoint over HTTPS. A protected tunnel can be used for
personal testing. Do not expose this MVP directly to the internet: it intentionally does
not include a separate OAuth resource server for access to the MCP server itself.

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

## Checks

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Unit tests do not contact Garmin and do not require credentials.

## Important limitations

- This is not an official Garmin Training API.
- Frequent logins may receive HTTP 429 responses; reuse saved tokens.
- Tokens provide access to Garmin Connect and must be protected like a password.
- If creation succeeds but scheduling fails, the tool returns `created_not_scheduled` and
  `workout_id`; the created workout remains in the library.
- Before regular use, test one simple workout in Garmin Connect and on the specific watch
  model.
