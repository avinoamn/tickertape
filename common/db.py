"""Postgres helpers shared by the services. All config comes from env vars.

DATABASE_URL wins if set; otherwise PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE.

Queue pattern: a stage claims rows with `SELECT ... FOR UPDATE SKIP LOCKED` inside a transaction, processes
them, updates them and commits. If the process dies mid-batch the transaction rolls back and the rows are
claimable again, so nothing gets stuck in a "processing" state.
"""
import os

import psycopg
from psycopg.rows import dict_row

MAX_ATTEMPTS = 3


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg.connect(url)
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "tickertape"),
    )


def claim(conn: psycopg.Connection, status: str, limit: int) -> list[dict]:
    """Lock up to `limit` oldest items with `status`. Rows stay locked until the caller commits or rolls back."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT * FROM items WHERE status = %s
               ORDER BY created_at, id LIMIT %s FOR UPDATE SKIP LOCKED""",
            (status, limit),
        )
        return cur.fetchall()


def record_failure(conn: psycopg.Connection, item_id: int, error: str) -> str:
    """Retry rule: attempts += 1; after MAX_ATTEMPTS the item becomes status='error'. Returns the new status.

    A failing item below the limit keeps its current status so the stage picks it up again.
    """
    row = conn.execute(
        """UPDATE items
              SET attempts = attempts + 1,
                  error = %s,
                  status = CASE WHEN attempts + 1 >= %s THEN 'error' ELSE status END
            WHERE id = %s
        RETURNING status""",
        (error[:2000], MAX_ATTEMPTS, item_id),
    ).fetchone()
    return row[0]
