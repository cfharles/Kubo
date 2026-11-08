"""Lesson-specific behavior independent of the database, audio system and provider."""

from copy import deepcopy

import pytest
from fastapi import HTTPException

from app import lesson

ENGLISH = {"subject": {"id": "english"}, "goal": {"id": lesson.GOAL_ID}, "child": {"age_band": "7-8"}}


def run_step(state, action, text=None):
    new, report, ended = lesson.transition(state, action, text)
    assert report is None and not ended
    return new


def test_only_target_goal_and_age_have_a_spoken_lesson():
    assert lesson.new_lesson({**ENGLISH, "child": {"age_band": "5-6"}}) is None
    assert lesson.new_lesson({**ENGLISH, "goal": {"id": "retell-a-story"}}) is None
    assert lesson.transition(None, "stop") == (None, None, True)


def test_repeat_hint_and_stop_do_not_create_evidence():
    state = lesson.new_lesson(ENGLISH)
    assert run_step(state, "repeat")["prompt"] == state["prompt"]
    hinted = run_step(state, "hint")
    assert hinted["hint_used"] and hinted["question"] == state["question"]
    assert not state["hint_used"]
    ended, report, done = lesson.transition(hinted, "stop")
    assert done and ended["phase"] == "ended" and report["outcome"] == "stopped"
    assert report["hint_used"] and report["home_question"]


def test_reject_meaning_then_confirm_corrected_observation(monkeypatch):
    followup = {"question": "How could rain change the drawing?", "idea": "Linked keeping the paper covered to rain."}
    monkeypatch.setattr(lesson.tutor, "followup", lambda _: followup)
    state = run_step(lesson.new_lesson(ENGLISH), "answer", "The rain could ruin it.")
    attempts = iter([{"observation": "You described a wet coat.", "demonstrated": False},
                     {"observation": "You connected wet paper to a ruined drawing.", "demonstrated": True}])
    monkeypatch.setattr(lesson.tutor, "assess", lambda _: next(attempts))
    candidate = run_step(state, "answer", "Wet paper could tear.")
    assert candidate["phase"] == "confirm"
    corrected = run_step(candidate, "reject")
    assert "candidate" not in corrected and corrected["prior_idea"] == ""
    confirmed = run_step(corrected, "answer", "I meant wet paper could ruin the drawing.")
    ended, report, done = lesson.transition(confirmed, "confirm")
    assert done and "candidate" not in ended and report["outcome"] == "demonstrated"
    assert report["observation"] == "You connected wet paper to a ruined drawing."
    returned = lesson.new_lesson(ENGLISH, report)
    assert returned["story_id"] != state["story_id"]
    with_hint = lesson.new_lesson(ENGLISH, {**report, "hint_used": True})
    assert with_hint["review_focus"] != returned["review_focus"]
    assert with_hint["opening"] != returned["opening"]


def test_provider_pause_leaves_previous_question_and_turn_intact(monkeypatch):
    def unavailable(_):
        raise HTTPException(503, "Tutor paused")
    monkeypatch.setattr(lesson.tutor, "followup", unavailable)
    state = lesson.new_lesson(ENGLISH)
    before = deepcopy(state)
    with pytest.raises(HTTPException) as error:
        lesson.transition(state, "answer", "Maybe it was a present")
    assert error.value.status_code == 503 and state == before
