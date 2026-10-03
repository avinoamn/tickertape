"""Build the Laya fine-tuning dataset from the DEV database (step 5). Runs in the laya image so it can import the
service's own `build_state` and `QUESTIONS` (labels, training rows and live inference then cannot drift apart):

  make dataset ARGS="select"            choose gold / val / train items, write training/data/split.jsonl
  make dataset ARGS="status"            progress of the labelling
  make dataset ARGS="batch --n 50"      print the next unlabelled items (what the labeller reads)
  make dataset ARGS="add" < labels.txt  validate + store labels (stdin, one JSON object per line)
  make dataset ARGS="export"            write train.jsonl / val.jsonl / gold_set.jsonl for the notebook and eval

Splits (by news item, never by question): `gold` = held-out evaluation set, drawn from LIVE items only (they match
what the model sees in production), never trained on; `val` = ~10% of the training items, used for calibration and
validation; `train`. Near-duplicate headlines are dropped before splitting. Labels are written by Claude Code
(`labeler: claude-code`), see training/labelling_guide.md: gold is therefore silver, and reports must say so.

Label line format (`add`): {"id": 12, "e": {"earnings": 0.8, "other": 0.2}, "s": {"neu": 1}, "a": {"log": 0.7, "alert": 0.3}}
= event_type / sentiment / action; omitted options are 0, each question is renormalised, unknown options are rejected.
"""
import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "services" / "laya")]
from core import build_state  # noqa: E402  (imports laya, does not load the model)
from questions import QUESTIONS  # noqa: E402

from common.db import connect  # noqa: E402

DATA = ROOT / "training" / "data"
SPLIT, LABELS, BACKFILL = DATA / "split.jsonl", DATA / "labels.jsonl", DATA / "backfill.jsonl"
GOLD_QUOTA = {"yahoo_ticker": 50, "sec_8k": 30, "cnbc_earnings": 25, "cnbc_finance": 22, "cnbc_top_news": 23}  # 150
TRAIN_TARGET, TRAIN_CAPS, VAL_FRACTION = 1000, {"sec_8k": 300}, 0.10  # SEC filings are boilerplate: cap their share
KEYS = {"e": "event_type", "s": "sentiment", "a": "action"}
LABELER = "claude-code"


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.open(encoding="utf-8")] if p.exists() else []


def write_jsonl(p: Path, rows: list[dict]):
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="")


def words(title: str) -> frozenset:
    return frozenset(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split())


def load_items() -> list[dict]:
    backfilled = {r["uid"] for r in read_jsonl(BACKFILL)}
    with connect() as conn:
        cur = conn.execute("""SELECT id, uid, source, title, summary, focus_ticker, entities, published_at FROM items
                              WHERE status IN ('ner_done', 'laya_done') ORDER BY id""")
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["origin"] = "backfill" if r["uid"] in backfilled else "live"
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    """Drop near-identical headlines (word-set Jaccard >= 0.8), live rows first. SEC titles are '8-K - <company>'
    so every filing is kept (uid is unique per accession)."""
    kept, seen = [], []
    for r in sorted(rows, key=lambda r: (r["origin"] != "live", r["id"])):
        if r["source"] != "sec_8k":
            w = words(r["title"])
            if any(len(w & s) / max(1, len(w | s)) >= 0.8 for s in seen):
                continue
            seen.append(w)
        kept.append(r)
    return sorted(kept, key=lambda r: r["id"])


def water_fill(pools: dict[str, list], target: int, caps: dict[str, int]) -> dict[str, list]:
    """Take one item at a time round-robin from every source until `target`, honouring caps and availability."""
    taken = {s: [] for s in pools}
    while sum(map(len, taken.values())) < target:
        progressed = False
        for s, pool in pools.items():
            if len(taken[s]) < min(len(pool), caps.get(s, 10**9)) and sum(map(len, taken.values())) < target:
                taken[s].append(pool[len(taken[s])])
                progressed = True
        if not progressed:
            break
    return taken


def cmd_select(a):
    rng = random.Random(a.seed)
    rows = dedupe(load_items())
    by_src: dict[str, list] = {}
    for r in rows:
        by_src.setdefault(r["source"], []).append(r)
    gold, pools = [], {}
    for s, items in by_src.items():
        rng.shuffle(items)
        live = [r for r in items if r["origin"] == "live"]
        quota = GOLD_QUOTA.get(s, 0)
        if len(live) < quota:
            print(f"warning: only {len(live)} live {s} items for a gold quota of {quota}")
        g = live[:quota]
        gold += g
        ids = {r["id"] for r in g}
        pools[s] = [r for r in items if r["id"] not in ids]
    chosen = water_fill(pools, TRAIN_TARGET, TRAIN_CAPS)
    out = []
    for r in gold:
        out.append((r, "gold"))
    for s, items in chosen.items():
        nval = round(len(items) * VAL_FRACTION)
        out += [(r, "val" if i < nval else "train") for i, r in enumerate(items)]
    out.sort(key=lambda x: x[0]["id"])
    write_jsonl(SPLIT, [{"id": r["id"], "uid": r["uid"], "source": r["source"], "origin": r["origin"], "split": sp,
                         "published_at": r["published_at"].isoformat() if r["published_at"] else None,
                         "state": build_state(r), "view": view(r)} for r, sp in out])
    print(f"{len(rows)} items after dedupe (from {len(load_items())}); wrote {len(out)} to {SPLIT}")
    cmd_status(a)


