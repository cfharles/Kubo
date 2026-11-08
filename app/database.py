"""PostgreSQL is the single source of truth, including login and device sessions."""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL or launch Kubo with bash scripts/run.sh.")
    return psycopg.connect(
        url,
        row_factory=dict_row,
        connect_timeout=5,
    )


def initialize():
    with connect() as db:
        # Serialize startup DDL when development workers start together.
        db.execute("SELECT pg_advisory_xact_lock(623094721)")
        db.execute("""CREATE TABLE IF NOT EXISTS kubo_schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )""")
        applied = {r["version"] for r in db.execute("SELECT version FROM kubo_schema_migrations")}
        root = Path(__file__).parent
        migrations = [(1, root / "schema.sql"), (2, root / "migrations/002_tutoring_sessions.sql")]
        if applied - {version for version, _ in migrations}:
            raise RuntimeError("This database requires a newer version of Kubo.")
        for version, path in migrations:
            if version not in applied:
                db.execute(path.read_text())
                db.execute("INSERT INTO kubo_schema_migrations(version) VALUES (%s)", (version,))
