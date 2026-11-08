"""Opt-in demonstration with real Mac speech, Vosk, PostgreSQL, and a live LLM.

Run from the repository root: bash scripts/with-local-env.sh .venv/bin/python -m tests.live_lesson
Use only the authored adult-voice test lines below. No microphone or demo data is used.
"""

import io
import json
import os
import re
import secrets
import wave
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient

from app import speech
from app.lesson import GOAL_ID
from app.main import create_app


def checked(response):
    if not response.is_success:
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:450]}")
    return response


def post(client, path, body):
    return checked(client.post(path, json=body)).json()


def norm(text):
    return re.findall(r"[a-z]+", text.lower())


def hear(cube, session):
    audio = checked(cube.get(f"/api/device/session/{session['id']}/audio", params={"revision": session["revision"]}))
    with wave.open(io.BytesIO(audio.content), "rb") as wav:
        assert wav.getnframes() > wav.getframerate() // 2
        assert any(wav.readframes(wav.getnframes())), "Approved tutor prompt was silent"


def recognize(cube, session, variants):
    """A retry may use another authored paraphrase. Only exact word matches count."""
    path = f"/api/device/session/{session['id']}/transcribe"
    for phrase in variants:
        response = cube.post(path, content=speech.speak(phrase), headers={"Content-Type": "audio/wav"})
        if response.status_code == 422:
            print("Adult voice:", phrase, "| Vosk requested another take", flush=True)
            continue
        result = checked(response).json()
        print("Adult voice:", phrase, "| Vosk:", result["text"], flush=True)
        if norm(result["text"]) == norm(phrase):
            return result["text"]
    raise RuntimeError("ASR changed one or more words on every take. Review the audio before confirming an answer.")


def action(cube, session, kind, **extra):
    return post(cube, f"/api/device/session/{session['id']}/action", {
        "request_id": str(uuid4()), "revision": session["revision"], "kind": kind, **extra
    })["session"]


def visit(phone, cube, learner, lines, expected_ideas, *, hint=False):
    post(phone, "/api/selection", {"child_id": learner["id"], "subject_id": "english", "goal_id": GOAL_ID})
    session = post(cube, "/api/device/start", {"request_id": str(uuid4())})["session"]
    opening = session["lesson"]["prompt"]
    print("\n" + learner["nickname"] + " — " + opening, flush=True)
    hear(cube, session)
    repeat = recognize(cube, session, ["Repeat."])
    repeated = action(cube, session, repeat)
    assert repeated["lesson"]["question"] == session["lesson"]["question"]
    session = repeated
    if hint:
        recognize(cube, session, ["Hint.", "Give me a hint.", "Hint please.", "A hint."])
        session = action(cube, session, "hint")
        hear(cube, session)
    answer = recognize(cube, session, lines[0])
    session = action(cube, session, "answer", confirmed=True, text=answer)
    assert session["lesson"]["phase"] == "followup"
    followup = session["lesson"]["question"]
    print("Live follow-up:", followup, flush=True)
    hear(cube, session)
    second = recognize(cube, session, lines[1])
    session = action(cube, session, "answer", confirmed=True, text=second)
    assert session["lesson"]["phase"] == "confirm"
    print("Meaning check:", session["lesson"]["question"], flush=True)
    # Simulate the adult checking the meaning as well as the sound. The phrases
    # below apply only to these authored fixtures; the live tutor has no answer key.
    observation = session["lesson"]["candidate"]["observation"].lower()
    if not all(any(word in observation for word in alternatives) for alternatives in expected_ideas):
        raise AssertionError("Adult fixture could not confirm this interpretation; inspect the meaning check.")
    hear(cube, session)
    assert norm(recognize(cube, session, ["Yes."])) == ["yes"]
    ended = action(cube, session, "confirm")
    assert ended["ended_at"] and ended["report"]["hint_used"] == hint
    reports = checked(phone.get(f"/api/children/{learner['id']}/reports")).json()["reports"]
    report = next(r for r in reports if r["session_id"] == ended["id"])
    print("Parent report:", json.dumps(report, ensure_ascii=False), flush=True)
    return {"child": learner["nickname"], "opening": opening, "first_answer": answer,
            "followup": followup, "second_answer": second, "report": report}


def main():
    test_url = os.getenv("TEST_DATABASE_URL", "")
    if urlsplit(test_url).path not in ("/kubo_test", "/kubo_live_test"):
        raise SystemExit("Set TEST_DATABASE_URL to an isolated kubo_test or kubo_live_test database.")
    os.environ["DATABASE_URL"] = test_url
    with TestClient(create_app()) as phone, TestClient(create_app()) as cube:
        post(phone, "/api/parents/register", {"family_name": f"Fictional Fern {uuid4().hex[:8]}",
                                             "password": secrets.token_urlsafe(24)})
        sam, riley = [post(phone, "/api/children", {"nickname": name, "age_band": "7-8"})["child"]
                      for name in ["Sam", "Riley"]]
        registration = post(cube, "/api/devices", {"name": "Adult voice demo cube"})
        cube.headers["Authorization"] = "Bearer " + registration["device_token"]
        post(phone, "/api/pair", {"code": registration["pairing_code"]})
        grounded = visit(phone, cube, sam, [
            ["She felt a raindrop. She wanted to keep the paper dry.", "The rain could ruin the drawing."],
            ["It was raining outside. The paper could get wet.", "The rain could ruin the drawing."],
        ], [("rain", "wet", "dry", "water"), ("paper", "drawing", "picture")])
        alternative = visit(phone, cube, riley, [
            ["Maybe the picture was a surprise for her brother.", "She wanted the picture to be a surprise."],
            ["She could wrap the picture in a gift box.", "She could hide a present for her brother."],
        ], [("surprise", "gift", "present"), ("could", "might", "maybe", "suggest", "imagin", "possible", "idea", "would", "propos", "wonder", "guess", "may ")])
        returned = visit(phone, cube, sam, [
            ["He wanted to make room for the bike.", "He moved to let the bike go past."],
            ["He heard the bell behind him. The path was narrow.", "He heard the bell and the bike needed room."],
        ], [("bell", "bicycle", "bike"), ("room", "space", "narrow", "pass", "way", "safe")], hint=True)
    assert grounded["opening"] == alternative["opening"]
    assert norm(grounded["followup"]) != norm(alternative["followup"])
    assert grounded["report"]["outcome"] == returned["report"]["outcome"] == "demonstrated"
    assert returned["report"]["story_id"] != grounded["report"]["story_id"]
    print("\nCompleted three visits with separate parent and cube clients.")


if __name__ == "__main__":
    main()
