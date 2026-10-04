# Operations

How to run tickertape on a Kubernetes cluster (developed on a single-node k3s home server). Read [architecture.md](architecture.md) first for what the pieces are.

In the scripts the node is called `computa`: an SSH alias and a hostname you should replace with your own (`~/.ssh/config` entry named `computa`, or edit the scripts). Below, `<node>` means that host.

> Anything that changes the cluster (apply, delete, restart, Helm upgrade, secrets) is a deliberate action. The scripts say so in their header comments.

## Access model

| Tool | Runs as | Use it for |
|---|---|---|
| `scripts/kc.sh <kubectl args>` | the `deployer` ServiceAccount, over the API at `https://<node>:6443` | everything day to day: the namespace `tickertape` only |
| `scripts/kc-admin.sh <kubectl args>` | cluster admin, over SSH on the node | cluster-scoped work only: namespace, RBAC, Helm and CRDs (monitoring) |

`deployer` is created by `make bootstrap` (`k8s/rbac.yaml`) and can manage workloads, services, config, secrets, jobs, ingresses and ServiceMonitors in `tickertape`. It cannot touch other namespaces or cluster-scoped objects, and cannot use the `scale` subresource: to stop or start a Deployment patch its replicas.

```sh
scripts/kc.sh patch deployment/laya -n tickertape --type=merge -p '{"spec":{"replicas":0}}'   # stop (1 to start)
```

The deployer kubeconfig is written to `~/.kube/tickertape` and contains a long-lived token. Never commit it. To revoke access, delete the Secret `deployer-token` in `tickertape`.

## The Helm chart

Everything in the `tickertape` namespace except the namespace, RBAC and Secrets is one Helm chart, `charts/tickertape`: Postgres (StatefulSet and volume), the schema (a ConfigMap applied by a hook Job after every install and upgrade), the poller CronJob, ner and laya (Deployments, cache volumes, ClusterIP and NodePort Services), the Ingresses and the ServiceMonitors. Settings live in `charts/tickertape/values.yaml`: image repositories and tags, the Laya model and revision, resources, NodePorts, ingress hosts and the Secret names. The kube-prometheus-stack is a separate release (below).

Notes on how it behaves:

- Resource names are fixed (`postgres`, `ner`, `laya`, ...), so install one release per namespace.
- The ServiceMonitors are only created when the Prometheus Operator CRDs exist. Install the monitoring stack first, or run the deploy again afterwards.
- Pod templates carry only the label `app: <name>`, on purpose: changing a pod template restarts the pod, and ner and laya reload their model on every start. The common labels are on object metadata.
- The chart never creates or deletes Secrets, and Helm does not delete the Postgres volume claim (it belongs to the StatefulSet, not to the release).

Check the chart without a cluster (lint, render three value sets, validate against the Kubernetes schemas; also runs in CI):

```sh
make chart-check
```

## First-time setup

1. **Bootstrap access** (admin step, once): `make bootstrap` creates the namespace, the RBAC and `~/.kube/tickertape`.
2. **Images.** Nothing to do for released versions: the chart pins public images on GHCR (`ghcr.io/avinoamn/tickertape-<svc>`), which the node pulls itself. To run unreleased code, build and import a local image and point the chart at it (see "Releasing a new version of a service" below).
3. **Deploy:** `SEC_USER_AGENT="Your Name you@example.com" make deploy`. It creates the Secrets (below) and runs `helm upgrade --install tickertape charts/tickertape --atomic` as the `deployer` ServiceAccount (`scripts/helm.sh`). `--atomic` waits until everything is ready and rolls back if the release fails. The first start of ner and laya downloads their models into a volume (allow several minutes). Extra Helm arguments go in `HELM_ARGS`, for example `make deploy HELM_ARGS="--set laya.image.tag=0.1.2"`.
4. **Check:** `make status`, `scripts/helm.sh status tickertape`, then open `http://<node>:30002` (ner) and `http://<node>:30003` (laya), and trigger a poll without waiting for the schedule:
   ```sh
   scripts/kc.sh create job poller-manual-$(date +%s) --from=cronjob/poller -n tickertape
   ```
5. **Monitoring** (optional, admin): `make monitoring-secrets` then `make monitoring-install` (Helm release `kps`, chart pinned in `scripts/install-monitoring.sh`, values in `k8s/monitoring/values.yaml`). Grafana is at `http://<node>:30004`: anonymous viewing, admin login for changes. The admin password is generated in the cluster:
   ```sh
   scripts/kc-admin.sh get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d
   ```

