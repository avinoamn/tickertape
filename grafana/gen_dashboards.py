#!/usr/bin/env python3
"""Generate grafana/dashboards/{poller,ner,laya,pipeline}.json. Plain stdlib, runs on any Python 3.

    python grafana/gen_dashboards.py

Edit the panels here, regenerate, then apply with scripts/apply-dashboards.sh. Datasource uids match the provisioning
in k8s/monitoring/values.yaml (and grafana/dev/ for the local test rig): Prometheus `prometheus`, Postgres `tickertape-pg`.
"""
import json
import os

PG = {"type": "grafana-postgresql-datasource", "uid": "tickertape-pg"}
PROM = {"type": "prometheus", "uid": "prometheus"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

GREEN, YELLOW, RED, BLUE = "green", "#EAB839", "red", "blue"


def pg(sql, fmt="table", ref="A"):
    return {"refId": ref, "datasource": PG, "format": fmt, "rawQuery": True, "editorMode": "code", "rawSql": sql}


def prom(expr, legend="", ref="A"):
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend, "editorMode": "code", "range": True}


def thresholds(*steps):
    """steps: (value_or_None, color) pairs, first one is the base."""
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


class Dash:
    def __init__(self, uid, title, tags, variables=None, refresh="1m", time_from="now-24h"):
        self.uid, self.title, self.tags = uid, title, tags
        self.variables, self.refresh, self.time_from = variables or [], refresh, time_from
        self.panels, self._x, self._y, self._rowh, self._id = [], 0, 0, 0, 0

    def _place(self, w, h):
        if self._x + w > 24:
            self._x, self._y, self._rowh = 0, self._y + self._rowh, 0
        pos = {"x": self._x, "y": self._y, "w": w, "h": h}
        self._x += w
        self._rowh = max(self._rowh, h)
        return pos

    def add(self, ptype, title, targets, w, h, ds, desc="", options=None, defaults=None, overrides=None, **extra):
        self._id += 1
        panel = {
            "id": self._id, "type": ptype, "title": title, "description": desc, "datasource": ds,
            "targets": targets, "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []},
            "options": options or {},
        }
        panel.update(extra)
        self.panels.append(panel)

    # ---- panel kinds -------------------------------------------------------------------------------------------
    def stat(self, title, target, ds, w=4, h=4, unit="short", th=None, desc="", decimals=None):
        d = {"unit": unit, "thresholds": th or thresholds((None, GREEN)), "color": {"mode": "thresholds"}}
        if decimals is not None:
            d["decimals"] = decimals
        self.add("stat", title, [target], w, h, ds, desc, defaults=d, options={
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "colorMode": "value", "graphMode": "none", "textMode": "auto", "justifyMode": "center"})

    def series(self, title, targets, ds, w=12, h=8, unit="short", bars=False, stack=False, desc="", minimum=None,
               maximum=None, **extra):
        d = {"unit": unit, "custom": {
            "drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 60 if bars else 15,
            "showPoints": "never", "spanNulls": False, "stacking": {"mode": "normal" if stack else "none"}}}
        if minimum is not None:
            d["min"] = minimum
        if maximum is not None:
            d["max"] = maximum
        self.add("timeseries", title, targets, w, h, ds, desc, defaults=d, options={
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"}}, **extra)

    def table(self, title, target, ds, w=12, h=8, desc="", unit_by_col=None, link_col=None, color_cols=None,
              sort=None):
        """unit_by_col: {col: unit}. link_col: column whose value is a URL (made clickable).
        color_cols: {col: thresholds} shown as coloured cell backgrounds."""
        overrides = []
        for col, unit in (unit_by_col or {}).items():
            overrides.append({"matcher": {"id": "byName", "options": col}, "properties": [{"id": "unit", "value": unit}]})
        if link_col:
            overrides.append({"matcher": {"id": "byName", "options": link_col}, "properties": [
                {"id": "links", "value": [{"title": "open", "url": "${__value.raw}", "targetBlank": True}]},
                {"id": "custom.width", "value": 90}]})
        for col, th in (color_cols or {}).items():
            overrides.append({"matcher": {"id": "byName", "options": col}, "properties": [
                {"id": "thresholds", "value": th}, {"id": "color", "value": {"mode": "thresholds"}},
                {"id": "custom.cellOptions", "value": {"type": "color-background"}}]})
        opts = {"showHeader": True, "cellHeight": "sm"}
        if sort:
            opts["sortBy"] = [{"displayName": sort[0], "desc": sort[1]}]
        self.add("table", title, [target], w, h, ds, desc, options=opts,
                 defaults={"custom": {"align": "auto", "filterable": True}}, overrides=overrides)

    def bars(self, title, target, ds, w=8, h=8, desc="", unit="short"):
        """Horizontal bar gauge: first string column = label, numeric column = value."""
        self.add("bargauge", title, [target], w, h, ds, desc, defaults={
            "unit": unit, "min": 0, "color": {"mode": "fixed", "fixedColor": BLUE}}, options={
            "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color",
            "reduceOptions": {"values": True, "calcs": [], "fields": ""}})

    def barchart(self, title, target, ds, xfield, w=8, h=8, desc="", **extra):
        self.add("barchart", title, [target], w, h, ds, desc, defaults={"custom": {"fillOpacity": 70}}, options={
            "xField": xfield, "orientation": "vertical", "showValue": "auto", "stacking": "none",
            "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}, **extra)

    # ---- output ------------------------------------------------------------------------------------------------
    def write(self):
        doc = {
            "uid": self.uid, "title": self.title, "tags": ["tickertape"] + self.tags, "timezone": "browser",
            "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1, "refresh": self.refresh,
            "time": {"from": self.time_from, "to": "now"}, "templating": {"list": self.variables},
            "annotations": {"list": []}, "links": [], "panels": self.panels,
        }
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, self.uid.replace("tickertape-", "") + ".json")
        with open(path, "w", newline="\n") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        print("wrote", path, len(self.panels), "panels")


def question_var():
    return {"name": "question", "label": "Question", "type": "query", "datasource": PG, "refresh": 1,
            "query": "SELECT DISTINCT question FROM decisions ORDER BY 1", "multi": True, "includeAll": True,
            "current": {"selected": True, "text": "All", "value": "$__all"}, "sort": 1}


# --------------------------------------------------------------------------------------------------------------------
def poller():
    d = Dash("tickertape-poller", "Tickertape / Poller", ["poller"])
    d.stat("Total items", pg("SELECT count(*) AS items FROM items"), PG)
    d.stat("Items, last 24h", pg("SELECT count(*) AS items FROM items WHERE created_at > now() - interval '24 hours'"), PG)
    d.stat("Since last successful poller run",
           prom('time() - kube_cronjob_status_last_successful_time{namespace="tickertape", cronjob="poller"}'),
           PROM, unit="s", w=6,
           th=thresholds((None, GREEN), (600, YELLOW), (1800, RED)),
           desc="The CronJob runs every 5 minutes. Yellow after 10 min, red after 30 min.")
    d.stat("Failed poller Jobs",
           prom('sum(kube_job_status_failed{namespace="tickertape", job_name=~"poller-.*"}) or vector(0)'), PROM,
           th=thresholds((None, GREEN), (1, RED)),
           desc="Failed Jobs still held by the CronJob history (kube-state-metrics).")
    d.stat("Sources", pg("SELECT count(DISTINCT source) AS sources FROM items"), PG, w=6)

    d.series("Items ingested per hour, by source", [pg(
        "SELECT $__timeGroupAlias(created_at, '1h'), source AS metric, count(*) AS items FROM items "
        "WHERE $__timeFilter(created_at) GROUP BY 1, 2 ORDER BY 1", "time_series")],
        PG, w=16, bars=True, stack=True)
    d.bars("Items, last 24h, by source", pg(
        "SELECT source, count(*) AS items FROM items WHERE created_at > now() - interval '24 hours' "
        "GROUP BY 1 ORDER BY 2 DESC"), PG, w=8)

    d.table("Feed freshness: minutes since the last new item", pg(
        "SELECT source, round((extract(epoch FROM now() - max(created_at)) / 60)::numeric) AS minutes_since_last_item "
        "FROM items GROUP BY source ORDER BY 2 DESC"), PG, w=12, h=7,
        desc="Red above 2 h. A quiet feed also shows up here, so read red as 'check this feed', not 'broken'.",
        color_cols={"minutes_since_last_item": thresholds((None, GREEN), (120, RED))})
    d.table("Ingest lag, last 24h (created_at - published_at)", pg(
        "SELECT source, count(*) AS items, "
        "round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM created_at - published_at))::numeric) AS p50, "
        "round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM created_at - published_at))::numeric) AS p95 "
        "FROM items WHERE published_at IS NOT NULL AND created_at > now() - interval '24 hours' "
        "GROUP BY 1 ORDER BY 1"), PG, w=12, h=7, unit_by_col={"p50": "s", "p95": "s"},
        desc="Only items that have a published_at. The very first poll after a deploy loads old items, so its lag is large.")

    d.table("Data quality: share of items missing a field", pg(
        "SELECT source, count(*) AS items, "
        "round(100.0 * avg((published_at IS NULL)::int), 1) AS no_published_at_pct, "
        "round(100.0 * avg((summary IS NULL OR summary = '')::int), 1) AS no_summary_pct "
        "FROM items GROUP BY 1 ORDER BY 1"), PG, w=12, h=7,
        unit_by_col={"no_published_at_pct": "percent", "no_summary_pct": "percent"})
    d.series("Poller job health", [
        prom('time() - kube_cronjob_status_last_successful_time{namespace="tickertape", cronjob="poller"}',
             "seconds since last success", "A"),
        prom('sum(kube_job_status_failed{namespace="tickertape", job_name=~"poller-.*"}) or vector(0)',
             "failed jobs", "B")], PROM, w=12, h=7,
        overrides=[{"matcher": {"id": "byName", "options": "seconds since last success"},
                    "properties": [{"id": "unit", "value": "s"}]},
                   {"matcher": {"id": "byName", "options": "failed jobs"},
                    "properties": [{"id": "custom.axisPlacement", "value": "right"}, {"id": "unit", "value": "short"}]}])

    d.table("Latest items", pg(
        "SELECT created_at AS ingested, source, title, link, status FROM items ORDER BY created_at DESC LIMIT 50"),
        PG, w=24, h=12, link_col="link")
    d.write()


def ner():
    d = Dash("tickertape-ner", "Tickertape / NER", ["ner"])
    d.stat("Processed (range)", prom("sum(increase(ner_items_processed_total[$__range]))"), PROM, decimals=0)
    d.stat("Errors (range)", prom("sum(increase(ner_errors_total[$__range])) or vector(0)"), PROM, decimals=0,
           th=thresholds((None, GREEN), (1, RED)))
    d.stat("Waiting for ner", pg("SELECT count(*) AS waiting FROM items WHERE status = 'new'"), PG,
           th=thresholds((None, GREEN), (50, YELLOW), (200, RED)))
    d.stat("With focus ticker (range)", pg(
        "SELECT 100.0 * avg((focus_ticker IS NOT NULL)::int) AS pct FROM items "
        "WHERE ner_done_at IS NOT NULL AND $__timeFilter(ner_done_at)"), PG, unit="percent", decimals=0,
        desc="Share of NER-processed items where a focus ticker was resolved.")
    d.stat("Median ner time per item (range)", pg(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY ner_ms) AS ms FROM items "
        "WHERE ner_done_at IS NOT NULL AND $__timeFilter(ner_done_at)"), PG, unit="ms", decimals=0)
    d.stat("Entities (range)", prom("sum(increase(ner_entities_total[$__range]))"), PROM, decimals=0)

    d.series("Throughput", [prom("sum(rate(ner_items_processed_total[$__rate_interval])) * 60", "items/min")],
             PROM, unit="short", desc="Items per minute.")
    d.series("Inference latency per call (p50 / p95)", [
        prom("histogram_quantile(0.5, sum by (le) (rate(ner_inference_seconds_bucket[$__rate_interval])))", "p50", "A"),
        prom("histogram_quantile(0.95, sum by (le) (rate(ner_inference_seconds_bucket[$__rate_interval])))", "p95", "B")],
        PROM, unit="s", desc="One model call covers a whole batch (up to 16 items). Per-item time is in the next panel.")
    d.series("Errors", [prom("sum(increase(ner_errors_total[$__rate_interval]))", "errors")], PROM, w=8, bars=True,
             unit="short")
    d.series("ner time per item, from the database (p50 / p95)", [pg(
        "SELECT $__timeGroupAlias(ner_done_at, '1h'), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY ner_ms) AS p50, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY ner_ms) AS p95 "
        "FROM items WHERE ner_done_at IS NOT NULL AND $__timeFilter(ner_done_at) GROUP BY 1 ORDER BY 1",
        "time_series")], PG, w=8, unit="ms", desc="items.ner_ms: the batch time split evenly across its items.")
    d.series("Entities by label", [prom("sum by (label) (increase(ner_entities_total[$__rate_interval]))", "{{label}}")],
             PROM, w=8, bars=True, stack=True)

    d.series("Items with a focus ticker (% per hour)", [pg(
        "SELECT $__timeGroupAlias(ner_done_at, '1h'), 100.0 * avg((focus_ticker IS NOT NULL)::int) AS focus_ticker_pct "
        "FROM items WHERE ner_done_at IS NOT NULL AND $__timeFilter(ner_done_at) GROUP BY 1 ORDER BY 1",
        "time_series")], PG, w=12, unit="percent", minimum=0, maximum=100)
    d.table("Focus ticker rate by source (range)", pg(
        "SELECT source, count(*) AS items, round(100.0 * avg((focus_ticker IS NOT NULL)::int), 1) AS focus_ticker_pct "
        "FROM items WHERE ner_done_at IS NOT NULL AND $__timeFilter(ner_done_at) GROUP BY 1 ORDER BY 1"),
        PG, w=12, unit_by_col={"focus_ticker_pct": "percent"})
    d.write()


def laya():
    d = Dash("tickertape-laya", "Tickertape / Laya", ["laya"], variables=[question_var()])
    d.stat("Processed (range)", prom("sum(increase(laya_items_processed_total[$__range]))"), PROM, decimals=0)
    d.stat("Errors (range)", prom("sum(increase(laya_errors_total[$__range])) or vector(0)"), PROM, decimals=0,
           th=thresholds((None, GREEN), (1, RED)))
    d.stat("Waiting for laya", pg("SELECT count(*) AS waiting FROM items WHERE status = 'ner_done'"), PG,
           th=thresholds((None, GREEN), (50, YELLOW), (200, RED)))
    d.stat("Low-confidence answers (range)", pg(
        "SELECT 100.0 * avg((confidence < 0.6)::int) AS pct FROM decisions WHERE $__timeFilter(created_at)"),
        PG, unit="percent", decimals=0, th=thresholds((None, GREEN), (50, YELLOW), (80, RED)),
        desc="Share of answers with calibrated confidence below 0.6.")
    d.stat("Median laya time per item (range)", pg(
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY laya_ms) AS ms FROM items "
        "WHERE laya_done_at IS NOT NULL AND $__timeFilter(laya_done_at)"), PG, unit="ms", decimals=0)
    d.stat("'alert' answers (range)", pg(
        "SELECT count(*) AS alerts FROM decisions WHERE question = 'action' AND answer = 'alert' "
        "AND $__timeFilter(created_at)"), PG, th=thresholds((None, BLUE)),
        desc="'alert' only means a human should look. Not trading advice.")

    d.series("Throughput", [prom("sum(rate(laya_items_processed_total[$__rate_interval])) * 60", "items/min")], PROM,
             desc="Items per minute.")
    d.series("Inference latency per item (p50 / p95)", [
        prom("histogram_quantile(0.5, sum by (le) (rate(laya_inference_seconds_bucket[$__rate_interval])))", "p50", "A"),
        prom("histogram_quantile(0.95, sum by (le) (rate(laya_inference_seconds_bucket[$__rate_interval])))", "p95", "B")],
        PROM, unit="s")
    d.series("Errors", [prom("sum(increase(laya_errors_total[$__rate_interval]))", "errors")], PROM, w=8, bars=True)
    d.series("Container memory vs limit", [
        prom('max(container_memory_working_set_bytes{namespace="tickertape", pod=~"laya-.*", container="laya"})',
             "working set", "A"),
        prom('max(kube_pod_container_resource_limits{namespace="tickertape", pod=~"laya-.*", resource="memory"})',
             "limit", "B")], PROM, w=8, unit="bytes",
        desc="laya sat close to its limit at first (page cache from the model files counts in the cgroup).")
    d.series("CPU (cores)", [
        prom('sum(rate(container_cpu_usage_seconds_total{namespace="tickertape", pod=~"laya-.*", container="laya"}[$__rate_interval]))',
             "laya", "A"),
        prom('sum(rate(container_cpu_usage_seconds_total{namespace="tickertape", pod=~"ner-.*", container="ner"}[$__rate_interval]))',
             "ner", "B")], PROM, w=8, unit="short")

    d.series("Answers per hour: $question", [pg(
        "SELECT $__timeGroupAlias(created_at, '1h'), answer AS metric, count(*) AS answers FROM decisions "
        "WHERE $__timeFilter(created_at) AND question = '$question' GROUP BY 1, 2 ORDER BY 1", "time_series")],
        PG, w=24, h=8, bars=True, stack=True, repeat="question", repeatDirection="h", maxPerRow=3,
        desc="Answer distribution over time, one panel per question (pick questions with the variable).")
    d.barchart("Confidence histogram: $question", pg(
        "SELECT to_char(floor(least(confidence, 0.9999) * 10) / 10, 'FM0.0') AS bucket, count(*) AS answers "
        "FROM decisions WHERE $__timeFilter(created_at) AND question = '$question' GROUP BY 1 ORDER BY 1"),
        PG, "bucket", w=24, h=7, repeat="question", repeatDirection="h", maxPerRow=3,
        desc="Calibrated confidence, bucketed by 0.1. This is the value the pipeline gates on.")
    d.series("Low-confidence share (< 0.6) per hour", [pg(
        "SELECT $__timeGroupAlias(created_at, '1h'), question AS metric, 100.0 * avg((confidence < 0.6)::int) AS low "
        "FROM decisions WHERE $__timeFilter(created_at) GROUP BY 1, 2 ORDER BY 1", "time_series")],
        PG, w=12, unit="percent", minimum=0, maximum=100)
    d.table("Answers by model revision", pg(
        "SELECT model_rev, question, answer, count(*) AS answers, round(avg(confidence)::numeric, 2) AS avg_confidence "
        "FROM decisions WHERE $__timeFilter(created_at) GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC"),
        PG, w=12, desc="Useful for comparing the base model with the fine-tuned one after step 5.")

    d.table("Items the model marked 'alert' (a human should look)", pg(
        "SELECT i.laya_done_at AS answered, i.source, i.focus_ticker AS ticker, i.title, i.link, "
        "round(d.confidence::numeric, 2) AS confidence "
        "FROM decisions d JOIN items i ON i.id = d.item_id WHERE d.question = 'action' AND d.answer = 'alert' "
        "AND $__timeFilter(d.created_at) ORDER BY i.laya_done_at DESC LIMIT 50"), PG, w=24, h=10, link_col="link",
        desc="Not trading advice: 'alert' only means a human should look.")
    d.write()


def pipeline():
    d = Dash("tickertape-pipeline", "Tickertape / Pipeline", ["pipeline"])
    for status, color in (("new", BLUE), ("ner_done", BLUE), ("laya_done", GREEN)):
        d.stat("status: " + status, pg("SELECT count(*) AS items FROM items WHERE status = '%s'" % status), PG, w=5,
               th=thresholds((None, color)))
    d.stat("status: error", pg("SELECT count(*) AS items FROM items WHERE status = 'error'"), PG, w=5,
           th=thresholds((None, GREEN), (1, RED)))
    d.stat("Total", pg("SELECT count(*) AS items FROM items"), PG, w=4)

    d.table("Backlog per stage", pg(
        "SELECT 'ner (status new)' AS stage, count(*) AS waiting, "
        "coalesce(extract(epoch FROM now() - min(created_at)), 0)::int AS oldest_waiting "
        "FROM items WHERE status = 'new' "
        "UNION ALL SELECT 'laya (status ner_done)', count(*), "
        "coalesce(extract(epoch FROM now() - min(ner_done_at)), 0)::int FROM items WHERE status = 'ner_done'"),
        PG, w=10, h=6, unit_by_col={"oldest_waiting": "s"},
        color_cols={"waiting": thresholds((None, GREEN), (50, YELLOW), (200, RED))},
        desc="Rows waiting for each stage and how long the oldest has waited.")
    d.series("Throughput per hour: ingested, ner done, laya done", [
        pg("SELECT $__timeGroupAlias(created_at, '1h'), 'ingested' AS metric, count(*) AS n FROM items "
           "WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1", "time_series", "A"),
        pg("SELECT $__timeGroupAlias(ner_done_at, '1h'), 'ner done' AS metric, count(*) AS n FROM items "
           "WHERE $__timeFilter(ner_done_at) GROUP BY 1 ORDER BY 1", "time_series", "B"),
        pg("SELECT $__timeGroupAlias(laya_done_at, '1h'), 'laya done' AS metric, count(*) AS n FROM items "
           "WHERE $__timeFilter(laya_done_at) GROUP BY 1 ORDER BY 1", "time_series", "C")],
        PG, w=14, h=6, bars=True, desc="Where arrivals outrun completions, the backlog grows.")

    d.series("End-to-end latency (laya_done_at - created_at), p50 / p95", [pg(
        "SELECT $__timeGroupAlias(laya_done_at, '1h'), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM laya_done_at - created_at)) AS p50, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM laya_done_at - created_at)) AS p95 "
        "FROM items WHERE laya_done_at IS NOT NULL AND $__timeFilter(laya_done_at) GROUP BY 1 ORDER BY 1",
        "time_series")], PG, w=14, unit="s",
        desc="Includes queueing time, so a backlog shows up here as a big number.")
    d.bars("Items by source", pg("SELECT source, count(*) AS items FROM items GROUP BY 1 ORDER BY 2 DESC"), PG, w=10)

    d.table("Error rows", pg(
        "SELECT id, source, title, attempts, error FROM items WHERE status = 'error' ORDER BY id DESC LIMIT 50"),
        PG, w=24, h=8, desc="Rows that failed 3 attempts. Empty is good.")
    d.write()


if __name__ == "__main__":
    poller()
    ner()
    laya()
    pipeline()
