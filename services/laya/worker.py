"""Laya worker loop: claim status='ner_done' items, answer the typed questions, set status='laya_done'."""
import os
import time

from prometheus_client import Counter, Histogram
from psycopg.types.json import Jsonb

from common.db import claim, connect, record_failure
from common.logging import log
from core import SVC, Predictor, build_state, flatten
from questions import QUESTIONS

# Small on purpose: rows stay locked until the batch commits, and one item takes seconds on computa's CPU.
BATCH_SIZE = int(os.environ.get("LAYA_BATCH_SIZE", "4"))
IDLE_SLEEP = float(os.environ.get("LAYA_IDLE_SLEEP", "5"))

ITEMS = Counter("laya_items_processed_total", "Items that reached laya_done")
ERRORS = Counter("laya_errors_total", "Item-level processing failures (each failed attempt)")
INFERENCE = Histogram("laya_inference_seconds", "Model inference time per item",
                      buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120))
BATCH = Histogram("laya_batch_size", "Items claimed per batch", buckets=(1, 2, 4, 8, 16))
ANSWERS = Counter("laya_answers_total", "Answers given", ["question", "answer"])
CONFIDENCE = Histogram("laya_confidence", "Calibrated confidence of each answer", ["question"],
                       buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))


def process_batch(conn, pred: Predictor) -> int:
    """Claim and process one batch in a single transaction. Returns the number of rows claimed."""
    rows = claim(conn, "ner_done", BATCH_SIZE)
    if not rows:
        conn.commit()
        return 0
    BATCH.observe(len(rows))

    logs = []
    for r in rows:
        t0 = time.monotonic()
        try:
            with INFERENCE.time():
                result = pred.predict(build_state(r), QUESTIONS)
            ms = int((time.monotonic() - t0) * 1000)
            answers = result["answers"]
            flat = flatten(answers)
            conn.execute(
                """UPDATE items SET decisions = %s, laya_ms = %s, laya_done_at = now(),
                          status = 'laya_done', attempts = 0, error = NULL
                    WHERE id = %s""",
                (Jsonb(answers), ms, r["id"]),
            )
            for question, answer, conf in flat:
                conn.execute(
                    """INSERT INTO decisions (item_id, question, answer, confidence, model_rev)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (item_id, question) DO UPDATE
                          SET answer = EXCLUDED.answer, confidence = EXCLUDED.confidence,
                              model_rev = EXCLUDED.model_rev, created_at = now()""",
                    (r["id"], question, answer, conf, pred.model_rev),
                )
                ANSWERS.labels(question, answer).inc()
                CONFIDENCE.labels(question).observe(min(max(conf, 0.0), 1.0))
            ITEMS.inc()
            entry = dict(item_id=r["id"], status="laya_done", ms=ms, error=None)
            if result.get("usage", {}).get("truncated"):
                entry["truncated"] = True  # the state did not fit the model's token budget
            logs.append(entry)
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            status = record_failure(conn, r["id"], repr(exc))
            logs.append(dict(item_id=r["id"], status=status, ms=ms, error=repr(exc)))
            ERRORS.inc()
    conn.commit()
    for entry in logs:  # log after commit so the log never claims a state that was rolled back
        log(SVC, **entry)
    return len(rows)


def run_loop(pred: Predictor) -> None:
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = connect()
            claimed = process_batch(conn, pred)
        except Exception as exc:  # DB hiccup etc.: log, reconnect, keep going
            log(SVC, status="error", error=repr(exc))
            try:
                conn and conn.close()
            except Exception:
                pass
            conn = None
            claimed = 0
        if claimed == 0:
            time.sleep(IDLE_SLEEP)
