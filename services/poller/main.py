"""Poller: fetch RSS/Atom feeds and insert new items into Postgres. Runs once and exits (k8s CronJob)."""
import calendar
import hashlib
import html
import os
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import feedparser
import httpx
import yaml

from common.db import connect
from common.logging import log

SVC = "poller"
SUMMARY_MAX = 1000
SEC_MIN_INTERVAL = 0.2  # <= 5 req/s, well under SEC's 10 req/s limit
DEFAULT_UA = "tickertape-poller/0.1"
FEEDS_FILE = Path(os.environ.get("FEEDS_FILE", Path(__file__).with_name("feeds.yaml")))


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def clean_text(raw: str | None, limit: int | None = None) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace, optionally truncate."""
    if not raw:
        return ""
    p = _TextExtractor()
    p.feed(raw)
    p.close()
    text = " ".join(html.unescape("".join(p.parts)).split())
    return text[:limit] if limit else text


def make_uid(entry) -> str | None:
    key = entry.get("id") or entry.get("link")
    return hashlib.sha256(key.encode()).hexdigest() if key else None


def published_at(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc) if t else None


def expand_feeds(cfg: dict) -> list[dict]:
    """Return one job per fetch: {key, name, url, sec}. per_ticker feeds expand over the watchlist."""
    jobs = []
    for f in cfg["feeds"]:
        if not f.get("enabled", True):
            continue
        sec = bool(f.get("sec"))
        if f.get("per_ticker"):
            for t in cfg["watchlist"]:
                jobs.append({"key": f"{f['name']}:{t}", "name": f["name"], "url": f["url"].format(ticker=t), "sec": sec})
        else:
            jobs.append({"key": f["name"], "name": f["name"], "url": f["url"], "sec": sec})
    return jobs


def poll_feed(client: httpx.Client, conn, job: dict, sec_ua: str | None, last_sec: list[float]) -> dict:
    """Fetch one feed and insert its items. Returns stats. Raises on fetch/parse/DB failure."""
    state = conn.execute(
        "SELECT etag, last_modified FROM feed_state WHERE feed_key = %s", (job["key"],)
    ).fetchone()
    headers = {"User-Agent": sec_ua if job["sec"] else DEFAULT_UA}
    if state:
        if state[0]:
            headers["If-None-Match"] = state[0]
        if state[1]:
            headers["If-Modified-Since"] = state[1]

    if job["sec"]:
        wait = SEC_MIN_INTERVAL - (time.monotonic() - last_sec[0])
        if wait > 0:
            time.sleep(wait)
        last_sec[0] = time.monotonic()

    resp = client.get(job["url"], headers=headers)
    if resp.status_code == 304:
        return {"http": 304, "entries": 0, "inserted": 0}
    resp.raise_for_status()

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.bozo_exception!r}")

    inserted = skipped = 0
    for e in parsed.entries:
        uid = make_uid(e)
        title = clean_text(e.get("title"))
        if not uid or not title:
            skipped += 1
            continue
        cur = conn.execute(
            """INSERT INTO items (uid, source, title, summary, link, published_at)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (uid) DO NOTHING""",
            (uid, job["name"], title, clean_text(e.get("summary"), SUMMARY_MAX) or None, e.get("link"), published_at(e)),
        )
        inserted += cur.rowcount

    conn.execute(
        """INSERT INTO feed_state (feed_key, etag, last_modified) VALUES (%s, %s, %s)
           ON CONFLICT (feed_key) DO UPDATE
           SET etag = EXCLUDED.etag, last_modified = EXCLUDED.last_modified, updated_at = now()""",
        (job["key"], resp.headers.get("etag"), resp.headers.get("last-modified")),
    )
    return {"http": resp.status_code, "entries": len(parsed.entries), "inserted": inserted, "skipped": skipped}


def main() -> int:
    cfg = yaml.safe_load(FEEDS_FILE.read_text())
    jobs = expand_feeds(cfg)
    sec_ua = os.environ.get("SEC_USER_AGENT")
    if any(j["sec"] for j in jobs) and not sec_ua:
        log(SVC, status="error", error="SEC_USER_AGENT is required for sec feeds (format: '<name> <email>')")
        return 2

    failures = 0
    total_inserted = 0
    last_sec = [0.0]
    with connect() as conn, httpx.Client(timeout=20, follow_redirects=True) as client:
        for job in jobs:
            t0 = time.monotonic()
            try:
                stats = poll_feed(client, conn, job, sec_ua, last_sec)
                conn.commit()
                total_inserted += stats["inserted"]
                log(SVC, feed=job["key"], status="ok", ms=int((time.monotonic() - t0) * 1000), **stats)
            except Exception as exc:  # one bad feed must not stop the others
                conn.rollback()
                failures += 1
                log(SVC, feed=job["key"], status="error", ms=int((time.monotonic() - t0) * 1000), error=repr(exc))

    log(SVC, status="done", feeds=len(jobs), failed=failures, inserted=total_inserted)
    return 1 if jobs and failures == len(jobs) else 0


if __name__ == "__main__":
    sys.exit(main())
