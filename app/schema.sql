CREATE TABLE IF NOT EXISTS families (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parent_sessions (
    token_hash TEXT PRIMARY KEY,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS children (
    id UUID PRIMARY KEY,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    nickname TEXT NOT NULL,
    age_band TEXT NOT NULL CHECK (age_band IN ('5-6', '7-8', '9-10')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (family_id, id)
);

CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    family_id UUID REFERENCES families(id) ON DELETE CASCADE,
    paired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS family_selections (
    family_id UUID PRIMARY KEY REFERENCES families(id) ON DELETE CASCADE,
    child_id UUID NOT NULL,
    subject_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (family_id, child_id) REFERENCES children(family_id, id)
);

CREATE TABLE IF NOT EXISTS learning_sessions (
    id UUID PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    selection_snapshot JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS children_family_idx ON children(family_id);
CREATE INDEX IF NOT EXISTS devices_family_idx ON devices(family_id);
CREATE INDEX IF NOT EXISTS pairing_codes_device_idx ON pairing_codes(device_id);