def view(r: dict) -> str:
    """Compact text the labeller reads (the model itself sees `state`)."""
    ents = "; ".join(f"{k}: {', '.join(v[:4])}" for k, v in (r["entities"] or {}).items() if v and k in ("COMPANY", "MONEY", "PERCENT"))
    day = r["published_at"].date().isoformat() if r["published_at"] else "?"
    return (f"#{r['id']} [{r['source']} {day}] focus={r['focus_ticker']} | {r['title']} || "
            f"{(r['summary'] or '')[:500]} || {ents}")


def cmd_status(_a):
    split, labels = read_jsonl(SPLIT), {r["id"] for r in read_jsonl(LABELS)}
    tally: dict = {}
    for r in split:
        t = tally.setdefault((r["split"], r["source"]), [0, 0])
        t[0] += 1
        t[1] += r["id"] in labels
    print(f"{'split':6} {'source':15} {'items':>6} {'labelled':>9}")
    for (sp, s), (n, l) in sorted(tally.items()):
        print(f"{sp:6} {s:15} {n:6} {l:9}")
    print(f"{'total':22} {len(split):6} {len([r for r in split if r['id'] in labels]):9}")


def cmd_batch(a):
    labels = {r["id"] for r in read_jsonl(LABELS)}
    todo = [r for r in read_jsonl(SPLIT) if r["id"] not in labels and (not a.split or r["split"] == a.split)]
    random.Random(a.seed).shuffle(todo)  # mixed sources per batch keeps the labeller's calibration even
    for r in todo[: a.n]:
        print(r["view"])
    print(f"-- {min(a.n, len(todo))} shown, {len(todo)} unlabelled", file=sys.stderr)


def cmd_add(_a):
    ids = {r["id"] for r in read_jsonl(SPLIT)}
    have = {r["id"] for r in read_jsonl(LABELS)}
    ok, bad = [], []
    for n, line in enumerate(sys.stdin, 1):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(re.sub(r"([:,\[])\s*\.(\d)", r"\g<1>0.\g<2>", line))  # accept the short form .5
            if o["id"] not in ids:
                raise ValueError("id not in split.jsonl")
            if o["id"] in have or any(o["id"] == x["id"] for x in ok):
                raise ValueError("already labelled")
            rec = {"id": o["id"], "labeler": LABELER}
            for k, q in KEYS.items():
                opts = QUESTIONS[q]["criteria"]
                p = {kk: float(v) for kk, v in o[k].items()}
                unknown = set(p) - set(opts)
                if unknown or any(v < 0 for v in p.values()) or sum(p.values()) <= 0:
                    raise ValueError(f"{q}: bad options {sorted(unknown)} or probabilities")
                tot = sum(p.values())
                rec[q] = {opt: round(p.get(opt, 0.0) / tot, 4) for opt in opts}  # criteria order, sums to 1
            ok.append(rec)
        except Exception as exc:  # report and keep going: the good lines are still stored
            bad.append(f"line {n}: {exc!r}: {line[:80]}")
    with LABELS.open("a", encoding="utf-8", newline="") as f:
        for r in ok:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"stored {len(ok)} labels, rejected {len(bad)}")
    for b in bad[:5]:
        print("  REJECTED", b)


def cmd_export(_a):
    labels = {r["id"]: r for r in read_jsonl(LABELS)}
    files = {"train": [], "val": [], "gold": []}
    for r in read_jsonl(SPLIT):
        lab = labels.get(r["id"])
        if not lab:
            continue
        gold = {q: {"label": max(lab[q], key=lab[q].get), "probabilities": lab[q]} for q in QUESTIONS}
        files[r["split"]].append({"id": r["id"], "source": r["source"], "origin": r["origin"], "labeler": lab["labeler"],
                                  "state": r["state"], "questions": QUESTIONS, "gold": gold})
    names = {"train": "train.jsonl", "val": "val.jsonl", "gold": "gold_set.jsonl"}
    for sp, rows in files.items():
        write_jsonl(DATA / names[sp], rows)
        print(f"{names[sp]}: {len(rows)} rows")
    ids = [{r["id"] for r in rows} for rows in files.values()]
    assert not (ids[0] & ids[2] or ids[1] & ids[2] or ids[0] & ids[1]), "splits overlap"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--seed", type=int, default=1)
    sub.add_parser("status")
    b = sub.add_parser("batch")
    b.add_argument("--n", type=int, default=50)
    b.add_argument("--split", choices=["gold", "val", "train"])
    b.add_argument("--seed", type=int, default=1)
    sub.add_parser("add")
    sub.add_parser("export")
    a = p.parse_args()
    {"select": cmd_select, "status": cmd_status, "batch": cmd_batch, "add": cmd_add, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
