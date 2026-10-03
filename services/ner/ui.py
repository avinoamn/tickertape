"""Gradio UI: try the NER model on any text."""
import gradio as gr
from core import LABELS, Extractor, group_entities

EXAMPLE = ("NVIDIA reports record Q3 revenue of $18.1 billion, up 206% year over year, "
           "as CEO Jensen Huang says data center demand for NVDA H100 chips remains strong.")


def build_ui(ex: Extractor) -> gr.Blocks:
    def analyze(text: str, labels: list[str]):
        labels = labels or list(LABELS)
        spans = sorted(ex.predict_spans(text, labels), key=lambda s: s["start"]) if text.strip() else []
        highlighted = {
            "text": text,
            "entities": [{"entity": s["label"], "start": s["start"], "end": s["end"]} for s in spans],
        }
        entities = group_entities(spans, valid_tickers=ex.resolver.valid_tickers)
        return highlighted, {
            "entities": entities,
            "focus_ticker": ex.focus_ticker(entities),
            "spans": [{k: (round(v, 3) if k == "score" else v) for k, v in s.items()} for s in spans],
        }

    with gr.Blocks(title="tickertape NER") as demo:
        gr.Markdown("# NER (GLiNER)\nEntities and the resolved focus ticker for a text. Same model as the worker.")
        text = gr.Textbox(label="Text", value=EXAMPLE, lines=6)
        labels = gr.CheckboxGroup(choices=list(LABELS), value=list(LABELS), label="Labels")
        run = gr.Button("Extract", variant="primary")
        highlighted = gr.HighlightedText(label="Entities")
        result = gr.JSON(label="Result")
        run.click(analyze, [text, labels], [highlighted, result])
        text.submit(analyze, [text, labels], [highlighted, result])
    return demo
