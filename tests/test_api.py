"""Integration tests against a real, isolated PostgreSQL database.

Set TEST_DATABASE_URL to a dedicated database named kubo_test.
Tests deliberately erase that database's application tables between cases.
"""

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import COOKIE_NAME, create_app
from app.security import token_hash

DEMO_PASSWORD = secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def isolated_postgres(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL database named kubo_test.")
    if urlparse(url).path != "/kubo_test":
        pytest.fail("Refusing to clear any database other than kubo_test.")
    monkeypatch.setenv("DATABASE_URL", url)
    with database.connect() as db:
        if db.info.dbname != "kubo_test":
            pytest.fail("PostgreSQL did not connect to the dedicated kubo_test database.")
    database.initialize()
    with database.connect() as db:
        db.execute("TRUNCATE families, devices CASCADE")
    yield


@pytest.fixture
def parent():
    with TestClient(create_app()) as client:
        register(client)
        yield client


def register(client, name="Fern Family", password=DEMO_PASSWORD):
    response = client.post("/api/parents/register", json={"family_name": name, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["family"]


def child(client, nickname="Milo", age_band="7-8"):
    response = client.post("/api/children", json={"nickname": nickname, "age_band": age_band})
    assert response.status_code == 201, response.text
    return response.json()["child"]


def choose(client, selected_child, subject="math", goal="share-equally"):
    response = client.post("/api/selection", json={
        "child_id": selected_child["id"], "subject_id": subject, "goal_id": goal,
    })
    assert response.status_code == 200, response.text
    return response.json()["selection"]


def provision(cube):
    response = cube.post("/api/devices", json={"name": "Mac cube"})
    assert response.status_code == 201, response.text
    result = response.json()
    cube.headers["Authorization"] = "Bearer " + result["device_token"]
    return result


def test_health_catalog_and_unauthorized_routes():
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/health").json() == {"status": "ok", "database": "ok"}
        catalog = anonymous.get("/api/catalog").json()["subjects"]
        assert {subject["id"] for subject in catalog} == {"math", "english", "history", "science"}
        assert all(len(subject["goals"]) == 2 for subject in catalog)
        assert anonymous.get("/api/parent").status_code == 401
        assert anonymous.post("/api/children", json={"nickname": "Milo", "age_band": "7-8"}).status_code == 401
        assert anonymous.post("/api/pair", json={"code": "AAAA-BBBB"}).status_code == 401
        assert anonymous.get("/api/device").status_code == 401
        assert anonymous.post("/api/device/start").status_code == 401
        assert anonymous.post("/api/device/pairing-code").status_code == 401


def test_registration_login_logout_and_hashed_credentials(parent):
    cookie = parent.cookies.get(COOKIE_NAME)
    duplicate = parent.post("/api/parents/register", json={"family_name": " fern   FAMILY ", "password": "different-password"})
    assert duplicate.status_code == 409
    with database.connect() as db:
        family = db.execute("SELECT password_hash FROM families").fetchone()
        session = db.execute("SELECT token_hash FROM parent_sessions").fetchone()
        assert family["password_hash"].startswith("pbkdf2_sha256$")
        assert DEMO_PASSWORD not in family["password_hash"]
        assert session["token_hash"] == token_hash(cookie)
        assert session["token_hash"] != cookie
    assert parent.post("/api/parents/logout").status_code == 200
    assert parent.get("/api/parent").status_code == 401
    with TestClient(create_app()) as stale:
        stale.cookies.set(COOKIE_NAME, cookie)
        assert stale.get("/api/parent").status_code == 401
    assert parent.post("/api/parents/login", json={"family_name": "Fern Family", "password": "incorrect-password"}).status_code == 401
    logged_in = parent.post("/api/parents/login", json={"family_name": "FERN FAMILY", "password": DEMO_PASSWORD})
    assert logged_in.status_code == 200
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert "SameSite=strict" in logged_in.headers["set-cookie"]
    assert parent.get("/api/parent").status_code == 200


def test_bad_inputs_do_not_create_partial_state(parent):
    assert parent.post("/api/children", json={"nickname": " ", "age_band": "7-8"}).status_code == 422
    assert parent.post("/api/children", json={"nickname": "Milo", "age_band": "11-12"}).status_code == 422
    assert parent.get("/api/parent").json()["children"] == []
    selected_child = child(parent)
    saved = choose(parent, selected_child)
    for subject, goal in [("math", "predict-a-change"), ("coding", "share-equally"), ("math", "unknown")]:
        response = parent.post("/api/selection", json={"child_id": selected_child["id"], "subject_id": subject, "goal_id": goal})
        assert response.status_code == 422
        assert parent.get("/api/parent").json()["selection"] == saved


def test_families_cannot_access_or_select_each_others_children(parent):
    first_child = child(parent)
    choose(parent, first_child)
    with TestClient(create_app()) as other, TestClient(create_app()) as cube:
        register(other, "Maple Family")
        other_child = child(other, "Zoe", "5-6")
        created = provision(cube)
        assert parent.post("/api/pair", json={"code": created["pairing_code"]}).status_code == 200
        other_state = other.get("/api/parent").json()
        assert other_state["children"] == [other_child]
        assert other_state["devices"] == []
        assert other_state["selection"] is None
        assert other.post("/api/selection", json={"child_id": first_child["id"], "subject_id": "math", "goal_id": "share-equally"}).status_code == 404
        assert other.get("/api/parent").json()["selection"] is None
        assert cube.post("/api/device/start").json()["session"]["child"] == first_child


def test_unpaired_and_missing_selection_are_actionable(parent):
    with TestClient(create_app()) as cube:
        created = provision(cube)
        state = cube.get("/api/device").json()
        assert state["paired"] is False and state["family"] is None
        assert cube.post("/api/device/start").status_code == 409
        assert parent.post("/api/pair", json={"code": created["pairing_code"]}).status_code == 200
        missing = cube.post("/api/device/start")
        assert missing.status_code == 409
        assert missing.json()["detail"] == "Ask your parent to choose a child and learning goal."
        with database.connect() as db:
            assert db.execute("SELECT count(*) AS n FROM learning_sessions").fetchone()["n"] == 0


def test_pairing_codes_expire_rotate_and_cannot_be_reused_or_reassigned(parent):
    with TestClient(create_app()) as cube, TestClient(create_app()) as other:
        register(other, "Maple Family")
        created = provision(cube)
        with database.connect() as db:
            db.execute("UPDATE pairing_codes SET expires_at = now() - interval '1 second'")
        assert parent.post("/api/pair", json={"code": created["pairing_code"]}).status_code == 400
        fresh = cube.post("/api/device/pairing-code").json()
        replacement = cube.post("/api/device/pairing-code").json()
        assert parent.post("/api/pair", json={"code": fresh["pairing_code"]}).status_code == 400
        human_code = replacement["pairing_code"].lower().replace("-", " ")
        assert parent.post("/api/pair", json={"code": human_code}).status_code == 200
        assert parent.post("/api/pair", json={"code": replacement["pairing_code"]}).status_code == 400
        assert other.post("/api/pair", json={"code": replacement["pairing_code"]}).status_code == 400
        assert cube.post("/api/device/pairing-code").status_code == 409
        assert cube.get("/api/device").json()["family"]["name"] == "Fern Family"
        with database.connect() as db:
            device = db.execute("SELECT token_hash FROM devices").fetchone()
            assert device["token_hash"] == token_hash(created["device_token"])
            assert db.execute("SELECT code_hash FROM pairing_codes WHERE consumed_at IS NOT NULL").fetchall()


def test_concurrent_pairing_has_exactly_one_winner(parent):
    with TestClient(create_app()) as other, TestClient(create_app()) as cube:
        register(other, "Maple Family")
        created = provision(cube)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda client: client.post("/api/pair", json={"code": created["pairing_code"]}).status_code,
                [parent, other],
            ))
        assert sorted(results) == [200, 400]
        winning_family = "Fern Family" if results[0] == 200 else "Maple Family"
        assert cube.get("/api/device").json()["family"]["name"] == winning_family


def test_selection_and_tokens_survive_closed_parent_and_new_app_instances():
    with TestClient(create_app()) as phone, TestClient(create_app()) as mac:
        family = register(phone)
        selected_child = child(phone)
        child(phone, "Nora", "9-10")
        created = provision(mac)
        assert phone.post("/api/pair", json={"code": created["pairing_code"]}).status_code == 200
        expected = choose(phone, selected_child, "science", "predict-a-change")
        saved_cookie = phone.cookies.get(COOKIE_NAME)
    # Both clients and the application lifespans end; only PostgreSQL retains state.
    with TestClient(create_app()) as restarted_mac:
        restarted_mac.headers["Authorization"] = "Bearer " + created["device_token"]
        assert not restarted_mac.cookies
        device = restarted_mac.get("/api/device").json()
        assert device["paired"] is True and device["family"] == family
        started = restarted_mac.post("/api/device/start")
        assert started.status_code == 201
        session = started.json()["session"]
        assert {key: session[key] for key in ("child", "subject", "goal")} == expected
        assert session["id"] and session["started_at"]
    with TestClient(create_app()) as reopened_phone:
        reopened_phone.cookies.set(COOKIE_NAME, saved_cookie)
        assert reopened_phone.get("/api/parent").json()["selection"] == expected


def test_start_reads_latest_selection_and_preserves_previous_snapshot(parent):
    with TestClient(create_app()) as cube:
        created = provision(cube)
        assert parent.post("/api/pair", json={"code": created["pairing_code"]}).status_code == 200
        milo = child(parent)
        nora = child(parent, "Nora", "9-10")
        old_selection = choose(parent, milo)
        first = cube.post("/api/device/start").json()["session"]
        new_selection = choose(parent, nora, "history", "put-events-in-order")
        second = cube.post("/api/device/start").json()["session"]
        assert first["id"] != second["id"]
        assert {key: second[key] for key in ("child", "subject", "goal")} == new_selection
        with database.connect() as db:
            snapshot = db.execute("SELECT selection_snapshot FROM learning_sessions WHERE id = %s", (first["id"],)).fetchone()
            assert snapshot["selection_snapshot"] == old_selection
            assert db.execute("SELECT count(*) AS n FROM learning_sessions").fetchone()["n"] == 2


def test_expired_parent_sessions_and_wrong_device_tokens_are_rejected(parent):
    with database.connect() as db:
        db.execute("UPDATE parent_sessions SET expires_at = now() - interval '1 second'")
    assert parent.get("/api/parent").status_code == 401
    with TestClient(create_app()) as cube:
        cube.headers["Authorization"] = "Bearer invented-token"
        assert cube.get("/api/device").status_code == 401
        assert cube.post("/api/device/start").status_code == 401


def test_cross_origin_mutations_are_rejected(parent):
    body = {"nickname": "Milo", "age_band": "7-8"}
    assert parent.post("/api/children", json=body, headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert parent.post("/api/children", json=body, headers={"Origin": "null"}).status_code == 403
    assert parent.post("/api/children", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert parent.get("/api/parent").json()["children"] == []
    valid = parent.post("/api/children", json=body, headers={"Origin": "http://testserver"})
    assert valid.status_code == 201
    assert valid.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in valid.headers
