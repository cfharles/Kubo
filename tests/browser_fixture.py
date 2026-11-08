"""Offline tutor double for the browser regression only. No provider keys or model requests."""
from time import sleep

from fastapi import HTTPException

from app import tutor
from app.main import create_app


def app_factory():
    def followup(context):
        if "slow turn" in context["answer"].lower():
            sleep(2.5)  # Allow the real Stop endpoint to overtake this answer.
        return {"idea": "The learner noticed rain.", "question": "What would happen to the paper if the rain reached it?"}

    def assess(context):
        if context["answer"] == "fixture unclear":
            raise HTTPException(422, "Please clarify that idea.")
        if context["answer"] == "fixture unavailable":
            raise HTTPException(503, "The tutor is unavailable.")
        if "tear" in context["answer"].lower():
            return {"observation": "You linked the raindrop to paper that could tear.", "demonstrated": True}
        return {"observation": "You mentioned a wet coat.", "demonstrated": False}

    tutor.followup, tutor.assess = followup, assess
    return create_app()
