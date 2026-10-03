"""Shared helpers that need no database."""
import json

from common import db as queue
from common.logging import log


def test_connect_prefers_database_url(monkeypatch):
    seen = {}
    monkeypatch.setattr(queue.psycopg, "connect", lambda *a, **kw: seen.update(args=a, kwargs=kw) or "conn")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert queue.connect() == "conn"
    assert seen["args"] == ("postgresql://u:p@h/db",)


def test_log_writes_one_json_line(capsys):
    log("unit", item_id=7, status="ok")
    record = json.loads(capsys.readouterr().out)
    assert record["svc"] == "unit" and record["item_id"] == 7 and record["status"] == "ok" and "ts" in record
