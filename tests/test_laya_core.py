"""Laya state building, answer flattening and the question definitions (the model itself is stubbed out)."""
import pytest


def row(**overrides):
    base = {"source": "cnbc_earnings", "title": "Nvidia beats estimates", "summary": "Revenue rose.",
            "focus_ticker": "NVDA", "entities": {"COMPANY": ["Nvidia"]}}
    return {**base, **overrides}


# --- state ------------------------------------------------------------------------------------------------------

def test_build_state_shape(laya_core):
    assert laya_core.build_state(row()) == {
        "source": "cnbc_earnings", "headline": "Nvidia beats estimates", "summary": "Revenue rose.",
        "focus_ticker": "NVDA", "entities": {"COMPANY": ["Nvidia"]},
    }


def test_build_state_truncates_the_summary(laya_core):
    state = laya_core.build_state(row(summary="x" * 5000))
    assert len(state["summary"]) == laya_core.SUMMARY_CHARS == 600


def test_build_state_handles_missing_values(laya_core):
    state = laya_core.build_state(row(summary=None, focus_ticker=None, entities=None))
    assert state["summary"] == ""
    assert state["focus_ticker"] is None
    assert state["entities"] == {}


# --- flatten ----------------------------------------------------------------------------------------------------

def answer(choice="pos", answer_confidence=0.8, confidence=0.1):
    return {"type": "choice", "choice": choice, "answer_confidence": answer_confidence, "confidence": confidence,
            "probabilities": {choice: answer_confidence}}


def test_flatten_uses_answer_confidence_not_entropy_confidence(laya_core):
    rows = laya_core.flatten({"sentiment": answer("pos", 0.8, confidence=0.1)})
    assert rows == [("sentiment", "pos", 0.8)]


def test_flatten_returns_one_row_per_question(laya_core):
    rows = laya_core.flatten({"event_type": answer("earnings", 0.9), "action": answer("log", 0.6)})
    assert rows == [("event_type", "earnings", 0.9), ("action", "log", 0.6)]


def test_flatten_accepts_integer_confidence(laya_core):
    assert laya_core.flatten({"q": answer("a", 1)}) == [("q", "a", 1.0)]


@pytest.mark.parametrize("bad", [
    {"type": "choice", "answer_confidence": 0.8},                       # no choice
    {"type": "choice", "choice": None, "answer_confidence": 0.8},
    {"type": "choice", "choice": "pos"},                                # no confidence
    {"type": "choice", "choice": "pos", "answer_confidence": "0.8"},    # wrong type
    {"type": "choice", "choice": "pos", "answer_confidence": float("nan")},
])
def test_flatten_rejects_malformed_answers(laya_core, bad):
    with pytest.raises(ValueError, match="malformed answer"):
        laya_core.flatten({"sentiment": bad})


# --- questions --------------------------------------------------------------------------------------------------

def test_questions_are_the_documented_three(laya_questions):
    assert list(laya_questions) == ["event_type", "sentiment", "action"]


def test_every_question_is_a_valid_choice_question(laya_questions):
    for name, q in laya_questions.items():
        assert q["type"] == "choice", name
        assert q["instructions"], name
        criteria = q["criteria"]
        assert 2 <= len(criteria) <= 10, name                # laya's option-count buckets stop at 10
        assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in criteria.items()), name


def test_option_keys_match_the_documented_labels(laya_questions):
    assert set(laya_questions["sentiment"]["criteria"]) == {"pos", "neu", "neg"}
    assert set(laya_questions["action"]["criteria"]) == {"ignore", "log", "alert"}
    assert set(laya_questions["event_type"]["criteria"]) == {"earnings", "guidance", "mna", "analyst", "legal", "other"}
