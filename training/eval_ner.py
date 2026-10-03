"""Check NER (and focus-ticker) quality. Runs inside the ner image: `make eval-ner ARGS="sanity"`.

  sanity                 stats from the DB, no labels needed
  cik                    SEC 8-K items: expected ticker from the CIK in the title vs. focus_ticker
  sample [--n 50]        write a labelling template (JSONL) to stdout, stratified by source
  diff                   replay current code on stored spans, list focus tickers that would change
  gold FILE [--sweep]    score against a hand-labelled file; --sweep replays stored spans at 0.3..0.9

Gold file: one JSON object per line, keyed by item `uid`:
  {"uid": "...", "gold": "NVDA"}        the focus ticker, or null when no single company is the focus
  {"uid": "...", "gold": null, "entities": {"COMPANY": ["Nvidia"], "MONEY": ["$5 billion"]}}   optional entities
`"gold": "?"` means not labelled yet and is skipped.
"""
import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict

from common.db import connect
from core import STORE_FLOOR, THRESHOLD, CompanyIndex, FocusResolver, entities_from_stored, load_sec_raw, load_watchlist

SWEEP = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def fetch(conn, where="", args=()):
    cur = conn.execute(
        "SELECT id, uid, source, title, summary, link, focus_ticker, entities, ner_spans FROM items "
        f"WHERE status IN ('ner_done', 'laya_done') {where}", args)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def make_resolver():
    raw, src = load_sec_raw()
    print(f"[sec ticker list: {src}, {len(raw)} companies]", file=sys.stderr)
    return raw, FocusResolver(load_watchlist(), CompanyIndex(raw))


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


# --- sanity ------------------------------------------------------------------------------------------------------
def cmd_sanity(conn, _args):
    raw, _ = make_resolver()
    valid = {e["ticker"].upper() for e in raw.values()}
    items = fetch(conn)
    print(f"{len(items)} items with NER done\n")
    by_src = defaultdict(list)
    for it in items:
        by_src[it["source"]].append(it)
    print(f"{'source':16}{'n':>6}{'focus':>8}{'no entities':>13}{'avg ents':>10}")
    for src, rows in sorted(by_src.items()):
        focus = sum(1 for r in rows if r["focus_ticker"])
        empty = sum(1 for r in rows if not r["entities"])
        avg = sum(sum(len(v) for v in (r["entities"] or {}).values()) for r in rows) / len(rows)
        print(f"{src:16}{len(rows):>6}{pct(focus, len(rows)):>8}{pct(empty, len(rows)):>13}{avg:>10.1f}")

    bad = Counter()
    for it in items:
        for t in (it["entities"] or {}).get("TICKER", []):
            clean = re.sub(r"[^A-Z0-9.\-]", "", t.upper())
            if clean not in valid:
                bad[t] += 1
    print(f"\nTICKER entities that are not in the SEC ticker list ({sum(bad.values())} total):")
    for t, n in bad.most_common(15):
        print(f"  {n:>3}  {t!r}")

    print("\nspan score distribution per label (all stored spans):")
    hist = defaultdict(Counter)
    for it in items:
        for s in it["ner_spans"] or []:
            hist[s["label"]][min(int(s["score"] * 10), 9)] += 1
    print(f"{'label':12}" + "".join(f"{b / 10:>6.1f}" for b in range(int(STORE_FLOOR * 10), 10)))
    for label, h in sorted(hist.items()):
        print(f"{label:12}" + "".join(f"{h[b]:>6}" for b in range(int(STORE_FLOOR * 10), 10)))
    print(f"(bucket = score >= value; entities use threshold {THRESHOLD})")


