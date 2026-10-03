"""Backfill historical items into the DEV database, to build a training set (step 5).

Runs in the poller image (needs Python 3.10+, feedparser, httpx, psycopg), against the dev DB only:

  make backfill ARGS="cnbc --per-feed 200"
  make backfill ARGS="sec --count 500"

Items get the same shape and uid scheme as the live poller (title/summary cleaned the same way, uid = sha256(guid or
link)), so ON CONFLICT DO NOTHING also dedupes against live rows. Everything inserted is also appended to
training/data/backfill.jsonl, which is how build_dataset.py tells backfilled items from live ones (the schema has no
origin column and the cluster schema should not change for a dev concern). training/data/ is git-ignored: the text is
third-party content, for private use only.

Sources:
  cnbc  Wayback Machine snapshots of the exact CNBC feed URLs from feeds.yaml (CDX API, one snapshot per day, sampled).
  sec   EDGAR daily form index (which 8-Ks were filed on a day) + data.sec.gov submissions API (item codes, size,
        acceptance time), rebuilt into the same text layout as the live atom feed. Needs SEC_USER_AGENT.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import feedparser
import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "poller"))
from main import SUMMARY_MAX, clean_text, make_uid, published_at  # noqa: E402  (the poller's own helpers)

from common.db import connect  # noqa: E402

OUT = ROOT / "training" / "data" / "backfill.jsonl"
FEEDS = yaml.safe_load((ROOT / "services" / "poller" / "feeds.yaml").read_text())["feeds"]

ITEM_CAPTIONS = {  # Form 8-K item captions, as printed in the live SEC feed
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety - Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement of a Registrant",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation or an Obligation under an Off-Balance Sheet Arrangement",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modifications to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements or a Related Audit Report or Completed Interim Review",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers: Compensatory Arrangements of Certain Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
    "5.04": "Temporary Suspension of Trading Under Registrant's Employee Benefit Plans",
    "5.05": "Amendments to the Registrant's Code of Ethics, or Waiver of a Provision of the Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


class Sink:
    """Inserts into the dev DB and mirrors every NEW row into backfill.jsonl."""

    def __init__(self):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect()
        self.out = OUT.open("a", encoding="utf-8")
        self.inserted = 0

    def add(self, source, uid, title, summary, link, published) -> bool:
        cur = self.conn.execute(
            """INSERT INTO items (uid, source, title, summary, link, published_at)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (uid) DO NOTHING""",
            (uid, source, title, summary, link, published))
        if not cur.rowcount:
            return False
        self.conn.commit()
        self.inserted += 1
        self.out.write(json.dumps({"uid": uid, "source": source, "title": title, "summary": summary, "link": link,
                                   "published_at": published.isoformat() if published else None},
                                  ensure_ascii=False) + "\n")
        self.out.flush()
        return True


def get(client: httpx.Client, url: str, tries: int = 4, ok: tuple = (200, 404), **kw) -> httpx.Response:
    """GET with backoff (the Wayback Machine is flaky). Returns the response for status codes in `ok`, else retries."""
    for i in range(tries):
        try:
            r = client.get(url, **kw)
            if r.status_code in ok:
                return r
            err = f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            err = repr(exc)
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"giving up on {url}: {err}")


# ------------------------------------------------------------------ CNBC via the Wayback Machine

def backfill_cnbc(a, sink: Sink):
    rng = random.Random(a.seed)
    feeds = [f for f in FEEDS if f["name"].startswith("cnbc_")]
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "tickertape-backfill/0.1 (private research)"}) as c:
        for f in feeds:
            cdx = get(c, "https://web.archive.org/cdx/search/cdx", params={
                "url": f["url"], "output": "json", "fl": "timestamp", "filter": "statuscode:200",
                "collapse": "timestamp:8", "from": a.since.replace("-", "")})
            snaps = [row[0] for row in cdx.json()[1:]] if cdx.status_code == 200 and cdx.text.strip() else []
            rng.shuffle(snaps)  # random days: spreads items over time, and few snapshots overlap
            print(f"{f['name']}: {len(snaps)} daily snapshots since {a.since}", flush=True)
            got = fetched = 0
            for ts in snaps:
                if got >= a.per_feed or fetched >= a.max_fetches:
                    break
                fetched += 1
                time.sleep(a.delay)
                r = get(c, f"http://web.archive.org/web/{ts}id_/{f['url']}")
                if r.status_code != 200:
                    continue
                taken = 0
                for e in feedparser.parse(r.content).entries:
                    if taken >= a.per_snapshot:
                        break  # each snapshot repeats ~30 items from the same few days: cap it to spread dates
                    uid, title = make_uid(e), clean_text(e.get("title"))
                    if not uid or not title or got >= a.per_feed:
                        continue
                    added = sink.add(f["name"], uid, title, clean_text(e.get("summary"), SUMMARY_MAX) or None,
                                     e.get("link"), published_at(e))
                    got += added
                    taken += added
                if fetched % 10 == 0:
                    print(f"  {f['name']}: {fetched} snapshots fetched, {got} new items", flush=True)
            print(f"{f['name']}: done, {got} new items from {fetched} snapshots", flush=True)


# ------------------------------------------------------------------ SEC 8-K via EDGAR indexes

IDX_LINE = re.compile(r"^8-K\s+(.+?)\s{2,}(\d+)\s+(\d{8})\s+(\S+)\s*$")


def sec_uid(accession: str) -> str:
    """Live atom entries have id 'urn:tag:sec.gov,2008:accession-number=<acc>' (checked against live rows by --check)."""
    return hashlib.sha256(f"urn:tag:sec.gov,2008:accession-number={accession}".encode()).hexdigest()


def business_days(since: dt.date, until: dt.date):
    d = since
    while d <= until:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def backfill_sec(a, sink: Sink):
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        sys.exit("SEC_USER_AGENT is required (format: '<name> <email>')")
    rng = random.Random(a.seed)
    days = list(business_days(dt.date.fromisoformat(a.since), dt.date.today() - dt.timedelta(days=2)))
    rng.shuffle(days)
    subs_cache: dict[int, dict] = {}
    got = 0
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": ua}) as c:
        for day in days:
            if got >= a.count:
                break
            time.sleep(0.2)  # <= 5 req/s, SEC allows 10
            q = (day.month - 1) // 3 + 1
            r = get(c, f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx",
                    ok=(200, 403, 404))  # EDGAR answers 403 (not 404) for a missing index file
            if r.status_code != 200:  # holiday / not published
                continue
            filings = [m.groups() for m in map(IDX_LINE.match, r.text.splitlines()) if m]
            rng.shuffle(filings)
            taken = 0
            for _name, cik, _filed, fname in filings:
                if taken >= a.per_day or got >= a.count:
                    break
                cik = int(cik)
                acc = Path(fname).stem  # edgar/data/<cik>/<accession>.txt
                if cik not in subs_cache:
                    time.sleep(0.2)
                    s = get(c, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
                    subs_cache[cik] = s.json() if s.status_code == 200 else {}
                sub = subs_cache[cik]
                rec = sub.get("filings", {}).get("recent", {})
                try:
                    i = rec["accessionNumber"].index(acc)
                except (KeyError, ValueError):
                    continue  # older than the 'recent' block, skip
                items = [x for x in (rec["items"][i] or "").split(",") if x]
                caps = " ".join(f"Item {x}: {ITEM_CAPTIONS[x]}" if x in ITEM_CAPTIONS else f"Item {x}" for x in items)
                size_kb = max(1, round(int(rec["size"][i] or 0) / 1024))
                title = f"8-K - {sub.get('name') or _name} ({cik:010d}) (Filer)"
                summary = f"Filed: {rec['filingDate'][i]} AccNo: {acc} Size: {size_kb} KB {caps}".strip()
                link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
                acc_time = dt.datetime.fromisoformat(rec["acceptanceDateTime"][i].replace("Z", "+00:00"))
                if sink.add("sec_8k", sec_uid(acc), title, summary[:SUMMARY_MAX], link, acc_time):
                    got += 1
                    taken += 1
            print(f"{day}: {taken} new ({got}/{a.count})", flush=True)
    print(f"sec_8k: done, {got} new items", flush=True)


def check_sec_uid():
    """Compare sec_uid() with the uid of a live sec_8k row (needs a live row whose summary has 'AccNo: <acc>')."""
    with connect() as conn:
        uid, summ = conn.execute("SELECT uid, summary FROM items WHERE source='sec_8k' AND summary LIKE '%AccNo:%' LIMIT 1").fetchone()
    acc = re.search(r"AccNo: (\S+)", summ).group(1)
    print(acc, "uid scheme matches live" if sec_uid(acc) == uid else "uid scheme DIFFERS from live")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cnbc", help="CNBC feeds from Wayback snapshots")
    c.add_argument("--per-feed", type=int, default=200)
    c.add_argument("--since", default="2024-01-01")
    c.add_argument("--per-snapshot", type=int, default=6, help="new items taken per snapshot (spreads dates)")
    c.add_argument("--max-fetches", type=int, default=400, help="snapshot fetch cap per feed")
    c.add_argument("--delay", type=float, default=1.0, help="seconds between Wayback requests")
    s = sub.add_parser("sec", help="SEC 8-K filings from EDGAR indexes")
    s.add_argument("--count", type=int, default=500)
    s.add_argument("--per-day", type=int, default=5, help="filings sampled per business day (spreads dates)")
    s.add_argument("--since", default=(dt.date.today() - dt.timedelta(days=365)).isoformat())
    sub.add_parser("check", help="verify the SEC uid scheme against a live row")
    for x in (c, s):
        x.add_argument("--seed", type=int, default=1)
    a = p.parse_args()
    if a.cmd == "check":
        return check_sec_uid()
    sink = Sink()
    print("target DB:", os.environ.get("DATABASE_URL", "").split("@")[-1] or "PG* env", flush=True)
    {"cnbc": backfill_cnbc, "sec": backfill_sec}[a.cmd](a, sink)
    print("inserted", sink.inserted, "new rows; mirror:", OUT)


if __name__ == "__main__":
    main()
