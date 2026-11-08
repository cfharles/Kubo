"""Checkpoint 2 durability, authorization and race regressions on real PostgreSQL."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from app import database
from app.main import COOKIE_NAME, create_app
from app.security import new_token, token_hash
from test_api import child, choose, isolated_postgres, parent, provision, register


def key():
    return str(uuid4())


def start(cube, request_id=None):
    return cube.post("/api/device/start", json={"request_id": request_id or key()})


def act(cube, session, kind, request_id=None, **extra):
    return cube.post(f"/api/device/session/{session['id']}/action", json={
        "request_id": request_id or key(), "revision": session["revision"], "kind": kind, **extra,
    })


def session_of(response):
    assert response.is_success, response.text
    return response.json()["session"]


@pytest.fixture
def visit(parent):
    with TestClient(create_app()) as cube:
        device = provision(cube)
        assert parent.post("/api/pair", json={"code": device["pairing_code"]}).is_success
        learner = child(parent)
        choose(parent, learner, "english", "explain-a-characters-choice")
        yield parent, cube, learner, device


def test_racing_starts_share_one_session_and_late_receipts_replay_original_result(visit):
    phone, cube, _, device = visit
    original_id = key()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda k: session_of(start(cube, k)), [original_id, original_id, key()]))
    original = results[0]
    assert results == [original] * 3
    assert original["revision"] == 0 and original["lesson"]["phase"] == "opening"
    hint_key = key()
    hinted = session_of(act(cube, original, "hint", hint_key))
    assert hinted["revision"] == 1 and hinted["lesson"]["hint_used"]
    repeated = session_of(act(cube, hinted, "repeat"))
    assert repeated == hinted
    stopped = session_of(act(cube, hinted, "stop"))
    assert stopped["ended_at"] and stopped["report"]["outcome"] == "stopped"
    assert cube.get("/api/device/session").json()["session"] is None
    choose(phone, child(phone, "Zoe", "5-6"), "math", "share-equally")
    next_session = session_of(start(cube))
    assert next_session["id"] != original["id"]
    assert next_session["lesson"] is None
    # Out-of-order retries after a later start must return the original result.
    with TestClient(create_app()) as reopened:
        reopened.headers["Authorization"] = "Bearer " + device["device_token"]
        assert session_of(start(reopened, original_id)) == original
        assert session_of(act(reopened, original, "hint", hint_key)) == hinted
        assert session_of(reopened.get(f"/api/device/receipts/{hint_key}")) == hinted
        assert session_of(reopened.get(f"/api/device/session/{original['id']}")) == stopped
        assert session_of(reopened.get("/api/device/session")) == next_session
        assert act(reopened, original, "stop", hint_key).status_code == 409
        assert act(reopened, next_session, "hint").status_code == 409
    with database.connect() as db:
        assert db.execute("SELECT count(*) AS n FROM learning_sessions").fetchone()["n"] == 2


def test_dupe_answer_runs_provider_once_and_never_persists_audio_or_raw_answer(visit, monkeypatch):
    from app import tutor

    phone, cube, _, _ = visit
    child(phone, "Pip", "5-6")
    session = session_of(start(cube))
    raw = "Milo and Pip in Fern Family noticed the purple-umbrella-private detail."
    called = []

    def fake_provider(context):
        called.append(context.copy())
        return {"idea": "The learner noticed a weather clue.", "question": "How could that clue affect the drawing?"}

    monkeypatch.setattr(tutor, "followup", fake_provider)
    request_id = key()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: session_of(act(cube, session, "answer", request_id, confirmed=True, text=raw)), range(2)
        ))
    assert results[0] == results[1]
    assert "prior_idea" not in results[0]["lesson"]
    assert len(called) == 1
    assert not any(name in called[0]["answer"] for name in ["Milo", "Pip", "Fern Family"])
    assert called[0]["answer"].count("[name]") == 3
    assert results[0]["revision"] == 1 and results[0]["lesson"]["phase"] == "followup"
    assert act(cube, session, "answer", request_id, confirmed=True, text="different").status_code == 409
    with database.connect() as db:
        saved = db.execute("SELECT lesson_state, report FROM learning_sessions").fetchall()
        receipts = db.execute("SELECT request_digest, response FROM device_request_receipts").fetchall()
    assert "purple-umbrella-private" not in json.dumps([saved, receipts])


def test_stop_overtakes_slow_model_and_late_answer_cannot_revive_session(visit, monkeypatch):
    from app import tutor

    _, cube, _, _ = visit
    session = session_of(start(cube))
    entered, release = Event(), Event()

    def slow(_):
        entered.set()
        if not release.wait(5):
            raise AssertionError("Stop waited behind the provider.")
        return {"idea": "A weather clue.", "question": "Where do you see that?"}

    monkeypatch.setattr(tutor, "followup", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(act, cube, session, "answer", text="It might rain.", confirmed=True)
        try:
            assert entered.wait(3)
            stop_response = pool.submit(act, cube, session, "stop").result(timeout=2)
            stopped = session_of(stop_response)
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 409
    assert stopped["revision"] == 1
    assert stopped["report"]["outcome"] == "stopped"
    assert cube.get("/api/device/session").json() == {"session": None}


def test_failed_or_unconfirmed_answers_leave_the_turn_retryable(visit, monkeypatch):
    from app import tutor

    _, cube, _, _ = visit
    session = session_of(start(cube))
    args = {"text": "She might lose her picture.", "confirmed": False}
    assert act(cube, session, "answer", **args).status_code == 422
    args["confirmed"] = True
    request_id = key()
    def unavailable(_):
        raise HTTPException(503, "Local model is unavailable.")
    monkeypatch.setattr(tutor, "followup", unavailable)
    assert act(cube, session, "answer", request_id, **args).status_code == 503
    assert cube.get(f"/api/device/receipts/{request_id}").status_code == 404
    assert session_of(cube.get("/api/device/session")) == session
    monkeypatch.setattr(tutor, "followup", lambda _: {"idea": "Protect the picture.", "question": "Which detail helped?"})
    recovered = session_of(act(cube, session, "answer", request_id, **args))
    assert recovered["revision"] == session["revision"] + 1
    assert act(cube, session, "hint").status_code == 409


def test_only_current_owned_prompt_can_speak_and_recognition_does_not_advance(visit, monkeypatch):
    from app import speech

    _, cube, _, _ = visit
    session = session_of(start(cube))
    path = f"/api/device/session/{session['id']}"
    spoken = []
    def synth(text):
        spoken.append(text)
        return b"RIFF-fixture"
    monkeypatch.setattr(speech, "speak", synth)
    audio = cube.get(path + "/audio", params={"revision": 0, "text": "arbitrary text"})
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert spoken == [session["lesson"]["prompt"]]
    assert cube.get(path + "/audio?revision=8").status_code == 409
    assert cube.post(path + "/transcribe", content=b"bad").status_code == 415
    headers = {"Content-Type": "audio/wav"}
    assert cube.post(path + "/transcribe", content=b"broken", headers=headers).status_code == 422
    assert cube.post(path + "/transcribe", content=b"x"*1_000_001, headers=headers).status_code == 413
    monkeypatch.setattr(speech, "transcribe", lambda _: {"text": "private recognized words", "confidence": .9})
    transcript = cube.post(path + "/transcribe", content=b"bounded fixture", headers=headers)
    assert transcript.json()["text"] == "private recognized words"
    assert session_of(cube.get("/api/device/session")) == session
    assert session_of(act(cube, session, "stop"))["ended_at"]
    assert cube.get(path + "/audio?revision=0").status_code == 409
    assert cube.post(path + "/transcribe", content=b"bad", headers=headers).status_code == 409


def test_sessions_receipts_speech_and_reports_are_scoped_to_device_and_family(visit):
    phone, cube, learner, _ = visit
    original_id = key()
    session = session_of(start(cube, original_id))
    path = f"/api/device/session/{session['id']}"
    assert phone.get("/api/device/session").status_code == 401
    with TestClient(create_app()) as another_cube, TestClient(create_app()) as other_phone:
        another = provision(another_cube)
        assert phone.post("/api/pair", json={"code": another["pairing_code"]}).is_success
        assert another_cube.get(path).status_code == 404
        assert another_cube.get(f"/api/device/receipts/{original_id}").status_code == 404
        assert another_cube.get(path + "/audio?revision=0").status_code == 404
        assert another_cube.post(path + "/transcribe", content=b"bad").status_code == 404
        assert act(another_cube, session, "stop").status_code == 404
        register(other_phone, "Different Family")
        assert other_phone.get(f"/api/children/{learner['id']}/reports").status_code == 404
        assert cube.get(f"/api/children/{learner['id']}/reports").status_code == 401


def test_correction_confirmed_report_and_child_specific_next_review(visit, monkeypatch):
    from app import tutor

    phone, cube, learner, _ = visit
    monkeypatch.setattr(tutor, "followup", lambda _: {"idea": "A possible motive.", "question": "Which detail supports it?"})
    assessments = iter([
        {"observation": "An idea the learner rejected.", "demonstrated": False},
        {"observation": "Linked the rain clue to keeping a drawing dry.", "demonstrated": True},
    ])
    monkeypatch.setattr(tutor, "assess", lambda _: next(assessments))
    session = session_of(start(cube))
    session = session_of(act(cube, session, "hint"))
    session = session_of(act(cube, session, "answer", text="For the paper.", confirmed=True))
    session = session_of(act(cube, session, "answer", text="It was wet.", confirmed=True))
    assert session["lesson"]["phase"] == "confirm"
    session = session_of(act(cube, session, "reject"))
    assert not session["report"] and session["lesson"]["phase"] == "followup"
    session = session_of(act(cube, session, "answer", text="It was the raindrop.", confirmed=True))
    confirmation = key()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: session_of(act(cube, session, "confirm", confirmation)), range(2)))
    assert results[0] == results[1] and results[0]["ended_at"]
    reports = phone.get(f"/api/children/{learner['id']}/reports").json()["reports"]
    assert len(reports) == 1
    assert reports[0]["session_id"] == session["id"] and reports[0]["child_id"] == learner["id"]
    assert reports[0]["hint_used"] and reports[0]["observation"].startswith("Linked the rain clue")
    assert reports[0]["home_question"] and reports[0]["next_review"] and reports[0]["outcome"] == "demonstrated"
    returning = session_of(start(cube))
    assert returning["lesson"]["story_id"] != session["lesson"]["story_id"]
    assert returning["lesson"]["review_focus"]
    session_of(act(cube, returning, "stop"))
    choose(phone, child(phone, "Nora"), "english", "explain-a-characters-choice")
    separate = session_of(start(cube))
    assert separate["lesson"]["story_id"] == session["lesson"]["story_id"]
    assert not separate["lesson"]["review_focus"]


def test_upgrades_real_m1_schema_with_existing_credentials_pairing_and_snapshot(monkeypatch):
    # Use a private schema *within kubo_test*. The baseline is the exact SQL
    # from checkpoint 1; no M2 tables/columns or version row exist at this point.
    schema = "upgrade_" + uuid4().hex
    baseline = Path(database.__file__).with_name("schema.sql").read_text()
    base_url = os.environ["DATABASE_URL"]
    with database.connect() as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    monkeypatch.setenv("DATABASE_URL", make_conninfo(base_url, options="-c search_path=" + schema))
    ff, cc, dd, ss = [key() for _ in range(4)]
    cookie, bearer = new_token(), new_token()
    selection = {"child": {"id": cc, "nickname": "Mae", "age_band": "7-8"},
                 "subject": {"id": "english", "label": "English"},
                 "goal": {"id": "explain-a-characters-choice", "label": "Explain a character’s choice"}}
    try:
        with database.connect() as db:
            db.execute(baseline)
            db.execute("INSERT INTO families(id,name,name_key,password_hash) VALUES (%s,'Fir','fir','fixture')", (ff,))
            db.execute("INSERT INTO children(id,family_id,nickname,age_band) VALUES (%s,%s,'Mae','7-8')", (cc,ff))
            db.execute("INSERT INTO devices(id,name,token_hash,family_id,paired_at) VALUES (%s,'Cube',%s,%s,now())", (dd,token_hash(bearer),ff))
            db.execute("INSERT INTO pairing_codes(code_hash,device_id,expires_at,consumed_at) VALUES (%s,%s,now(),now())", (token_hash("OLDCODE"),dd))
            db.execute("INSERT INTO parent_sessions(token_hash,family_id,expires_at) VALUES (%s,%s,now()+interval '1 day')", (token_hash(cookie),ff))
            db.execute("INSERT INTO family_selections(family_id,child_id,subject_id,goal_id) VALUES (%s,%s,'english','explain-a-characters-choice')", (ff,cc))
            db.execute("INSERT INTO learning_sessions(id,device_id,family_id,selection_snapshot) VALUES (%s,%s,%s,%s)", (ss,dd,ff,Jsonb(selection)))
        with TestClient(create_app()) as phone, TestClient(create_app()) as cube:
            phone.cookies.set(COOKIE_NAME, cookie)
            cube.headers["Authorization"] = "Bearer " + bearer
            assert phone.get("/api/parent").json()["selection"] == selection
            assert phone.get("/api/parent").json()["devices"] == [{"id": dd, "name": "Cube"}]
            assert cube.get("/api/device").json()["paired"]
            legacy = session_of(cube.get(f"/api/device/session/{ss}"))
            assert legacy["started_at"] == legacy["ended_at"] and legacy["lesson"] is None
            assert {k: legacy[k] for k in selection} == selection
            assert cube.get("/api/device/session").json() == {"session": None}
            assert phone.post("/api/pair", json={"code": "OLDCODE"}).status_code == 400
            new = session_of(start(cube))
        with TestClient(create_app()) as reopened:
            reopened.headers["Authorization"] = "Bearer " + bearer
            assert session_of(reopened.get("/api/device/session")) == new
        with database.connect() as db:
            assert [r["version"] for r in db.execute("SELECT version FROM kubo_schema_migrations ORDER BY version")] == [1,2]
    finally:
        monkeypatch.setenv("DATABASE_URL", base_url)
        with database.connect() as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
