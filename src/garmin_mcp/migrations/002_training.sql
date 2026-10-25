-- Full, typed plan snapshots; operational records retain their assessed revision.
CREATE TABLE training_cycles (
 account_id TEXT NOT NULL, id TEXT NOT NULL, current_revision_id TEXT,
 status TEXT NOT NULL CHECK(status IN ('draft','active','completed','cancelled')),
 created_at TEXT NOT NULL, PRIMARY KEY(account_id,id),
 FOREIGN KEY(account_id,id,current_revision_id)
 REFERENCES plan_revisions(account_id,cycle_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX one_active_training_cycle ON training_cycles(account_id) WHERE status='active';
CREATE TABLE plan_revisions (
 account_id TEXT NOT NULL, cycle_id TEXT NOT NULL, id TEXT NOT NULL,
 number INTEGER NOT NULL, parent_revision_id TEXT, base_revision_id TEXT,
 status TEXT NOT NULL CHECK(status IN ('draft','accepted','superseded','discarded')),
 version INTEGER NOT NULL DEFAULT 1, author TEXT NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, accepted_at TEXT,
 override_explanation TEXT, source_capture_ids_json TEXT NOT NULL,
 PRIMARY KEY(account_id,cycle_id,id), UNIQUE(account_id,cycle_id,number),
 FOREIGN KEY(account_id,cycle_id) REFERENCES training_cycles(account_id,id),
 FOREIGN KEY(account_id,cycle_id,parent_revision_id) REFERENCES plan_revisions(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id,base_revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE TABLE training_commands (
 account_id TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(account_id,key)
);
CREATE TABLE cycle_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 start_date TEXT NOT NULL,
 end_date TEXT NOT NULL,
 timezone TEXT,
 goal_type TEXT,
 target_time_s REAL,
 goal_description TEXT,
 name TEXT NOT NULL,
 status TEXT,
 main_event_id TEXT,
 principles TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE TRIGGER immutable_cycle_versions_insert BEFORE INSERT ON cycle_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_cycle_versions_update BEFORE UPDATE ON cycle_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_cycle_versions_delete BEFORE DELETE ON cycle_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE event_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 timezone TEXT,
 goal_type TEXT,
 target_time_s REAL,
 goal_description TEXT,
 id TEXT NOT NULL,
 name TEXT NOT NULL,
 date TEXT,
 distance_m REAL,
 elevation_m REAL,
 role TEXT,
 participation TEXT,
 notes TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE TRIGGER immutable_event_versions_insert BEFORE INSERT ON event_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_event_versions_update BEFORE UPDATE ON event_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_event_versions_delete BEFORE DELETE ON event_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE constraint_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 start_date TEXT NOT NULL,
 end_date TEXT NOT NULL,
 id TEXT NOT NULL,
 name TEXT NOT NULL,
 available_days_json TEXT,
 daily_budget_s REAL,
 weekly_budget_s REAL,
 preferences TEXT,
 limitations TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE TRIGGER immutable_constraint_versions_insert BEFORE INSERT ON constraint_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_constraint_versions_update BEFORE UPDATE ON constraint_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_constraint_versions_delete BEFORE DELETE ON constraint_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE phase_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 start_date TEXT NOT NULL,
 end_date TEXT NOT NULL,
 id TEXT NOT NULL,
 name TEXT NOT NULL,
 objective TEXT,
 explanation TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE TRIGGER immutable_phase_versions_insert BEFORE INSERT ON phase_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_phase_versions_update BEFORE UPDATE ON phase_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_phase_versions_delete BEFORE DELETE ON phase_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE week_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 distance_min_m REAL,
 distance_max_m REAL,
 duration_min_s REAL,
 duration_max_s REAL,
 pace_min_s_per_km REAL,
 pace_max_s_per_km REAL,
 heart_rate_min_bpm REAL,
 heart_rate_max_bpm REAL,
 elevation_min_m REAL,
 elevation_max_m REAL,
 id TEXT NOT NULL,
 start_date TEXT NOT NULL,
 phase_id TEXT,
 detail TEXT,
 focus TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id,phase_id) REFERENCES phase_versions(account_id,cycle_id,revision_id,id)
);
CREATE TRIGGER immutable_week_versions_insert BEFORE INSERT ON week_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_week_versions_update BEFORE UPDATE ON week_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_week_versions_delete BEFORE DELETE ON week_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE session_versions (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 revision_id TEXT NOT NULL,
 timezone TEXT,
 distance_min_m REAL,
 distance_max_m REAL,
 duration_min_s REAL,
 duration_max_s REAL,
 pace_min_s_per_km REAL,
 pace_max_s_per_km REAL,
 heart_rate_min_bpm REAL,
 heart_rate_max_bpm REAL,
 elevation_min_m REAL,
 elevation_max_m REAL,
 id TEXT NOT NULL,
 week_id TEXT NOT NULL,
 date TEXT NOT NULL,
 start_time TEXT,
 order_in_day INTEGER,
 kind TEXT NOT NULL,
 sport TEXT,
 purpose TEXT,
 certainty TEXT,
 state TEXT,
 structure_json TEXT,
 variants_json TEXT,
 selected_variant_id TEXT,
 replaces_session_id TEXT,
 PRIMARY KEY(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id) REFERENCES plan_revisions(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id,revision_id,week_id) REFERENCES week_versions(account_id,cycle_id,revision_id,id)
);
CREATE TRIGGER immutable_session_versions_insert BEFORE INSERT ON session_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_session_versions_update BEFORE UPDATE ON session_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=NEW.account_id
 AND cycle_id=NEW.cycle_id AND id=NEW.revision_id) != 'draft'
OR (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TRIGGER immutable_session_versions_delete BEFORE DELETE ON session_versions
WHEN (SELECT status FROM plan_revisions WHERE account_id=OLD.account_id
 AND cycle_id=OLD.cycle_id AND id=OLD.revision_id) != 'draft'
BEGIN SELECT RAISE(ABORT,'Accepted/discarded plan snapshots are immutable'); END;
CREATE TABLE session_outcomes (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 session_revision_id TEXT NOT NULL,
 status TEXT,
 actual_date TEXT NOT NULL,
 metrics_source TEXT,
 duration_s REAL,
 distance_m REAL,
 deviation_reason TEXT,
 comment TEXT,
 version INTEGER NOT NULL,
 author TEXT NOT NULL,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(account_id,cycle_id,session_id),
 FOREIGN KEY(account_id,cycle_id) REFERENCES training_cycles(account_id,id),
 FOREIGN KEY(account_id,cycle_id,session_revision_id,session_id) REFERENCES session_versions(account_id,cycle_id,revision_id,id)
);
CREATE TABLE activity_matches (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 session_revision_id TEXT NOT NULL,
 activity_id INTEGER NOT NULL,
 state TEXT,
 basis TEXT NOT NULL,
 allocated_duration_s REAL,
 allocated_distance_m REAL,
 source_distance_m REAL,
 source_duration_s REAL,
 source_start_time_utc TEXT,
 source_local_date TEXT,
 source_timezone TEXT,
 source_capture_ids_json TEXT NOT NULL,
 version INTEGER NOT NULL,
 author TEXT NOT NULL,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id) REFERENCES training_cycles(account_id,id),
 FOREIGN KEY(account_id,cycle_id,session_revision_id,session_id) REFERENCES session_versions(account_id,cycle_id,revision_id,id),
 FOREIGN KEY(account_id,activity_id) REFERENCES activities(account_id,activity_id),
 UNIQUE(account_id,cycle_id,session_id,activity_id)
);
CREATE TABLE training_feedback (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 id TEXT NOT NULL,
 date TEXT NOT NULL,
 session_id TEXT,
 effort INTEGER,
 wellbeing TEXT,
 comment TEXT,
 version INTEGER NOT NULL,
 author TEXT NOT NULL,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id) REFERENCES training_cycles(account_id,id)
);
CREATE TABLE training_reviews (
 account_id TEXT NOT NULL,
 cycle_id TEXT NOT NULL,
 start_date TEXT NOT NULL,
 end_date TEXT NOT NULL,
 id TEXT NOT NULL,
 plan_revision_id TEXT NOT NULL,
 proposed_revision_id TEXT,
 source_capture_ids_json TEXT,
 conclusions TEXT NOT NULL,
 limitations TEXT,
 version INTEGER NOT NULL,
 author TEXT NOT NULL,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'current',
 PRIMARY KEY(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id) REFERENCES training_cycles(account_id,id),
 FOREIGN KEY(account_id,cycle_id,plan_revision_id) REFERENCES plan_revisions(account_id,cycle_id,id),
 FOREIGN KEY(account_id,cycle_id,proposed_revision_id) REFERENCES plan_revisions(account_id,cycle_id,id)
);
CREATE INDEX training_sessions_date ON session_versions(account_id,cycle_id,revision_id,date);
CREATE INDEX training_matches_activity ON activity_matches(account_id,activity_id);
CREATE TRIGGER training_activity_corrected AFTER UPDATE ON activities
WHEN OLD.local_date IS NOT NEW.local_date OR OLD.start_time_utc IS NOT NEW.start_time_utc
 OR OLD.start_time_local IS NOT NEW.start_time_local OR OLD.timezone IS NOT NEW.timezone
 OR OLD.sport IS NOT NEW.sport OR OLD.distance_m IS NOT NEW.distance_m
 OR OLD.duration_s IS NOT NEW.duration_s
