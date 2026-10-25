-- NULL preserves the all-sports meaning of existing plans.
ALTER TABLE constraint_versions ADD COLUMN sport TEXT;
ALTER TABLE week_versions ADD COLUMN sport TEXT;
