"""The typed questions asked about every item. `choice` with neutral keys, at most 10 options each."""

QUESTIONS = {
    "event_type": {
        "type": "choice",
        "instructions": "What kind of event is described for the focus company?",
        "criteria": {
            "earnings": "reported results",
            "guidance": "outlook raised, cut or reaffirmed",
            "mna": "merger, acquisition, divestiture",
            "analyst": "rating or price-target change",
            "legal": "lawsuit, investigation, regulatory action",
            "other": "anything else",
        },
    },
    "sentiment": {
        "type": "choice",
        "instructions": "How is this news for the focus company?",
        "criteria": {"pos": "positive", "neu": "neutral", "neg": "negative"},
    },
    "action": {
        "type": "choice",
        "instructions": "What should the system do with this item?",
        "criteria": {"ignore": "not relevant", "log": "store only", "alert": "a human should look now"},
    },
}
