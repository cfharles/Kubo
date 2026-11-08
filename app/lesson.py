"""One spoken English goal. PostgreSQL owns the state passed between turns."""

from fastapi import HTTPException

from . import tutor

GOAL_ID = "explain-a-characters-choice"
GOAL = "Explain a character’s choice"
STORIES = {
    "drawing": {
        "title": "The drawing",
        "text": "Tess felt a raindrop. She tucked her drawing under her coat and hurried inside.",
        "opening": "Why did Tess tuck her drawing away?",
        "teacher_note": "Tess feels a raindrop before covering her drawing. Keeping paper dry is a reasonable inference from both details. An inference can be grounded even when the narrator never says the motive out loud.",
        "hint": "Think about the raindrop and what might happen to paper.",
        "home": "Jo hears a puppy bark and lifts her sandwich off the floor. What clue helps explain why Jo moved her sandwich?",
    },
    "wagon": {
        "title": "The narrow path",
        "text": "Ben pulled his wagon along a narrow path. He heard a bicycle bell behind him. He stepped onto the grass and pulled his wagon beside him.",
        "opening": "Why did Ben move his wagon onto the grass?",
        "teacher_note": "The bell behind Ben and the narrow path are existing clues. Moving the wagon makes room for the approaching bicycle. The text allows other motives if the learner can explain a connection.",
        "hint": "Think about the sound behind Ben and how much room there was on the path.",
        "home": "Mia sees a puddle by the door and moves her shoes onto a shelf. Which detail helps explain why Mia moved her shoes?",
    },
}


def new_lesson(selection, previous_report=None):
    if (selection["goal"]["id"] != GOAL_ID or selection["child"]["age_band"] != "7-8"
            or selection["subject"]["id"] != "english"):
        return None
    story_id = "wagon" if previous_report and previous_report.get("story_id") == "drawing" else "drawing"
    story = STORIES[story_id]
    question = story["opening"]
    review_focus = ""
    if previous_report:
        scaffold = previous_report.get("hint_used") or previous_report.get("outcome") != "demonstrated"
        review_focus = "Identify one clue, then link it to a reason." if scaffold else "Explore how a clue supports one possible motive."
        if scaffold:
            question = ("Think about what Ben heard. " if story_id == "wagon" else "Think about what Tess felt. ") + question
    return {
        "phase": "opening", "story_id": story_id, "story_title": story["title"],
        "story_text": story["text"], "opening": question, "question": question,
        "prompt": f'{story["text"]} {question}', "hint_used": False,
        "prior_idea": "", "review_focus": review_focus,
    }


def finish(state, confirmed=False):
    candidate = state.pop("candidate", None)
    if not confirmed:
        candidate = None
    observation = candidate["observation"] if candidate else "The visit ended before the learner confirmed an explanation."
    demonstrated = bool(candidate and candidate["demonstrated"])
    report = {
        "story_id": state["story_id"], "story_title": state["story_title"],
        "observation": observation, "hint_used": state["hint_used"],
        "outcome": "demonstrated" if demonstrated else ("developing" if confirmed else "stopped"),
        "home_question": STORIES[state["story_id"]]["home"],
        "next_review": ("Try explaining a different choice using a clue from a fresh story."
                        if demonstrated and not state["hint_used"]
                        else "Find one clue in a fresh story, then use it to explain the choice."),
    }
    state.update(phase="ended", question="", prior_idea="",
                 prompt="Thanks for sharing your idea. See you next time!" if confirmed else "We can stop here.")
    return state, report, True


def transition(state, kind, text=None):
    """Produce the next saved state only after all required model calls succeed."""
    if state is None:
        if kind == "stop":
            return None, None, True
        raise HTTPException(409, "This goal has a saved selection only. Choose the ages 7–8 English character-choice lesson to talk.")
    state = dict(state)
    phase = state["phase"]
    if phase == "ended":
        raise HTTPException(409, "This session has ended.")
    if kind == "stop":
        return finish(state)
    if kind == "repeat":
        state["prompt"] = (state["story_text"] + " " if phase == "opening" else "") + state["question"]
    elif kind == "hint" and phase != "confirm":
        state.update(hint_used=True, prompt=STORIES[state["story_id"]]["hint"] + " " + state["question"])
    elif phase == "confirm" and kind == "confirm":
        return finish(state, confirmed=True)
    elif phase == "confirm" and kind == "reject":
        question = "Tell me what I missed. " + STORIES[state["story_id"]]["opening"]
        state.pop("candidate")
        state.update(phase="followup", question=question, prompt=question, prior_idea="")
    elif kind == "answer" and phase in {"opening", "followup"}:
        context = {
            "goal": GOAL, "story": state["story_text"], "opening": state["opening"],
            "question": state["question"], "answer": text, "prior_idea": state["prior_idea"],
            "review_focus": state["review_focus"], "teacher_note": STORIES[state["story_id"]]["teacher_note"],
        }
        if phase == "opening":
            draft = tutor.followup(context)
            state.update(phase="followup", prior_idea=draft["idea"],
                         question=draft["question"], prompt=draft["question"])
        else:
            assessment = tutor.assess(context)
            question = f'Here is how I understood you. {assessment["observation"]} Did I understand your idea? Say yes or no.'
            state.update(phase="confirm", candidate=assessment, prior_idea="", question=question, prompt=question)
    elif kind == "hint" and phase == "confirm":
        state["prompt"] = "Say yes if I understood, or no to explain it again. " + state["question"]
    else:
        raise HTTPException(409, "That action does not match the current question. Reload the cube to continue.")
    return state, None, False