# --- CIK cross-check -----------------------------------------------------------------------------------------------
def cmd_cik(conn, _args):
    raw, _ = make_resolver()
    by_cik = defaultdict(set)
    for e in raw.values():
        by_cik[int(e["cik_str"])].add(e["ticker"].upper())
    items = fetch(conn, "AND source = 'sec_8k'")
    ok = miss = wrong = unlisted = 0
    examples = []
    for it in items:
        m = re.search(r"\((\d{7,10})\)", it["title"])
        expected = by_cik.get(int(m.group(1))) if m else None
        if not expected:
            unlisted += 1  # CIK not in the SEC list (not an exchange-listed company)
            continue
        pred = it["focus_ticker"]
        if pred in expected:
            ok += 1
        elif pred is None:
            miss += 1
            examples.append(("MISS ", it["title"], sorted(expected), pred))
        else:
            wrong += 1
            examples.append(("WRONG", it["title"], sorted(expected), pred))
    checkable = ok + miss + wrong
    print(f"{len(items)} sec_8k items, {unlisted} without a listed CIK (cannot check), {checkable} checkable")
    print(f"  correct {ok} ({pct(ok, checkable)}), no ticker found {miss}, wrong ticker {wrong}")
    for kind, title, exp, pred in examples[:20]:
        print(f"  {kind} {title[:70]:70} expected {exp} got {pred}")


# --- replay diff ---------------------------------------------------------------------------------------------------
def cmd_diff(conn, args):
    """Replay the current resolver/cleanup code on stored spans and list every focus ticker that would change."""
    _, resolver = make_resolver()
    resolver.first_company_only = args.first_only
    items = fetch(conn)
    changed = []
    for it in items:
        ents = entities_from_stored(it["ner_spans"] or [], THRESHOLD, resolver.valid_tickers)
        new = resolver.resolve(ents)
        if new != it["focus_ticker"]:
            changed.append((it, ents, new))
    print(f"{len(items)} items, {len(changed)} focus tickers would change")
    for it, ents, new in changed:
        print(f"  {str(it['focus_ticker']):6} -> {str(new):6} {it['title'][:70]:70} {ents.get('COMPANY')}")


# --- labelling template --------------------------------------------------------------------------------------------
def cmd_sample(conn, args):
    rng = random.Random(args.seed)
    by_src = defaultdict(list)
    for it in fetch(conn):
        by_src[it["source"]].append(it)
    for rows in by_src.values():
        rng.shuffle(rows)
    picked = []
    while len(picked) < args.n and any(by_src.values()):
        for src in sorted(by_src):
            if by_src[src] and len(picked) < args.n:
                picked.append(by_src[src].pop())
    for it in picked:
        print(json.dumps({"uid": it["uid"], "source": it["source"], "title": it["title"],
                          "summary": (it["summary"] or "")[:300], "link": it["link"], "gold": "?"},
                         ensure_ascii=False))
    print(f"wrote {len(picked)} template rows. Fill in \"gold\" (ticker string, or null for none).", file=sys.stderr)


# --- gold scoring --------------------------------------------------------------------------------------------------
def focus_metrics(pairs):
    """pairs: [(gold, pred)]"""
    n = len(pairs)
    correct = sum(1 for g, p in pairs if g == p)
    preds = [(g, p) for g, p in pairs if p]
    tp = sum(1 for g, p in preds if g == p)
    golds = [(g, p) for g, p in pairs if g]
    return n, correct, tp, len(preds), len(golds)


def entity_metrics(rows, resolver, threshold):
    """Per-label P/R/F1 over case-insensitive text sets, for rows whose gold has `entities`."""
    tp, fp, fn = Counter(), Counter(), Counter()
    for r in rows:
        if "entities" not in r["gold_rec"]:
            continue
        pred = entities_from_stored(r["ner_spans"] or [], threshold, resolver.valid_tickers)
        gold = r["gold_rec"]["entities"]
        for label in set(pred) | set(gold):
            p = {x.lower() for x in pred.get(label, [])}
            g = {x.lower() for x in gold.get(label, [])}
            tp[label] += len(p & g)
            fp[label] += len(p - g)
            fn[label] += len(g - p)
    out = {}
    for label in sorted(set(tp) | set(fp) | set(fn)):
        prec = tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        rec = tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[label] = (prec, rec, f1, tp[label] + fn[label])
    return out


