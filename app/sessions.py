"""Transactional session commands: one active session and durable command receipts per cube."""

import hashlib
import hmac
import json
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StartCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class Action(StartCommand):
    revision: int = Field(ge=0)
    kind: Literal["answer", "hint", "repeat", "stop", "confirm", "reject"]
    text: str | None = Field(default=None, max_length=600)
    confirmed: bool | None = None

    @model_validator(mode="after")
    def check_answer(self):
        if self.kind == "answer":
            if self.confirmed is not True or not self.text or not self.text.strip():
                raise ValueError("Check the answer before sending it to Kubo.")
        elif self.text is not None or self.confirmed is not None:
            raise ValueError("Only a checked answer accepts text or confirmation.")
        return self


def visible(row):
    if row is None:
        return None
    # A model's short working paraphrase can resemble the utterance; keep it
    # only in the active row and never copy it into a public command receipt.
    lesson = row["lesson_state"]
    if lesson is not None:
        lesson = {k: v for k, v in lesson.items() if k != "prior_idea"}
    return {
        "id": str(row["id"]),
        **row["selection_snapshot"],
        "started_at": row["started_at"].isoformat(),
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "revision": row["revision"],
        "lesson": lesson,
        "report": row["report"],
    }


def active(db, device):
    return db.execute(
        """SELECT * FROM learning_sessions
           WHERE device_id = %s AND family_id = %s AND ended_at IS NULL""",
        (device["id"], device["family_id"]),
    ).fetchone()


def owned(db, device, session_id):
    row = db.execute(
        "SELECT * FROM learning_sessions WHERE id = %s AND device_id = %s AND family_id = %s",
        (session_id, device["id"], device["family_id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "This session was not found on this cube.")
    return row


def receipt(db, device, request_id):
    return db.execute(
        "SELECT request_digest, response FROM device_request_receipts WHERE device_id = %s AND request_id = %s",
        (device["id"], request_id),
    ).fetchone()


def begin_command(db, device, command, operation, device_secret):
    # Identical concurrent deliveries wait for the first commit. Other commands
    # (especially Stop) can proceed while this command waits on a model.
    key = hashlib.sha256(f"{device['id']}:{command.request_id}".encode()).digest()[:8]
    db.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (int.from_bytes(key, "big", signed=True),))
    serialized = json.dumps([operation, command.model_dump(mode="json")], sort_keys=True, separators=(",", ":"))
    # The digest binds retries to one payload without retaining the utterance or
    # enabling dictionary checks from receipt contents alone.
    digest = hmac.new(device_secret.encode(), serialized.encode(), hashlib.sha256).hexdigest()
    saved = receipt(db, device, command.request_id)
    if saved and not hmac.compare_digest(saved["request_digest"], digest):
        raise HTTPException(409, "This request ID was already used for a different command.")
    return digest, saved["response"] if saved else None


def finish_command(db, device, command, digest, row):
    result = {"session": visible(row)}
    db.execute(
        """INSERT INTO device_request_receipts(device_id, request_id, request_digest, response)
           VALUES (%s, %s, %s, %s)""",
        (device["id"], command.request_id, digest, Jsonb(result)),
    )
    return result


def previous_report(db, family_id, selection):
    row = db.execute(
        """SELECT report FROM learning_sessions WHERE family_id = %s AND report IS NOT NULL
           AND selection_snapshot->'child'->>'id' = %s
           AND selection_snapshot->'goal'->>'id' = %s ORDER BY ended_at DESC, id DESC LIMIT 1""",
        (family_id, selection["child"]["id"], selection["goal"]["id"]),
    ).fetchone()
    return row["report"] if row else None


def start(db, device, command, device_secret, selection_for):
    from .lesson import new_lesson

    digest, saved = begin_command(db, device, command, "start", device_secret)
    if saved:
        return saved
    db.execute("SELECT id FROM devices WHERE id = %s FOR UPDATE", (device["id"],))
    if not device["family_id"]:
        raise HTTPException(409, "Pair this cube with your family before starting.")
    row = active(db, device)
    if row is None:
        selection = selection_for(db, device["family_id"])
        if selection is None:
            raise HTTPException(409, "Ask your parent to choose a child and learning goal.")
        state = new_lesson(selection, previous_report(db, device["family_id"], selection))
        row = db.execute(
            """INSERT INTO learning_sessions(id, device_id, family_id, selection_snapshot, lesson_state)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (uuid4(), device["id"], device["family_id"], Jsonb(selection), Jsonb(state) if state is not None else None),
        ).fetchone()
    return finish_command(db, device, command, digest, row)


def ensure_current(row, revision):
    if row["ended_at"]:
        raise HTTPException(409, "This session has ended. Reload the cube to see its current state.")
    if row["revision"] != revision:
        raise HTTPException(409, "The cube has a newer turn. Reload it before trying again.")


def scrub_answer(db, device, row, text):
    if text is None:
        return None
    names = [r["nickname"] for r in db.execute("SELECT nickname FROM children WHERE family_id = %s", (device["family_id"],))]
    names.append(db.execute("SELECT name FROM families WHERE id = %s", (device["family_id"],)).fetchone()["name"])
    names.append(row["selection_snapshot"]["child"]["nickname"])
    for name in sorted(set(names), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", "[name]", text, flags=re.IGNORECASE)
    return text.strip()


def action(db, device, session_id, command, device_secret):
    from .lesson import transition

    digest, saved = begin_command(db, device, command, str(session_id), device_secret)
    if saved:
        return saved
    row = owned(db, device, session_id)
    ensure_current(row, command.revision)
    state, report, ended = row["lesson_state"], None, False
    if command.kind == "repeat":
        if state is None:
            raise HTTPException(409, "There is no spoken lesson for this goal yet.")
    elif state is None:
        if command.kind != "stop":
            raise HTTPException(409, "There is no spoken lesson for this goal yet.")
        ended = True
    else:
        text = scrub_answer(db, device, row, command.text)
        try:
            state, report, ended = transition(state, command.kind, text)
        except ValueError:
            raise HTTPException(422, "That action does not fit the current question. Check the cube prompt.") from None
    # Take the shared mutation lock only AFTER the potentially slow model call.
    # Stop/other actions can win while recognition or interpretation is pending.
    db.execute("SELECT id FROM devices WHERE id = %s FOR UPDATE", (device["id"],))
    row = owned(db, device, session_id)
    ensure_current(row, command.revision)
    if command.kind != "repeat":
        row = db.execute(
            """UPDATE learning_sessions SET revision = revision + 1, lesson_state = %s, report = %s,
               ended_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE id = %s RETURNING *""",
            (Jsonb(state) if state is not None else None, Jsonb(report) if report is not None else None, ended, session_id),
        ).fetchone()
    return finish_command(db, device, command, digest, row)


def reports(db, family, child_id):
    child = db.execute("SELECT id FROM children WHERE family_id = %s AND id = %s", (family["id"], child_id)).fetchone()
    if child is None:
        raise HTTPException(404, "Choose a child from your family.")
    rows = db.execute(
        """SELECT id, ended_at, report FROM learning_sessions WHERE family_id = %s
           AND report IS NOT NULL AND selection_snapshot->'child'->>'id' = %s ORDER BY ended_at DESC, id DESC""",
        (family["id"], str(child_id)),
    )
    return {"reports": [
        {**r["report"], "session_id": str(r["id"]), "child_id": str(child_id), "ended_at": r["ended_at"].isoformat()}
        for r in rows
    ]}
