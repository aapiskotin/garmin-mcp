CREATE TABLE activities (
    account_id TEXT NOT NULL,
    activity_id INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    start_time_local TEXT,
    start_time_utc TEXT,
    timezone TEXT NOT NULL,
    name TEXT,
    sport TEXT,
    distance_m REAL,
    duration_s REAL,
    moving_duration_s REAL,
    average_heart_rate_bpm REAL,
    max_heart_rate_bpm REAL,
    average_power_watts REAL,
    elevation_gain_m REAL,
    calories REAL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(account_id, activity_id)
);
CREATE INDEX activities_date ON activities(account_id, local_date);
CREATE TABLE days (
    account_id TEXT NOT NULL,
    local_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    fetched_at TEXT,
    PRIMARY KEY(account_id, local_date)
);
CREATE TABLE captures (
    capture_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    object_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE sources (
    account_id TEXT NOT NULL,
    entity TEXT NOT NULL CHECK(entity IN ('activities','days')),
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL,
    capture_id TEXT REFERENCES captures(capture_id),
    normalized_json TEXT,
    source_date TEXT,
    status TEXT NOT NULL,
    error TEXT,
    fetched_at TEXT,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(account_id, entity, entity_id, source)
);
CREATE TABLE sync_state (
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    cursor TEXT,
    coverage_start TEXT,
    status TEXT NOT NULL,
    error TEXT,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(account_id, source)
);
CREATE TABLE coverage (
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    boundary TEXT NOT NULL,
    PRIMARY KEY(account_id, source, start_date, end_date)
);
CREATE TABLE feature_definitions (
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    entity TEXT NOT NULL,
    sql_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    lookback_days INTEGER NOT NULL,
    code_hash TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    PRIMARY KEY(name, version)
);
CREATE TABLE feature_results (
    account_id TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    computed_at TEXT,
    source_refs_json TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(account_id, entity, entity_id, name),
    FOREIGN KEY(name, version) REFERENCES feature_definitions(name, version)
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    happened_at TEXT NOT NULL,
    account_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
CREATE TABLE write_context (purpose TEXT PRIMARY KEY);
CREATE TRIGGER source_changed AFTER UPDATE ON sources
WHEN OLD.normalized_json IS NOT NEW.normalized_json OR OLD.status IS NOT NEW.status
BEGIN
    UPDATE feature_results SET status='invalidated'
      WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER source_added AFTER INSERT ON sources
BEGIN
    UPDATE feature_results SET status='invalidated'
      WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER activity_changed AFTER UPDATE ON activities
WHEN NOT EXISTS(SELECT 1 FROM write_context WHERE purpose='features')
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=NEW.account_id;
    INSERT INTO audit_log(happened_at,account_id,action,detail_json)
      VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),NEW.account_id,
        'activity_update',json_object('activity_id',NEW.activity_id,
        'old_date',OLD.local_date,'new_date',NEW.local_date));
END;
CREATE TRIGGER day_changed AFTER UPDATE ON days
WHEN NOT EXISTS(SELECT 1 FROM write_context WHERE purpose='features')
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=NEW.account_id;
    INSERT INTO audit_log(happened_at,account_id,action,detail_json)
      VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),NEW.account_id,
        'day_update',json_object('local_date',NEW.local_date));
END;
CREATE TRIGGER source_deleted AFTER DELETE ON sources
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=OLD.account_id;
END;
CREATE TRIGGER activity_deleted AFTER DELETE ON activities
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=OLD.account_id;
    INSERT INTO audit_log(happened_at,account_id,action,detail_json)
      VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),OLD.account_id,
        'activity_delete',json_object('activity_id',OLD.activity_id,'local_date',OLD.local_date));
END;
CREATE TRIGGER day_deleted AFTER DELETE ON days
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=OLD.account_id;
END;
CREATE TRIGGER activity_added AFTER INSERT ON activities
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER day_added AFTER INSERT ON days
BEGIN
    UPDATE feature_results SET status='invalidated' WHERE account_id=NEW.account_id;
END;
