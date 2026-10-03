"""Laya model wrapper: load once, build the state for an item, flatten answers for the `decisions` table."""
import os
import threading

os.environ.setdefault("USE_TF", "0")  # before importing laya/transformers

import laya  # noqa: E402

from common.logging import log  # noqa: E402
from questions import QUESTIONS  # noqa: E402

SVC = "laya"
MODEL = os.environ.get("LAYA_MODEL", "convaiinnovations/laya")
# Optional commit pin. Unset: laya pins its own known checkpoints, and a fine-tuned repo gets its latest revision.
REVISION = os.environ.get("LAYA_REVISION") or None
SUMMARY_CHARS = 600  # the English checkpoint has ~320 tokens for state


def build_state(row: dict) -> dict:
    return {
        "source": row["source"],
        "headline": row["title"],
        "summary": (row["summary"] or "")[:SUMMARY_CHARS],
        "focus_ticker": row["focus_ticker"],
        "entities": row["entities"] or {},
    }


class Predictor:
    def __init__(self):
        self.agent = laya.load(MODEL, revision=REVISION)
        rev = getattr(self.agent, "revision", None)
        self.model_rev = f"{MODEL}@{rev[:12]}" if rev else MODEL  # recorded in decisions.model_rev
        self._lock = threading.Lock()  # the worker thread and the UI share one model
        self._shape_logged = False
        log(SVC, status="model_loaded", model_rev=self.model_rev)

    def predict(self, state: dict, questions: dict | None = None) -> dict:
        with self._lock:
            result = self.agent.predict(state, questions or QUESTIONS)
        if not self._shape_logged:  # spec: log the answer shape once, on the first run
            self._shape_logged = True
            first = next(iter(result["answers"].values()), {})
            log(SVC, status="answer_shape", answer_keys=sorted(first), result_keys=sorted(result),
                usage_keys=sorted(result.get("usage", {})))
        return result


def flatten(answers: dict) -> list[tuple[str, str, float]]:
    """(question, answer, confidence) per question. For `choice` questions laya's `answer_confidence` is the calibrated
    top probability (max p after temperature scaling), the one to gate on; its `confidence` is entropy-based and sits
    far lower (verified on 150 items: mean 0.41 vs 0.69), and `action.act_probability` is ignored. Raises if an answer
    is malformed so the item takes the normal retry path instead of storing garbage."""
    rows = []
    for question, ans in answers.items():
        choice, conf = ans.get("choice"), ans.get("answer_confidence")
        if not isinstance(choice, str) or not isinstance(conf, (int, float)) or conf != conf:
            raise ValueError(f"malformed answer for {question!r}: {ans!r}"[:500])
        rows.append((question, choice, float(conf)))
    return rows
