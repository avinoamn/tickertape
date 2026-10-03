"""Test setup.

The services are flat scripts (`from core import ...`), and ner and laya both have a `core.py`, so each module
is loaded by file path under a unique name. Heavy libraries (torch, laya, gliner) are never imported: laya is
replaced by an empty stub, and GLiNER is only imported inside `Extractor.__init__`, which the tests do not call.

Database tests need a Postgres reachable through TEST_DATABASE_URL and are skipped otherwise. Each test gets its
own throw-away schema (search_path), so pointing the URL at a development database is safe.
"""
import importlib.util
import os
import sys
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `common` package

os.environ.setdefault("FEEDS_FILE", str(ROOT / "services" / "poller" / "feeds.yaml"))  # read by ner and poller at import


def load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def ner_core():
    return load_module("ner_core", "services/ner/core.py")


@pytest.fixture(scope="session")
def laya_core():
    sys.modules.setdefault("laya", types.ModuleType("laya"))  # the real package pulls in torch
    sys.path.insert(0, str(ROOT / "services" / "laya"))  # `from questions import QUESTIONS`
    try:
        return load_module("laya_core", "services/laya/core.py")
    finally:
        sys.path.remove(str(ROOT / "services" / "laya"))


@pytest.fixture(scope="session")
def laya_questions(laya_core):
    return laya_core.QUESTIONS


@pytest.fixture(scope="session")
def poller():
    return load_module("poller_main", "services/poller/main.py")


# --- Postgres ---------------------------------------------------------------------------------------------------

@pytest.fixture
def db_connect():
    """connect() -> a new connection whose tables live in a fresh schema with db/schema.sql applied."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    opened = []

    def connect():
        conn = psycopg.connect(url, options=f"-c search_path={schema}")
        opened.append(conn)
        return conn

    setup = connect()
    setup.execute((ROOT / "db" / "schema.sql").read_text())
    setup.commit()
    yield connect
    for conn in opened:
        if not conn.closed:
            conn.rollback()
            conn.close()
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def db(db_connect):
    conn = db_connect()
    yield conn
    conn.rollback()
