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
        db.execute(Path(__file__).with_name("schema.sql").read_text())
