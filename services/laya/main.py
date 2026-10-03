"""laya service entrypoint: model once, then metrics (8000), worker thread, Gradio UI (7860)."""
import threading

from core import Predictor
from prometheus_client import start_http_server
from ui import build_ui
from worker import run_loop

if __name__ == "__main__":
    predictor = Predictor()
    start_http_server(8000)  # after the model is loaded, so the /metrics readiness probe means "model ready"
    threading.Thread(target=run_loop, args=(predictor,), daemon=True, name="laya-worker").start()
    build_ui(predictor).launch(server_name="0.0.0.0", server_port=7860)
