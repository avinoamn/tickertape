"""ner service entrypoint: model once, then metrics (8000), worker thread, Gradio UI (7860)."""
import threading

from core import Extractor
from prometheus_client import start_http_server
from ui import build_ui
from worker import run_loop

if __name__ == "__main__":
    extractor = Extractor()
    start_http_server(8000)  # after the model is loaded, so the /metrics readiness probe means "model ready"
    threading.Thread(target=run_loop, args=(extractor,), daemon=True, name="ner-worker").start()
    build_ui(extractor).launch(server_name="0.0.0.0", server_port=7860)
