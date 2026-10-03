"""The Postgres queue: schema, claiming with SKIP LOCKED, and the retry rule. Needs TEST_DATABASE_URL."""
import pytest

from common import db as queue

pytestmark = pytest.mark.db


def add_item(conn, n, status="new", age_minutes=0):
    conn.execute(
        """INSERT INTO items (uid, source, title, status, created_at)
           VALUES (%s, 'test', %s, %s, now() - make_interval(mins => %s))""",
        (f"uid-{n}", f"title {n}", status, age_minutes),
    )


def test_schema_can_be_applied_twice(db, schema_sql):
    db.execute(schema_sql)   # the init Job re-applies it on every deploy
    db.execute(schema_sql)
    tables = {r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
    assert {"items", "decisions", "feed_state"} <= tables


def test_status_defaults_to_new_and_uid_is_unique(db):
    add_item(db, 1)
    assert db.execute("SELECT status, attempts FROM items").fetchone() == ("new", 0)
    with pytest.raises(Exception, match="duplicate key"):
        add_item(db, 1)


def test_claim_returns_the_oldest_rows_of_the_requested_status(db):
    add_item(db, 1, "new", age_minutes=10)
    add_item(db, 2, "new", age_minutes=30)     # oldest
    add_item(db, 3, "ner_done", age_minutes=60)
    add_item(db, 4, "new", age_minutes=20)
    rows = queue.claim(db, "new", 2)
    assert [r["uid"] for r in rows] == ["uid-2", "uid-4"]
    assert [r["uid"] for r in queue.claim(db, "ner_done", 5)] == ["uid-3"]


def test_claim_skips_rows_locked_by_another_worker(db, db_connect):
    for n in range(4):
        add_item(db, n, age_minutes=100 - n)
    db.commit()
    first = db_connect()
    second = db_connect()
    mine = queue.claim(first, "new", 2)                 # transaction stays open: the rows are locked
    theirs = queue.claim(second, "new", 10)
    assert len(mine) == 2 and len(theirs) == 2
    assert {r["uid"] for r in mine}.isdisjoint({r["uid"] for r in theirs})
    first.rollback()                                     # a crashed worker: the rows become claimable again
    assert len(queue.claim(second, "new", 10)) == 4


def test_record_failure_retries_then_marks_error(db):
    add_item(db, 1)
    (item_id,) = db.execute("SELECT id FROM items").fetchone()
    statuses = [queue.record_failure(db, item_id, f"boom {i}") for i in range(queue.MAX_ATTEMPTS)]
    assert statuses == ["new", "new", "error"]
    assert db.execute("SELECT status, attempts, error FROM items").fetchone() == ("error", 3, "boom 2")


def test_record_failure_keeps_the_current_stage_below_the_limit(db):
    add_item(db, 1, "ner_done")
    (item_id,) = db.execute("SELECT id FROM items").fetchone()
    assert queue.record_failure(db, item_id, "boom") == "ner_done"


def test_record_failure_truncates_long_errors(db):
    add_item(db, 1)
    (item_id,) = db.execute("SELECT id FROM items").fetchone()
    queue.record_failure(db, item_id, "x" * 5000)
    assert len(db.execute("SELECT error FROM items").fetchone()[0]) == 2000


def test_decisions_are_one_row_per_item_and_question(db):
    add_item(db, 1)
    (item_id,) = db.execute("SELECT id FROM items").fetchone()
    upsert = """INSERT INTO decisions (item_id, question, answer, confidence, model_rev)
                VALUES (%s, 'sentiment', %s, %s, %s)
                ON CONFLICT (item_id, question) DO UPDATE
                SET answer = EXCLUDED.answer, confidence = EXCLUDED.confidence, model_rev = EXCLUDED.model_rev"""
    db.execute(upsert, (item_id, "neu", 0.5, "base@aaa"))
    db.execute(upsert, (item_id, "pos", 0.9, "ft@bbb"))   # a newer model overwrites the old answer
    assert db.execute("SELECT answer, confidence, model_rev FROM decisions").fetchall() == [("pos", pytest.approx(0.9), "ft@bbb")]
