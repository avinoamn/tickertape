#!/usr/bin/env python3
"""Run every query of every dashboard in grafana/dashboards through a live Grafana (/api/ds/query) and report
errors and row counts per panel. Stdlib only.

    python grafana/verify_dashboards.py                       # local rig: http://localhost:3000 (anonymous admin)
    GRAFANA_URL=http://computa:30004 GRAFANA_USER=admin GRAFANA_PASSWORD=... python grafana/verify_dashboards.py

Exit code 1 if any query errors. Empty results are reported as NO DATA but are not failures (some panels are
legitimately empty, e.g. 'Failed poller Jobs' or the error table).
"""
import base64
import glob
import json
import os
import sys
import urllib.request

URL = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
USER, PASSWORD = os.environ.get("GRAFANA_USER"), os.environ.get("GRAFANA_PASSWORD")
RANGE = os.environ.get("VERIFY_RANGE", "now-24h")
HERE = os.path.dirname(os.path.abspath(__file__))


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    if USER:
        req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (USER, PASSWORD)).encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # Grafana returns 400 with a JSON body for query errors
        try:
            return json.load(e)
        except Exception:
            return {"message": "HTTP %s" % e.code}


def expand(text, question):
    for a, b in (("$__range", "6h"), ("$__rate_interval", "1m"), ("$question", question)):
        text = text.replace(a, b)
    return text


def run_target(t, question):
    q = {k: v for k, v in t.items() if k != "datasource"}
    q.update({"datasource": t["datasource"], "intervalMs": 60000, "maxDataPoints": 500})
    for key in ("rawSql", "expr"):
        if key in q:
            q[key] = expand(q[key], question)
    res = post("/api/ds/query", {"queries": [q], "from": RANGE, "to": "now"})
    r = (res.get("results") or {}).get(q["refId"])
    if r is None:
        return "ERROR", res.get("message") or json.dumps(res)[:300]
    if r.get("error"):
        return "ERROR", r["error"][:300]
    rows = sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in r.get("frames", []))
    return ("ok" if rows else "NO DATA"), "%d rows, %d frames" % (rows, len(r.get("frames", [])))


def main():
    failed = 0
    for path in sorted(glob.glob(os.path.join(HERE, "dashboards", "*.json"))):
        dash = json.load(open(path))
        print("\n== %s (%s)" % (dash["title"], os.path.basename(path)))
        for p in dash["panels"]:
            for t in p["targets"]:
                status, info = run_target(t, "action")
                failed += status == "ERROR"
                print("  %-8s %-58s [%s] %s" % (status, p["title"][:58], t["refId"], info))
    print("\n%s" % ("FAILED: %d query errors" % failed if failed else "no query errors"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
