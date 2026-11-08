-- Runs once, under the startup advisory lock and in the migration transaction.
ALTER TABLE learning_sessions
    ADD COLUMN ended_at TIMESTAMPTZ,
    ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    ADD COLUMN lesson_state JSONB,
    ADD COLUMN report JSONB;
-- M1 stored a snapshot at Start and had no resumable active turn. Preserve each
-- historical snapshot, without treating one as an in-progress M2 lesson.
UPDATE learning_sessions SET ended_at = started_at;
CREATE UNIQUE INDEX learning_sessions_one_active_cube
    ON learning_sessions(device_id) WHERE ended_at IS NULL;
CREATE INDEX learning_sessions_child_reports
    ON learning_sessions(family_id, (selection_snapshot->'child'->>'id'), ended_at DESC)
    WHERE report IS NOT NULL;
CREATE TABLE device_request_receipts (
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    request_id UUID NOT NULL,
    request_digest TEXT NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, request_id)
);
