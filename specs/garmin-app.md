# Garmin app specification

Status: draft; requirements updated from user decisions.

- Agent is an active developer and direct SQLite user; it owns analysis,
  Python feature code/migrations, raw-object exploration, and training advice.
- Run everything on this machine via Docker Compose; use SQLite.
  Preserve existing tools and explicit confirmation for Garmin writes.

## Storage and identity

- Tables: activities(account_id, activity_id); days(account_id, local_date).
- Activity IDs are Garmin IDs; index local start date per account.
  Date corrections update the same activity and invalidate affected days.
- Prefer individual typed SQL columns for scalar features; JSON is for
  nested values/metadata where columns are impractical, not the default.
- Store source references, fetched/computed times, feature definition
  versions, and statuses; keep source coverage/cursors in sync_state.
- Local SQLite: patched WAL, short serialized writes, and busy timeout.
- Calendar timezone defaults to Europe/Berlin; preserve source dates/time
  context, including sleep dates; fetch/computation timestamps use UTC.
- Archive raw JSON before normalization as immutable local object files.
  Keep hashes/capture manifests and superseded versions; tokens stay separate.

## Requested retrieval and feature generation

- Garmin access and feature work require requests: no initial import,
  polling, background ingestion, eager features, or automatic backfill.
- Agent triggers polling/recalculation explicitly; refresh is synchronous.
- Refresh activity coverage since its last successful refresh through a
  fixed request-start boundary; paginate fully and deduplicate by ID.
- Recheck a configurable recent overlap for late uploads/corrections.
  Older history and missing lookback inputs are fetched only when requested.
- Fetch only requested activity details/splits and daily health sources.
  Support sleep, HRV, Body Battery, Training Readiness, and Training Status.
- Agent edits versioned Python modules/migrations in the project; definitions
  declare entity, type/unit, inputs/lookback; validate recalculated results.
- Python computes from archived objects; SQLite stores typed feature results.
- Apply atomic versioned migrations; compute only requested features/rows.
- Report missing inputs without silently downloading them; support dry-run.
  Invalidate dependent results after corrections; recompute when requested.
- Direct DB repairs use transactions, retain provenance, invalidate features.
- Store objects first; atomically commit rows, metadata, and coverage.
  Preserve unrelated columns; retries/resumes must be idempotent.
- Coalesce concurrent refreshes and bound retries/wait time; reuse tokens.
- Return explicit errors, partial coverage, missing sources, and staleness.
  The agent decides whether to retry, use cached data, or defer advice.
  Missing/unavailable is not zero; failed refreshes do not advance coverage.

## Local API and web UI

- MCP/HTTP control polling, recalculation, status, backup/restore, and
  Garmin actions; share services and preserve Swagger/OpenAPI documentation.
- Ship an agent skill covering filesystem paths, DB schema, object formats,
  Python modules/migrations, direct SQL, operational endpoints, and errors.
  Agent runtime needs local filesystem, database, and endpoint access.
- Read-only web UI: activity/day lookup by date/ID with freshness/status.
- Persist database, objects, tokens, and backups on this machine.
  Bind services locally; return compact MCP data without GPS/owner details.

## Daily backups and acceptance

- Back up once daily using SQLite's online backup API; time is configurable.
  Include referenced objects/manifests, definitions, migrations, checksums,
  and code/config IDs. Protect secret copies; restore only when requested.
- Verify integrity/source references on restore; never replay Garmin writes.
  Local copies cover accidental changes; host loss needs a second location.
- Acceptance: request-only Garmin work, separate tables, typed columns,
  incremental/resumable refresh, explicit failures, on-demand new features,
  correction invalidation, local Swagger/lookup UI, restore, workout tests.
