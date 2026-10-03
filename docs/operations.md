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
2. **Make the images available to the node.** There is no registry yet, so images are built locally and imported into k3s:
   ```sh
   make build SVC=poller TAG=0.1.0        # likewise ner and laya; TAG must match the tag in charts/tickertape/values.yaml
   make push  SVC=poller TAG=0.1.0        # docker save, scp, `sudo k3s ctr images import` on the node
   ```
   `make push` asks for the node's sudo password, so run it in a real terminal.
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
scripts/helm.sh upgrade --install tickertape charts/tickertape --take-ownership --dry-run=server   # review: what would change
scripts/deploy.sh --take-ownership
```

Afterwards `scripts/helm.sh list` shows the release, the pods keep their age, and the item count in Postgres is unchanged. From then on use plain `make deploy`.

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

## Releasing a new version of a service

1. Change the code, build with a **new** tag (`make build SVC=laya TAG=0.1.2`) and import it (`make push SVC=laya TAG=0.1.2`).
2. Set the tag in `charts/tickertape/values.yaml` (`laya.image.tag`; tags are explicit and never `latest`, and `imagePullPolicy` is `IfNotPresent`, so an existing tag would not be re-pulled), or pass it once with `make deploy HELM_ARGS="--set laya.image.tag=0.1.2"`.
3. `make deploy` and watch `scripts/kc.sh rollout status deployment/laya -n tickertape`.

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
| New ServiceMonitor target missing in Prometheus | Allow about 3 minutes for the operator to reload. |
| A poller feed returns nothing | Feeds change and block bots. Test the URL from your machine and from the node; disable it in `services/poller/feeds.yaml` with `enabled: false` and note why. |

Useful read-only queries (the `psql` pod is `postgres-0`):

```sh
scripts/kc.sh exec -n tickertape postgres-0 -- psql -U tickertape -Atc "select status, count(*) from items group by 1"
scripts/kc.sh exec -n tickertape postgres-0 -- psql -U tickertape -Atc "select model_rev, count(*) from decisions group by 1"
```

## Backups

There are none yet, and Postgres is the only irreplaceable data. The volumes use the `local-path` storage class with reclaim policy `Delete`, so deleting the PVC or the namespace deletes the data. Until a backup job exists (tracked in the issues), take a dump by hand before risky changes:

```sh
scripts/kc.sh exec -n tickertape postgres-0 -- pg_dump -U tickertape -Fc tickertape > tickertape-$(date +%F).dump
```

## Network exposure

The UIs (ner, laya) have no authentication and Grafana allows anonymous viewing. Keep them on a private network (LAN or a VPN such as Tailscale). Do not forward ports on a router or expose NodePorts to the internet.
