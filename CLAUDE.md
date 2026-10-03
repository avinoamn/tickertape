# CLAUDE.md

Guidance for AI coding assistants working in this repo (humans: see `README.md` and `docs/`). The rules below are binding.

**Start here:** `README.md` and `docs/` describe the project. If they exist locally, also read `CLAUDE.local.md` (real cluster access details, git-ignored), `HANDOFF.md` (current state, commands, gotchas, next step) and `PROGRESS.md` (tracker and decisions log); both are git-ignored working notes. Open work is in GitHub Issues.

## Project

"tickertape": RSS feeds → NER → decisions → Grafana dashboards, running on a single-node k3s home lab ("computa") with Postgres, three Python services (poller, ner, laya) and kube-prometheus-stack. Design: `docs/architecture.md`.

## Rules

- **Ask, don't assume.** If anything in a task is unclear (identities, credentials, feed choices, trade-offs), ask the user before acting. Never guess values like `SEC_USER_AGENT`.
- **Public repo: nothing private in tracked files.** No secrets, tokens, real IP addresses, SSH user names, personal emails, or third-party text (news titles/summaries, `training/data/`). Use placeholders in tracked files; real values live in `CLAUDE.local.md`, cluster Secrets, env vars, or GitHub Secrets/Variables.
- **Docs are part of the change.** Update `README.md` / `docs/` in the same PR as the behaviour they describe. They are written for newcomers who do not know the project or its history.
- **Develop locally, deploy on the cluster.** Write and test code on the dev machine (Docker, local Postgres via `docker-compose.yml`). The cluster is only for deploys and verification. Never run dev work against cluster data.
- **Namespaces:** `tickertape` (workloads) and `monitoring` (Prometheus/Grafana, Helm release `kps`). Don't touch `kube-system` or `tools`. Change `monitoring` only through `k8s/monitoring/values.yaml` + `make monitoring-install` (and `scripts/apply-dashboards.sh`), with admin kubectl, after asking.
- **Ask before any cluster state change** (apply, delete, restart, rollout, Helm upgrade, secret changes, image import). Approval covers that one deploy, not later ones. Read-only `kubectl get/describe/logs` and client-side dry-runs are fine. Read-only checks over SSH are fine; changes need approval.
- **Images:** explicit version tags (never `latest`) and `imagePullPolicy: IfNotPresent`. Until the registry flow ships (see GitHub Issues): build locally (`scripts/build.sh`), ship with `docker save` + `scp` + `sudo k3s ctr images import` (`scripts/ship.sh`); the import needs a sudo password and a real TTY, so tell the user to run `make push SVC=<svc>` in their own terminal.
- **kubectl access:** day-to-day work uses `scripts/kc.sh`, which runs local `kubectl` as the namespace-scoped `deployer` ServiceAccount (`k8s/rbac.yaml`, kubeconfig `~/.kube/tickertape`, created by `make bootstrap`). It manages workloads in `tickertape` only and cannot use the `scale` subresource: to stop or start a Deployment use `kc.sh patch deployment/<name> -n tickertape --type=merge -p '{"spec":{"replicas":N}}'`. For cluster-scoped work (namespace, RBAC, Helm/CRDs such as kube-prometheus-stack) use `scripts/kc-admin.sh` (SSH + admin kubeconfig) and ask first. Never print or read `~/.kube/tickertape` (no `kubectl config view --raw`), and never put its token in the repo.
- **Web UIs must be reachable from the user's other devices without a tunnel or per-device setup.** Port-forward / `localhost` is a debugging aid only. Expose every UI with its own NodePort Service (separate from the ClusterIP Service that carries `/metrics`) so it opens at `http://<node>:<port>`. Ports: uptime-kuma 30001, **ner 30002**, **laya 30003**, **grafana 30004**. The Traefik Ingress hosts (`ner.local`, ...) stay as an extra. After deploying a UI, verify it with `curl` and give the user the URL. The ner and laya UIs have no authentication and Grafana allows anonymous read-only viewing: never expose any of them to the internet (no router port-forwarding).
- **No secrets in the repo.** Cluster Secrets are made by `scripts/create-secrets.sh` (and friends) from env vars or generated values. All service config comes from env vars.
- **Windows binaries:** `scripts/kc.sh` runs native `kubectl.exe`, so don't pass `/dev/stdin` or MSYS-only paths to it (use repo-relative paths). Don't `taskkill` or `pkill` kubectl broadly; stop only the process you started.
- **Git:** commit only when the user asks. Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`...).
- **Shell:** use Git Bash for `scripts/*.sh` and `make` (Chocolatey's `C:\ProgramData\chocolatey\bin` may need adding to PATH). `make help` lists the targets. Files use LF line endings (`.gitattributes`).
- **Check external sources before relying on them.** Feed URLs in `services/poller/feeds.yaml` must be tested from both the dev machine and the cluster.
- **Not trading advice.** `alert` means "a human should look", nothing more.
