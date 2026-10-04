"""Rules about the rendered Helm chart that protect `helm upgrade --wait` / `--atomic`.

They render the chart with `helm template`, so they are skipped when helm is not installed (CI installs it).
"""
import shutil
import subprocess

import pytest
import yaml
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

CHART = ROOT / "charts" / "tickertape"


def render(*args):
    out = subprocess.run(
        ["helm", "template", "tickertape", str(CHART), "-n", "tickertape", "--api-versions", "monitoring.coreos.com/v1", *args],
        check=True, capture_output=True, text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def claims_used_by(docs, kinds):
    """Names of the claims mounted by pods of the given workload kinds (hook Jobs excluded: they run after the wait)."""
    used = set()
    for d in docs:
        if d["kind"] not in kinds or "helm.sh/hook" in d["metadata"].get("annotations", {}):
            continue
        for volume in d["spec"]["template"]["spec"].get("volumes", []):
            if "persistentVolumeClaim" in volume:
                used.add(volume["persistentVolumeClaim"]["claimName"])
    return used


@pytest.mark.parametrize("args", [[], ["--set", "backup.enabled=false"]])
def test_every_claim_is_used_by_a_pod_that_exists_during_the_wait(args):
    """Storage classes with WaitForFirstConsumer binding (k3s's local-path) bind a claim only when a pod uses it, and
    `helm --wait` waits for every claim to be Bound. A claim used only by a CronJob would stay Pending until the CronJob
    first runs, so the deploy would hang until its timeout (it did, once). Every claim needs a Deployment, StatefulSet or
    (non-hook) Job that mounts it."""
    docs = render(*args)
    claims = {d["metadata"]["name"] for d in docs if d["kind"] == "PersistentVolumeClaim"}
    assert claims, "the chart renders no claims at all"
    unused = claims - claims_used_by(docs, {"Deployment", "Job"})
    assert not unused, f"claims with no early consumer, helm --wait would hang on them: {sorted(unused)}"


def test_the_backup_volume_is_kept_on_uninstall():
    claim = next(d for d in render() if d["kind"] == "PersistentVolumeClaim" and d["metadata"]["name"] == "postgres-backups")
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def alert_rules(*args):
    rule = next(d for d in render(*args) if d["kind"] == "PrometheusRule")
    return [r for g in rule["spec"]["groups"] for r in g["rules"]]


def test_alert_rules_cover_the_pipeline_services_and_backups():
    names = {r["alert"] for r in alert_rules()}
    assert {"PollerNotSucceeding", "ModelServiceDown", "ContainerRestarting", "BackupJobFailed", "BackupStale"} <= names


def test_every_alert_says_what_is_wrong_and_how_urgent_it_is():
    for r in alert_rules():
        assert r["annotations"]["summary"], r["alert"]
        assert r["labels"]["severity"] in {"info", "warning", "critical"}, r["alert"]


def test_alert_thresholds_follow_the_values():
    rules = {r["alert"]: r["expr"] for r in alert_rules("--set", "alerts.pollerMaxAgeMinutes=30", "--set", "backup.restoreTest.maxAgeHours=48")}
    assert "> 1800" in rules["PollerNotSucceeding"]
    assert "> 172800" in rules["BackupStale"]


def test_alerts_can_be_turned_off_and_need_the_operator_crds():
    assert not [d for d in render("--set", "alerts.enabled=false") if d["kind"] == "PrometheusRule"]
    out = subprocess.run(["helm", "template", "tickertape", str(CHART), "-n", "tickertape"], check=True, capture_output=True, text=True).stdout
    assert "PrometheusRule" not in out