### Adopting an install that was made with `kubectl apply`

Earlier versions of this project were deployed with plain manifests. Helm refuses to take over objects it did not create, unless you ask for it once with `--take-ownership` (Helm 3.17 or newer). The chart's defaults reproduce those manifests exactly (checked field by field), so adoption changes only labels and annotations and restarts no pod. The sequence, with a database dump first because Postgres holds the only irreplaceable data:

```sh
scripts/kc.sh exec -n tickertape postgres-0 -- pg_dump -U tickertape -Fc tickertape > tickertape-before-helm.dump
scripts/kc.sh delete job db-init -n tickertape --ignore-not-found      # the hook Job recreates it
scripts/helm.sh upgrade --install tickertape charts/tickertape --take-ownership --dry-run=server   # must succeed
helm template tickertape charts/tickertape -n tickertape --api-versions monitoring.coreos.com/v1 | scripts/kc.sh diff -f -   # review: only added labels and the Job
scripts/helm.sh upgrade --install tickertape charts/tickertape --take-ownership --wait --timeout 20m   # the adoption itself
```

**Do not use `--atomic` (and so not `scripts/deploy.sh`) for the adoption run.** If a first install fails, `--atomic` uninstalls the release, and uninstalling a release that adopted live objects deletes them: the Postgres StatefulSet (its volume claim survives, but the database goes down), the Deployments and the model caches. Without `--atomic`, a failure leaves the release marked as failed and every object in place, and you fix the problem and run the same command again. The same applies to `helm uninstall`: never run it on this release unless you mean to take the namespace down.

Afterwards `scripts/helm.sh list` shows the release, the pods keep their age, and the item count in Postgres is unchanged. From then on use plain `make deploy` (with `--atomic`: an upgrade that fails rolls back to the previous revision, which is safe).

### Secrets

Secrets are never in the repository or in the chart; the chart only refers to them by name (`secrets.*` in the values). They are created from environment variables or generated:

| Secret | Created by | Content |
|---|---|---|
| `postgres-credentials` | `scripts/create-secrets.sh` (once, generated; it is never rotated by the script because that would lock out the existing volume) | database user, password, URL |
| `poller-config` | `scripts/create-secrets.sh` | `SEC_USER_AGENT` |
| `hf-read-token` | `scripts/create-hf-secret.sh` | a Hugging Face read token, only needed to run a private fine-tuned model (set `secrets.hfToken=""` to run without it) |
| `grafana-admin`, `grafana-db` (namespace `monitoring`) | `scripts/create-monitoring-secrets.sh` | generated Grafana admin password and the password of the read-only database role `grafana_ro` |

For a token, avoid shell history and the process list:

```sh
read -s HF_READ_TOKEN; export HF_READ_TOKEN; scripts/create-hf-secret.sh; unset HF_READ_TOKEN
```

Create Secrets before upgrading a release that references them: ner and laya use the `Recreate` strategy, so a missing Secret leaves the old pod stopped and the new one unable to start.

## Deploying a new version of a service

Each service and the chart are released separately ([releasing.md](releasing.md)). To get a change onto the cluster:

1. **Release the service**: version bump and changelog in a PR, merge, push the `<service>-vX.Y.Z` tag. The release workflow publishes the image to GHCR.
2. **Release the chart** with the new pin: `values.yaml` (`<service>.image.tag`), the chart version and the changelog in a PR, merge, push the `chart-vX.Y.Z` tag.
3. **Optionally pre-pull** the new image so the pause is only the model load. ner and laya use the `Recreate` strategy, so the old pod stops before the new one pulls its image, and the pull (about 70 s for each of them) is added to the pause. A throwaway pod caches the image on the node without touching the running pods:
   ```sh
   scripts/kc.sh run prepull-ner -n tickertape --image=ghcr.io/<owner>/tickertape-ner:0.2.0 --restart=Never --command -- true
   scripts/kc.sh wait --for=jsonpath='{.status.phase}'=Succeeded pod/prepull-ner -n tickertape --timeout=10m
   scripts/kc.sh delete pod prepull-ner -n tickertape
   ```