def cmd_gold(conn, args):
    gold = {}
    for line in open(args.file, encoding="utf-8"):
        if line.strip():
            rec = json.loads(line)
            if rec.get("gold") != "?":
                gold[rec["uid"]] = rec
    if not gold:
        sys.exit("no labelled rows (every gold is still \"?\")")
    _, resolver = make_resolver()
    resolver.first_company_only = args.first_only
    items = {it["uid"]: it for it in fetch(conn, "AND uid = ANY(%s)", (list(gold),))}
    rows = [{**items[u], "gold_rec": g} for u, g in gold.items() if u in items]
    missing = len(gold) - len(rows)
    print(f"{len(rows)} labelled items scored" + (f" ({missing} gold rows not found in this DB)" if missing else ""))

    # Default: replay the current code on the stored spans (no model needed). --stored: what the worker wrote.
    for r in rows:
        r["pred"] = r["focus_ticker"] if args.stored else resolver.resolve(
            entities_from_stored(r["ner_spans"] or [], THRESHOLD, resolver.valid_tickers))
    mode = "as stored by the worker" if args.stored else "replayed with current code"
    print(f"\nfocus ticker, {mode} (threshold {THRESHOLD}):")
    pairs = [(r["gold_rec"]["gold"], r["pred"]) for r in rows]
    n, correct, tp, npred, ngold = focus_metrics(pairs)
    print(f"  accuracy {pct(correct, n)} ({correct}/{n})   precision {pct(tp, npred)} ({tp}/{npred})"
          f"   recall {pct(tp, ngold)} ({tp}/{ngold})")
    by_src = defaultdict(list)
    for r in rows:
        by_src[r["source"]].append((r["gold_rec"]["gold"], r["pred"]))
    for src, ps in sorted(by_src.items()):
        n_, c_, *_ = focus_metrics(ps)
        print(f"    {src:16} accuracy {pct(c_, n_)} ({c_}/{n_})")
    errors = [r for r in rows if r["gold_rec"]["gold"] != r["pred"]]
    print(f"\n  errors ({len(errors)}):")
    for r in errors:
        print(f"    gold {str(r['gold_rec']['gold']):6} got {str(r['pred']):6} {r['title'][:80]}")

    if args.sweep:
        print("\nthreshold sweep, replaying stored spans:")
        has_ent = any("entities" in r["gold_rec"] for r in rows)
        print(f"  {'thr':>4}{'accuracy':>10}{'precision':>11}{'recall':>8}")
        for t in SWEEP:
            ps = [(r["gold_rec"]["gold"], resolver.resolve(entities_from_stored(r["ner_spans"] or [], t, resolver.valid_tickers))) for r in rows]
            n, c, tp, npred, ngold = focus_metrics(ps)
            print(f"  {t:>4.1f}{pct(c, n):>10}{pct(tp, npred):>11}{pct(tp, ngold):>8}")
        if has_ent:
            print("\nentity P/R/F1 per label by threshold:")
            for t in SWEEP:
                m = entity_metrics(rows, resolver, t)
                print(f"  thr {t:.1f}: " + "  ".join(f"{k} {v[2]:.2f}" for k, v in m.items()))
    elif any("entities" in r["gold_rec"] for r in rows):
        print(f"\nentities at threshold {THRESHOLD}:  (label: precision / recall / F1, support)")
        for label, (p, r_, f, sup) in entity_metrics(rows, resolver, THRESHOLD).items():
            print(f"  {label:12} {p:.2f} / {r_:.2f} / {f:.2f}   n={sup}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sanity")
    sub.add_parser("cik")
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--seed", type=int, default=7)
    d = sub.add_parser("diff")
    d.add_argument("--first-only", action="store_true")
    g = sub.add_parser("gold")
    g.add_argument("file")
    g.add_argument("--sweep", action="store_true")
    g.add_argument("--stored", action="store_true", help="score items.focus_ticker as written, not a replay")
    g.add_argument("--first-only", action="store_true", help="try the first-company-only focus rule")
    args = ap.parse_args()
    with connect() as conn:
        {"sanity": cmd_sanity, "cik": cmd_cik, "sample": cmd_sample, "gold": cmd_gold, "diff": cmd_diff}[args.cmd](conn, args)


if __name__ == "__main__":
    main()
