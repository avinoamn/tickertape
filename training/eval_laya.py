"""Evaluate Laya checkpoints on the gold set (step 5). Two commands, the same code for base and fine-tuned:

  make eval-laya ARGS="predict --name base --model convaiinnovations/laya"
  make eval-laya ARGS="predict --name ft --model <hf-user>/<repo>"            (private repo: HF_TOKEN=... in the env)
  python training/eval_laya.py report base ft --out training/reports/laya_eval.md

`predict` runs in the laya image (it needs the model) and caches every answer's probabilities in
training/data/preds/<name>.jsonl (resumable). `report` is stdlib only (host Python is fine) and compares the cached
predictions against training/data/gold_set.jsonl; the first name is the reference ("base").

THE GOLD LABELS ARE CLAUDE CODE'S, NOT HUMAN: every number measures agreement with Claude Code's labels, and the
report says so on its first lines.

Metrics, per question, over the gold items (all computed from the predicted `probabilities`, so they do not depend
on laya's confidence fields):
  accuracy  top-1 option == the gold `label`
  Brier     multiclass, sum_k (p_k - q_k)^2 against the SOFT gold distribution q, mean over items (lower is better)
  ECE       10 equal-width bins on max(p) vs top-1 correctness (the same quantity as laya's `answer_confidence`)
  95% CIs   percentile bootstrap over items (2000 resamples, seed 0), and paired differences against the reference
Also: the class prior of the training labels as a "prior" baseline (what you get by ignoring the text), and for
`action` the alert probability mass, because alert is almost absent from the labels.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "training" / "data"
PREDS = DATA / "preds"
GOLD, TRAIN = DATA / "gold_set.jsonl", DATA / "train.jsonl"
BINS, BOOT = 10, 2000
PRIOR = "prior"


def read_jsonl(p: Path) -> list:
    return [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()]


# ---------------------------------------------------------------- predict (needs laya)

def cmd_predict(a):
    os.environ.setdefault("USE_TF", "0")  # before importing laya/transformers
    import importlib.metadata

    import laya

    rows = read_jsonl(Path(a.data))
    PREDS.mkdir(parents=True, exist_ok=True)
    out = PREDS / f"{a.name}.jsonl"
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    agent = laya.load(a.model, revision=a.revision)
    rev = getattr(agent, "revision", None)
    meta = {"name": a.name, "model": a.model, "revision": rev, "laya": importlib.metadata.version("laya"),
            "data": a.data, "items": len(rows), "date": time.strftime("%Y-%m-%d")}
    (PREDS / f"{a.name}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"model {a.model}@{rev}, {len(rows) - len(done)} of {len(rows)} items to do", flush=True)
    t0 = time.time()
    with out.open("a", encoding="utf-8", newline="") as f:
        for n, row in enumerate(rows, 1):
            if row["id"] in done:
                continue
            res = agent.predict(row["state"], row["questions"])
            answers = {q: {k: ans.get(k) for k in ("choice", "probabilities", "confidence", "answer_confidence")}
                       for q, ans in res["answers"].items()}
            f.write(json.dumps({"id": row["id"], "answers": answers,
                                "truncated": (res.get("usage") or {}).get("truncated")}) + "\n")
            f.flush()
            if n % 10 == 0:
                print(f"{n}/{len(rows)}  {time.time() - t0:.0f}s", flush=True)
    print(f"done: {out}", flush=True)


# ---------------------------------------------------------------- report (stdlib only)

def item_stats(gold: list, preds: dict, qid: str) -> dict:
    """Per-item arrays for one question: correct (0/1), brier, conf (max p), p_alert, top option."""
    opts = list(gold[0]["questions"][qid]["criteria"])
    s = {"opts": opts, "correct": [], "brier": [], "conf": [], "top": [], "p": []}
    for r in gold:
        g = r["gold"][qid]
        p = preds[r["id"]]["answers"][qid]["probabilities"]
        pv = [float(p.get(o, 0.0)) for o in opts]
        qv = [float(g["probabilities"].get(o, 0.0)) for o in opts]
        top = max(range(len(opts)), key=lambda k: pv[k])
        s["correct"].append(1.0 if opts[top] == g["label"] else 0.0)
        s["brier"].append(sum((x - y) ** 2 for x, y in zip(pv, qv)))
        s["conf"].append(pv[top])
        s["top"].append(opts[top])
        s["p"].append(pv)
    return s


def m_acc(s, idx): return sum(s["correct"][i] for i in idx) / len(idx)
def m_brier(s, idx): return sum(s["brier"][i] for i in idx) / len(idx)
def m_conf(s, idx): return sum(s["conf"][i] for i in idx) / len(idx)


def m_ece(s, idx):
    bins = [[0, 0.0, 0.0] for _ in range(BINS)]
    for i in idx:
        b = bins[min(int(s["conf"][i] * BINS), BINS - 1)]
        b[0] += 1
        b[1] += s["conf"][i]
        b[2] += s["correct"][i]
    return sum(abs(conf_sum - hit_sum) for cnt, conf_sum, hit_sum in bins if cnt) / len(idx)


METRICS = [("accuracy", m_acc), ("Brier", m_brier), ("ECE", m_ece), ("mean confidence", m_conf)]


def pct(vals: list, q: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(q * len(vals))))]


def ci(point: float, boots: list, digits: int = 3) -> str:
    return f"{point:.{digits}f} [{pct(boots, 0.025):.{digits}f}, {pct(boots, 0.975):.{digits}f}]"


def prior_preds(gold: list, train: list) -> dict:
    """Predict every item with the mean training label distribution (the 'ignore the text' baseline)."""
    dist = {}
    for qid, spec in gold[0]["questions"].items():
        opts = list(spec["criteria"])
        dist[qid] = {o: sum(r["gold"][qid]["probabilities"].get(o, 0.0) for r in train) / len(train) for o in opts}
    return {r["id"]: {"answers": {q: {"probabilities": d} for q, d in dist.items()}} for r in gold}


def per_class(s: dict, gold_labels: list) -> list:
    rows = []
    for o in s["opts"]:
        support = sum(1 for g in gold_labels if g == o)
        pred_n = sum(1 for t in s["top"] if t == o)
        hit = sum(1 for g, t in zip(gold_labels, s["top"]) if g == o and t == o)
        rows.append((o, support, pred_n, hit))
    return rows


def cmd_report(a):
    gold = read_jsonl(GOLD)
    n = len(gold)
    models, meta = {}, {}
    for name in a.names:
        models[name] = {r["id"]: r for r in read_jsonl(PREDS / f"{name}.jsonl")}
        missing = [r["id"] for r in gold if r["id"] not in models[name]]
        if missing:
            sys.exit(f"{name}: {len(missing)} gold items have no prediction (run `predict` first), e.g. {missing[:5]}")
        mp = PREDS / f"{name}.meta.json"
        meta[name] = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    if TRAIN.exists():
        models[PRIOR] = prior_preds(gold, read_jsonl(TRAIN))
    names = list(models)
    ref = a.names[0]
    qids = list(gold[0]["questions"])

    rng = random.Random(0)
    boots = [[rng.randrange(n) for _ in range(n)] for _ in range(BOOT)]
    full = list(range(n))
    stats = {m: {q: item_stats(gold, models[m], q) for q in qids} for m in names}
    gold_labels = {q: [r["gold"][q]["label"] for r in gold] for q in qids}

    sources = {}
    for r in gold:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    L = []
    L += ["# Laya evaluation on the gold set", "",
          "> **The gold labels were written by Claude Code (an LLM), not by a human.** Everything below measures "
          "agreement with Claude Code's labels, not ground truth. The same labeller wrote the training labels, so a "
          "fine-tuned model learning Claude Code's habits is rewarded here. Spot-check "
          "`training/data/gold_set.jsonl` before reading much into small differences.", ""]
    L += [f"- Gold items: **{n}**, live items only, never trained on or used for calibration "
          f"({', '.join(f'{k} {v}' for k, v in sorted(sources.items()))}).",
          f"- Date: {time.strftime('%Y-%m-%d')}. 95% intervals are percentile bootstraps over items "
          f"({BOOT} resamples); differences are paired (same resamples).",
          "- Accuracy: top-1 against the gold `label`. Brier: multiclass, against the soft gold distribution "
          "(lower is better; always predicting the training prior is the reference). ECE: 10 bins of max probability "
          "vs top-1 correctness (lower is better).", ""]
    for m in a.names:
        mm = meta[m]
        L.append(f"- `{m}`: {mm.get('model', '?')} @ {str(mm.get('revision') or '?')[:12]}, laya {mm.get('laya', '?')}")
    if PRIOR in models:
        L.append(f"- `{PRIOR}`: every item gets the mean training label distribution (no model).")
    L.append("")

    L += ["## Headline", ""]
    for q in qids:
        L += [f"### {q}", "", "| model | accuracy | Brier | ECE | mean confidence |", "|---|---|---|---|---|"]
        for m in names:
            cells = []
            for _, fn in METRICS:
                cells.append(ci(fn(stats[m][q], full), [fn(stats[m][q], b) for b in boots]))
            L.append(f"| {m} | " + " | ".join(cells) + " |")
        L.append("")

    if len(a.names) > 1:
        L += [f"## Difference against `{ref}` (paired)", "",
              "Accuracy: higher is better. Brier and ECE: lower is better. An interval that excludes 0 is a real "
              "difference on this gold set (still agreement with Claude Code's labels).", ""]
        for m in a.names[1:]:
            L += [f"### `{m}` minus `{ref}`", "", "| question | accuracy | Brier | ECE |", "|---|---|---|---|"]
            for q in qids:
                cells = []
                for _, fn in METRICS[:3]:
                    point = fn(stats[m][q], full) - fn(stats[ref][q], full)
                    bs = [fn(stats[m][q], b) - fn(stats[ref][q], b) for b in boots]
                    cells.append(("**" if (pct(bs, 0.025) > 0 or pct(bs, 0.975) < 0) else "") + ci(point, bs)
                                 + ("**" if (pct(bs, 0.025) > 0 or pct(bs, 0.975) < 0) else ""))
                L.append(f"| {q} | " + " | ".join(cells) + " |")
            L.append("")

    if "action" in qids and "alert" in stats[ref]["action"]["opts"]:
        k = stats[ref]["action"]["opts"].index("alert")
        gold_mass = sum(r["gold"]["action"]["probabilities"].get("alert", 0.0) for r in gold) / n
        gold_top = sum(1 for g in gold_labels["action"] if g == "alert")
        L += ["## Alert (the `action` question)", "",
              f"Alert is rare in the labels (mean probability {gold_mass:.1%}, {gold_top} of {n} gold items have it on "
              "top), so it cannot be scored as an accuracy class. What matters is whether the model floods the "
              "dashboard with alerts.", "",
              "| model | mean P(alert) | items with alert on top | share |", "|---|---|---|---|",
              f"| gold labels | {gold_mass:.1%} | {gold_top} | {gold_top / n:.1%} |"]
        for m in names:
            s = stats[m]["action"]
            mass = sum(p[k] for p in s["p"]) / n
            tops = sum(1 for t in s["top"] if t == "alert")
            L.append(f"| {m} | {mass:.1%} | {tops} | {tops / n:.1%} |")
        L.append("")

    L += ["## Per-class counts (top-1)", "",
          "Support is the number of gold items whose top label is the class; the rarer classes have only a handful, "
          "so per-class recall is noisy.", ""]
    for q in qids:
        L += [f"### {q}", "", "| class | gold | " + " | ".join(f"{m} predicted / correct" for m in names) + " |",
              "|---|---|" + "---|" * len(names)]
        per = {m: per_class(stats[m][q], gold_labels[q]) for m in names}
        for i, o in enumerate(stats[ref][q]["opts"]):
            L.append(f"| {o} | {per[ref][i][1]} | " + " | ".join(f"{per[m][i][2]} / {per[m][i][3]}" for m in names) + " |")
        L.append("")

    trunc = {m: sum(1 for r in models[m].values() if r.get("truncated")) for m in a.names}
    L += ["## Caveats", "",
          "- Silver labels (Claude Code), titles and summaries only, no hindsight; same-story items were labelled alike.",
          "- Small gold set: about ±9 points on accuracy per question at 100 items, a bit better at 150; the rarer "
          "event types have few examples.",
          "- Gold items are live items (Yahoo, CNBC, SEC from the poller); training items are mostly backfilled "
          "CNBC and SEC, so a gap between train-time and gold-time behaviour is possible.",
          "- Items whose state was truncated by laya: " + ", ".join(f"{m} {c}" for m, c in trunc.items()) + ".", ""]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:  # write_text(newline=) needs Python 3.10
        f.write("\n".join(L))
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--name", required=True, help="short name for the cache file, e.g. base or ft")
    p.add_argument("--model", required=True, help="laya checkpoint: HF repo id or local path")
    p.add_argument("--revision", default=None, help="optional commit pin")
    p.add_argument("--data", default=str(GOLD.relative_to(ROOT)), help="rows to predict (default: the gold set)")
    p.set_defaults(fn=cmd_predict)
    r = sub.add_parser("report")
    r.add_argument("names", nargs="+", help="cached prediction names; the first is the reference")
    r.add_argument("--out", default="training/reports/laya_eval.md")
    r.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