4. **Deploy**, either from GitHub (Actions, Deploy, with the chart version: dry run first, then for real; it pre-pulls, upgrades and smoke-tests, see [releasing.md](releasing.md#deploying-from-github-actions)) or from your machine, from the chart tag's checkout: `git switch --detach chart-vX.Y.Z && make deploy`. Only the services whose pin changed restart. Watch with `scripts/kc.sh rollout status deployment/<service> -n tickertape`.

Unreleased code is tried locally with docker compose ([development.md](development.md)); it is not deployed to the cluster.

ner and laya reload their model on every start (about a minute), during which that stage pauses; items wait in Postgres and nothing is lost. To undo a bad release: `scripts/helm.sh rollback tickertape` (to the previous revision) or `scripts/helm.sh history tickertape` and `rollback tickertape <revision>`.

## Changing or rolling back the Laya model

The model is configuration, not code: `laya.model` and `laya.revision` in `charts/tickertape/values.yaml` (the revision is a pinned Hugging Face commit). To switch, edit them and `make deploy`. To go back to the public base model set `laya.model=convaiinnovations/laya` and `laya.revision=""` (and `secrets.hfToken=""` if no private repository is involved).

Items already answered keep their answers (the `decisions.model_rev` column says which model gave them). To re-answer old items with a new model, reset them in SQL; the answers are upserted:

```sql
update items set status = 'ner_done', attempts = 0 where status = 'laya_done';
```

Reprocessing runs at the model's speed (around 17 s per item on the reference node), and the dashboards' end-to-end latency is inflated for those items until they drain.

## Monitoring and dashboards

- Dashboards are provisioned from ConfigMaps labelled `grafana_dashboard=1`. After editing `grafana/gen_dashboards.py` run `make dashboards`, then `scripts/apply-dashboards.sh` (admin) to publish; Grafana picks them up within a minute.
- Check every panel against the live Grafana: `GRAFANA_URL=http://<node>:30004 make verify-dashboards`.
- Prometheus is internal only. It keeps 365 days of history, capped at 5 GB on a 6 Gi volume, which works because the scrape set is trimmed. Watch `prometheus_tsdb_storage_blocks_bytes`; local-path volumes cannot be resized, so growing it means a new volume.
- Postgres-backed panels are not limited by Prometheus retention (items are kept forever).

## Troubleshooting

| Symptom | Things to check |
|---|---|
| Items stay `new` | ner pod running and ready? `scripts/kc.sh logs deployment/ner -n tickertape`. |
| Items stay `ner_done` | laya pod, memory (limit 3.5 Gi) and restarts: `scripts/kc.sh get pods -n tickertape`. Laya needs about 17 s per item, so a burst of news produces a backlog by design (Pipeline dashboard, "Backlog per stage"). |
| Items in `error` | `select error, count(*) from items where status = 'error' group by 1;` then fix the cause and reset: `update items set status = 'ner_done' (or 'new'), attempts = 0 where status = 'error';` |
| ner container exits with code 132 | `SIGILL`: the CPU lacks AVX and a library used an instruction it does not have. It happened once at first start and did not recur. If it does: set `ONEDNN_MAX_CPU_ISA=SSE41` in the Deployment, or use another torch build. Rows are safe across crashes. |
| laya does not start after a model change | `Recreate` stops the old pod first. Check `kubectl describe pod` for a missing Secret or an unreachable Hugging Face repository (private repo: `hf-read-token` and its repository access). |
| `make deploy` hangs and then rolls back, with a volume claim stuck in `Pending` | A claim that no pod uses yet stays unbound on `local-path` storage, and `helm --wait` waits for it. Every claim in the chart has a consumer that exists during the wait (`tests/test_chart.py` enforces it); if you add a claim, give it one. To unblock a hung deploy, start any pod that mounts the claim (for a CronJob: `scripts/kc.sh create job x-$(date +%s) --from=cronjob/<name> -n tickertape`). |
| New ServiceMonitor target missing in Prometheus | Allow about 3 minutes for the operator to reload. |
| A poller feed returns nothing | Feeds change and block bots. Test the URL from your machine and from the node; disable it in `services/poller/feeds.yaml` with `enabled: false` and note why. |

Useful read-only queries (the `psql` pod is `postgres-0`):

```sh
scripts/kc.sh exec -n tickertape postgres-0 -- psql -U tickertape -Atc "select status, count(*) from items group by 1"
scripts/kc.sh exec -n tickertape postgres-0 -- psql -U tickertape -Atc "select model_rev, count(*) from decisions group by 1"
```

## Backups

Postgres is the only irreplaceable data (items and answers). Everything else is rebuilt from the repository, the Secrets (recreated by the scripts) or the Hugging Face model. The volumes use the `local-path` storage class with reclaim policy `Delete`, so deleting the claim or the namespace deletes the data: that is what the backups are for.

### What runs

The chart (`backup.enabled`, on by default) creates:

| Object | When | What it does |
|---|---|---|
| CronJob `postgres-backup` | daily at 03:00 (`backup.schedule`) | `pg_dump -Fc` into the volume `postgres-backups`. The archive is checked to be readable and to hold the data of every table before it replaces anything, and only the newest 14 (`backup.keep`) are kept. |
| CronJob `postgres-restore-test` | Sundays at 04:30 (`backup.restoreTest.schedule`) | Restores the newest dump into a scratch database on the same server, checks that the tables have data, drops the scratch database, and **fails if the newest dump is older than 36 hours** (`backup.restoreTest.maxAgeHours`), which means backups have stopped. |

A failing job shows in `scripts/kc.sh get jobs -n tickertape` and in the kube-state-metrics job-health data that the Poller dashboard already uses; alert rules for it are tracked in the issues. Look at them, and run either one right now instead of waiting for the schedule:

```sh
scripts/kc.sh get cronjob postgres-backup postgres-restore-test -n tickertape
scripts/kc.sh create job backup-now-$(date +%s) --from=cronjob/postgres-backup -n tickertape
scripts/kc.sh create job restore-test-now-$(date +%s) --from=cronjob/postgres-restore-test -n tickertape
scripts/kc.sh logs -n tickertape -l job-name=backup-now-<timestamp>      # one JSON line: file, bytes, how many are kept
```

The scripts (`charts/tickertape/files/backup.sh` and `restore-test.sh`) are tested end to end by `make backup-test` (also in CI) against a throw-away Postgres: a good backup, retention, a missing, stale and corrupt backup (the restore test must fail), cleanup of the scratch database, and the disaster-recovery command below.

### Copy a dump off the node

The in-cluster dumps are on the same disk as the database, so they do not survive losing the node. Pull a copy to your machine regularly, and before anything risky (a major Postgres upgrade, changing the volume):

```sh
make backup-pull          # saves ~/tickertape-backups/tickertape-<timestamp>.dump, verified readable; read-only on the cluster
```

Sync that folder wherever you keep backups. The dumps contain third-party news text: keep them private.

### Restoring

Always rehearse against a scratch database first (the weekly job does this). To bring a dump back into the **live** database after data loss:

1. Stop the writers so nothing is inserted while you restore: suspend the poller and stop ner and laya.
   ```sh
   scripts/kc.sh patch cronjob/poller -n tickertape -p '{"spec":{"suspend":true}}'
   scripts/kc.sh patch deployment/ner  -n tickertape --type=merge -p '{"spec":{"replicas":0}}'
   scripts/kc.sh patch deployment/laya -n tickertape --type=merge -p '{"spec":{"replicas":0}}'
   ```
2. Restore. From a dump on your machine (after `make backup-pull`):
   ```sh
   scripts/kc.sh exec -i -n tickertape postgres-0 -- pg_restore --clean --if-exists --no-owner -U tickertape -d tickertape < ~/tickertape-backups/<dump>
   ```
   From the in-cluster volume, run the same `pg_restore` in a throw-away pod that mounts the claim `postgres-backups`.
3. Check the counts (`select status, count(*) from items group by 1`), then start everything again: `"suspend":false` for the poller, and `make deploy` (it resets ner and laya to one replica, as in the chart).

`--clean --if-exists` drops and recreates the tables before loading, so the result is exactly the dump: rows added after it are gone. If the whole database is gone (volume deleted), let the chart create an empty Postgres first (`make deploy`), then restore the same way.

What is not in the dumps: the Secrets (recreate them with the scripts in "Secrets" above; the restore does not depend on the old Postgres password), and the monitoring stack's history (Prometheus data is disposable).

## Network exposure

The UIs (ner, laya) have no authentication and Grafana allows anonymous viewing. Keep them on a private network (LAN or a VPN such as Tailscale). Do not forward ports on a router or expose NodePorts to the internet.
