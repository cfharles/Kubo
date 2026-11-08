"""Provider boundaries plus a real, offline adult synthetic voice check on the prepared Mac."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import wave

from fastapi import HTTPException
import pytest

from app import speech, tutor


CONTEXT = {"story": "Tess put her drawing inside her coat as rain started.",
           "opening": "Why did Tess put the drawing there?",
           "answer": "She wanted to keep her picture dry."}


@pytest.mark.parametrize("answer", ["My name is Riley; I think it was the rain.",
                                    "i am robin and the paper could get wet",
                                    "We live at 123 Main Street.", "Email me at pat@example.com.",
                                    "My number is two one four five six seven eight.",
                                    "I don't know."])
def test_private_or_unclear_answer_never_reaches_model(monkeypatch, answer):
    monkeypatch.setattr(tutor, "_chat", lambda *_a, **_kw: pytest.fail("private or unclear words left the filter"))
    with pytest.raises(HTTPException) as error:
        tutor.followup({**CONTEXT, "answer": answer})
    assert error.value.status_code == 422


def test_review_failure_regenerates_once_then_pauses(monkeypatch):
    calls = []
    def chat(kind, payload, repair=False):
        assert "nickname" not in json.dumps(payload)
        calls.append((kind, bool(repair)))
        return {"approved": False, "reason_phrase": "picture dry"} if kind == "review" else {"question": "What clue shows the picture might get wet?", "idea": "Saving the picture."}
    monkeypatch.setattr(tutor, "_chat", chat)
    with pytest.raises(HTTPException) as error:
        tutor.followup({**CONTEXT, "nickname": "Never send nicknames", "session_id": "secret"})
    assert error.value.status_code == 503
    assert calls == [("followup", False), ("review", False), ("followup", True), ("review", False)]


def test_local_never_sends_hosted_key_or_follows_external_urls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-on-loopback")
    monkeypatch.delenv("KUBO_LLM_MODE", raising=False)
    monkeypatch.setenv("KUBO_LLM_BASE_URL", "http://127.0.0.1:8081/v1")
    conn, headers, _ = tutor._connection()
    assert not headers and conn.host == "127.0.0.1"
    conn.close()
    monkeypatch.setenv("KUBO_LLM_BASE_URL", "https://example.org/v1")
    with pytest.raises(HTTPException) as error:
        tutor._connection()
    assert error.value.status_code == 503


def test_repeated_opening_or_missing_evidence_cannot_slip_through_review(monkeypatch):
    calls = []
    def chat(kind, payload, repair=False):
        calls.append(kind)
        if kind == "review":
            return {"approved": True, "reason_phrase": "an invented quote"}
        if not repair:
            return {"question": CONTEXT["opening"], "idea": "The learner wanted to protect the picture."}
        return {"question": "Which clue makes you think water might damage the art?", "idea": "Protecting it."}
    monkeypatch.setattr(tutor, "_chat", chat)
    with pytest.raises(HTTPException):
        tutor.followup(CONTEXT)
    assert calls == ["followup", "followup", "review"]


def test_review_can_approve_synonymous_question_with_actual_input_evidence(monkeypatch):
    def chat(kind, payload, repair=False):
        return ({"approved": True, "reason_phrase": "keep her picture dry"} if kind == "review"
                else {"question": "Which detail suggests water could damage the artwork?", "idea": "Protecting it."})
    monkeypatch.setattr(tutor, "_chat", chat)
    assert "water" in tutor.followup(CONTEXT)["question"]


def test_birthday_gift_is_a_story_idea_and_long_verbatim_idea_is_rejected(monkeypatch):
    text = "Maybe the picture was a birthday gift for her brother."
    assert tutor._context({**CONTEXT, "answer": text})["answer"] == text
    def chat(kind, payload, repair=False):
        assert kind == "followup"
        return {"question": "What detail might show he would receive a present?", "idea": "You said: " + text}
    monkeypatch.setattr(tutor, "_chat", chat)
    with pytest.raises(HTTPException) as error:
        tutor.followup({**CONTEXT, "answer": text})
    assert error.value.status_code == 503


def test_no_redirect_or_response_body_relay(monkeypatch):
    class Redirect:
        status = 302
        def read(self, _):
            pytest.fail("No redirect body should be relayed")
    class FakeConnection:
        requests = []
        def request(self, method, path, body, headers):
            self.requests.append((method, path, json.loads(body)))
        def getresponse(self):
            return Redirect()
        def close(self):
            pass
    conn = FakeConnection()
    monkeypatch.setattr(tutor, "_connection", lambda: (conn, {"Authorization": "Bearer test"}, "test"))
    with pytest.raises(HTTPException) as error:
        tutor._chat("assess", CONTEXT)
    assert error.value.status_code == 503
    assert len(conn.requests) == 1 and conn.requests[0][0:2] == ("POST", "/v1/chat/completions")
    assert conn.requests[0][2]["store"] is False


def test_malformed_audio_rejected_before_decoder(monkeypatch):
    monkeypatch.setattr(speech, "_model", lambda *_: pytest.fail("invalid WAV reached decoder"))
    with pytest.raises(HTTPException) as error:
        speech.transcribe(b"not a WAV" * 20)
    assert error.value.status_code == 422


@pytest.mark.skipif(sys.platform != "darwin" or not importlib.util.find_spec("vosk") or not
                    (Path(os.environ.get("KUBO_VOSK_MODEL") or speech.DEFAULT_MODEL) / "am" / "final.mdl").is_file(),
                    reason="Requires the Mac voice and scripts/setup-speech.sh")
def test_real_adult_synthetic_voice_recognition_and_silence():
    for phrase in ["She wanted to keep the picture dry.", "She wanted to surprise her brother."]:
        audio = speech.speak(phrase)
        with wave.open(io.BytesIO(audio), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            assert wav.getnframes() > 16000
        result = speech.transcribe(audio)
        assert result["text"] == phrase.rstrip(".").lower() and result["confidence"] >= .65
    for phrase in ("Hint", "Give me a hint", "Give me a hand", "Green", "She could wrap the picture in a gift box"):
        assert speech.transcribe(speech.speak(phrase))["text"] == phrase.lower()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(32000))
    with pytest.raises(HTTPException) as error:
        speech.transcribe(buffer.getvalue())
    assert error.value.status_code == 422
