"""The small, explicit learning-goal catalog for this prototype."""

SUBJECTS = [
    {
        "id": "math",
        "label": "Math",
        "goals": [
            {"id": "solve-number-stories", "label": "Solve number stories"},
            {"id": "share-equally", "label": "Share equally"},
        ],
    },
    {
        "id": "english",
        "label": "English",
        "goals": [
            {"id": "retell-a-story", "label": "Retell a story"},
            {"id": "explain-a-characters-choice", "label": "Explain a character’s choice"},
        ],
    },
    {
        "id": "history",
        "label": "History",
        "goals": [
            {"id": "put-events-in-order", "label": "Put events in order"},
            {"id": "use-a-historical-clue", "label": "Use a historical clue"},
        ],
    },
    {
        "id": "science",
        "label": "Science",
        "goals": [
            {"id": "predict-a-change", "label": "Predict a change"},
            {"id": "choose-a-fair-test", "label": "Choose a fair test"},
        ],
    },
]


def find_goal(subject_id: str, goal_id: str):
    for subject in SUBJECTS:
        if subject["id"] == subject_id:
            for goal in subject["goals"]:
                if goal["id"] == goal_id:
                    return {"id": subject["id"], "label": subject["label"]}, dict(goal)
    return None