BEGIN
 INSERT INTO audit_log(happened_at,account_id,action,detail_json)
 SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),NEW.account_id,'training_match_invalidated',
 json_object('id',id,'cycle_id',cycle_id,'before_state',state,'after_state','needs_review',
 'activity_id',NEW.activity_id,'reason','Garmin source corrected','author','import')
 FROM activity_matches WHERE account_id=NEW.account_id AND activity_id=NEW.activity_id
 AND state IN ('confirmed','suggested');
 UPDATE activity_matches SET state='needs_review',version=version+1,
 updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 WHERE account_id=NEW.account_id AND activity_id=NEW.activity_id
 AND state IN ('confirmed','suggested');
 UPDATE training_reviews SET state='needs_review',version=version+1
 WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER training_source_corrected AFTER UPDATE ON sources
WHEN OLD.normalized_json IS NOT NEW.normalized_json OR OLD.status IS NOT NEW.status
BEGIN
 INSERT INTO audit_log(happened_at,account_id,action,detail_json)
 SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),NEW.account_id,'training_match_invalidated',
 json_object('id',id,'cycle_id',cycle_id,'before_state',state,'after_state','needs_review',
 'activity_id',activity_id,'reason','Garmin source corrected','author','import')
 FROM activity_matches WHERE account_id=NEW.account_id
 AND CAST(activity_id AS TEXT)=NEW.entity_id AND NEW.entity='activities'
 AND state IN ('confirmed','suggested');
 UPDATE activity_matches SET state='needs_review',version=version+1,
 updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 WHERE account_id=NEW.account_id AND CAST(activity_id AS TEXT)=NEW.entity_id
 AND NEW.entity='activities' AND state IN ('confirmed','suggested');
 UPDATE training_reviews SET state='needs_review',version=version+1
 WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER training_source_added AFTER INSERT ON sources
BEGIN
 UPDATE training_reviews SET state='needs_review',version=version+1
 WHERE account_id=NEW.account_id;
END;
CREATE TRIGGER training_review_invalidated AFTER UPDATE ON training_reviews
WHEN NEW.state='needs_review' AND NEW.version!=OLD.version
BEGIN
 INSERT INTO audit_log(happened_at,account_id,action,detail_json)
 VALUES(strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),NEW.account_id,'training_review_invalidated',
 json_object('id',NEW.id,'cycle_id',NEW.cycle_id,'before_state',OLD.state,
 'after_state',NEW.state,'reason','Source data changed','author','import',
 'before_version',OLD.version,'after_version',NEW.version));
END;
