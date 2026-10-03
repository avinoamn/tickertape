"""NER worker loop: claim status='new' items, extract entities, set status='ner_done'."""
import os
import time

from core import SVC, Extractor, compact_spans, group_entities, item_text
from prometheus_client import Counter, Histogram
from psycopg.types.json import Jsonb

from common.db import claim, connect, record_failure
from common.logging import log

BATCH_SIZE = int(os.environ.get("NER_BATCH_SIZE", "16"))
IDLE_SLEEP = float(os.environ.get("NER_IDLE_SLEEP", "5"))

ITEMS = Counter("ner_items_processed_total", "Items that reached ner_done")
ERRORS = Counter("ner_errors_total", "Item-level processing failures (each failed attempt)")
INFERENCE = Histogram("ner_inference_seconds", "Model inference time per call",
                      buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
BATCH = Histogram("ner_batch_size", "Items claimed per batch", buckets=(1, 2, 4, 8, 12, 16, 32))
ENTITIES = Counter("ner_entities_total", "Entities extracted", ["label"])


def process_batch(conn, ex: Extractor) -> int:
    """Claim and process one batch in a single transaction. Returns the number of rows claimed."""
    rows = claim(conn, "new", BATCH_SIZE)
    if not rows:
        conn.commit()
        return 0
    BATCH.observe(len(rows))
    texts = [item_text(r["title"], r["summary"]) for r in rows]

    # (row, spans | None, error | None, ms)
    outcomes = []
    t0 = time.monotonic()
    try:
        results = extract_timed(ex, texts)
        ms = int((time.monotonic() - t0) * 1000 / len(rows))  # batch time split evenly across items
        outcomes = [(r, res, None, ms) for r, res in zip(rows, results)]
    except Exception:
        # One bad item must not sink the batch: retry one by one to find it.
        for r, text in zip(rows, texts):
            t1 = time.monotonic()
            try:
                outcomes.append((r, extract_timed(ex, [text])[0], None, int((time.monotonic() - t1) * 1000)))
            except Exception as exc:
                outcomes.append((r, None, exc, int((time.monotonic() - t1) * 1000)))

    logs = []
    for r, spans, exc, ms in outcomes:
        if exc is None:
            entities = group_entities(spans, valid_tickers=ex.resolver.valid_tickers)
            conn.execute(
                """UPDATE items SET entities = %s, ner_spans = %s, focus_ticker = %s, ner_ms = %s,
                          ner_done_at = now(), status = 'ner_done', attempts = 0, error = NULL
                    WHERE id = %s""",
                (Jsonb(entities), Jsonb(compact_spans(spans)), ex.focus_ticker(entities), ms, r["id"]),
            )
            logs.append(dict(item_id=r["id"], status="ner_done", ms=ms, error=None))
            ITEMS.inc()
            for label, values in entities.items():
                ENTITIES.labels(label).inc(len(values))
        else:
            status = record_failure(conn, r["id"], repr(exc))
            logs.append(dict(item_id=r["id"], status=status, ms=ms, error=repr(exc)))
            ERRORS.inc()
    conn.commit()
    for entry in logs:  # log after commit so the log never claims a state that was rolled back
        log(SVC, **entry)
    return len(rows)


def extract_timed(ex: Extractor, texts: list[str]):
    with INFERENCE.time():
        return ex.extract_batch(texts)


def run_loop(ex: Extractor) -> None:
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = connect()
            claimed = process_batch(conn, ex)
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
