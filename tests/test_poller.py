"""Poller: text cleaning, ids, feed expansion (pure), and fetching against a fake HTTP server plus Postgres."""
import html
import time
from datetime import UTC, datetime

import httpx
import pytest

# --- pure helpers -----------------------------------------------------------------------------------------------


def test_clean_text_strips_tags_entities_and_whitespace(poller):
    raw = "<p>Revenue&nbsp;rose <b>12%</b> &amp; margins\n\n  widened</p>"
    assert poller.clean_text(raw) == "Revenue rose 12% & margins widened"


def test_clean_text_truncates(poller):
    assert poller.clean_text("a" * 50, limit=10) == "a" * 10


def test_clean_text_empty_values(poller):
    assert poller.clean_text(None) == ""
    assert poller.clean_text("") == ""
    assert poller.clean_text("<br/>") == ""


def test_make_uid_prefers_the_id_and_falls_back_to_the_link(poller):
    by_id = poller.make_uid({"id": "guid-1", "link": "https://example.com/a"})
    by_link = poller.make_uid({"link": "https://example.com/a"})
    assert by_id != by_link
    assert len(by_id) == 64 and by_id == poller.make_uid({"id": "guid-1"})   # stable sha256 hex digest


def test_make_uid_without_id_or_link(poller):
    assert poller.make_uid({"title": "no identity"}) is None


def test_published_at_uses_published_then_updated(poller):
    published = time.struct_time((2026, 10, 3, 12, 30, 0, 0, 0, 0))
    updated = time.struct_time((2026, 10, 4, 1, 0, 0, 0, 0, 0))
    assert poller.published_at({"published_parsed": published, "updated_parsed": updated}) == datetime(
        2026, 10, 3, 12, 30, tzinfo=UTC)
    assert poller.published_at({"updated_parsed": updated}) == datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
    assert poller.published_at({}) is None


def test_expand_feeds_skips_disabled_and_expands_per_ticker(poller):
    cfg = {
        "watchlist": ["NVDA", "AAPL"],
        "feeds": [
            {"name": "plain", "url": "https://example.com/rss"},
            {"name": "off", "url": "https://example.com/off", "enabled": False},
            {"name": "per", "url": "https://example.com/{ticker}.rss", "per_ticker": True},
            {"name": "filings", "url": "https://sec.example/atom", "sec": True},
        ],
    }
    jobs = poller.expand_feeds(cfg)
    assert [j["key"] for j in jobs] == ["plain", "per:NVDA", "per:AAPL", "filings"]
    assert jobs[1]["url"] == "https://example.com/NVDA.rss"
    assert [j["sec"] for j in jobs] == [False, False, False, True]


def test_the_shipped_feed_config_is_consistent(poller):
    import yaml

    cfg = yaml.safe_load(poller.FEEDS_FILE.read_text())
    jobs = poller.expand_feeds(cfg)
    assert jobs, "no enabled feeds"
    keys = [j["key"] for j in jobs]
    assert len(keys) == len(set(keys)), "duplicate feed keys"
    assert all("{" not in j["url"] and j["url"].startswith("https://") for j in jobs)
    assert cfg["watchlist"] and all(t == t.upper() for t in cfg["watchlist"])


# --- poll_feed against a fake server and a real database ---------------------------------------------------------

def rss(*items):
    body = "".join(
        f"<item><title>{html.escape(t)}</title><link>{link}</link><guid>{link}</guid>"
        f"<description>{html.escape(summary)}</description><pubDate>Fri, 03 Oct 2026 12:30:00 GMT</pubDate></item>"
        for t, link, summary in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


JOB = {"key": "test_feed", "name": "test_feed", "url": "https://feeds.example/rss", "sec": False}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.db
def test_poll_feed_inserts_items_and_dedupes(poller, db):
    body = rss(("First <b>headline</b>", "https://example.com/1", "<p>Some   summary</p>"),
               ("Second headline", "https://example.com/2", ""))
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=body, headers={"ETag": '"v1"', "Last-Modified": "Fri, 03 Oct 2026 12:31:00 GMT"})

    with client_for(handler) as client:
        first = poller.poll_feed(client, db, JOB, None, [0.0])
        second = poller.poll_feed(client, db, JOB, None, [0.0])
    db.commit()

    assert (first["entries"], first["inserted"]) == (2, 2)
    assert second["inserted"] == 0                                 # same uids: ON CONFLICT DO NOTHING
    rows = db.execute("SELECT title, summary, source, status FROM items ORDER BY id").fetchall()
    assert rows == [("First headline", "Some summary", "test_feed", "new"), ("Second headline", None, "test_feed", "new")]
    assert seen[0].headers["user-agent"] == poller.DEFAULT_UA


@pytest.mark.db
def test_poll_feed_truncates_long_summaries(poller, db):
    body = rss(("Long one", "https://example.com/long", "word " * 1000))
    with client_for(lambda r: httpx.Response(200, content=body)) as client:
        poller.poll_feed(client, db, JOB, None, [0.0])
    (summary,) = db.execute("SELECT summary FROM items").fetchone()
    assert len(summary) <= poller.SUMMARY_MAX


@pytest.mark.db
def test_poll_feed_skips_entries_without_a_title(poller, db):
    body = rss(("", "https://example.com/untitled", "x"), ("Titled", "https://example.com/t", "x"))
    with client_for(lambda r: httpx.Response(200, content=body)) as client:
        stats = poller.poll_feed(client, db, JOB, None, [0.0])
    assert (stats["inserted"], stats["skipped"]) == (1, 1)


@pytest.mark.db
def test_poll_feed_sends_validators_back_and_handles_304(poller, db):
    """The conditional-GET path: stored ETag / Last-Modified go out as If-None-Match / If-Modified-Since."""
    body = rss(("Only item", "https://example.com/only", "s"))
    requests = []

    def handler(request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=body, headers={"ETag": '"v1"', "Last-Modified": "Fri, 03 Oct 2026 12:31:00 GMT"})

    with client_for(handler) as client:
        poller.poll_feed(client, db, JOB, None, [0.0])
        db.commit()
        stats = poller.poll_feed(client, db, JOB, None, [0.0])

    assert stats == {"http": 304, "entries": 0, "inserted": 0}
    assert "if-none-match" not in requests[0].headers
    assert requests[1].headers["if-none-match"] == '"v1"'
    assert requests[1].headers["if-modified-since"] == "Fri, 03 Oct 2026 12:31:00 GMT"
    assert db.execute("SELECT count(*) FROM items").fetchone()[0] == 1


@pytest.mark.db
def test_poll_feed_uses_the_sec_user_agent_only_for_sec_feeds(poller, db):
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        return httpx.Response(200, content=rss(("t", "https://example.com/s", "s")))

    with client_for(handler) as client:
        poller.poll_feed(client, db, {**JOB, "sec": True}, "Test Suite test@example.com", [0.0])
        poller.poll_feed(client, db, {**JOB, "key": "other"}, "Test Suite test@example.com", [0.0])
    assert agents == ["Test Suite test@example.com", poller.DEFAULT_UA]


@pytest.mark.db
def test_poll_feed_rejects_an_unparseable_response(poller, db):
    with client_for(lambda r: httpx.Response(200, content=b"<html>not a feed")) as client, pytest.raises(ValueError):
        poller.poll_feed(client, db, JOB, None, [0.0])


@pytest.mark.db
def test_poll_feed_raises_on_http_errors(poller, db):
    with client_for(lambda r: httpx.Response(503)) as client, pytest.raises(httpx.HTTPStatusError):
        poller.poll_feed(client, db, JOB, None, [0.0])
