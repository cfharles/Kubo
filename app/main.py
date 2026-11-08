"""Parent choice, paired cubes and recoverable tutoring sessions."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import database, sessions
from .catalog import SUBJECTS, find_goal
from .security import (
    new_pairing_code,
    new_token,
    normalize_code,
    password_hash,
    password_matches,
    token_hash,
)

COOKIE_NAME = "kubo_parent"
SESSION_SECONDS = 30 * 24 * 60 * 60
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParentCredentials(Body):
    family_name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("family_name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Family name must contain at least two characters.")
        return value


class NewChild(Body):
    nickname: str = Field(min_length=1, max_length=40)
    age_band: Literal["5-6", "7-8", "9-10"]

    @field_validator("nickname")
    @classmethod
    def clean_nickname(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a nickname.")
        return value


class NewDevice(Body):
    name: str = Field(default="Mac cube", min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a device name.")
        return value


class PairDevice(Body):
    code: str = Field(min_length=1, max_length=32)


class Selection(Body):
    child_id: UUID
    subject_id: str = Field(max_length=40)
    goal_id: str = Field(max_length=80)


def get_db():
    # One transaction per request; failed requests roll back completely.
    with database.connect() as db:
        yield db


def parent_family(request: Request, db=Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    family = None
    if token:
        family = db.execute(
            """SELECT f.id, f.name FROM parent_sessions s
               JOIN families f ON f.id = s.family_id
               WHERE s.token_hash = %s AND s.expires_at > now()""",
            (token_hash(token),),
        ).fetchone()
    if not family:
        raise HTTPException(401, "Sign in to your family to continue.")
    return family


def cube_device(request: Request, db=Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    device = None
    if scheme.lower() == "bearer" and token:
        device = db.execute(
            "SELECT id, name, family_id FROM devices WHERE token_hash = %s",
            (token_hash(token),),
        ).fetchone()
    if not device:
        raise HTTPException(401, "Set up this cube simulator to continue.")
    return device


def public_family(family):
    return {"id": str(family["id"]), "name": family["name"]}


def public_device(device):
    return {"id": str(device["id"]), "name": device["name"]}


def selection_for(db, family_id):
    row = db.execute(
        """SELECT c.id, c.nickname, c.age_band, s.subject_id, s.goal_id
           FROM family_selections s JOIN children c
           ON c.id = s.child_id AND c.family_id = s.family_id
           WHERE s.family_id = %s""",
        (family_id,),
    ).fetchone()
    if row is None:
        return None
    catalog_entry = find_goal(row["subject_id"], row["goal_id"])
    if catalog_entry is None:
        raise HTTPException(409, "Ask your parent to choose a current learning goal.")
    subject, goal = catalog_entry
    return {
        "child": {"id": str(row["id"]), "nickname": row["nickname"], "age_band": row["age_band"]},
        "subject": subject,
        "goal": goal,
    }


def set_parent_session(db, family_id, response, previous_token=None):
    if previous_token:
        db.execute("DELETE FROM parent_sessions WHERE token_hash = %s", (token_hash(previous_token),))
    token = new_token()
    db.execute(
        "INSERT INTO parent_sessions (token_hash, family_id, expires_at) VALUES (%s, %s, %s)",
        (token_hash(token), family_id, datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)),
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=os.environ.get("KUBO_COOKIE_SECURE") == "1",
        samesite="strict",
        path="/",
    )


def issue_pairing_code(db, device_id):
    # Callers hold the device lock or have just inserted this device.
    db.execute(
        "UPDATE pairing_codes SET consumed_at = now() WHERE device_id = %s AND consumed_at IS NULL",
        (device_id,),
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    for _ in range(8):
        code = new_pairing_code()
        inserted = db.execute(
            """INSERT INTO pairing_codes (code_hash, device_id, expires_at)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING code_hash""",
            (token_hash(normalize_code(code)), device_id, expires_at),
        ).fetchone()
        if inserted:
            return {"pairing_code": code, "expires_at": expires_at}
    raise HTTPException(503, "Could not create a pairing code. Please try again.")


@asynccontextmanager
async def lifespan(app):
    database.initialize()
    yield


def create_app():
    app = FastAPI(title="Kubo", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin_and_no_cache(request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if (origin is not None and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Use the Kubo app at this server address."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request, exc):
        return JSONResponse({"detail": "Kubo cannot reach its database. Check that PostgreSQL is running."}, status_code=503)

    @app.get("/api/health")
    def health(db=Depends(get_db)):
        db.execute("SELECT 1")
        return {"status": "ok", "database": "ok"}

    @app.get("/api/catalog")
    def catalog():
        return {"subjects": SUBJECTS}

    @app.post("/api/parents/register", status_code=201)
    def register(body: ParentCredentials, request: Request, response: Response, db=Depends(get_db)):
        family_id = uuid4()
        inserted = db.execute(
            """INSERT INTO families (id, name, name_key, password_hash) VALUES (%s, %s, %s, %s)
               ON CONFLICT (name_key) DO NOTHING RETURNING id, name""",
            (family_id, body.family_name, body.family_name.casefold(), password_hash(body.password)),
        ).fetchone()
        if not inserted:
            raise HTTPException(409, "That family name is already in use. Sign in or choose another name.")
        set_parent_session(db, family_id, response, request.cookies.get(COOKIE_NAME))
        return {"family": public_family(inserted)}

    @app.post("/api/parents/login")
    def login(body: ParentCredentials, request: Request, response: Response, db=Depends(get_db)):
        family = db.execute(
            "SELECT id, name, password_hash FROM families WHERE name_key = %s",
            (body.family_name.casefold(),),
        ).fetchone()
        if not family or not password_matches(body.password, family["password_hash"]):
            raise HTTPException(401, "Family name or password is incorrect.")
        set_parent_session(db, family["id"], response, request.cookies.get(COOKIE_NAME))
        return {"family": public_family(family)}

    @app.post("/api/parents/logout")
    def logout(request: Request, response: Response, db=Depends(get_db)):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            db.execute("DELETE FROM parent_sessions WHERE token_hash = %s", (token_hash(token),))
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
        return {"status": "signed_out"}

    @app.get("/api/parent")
    def get_parent(family=Depends(parent_family), db=Depends(get_db)):
        children = db.execute(
            "SELECT id, nickname, age_band FROM children WHERE family_id = %s ORDER BY created_at, id",
            (family["id"],),
        ).fetchall()
        devices = db.execute(
            "SELECT id, name FROM devices WHERE family_id = %s ORDER BY created_at, id", (family["id"],)
        ).fetchall()
        return {
            "family": public_family(family),
            "children": children,
            "devices": devices,
            "selection": selection_for(db, family["id"]),
        }

    @app.post("/api/children", status_code=201)
    def add_child(body: NewChild, family=Depends(parent_family), db=Depends(get_db)):
        child = db.execute(
            """INSERT INTO children (id, family_id, nickname, age_band) VALUES (%s, %s, %s, %s)
               RETURNING id, nickname, age_band""",
            (uuid4(), family["id"], body.nickname, body.age_band),
        ).fetchone()
        return {"child": child}

    @app.post("/api/selection")
    def save_selection(body: Selection, family=Depends(parent_family), db=Depends(get_db)):
        if find_goal(body.subject_id, body.goal_id) is None:
            raise HTTPException(422, "Choose a valid goal from the selected subject.")
        child = db.execute(
            "SELECT id FROM children WHERE id = %s AND family_id = %s", (body.child_id, family["id"])
        ).fetchone()
        if not child:
            raise HTTPException(404, "Choose a child from your family.")
        db.execute(
            """INSERT INTO family_selections (family_id, child_id, subject_id, goal_id)
               VALUES (%s, %s, %s, %s) ON CONFLICT (family_id) DO UPDATE SET
               child_id = EXCLUDED.child_id, subject_id = EXCLUDED.subject_id,
               goal_id = EXCLUDED.goal_id, updated_at = now()""",
            (family["id"], body.child_id, body.subject_id, body.goal_id),
        )
        return {"selection": selection_for(db, family["id"])}

    @app.post("/api/devices", status_code=201)
    def register_device(body: NewDevice, db=Depends(get_db)):
        device_id, token = uuid4(), new_token()
        db.execute(
            "INSERT INTO devices (id, name, token_hash) VALUES (%s, %s, %s)",
            (device_id, body.name, token_hash(token)),
        )
        return {"device_token": token, **issue_pairing_code(db, device_id)}

    @app.get("/api/device")
    def get_device(device=Depends(cube_device), db=Depends(get_db)):
        family = None
        if device["family_id"]:
            family = public_family(db.execute("SELECT id, name FROM families WHERE id = %s", (device["family_id"],)).fetchone())
        return {"device": public_device(device), "paired": family is not None, "family": family}

    @app.post("/api/device/pairing-code")
    def renew_pairing_code(device=Depends(cube_device), db=Depends(get_db)):
        locked = db.execute("SELECT family_id FROM devices WHERE id = %s FOR UPDATE", (device["id"],)).fetchone()
        if locked["family_id"]:
            raise HTTPException(409, "This cube is already paired with a family.")
        return issue_pairing_code(db, device["id"])

    @app.post("/api/pair")
    def pair(body: PairDevice, family=Depends(parent_family), db=Depends(get_db)):
        # Lock the device before its code, matching the renewal endpoint's lock order.
        code_hash = token_hash(normalize_code(body.code))
        row = db.execute("SELECT device_id FROM pairing_codes WHERE code_hash = %s", (code_hash,)).fetchone()
        if not row:
            raise HTTPException(400, "That pairing code is invalid or expired. Get a new code on the cube.")
        device = db.execute("SELECT id, name, family_id FROM devices WHERE id = %s FOR UPDATE", (row["device_id"],)).fetchone()
        code = db.execute(
            "SELECT consumed_at, expires_at > now() AS valid FROM pairing_codes WHERE code_hash = %s FOR UPDATE",
            (code_hash,),
        ).fetchone()
        if code["consumed_at"] or not code["valid"]:
            raise HTTPException(400, "That pairing code is invalid or expired. Get a new code on the cube.")
        if device["family_id"]:
            raise HTTPException(409, "This cube is already paired with a family.")
        db.execute("UPDATE devices SET family_id = %s, paired_at = now() WHERE id = %s", (family["id"], device["id"]))
        db.execute("UPDATE pairing_codes SET consumed_at = now() WHERE code_hash = %s", (code_hash,))
        return {"device": public_device(device)}

    @app.post("/api/device/start", status_code=201)
    def start(body: sessions.StartCommand, request: Request, device=Depends(cube_device), db=Depends(get_db)):
        return sessions.start(db, device, body, request.headers["authorization"].partition(" ")[2], selection_for)

    @app.get("/api/device/session")
    def active_session(device=Depends(cube_device), db=Depends(get_db)):
        return {"session": sessions.visible(sessions.active(db, device))}

    @app.get("/api/device/session/{session_id}")
    def get_session(session_id: UUID, device=Depends(cube_device), db=Depends(get_db)):
        return {"session": sessions.visible(sessions.owned(db, device, session_id))}

    @app.get("/api/device/receipts/{request_id}")
    def get_receipt(request_id: UUID, device=Depends(cube_device), db=Depends(get_db)):
        stored = sessions.receipt(db, device, request_id)
        if not stored:
            raise HTTPException(404, "This command has no completed receipt on this cube.")
        return stored["response"]

    @app.post("/api/device/session/{session_id}/action")
    def act(session_id: UUID, body: sessions.Action, request: Request, device=Depends(cube_device), db=Depends(get_db)):
        return sessions.action(db, device, session_id, body, request.headers["authorization"].partition(" ")[2])

    @app.get("/api/children/{child_id}/reports")
    def child_reports(child_id: UUID, family=Depends(parent_family), db=Depends(get_db)):
        return sessions.reports(db, family, child_id)

    @app.get("/api/device/session/{session_id}/audio")
    def session_audio(session_id: UUID, revision: int = Query(ge=0), device=Depends(cube_device), db=Depends(get_db)):
        from .speech import speak
        row = sessions.owned(db, device, session_id)
        sessions.ensure_current(row, revision)
        state = row["lesson_state"]
        if not state or not state.get("prompt"):
            raise HTTPException(409, "There is no spoken prompt for this session.")
        return Response(speak(state["prompt"]), media_type="audio/wav")

    @app.post("/api/device/session/{session_id}/transcribe")
    async def session_transcribe(session_id: UUID, request: Request, device=Depends(cube_device), db=Depends(get_db)):
        from .speech import transcribe
        row = await run_in_threadpool(sessions.owned, db, device, session_id)
        if row["ended_at"] or not row["lesson_state"]:
            raise HTTPException(409, "Start a spoken lesson before recording an answer.")
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "audio/wav":
            raise HTTPException(415, "Send a mono 16 kHz PCM WAV recording.")
        wav = bytearray()
        async for chunk in request.stream():
            if len(wav) + len(chunk) > 1_000_000:
                raise HTTPException(413, "That recording is too long. Try a shorter answer.")
            wav.extend(chunk)
        return await run_in_threadpool(transcribe, bytes(wav))

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/parent")

    @app.get("/parent", include_in_schema=False)
    def parent_page():
        return FileResponse(WEB_ROOT / "parent.html")

    @app.get("/cube", include_in_schema=False)
    def cube_page():
        return FileResponse(WEB_ROOT / "cube.html")

    app.mount("/static", StaticFiles(directory=WEB_ROOT, check_dir=False), name="static")
    return app


app = create_app()
