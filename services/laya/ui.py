"""Gradio UI: ask Laya any typed questions about any state. Same model (and lock) as the worker."""
import json

import gradio as gr

from core import Predictor
from questions import QUESTIONS

EXAMPLE_STATE = {
    "source": "cnbc_top_news",
    "headline": "Nvidia beats estimates and raises guidance as data center demand stays strong",
    "summary": "NVIDIA reported record quarterly revenue of $18.1 billion, up 206% from a year ago, and guided "
               "next quarter above Wall Street expectations.",
    "focus_ticker": "NVDA",
    "entities": {"COMPANY": ["NVIDIA"], "TICKER": ["NVDA"], "MONEY": ["$18.1 billion"], "PERCENT": ["206%"]},
}


def build_ui(pred: Predictor) -> gr.Blocks:
    def ask(state_text: str, questions_text: str):
        try:
            state, questions = json.loads(state_text), json.loads(questions_text)
        except json.JSONDecodeError as exc:
            raise gr.Error(f"Invalid JSON: {exc}")
        result = pred.predict(state, questions)
        table = [
            [q, a.get("choice"), round(a.get("confidence", float("nan")), 3),
             round(a.get("answer_confidence", float("nan")), 3),
             ", ".join(f"{k} {v:.2f}" for k, v in a.get("probabilities", {}).items())]
            for q, a in result["answers"].items()
        ]
        return table, result

    with gr.Blocks(title="tickertape Laya") as demo:
        gr.Markdown(f"# Laya decisions\nModel `{pred.model_rev}`. `answer_confidence` is the calibrated top "
                    "probability the pipeline gates on; `confidence` is laya's entropy-based value. Not trading advice: `alert` only "
                    "means a human should look.")
        state = gr.Code(label="State (JSON)", language="json", value=json.dumps(EXAMPLE_STATE, indent=2), lines=12)
        questions = gr.Code(label="Questions (JSON, editable)", language="json",
                            value=json.dumps(QUESTIONS, indent=2), lines=12)
        run = gr.Button("Ask", variant="primary")
        table = gr.Dataframe(headers=["question", "answer", "confidence", "answer_confidence", "probabilities"],
                             label="Answers", interactive=False, wrap=True)
        raw = gr.JSON(label="Raw result")
        run.click(ask, [state, questions], [table, raw])
    return demo
