# Design decisions

The choices that shaped the project, with the reason for each. Newest topics last. A decision here can be revisited; the reason says what would have to change.

## Architecture

**Postgres is the queue, storage and dashboard source (no Kafka or other broker).**
At a few thousand items a day a broker adds an extra moving part without a benefit. `SELECT ... FOR UPDATE SKIP LOCKED` gives safe claiming, crash recovery (a rolled-back transaction releases the rows) and a backlog that is one SQL count. Revisit when throughput or the number of consumers outgrows one database.

**The poller is a CronJob, not a long-running service.**
Polling every 5 minutes is naturally periodic and a job that lives for seconds has nothing to leak. It exposes no `/metrics`; its dashboard uses Postgres and kube-state-metrics.

**The model services run the worker loop and the web UI in one pod.**
One image, one model in memory, and the UI is a convenient way to try the model with the exact same code. Cost: restarts reload the model (about a minute).

**ner and laya use the `Recreate` deployment strategy with one replica.**
Each owns a ReadWriteOnce cache volume and a large memory limit on a small node, so two pods at once would not fit. The price is a short pause on every rollout; items wait safely in Postgres.

**Laya processes one item per call.**
`predict_batch` was slower than looping `predict` here (55 s against 36 s for six items) with identical answers, and small claims keep row locks short.

**Decisions are upserted, with the model revision stored.**
Swapping the model should overwrite old answers per `(item, question)`, and `model_rev` makes it visible which model answered. This allowed reprocessing after the fine-tune without a migration.

## Feeds and NER

**Three CNBC feeds replace the originally planned "Market Insider" feed.** That feed held about two items. GlobeNewswire is kept but disabled: it blocks automated requests from both the workstation and the cluster.

**The poller fails only if every feed fails.** One flaky feed should not turn the job red.

**Company-to-ticker matching is a normalised exact name match plus a conservative prefix rule.** Fuzzy matching produced false positives ("American"). The cost is misses on short brand names, which are visible in the focus-ticker rate.

**NER thresholds stay at 0.5, with all spans down to 0.3 stored.** A threshold sweep on the labelled sample gave identical scores from 0.3 to 0.7, so there was no reason to move it; storing spans lets it be re-tuned offline.

## Model

**Fine-tune Laya instead of using the base model.** On the evaluation set the base model lost to a no-model baseline on two of three questions (see [model.md](model.md)).

**Labels come from an LLM, and every report says so.** There is no budget for human labelling. This keeps the project honest about what its metrics mean, and the human-labelled evaluation set is listed as the main open quality item.

**The gold set is 150 items (not 100), from live items only, and never trained on.** 100 items give about plus or minus 9 points per question; live items match what the model sees in production.

**The `alert` rule was kept even though it is almost never positive.** An alert should be rare. The consequence, documented in the model page, is that `alert` cannot be evaluated as an accuracy class and is effectively unused until the rubric changes.

**The fine-tuned weights live in a private Hugging Face repository.** The training text comes from third-party publishers. The cluster reads it with a fine-grained read-only token, and a commit hash pins the revision so a rollout is reproducible.

**Confidence is the probability of the chosen answer, not Laya's entropy-based `confidence`.** The entropy value gave about 0.1 on average and made the "low confidence" panel meaningless. This was found by reading the library and fixed before the fine-tuned model went live.

## Operations

**The cluster is deployed through a namespace-scoped ServiceAccount, not admin access.** Day-to-day deploys cannot touch other namespaces or cluster-scoped objects. Admin access over SSH is used only for the monitoring stack and bootstrapping.

**Every UI gets its own NodePort Service, separate from the ClusterIP Service that carries metrics.** The UIs must open from any device on the private network without a tunnel or per-device setup. They have no authentication, so they stay on a private network.

**Images use explicit version tags and `imagePullPolicy: IfNotPresent`.** Mutable tags and `latest` make a rollout depend on what the node cached. Images come from the registry only (see Releases); the early flow of building locally and importing into k3s by hand (`docker save`, `scp`, `sudo k3s ctr images import`) was retired once the release workflow had published and deployed the first versions, because it needed a sudo password and a terminal, and produced images nobody else could reproduce.

**laya has a CPU limit of 2 cores and matching thread counts.** Uncapped it took 3.5 of the node's 4 cores and starved everything else. Torch threads must match the limit or they are throttled.

**Prometheus keeps 365 days with a trimmed scrape set.** The default scrape set produced 86,000 series, which would have filled the volume in about ten days; the dashboards use about 5,000. Postgres-backed panels were never limited by retention.

**Grafana allows anonymous viewing, with an admin login for changes, and reads Postgres through a read-only role.** Viewing should not need a login on a private network, and a dashboard cannot modify data.

**Dashboards are generated from code (`grafana/gen_dashboards.py`) and shipped as labelled ConfigMaps.** Hand-edited JSON drifts and cannot be reviewed; generated output can be checked by a script that runs every query.

## Testing and CI

**Database tests run against a real Postgres, in a throw-away schema per test.** The queue's guarantees (`SKIP LOCKED`, the retry rule, idempotent schema, upserts) are properties of the SQL, so a mock would only test itself. A schema per test makes it safe to point the suite at a dev database, and CI uses a Postgres service container.

**Tests cover logic, not models.** torch, Laya and GLiNER are never imported in tests, so the suite takes seconds and runs anywhere. Model quality is measured by the evaluation scripts, which are measurements and not pass/fail gates.

**CI builds every service image on each pull request, without pushing.** The images pin heavy dependencies, so a broken Dockerfile or an unresolvable requirement is the most likely failure of a dependency update. Layer caching keeps it affordable.

**One summary job (`CI passed`) is what branch protection requires.** The list of checks can change without touching the repository settings.

## Helm chart

**One chart for the namespace, with fixed resource names.** The live resources already had plain names (`ner`, `laya`, `postgres`). Keeping them lets the existing install be adopted instead of recreated, at the price of one release per namespace.

**The defaults reproduce the previous manifests exactly, and the pod templates keep only the label `app: <name>`.** A pod template change restarts the pod, and ner and laya reload their model on every start, so the chart's standard labels live on object metadata only. A field-by-field comparison of the rendered output with the old manifests (17 objects, zero differences) is how adoption is known to be a no-op.

**Secrets stay outside the chart and are referenced by name.** A chart that creates Secrets either stores them in values (in git) or regenerates them on upgrade (locking the database out of its own volume).

**The schema is a ConfigMap applied by a post-install and post-upgrade hook Job.** The schema is idempotent, so applying it on every release is safe, and the hook runs after Postgres is ready. The file moved into the chart because Helm cannot read files outside it; docker-compose and the tests read it from there, so there is one copy.

**ServiceMonitors render only when the CRDs exist.** The chart installs on a cluster without the Prometheus Operator, and picks them up on the next upgrade once it is installed.

**Deploys use `helm upgrade --install --atomic` as the namespace-scoped deployer account.** Helm stores its release state in Secrets inside the namespace, which that account may already manage, so no extra permissions were needed. Rollback is `helm rollback`.

## Releases

**poller, ner, laya and the chart are versioned independently.** The services change at very different rates (the model services almost never, the poller whenever a feed misbehaves), and a changed image tag restarts a pod: ner and laya reload their model on every start, so with one shared version a poller fix would pause the model stages for a minute. Independent versions also say what actually changed. The price is more bookkeeping, handled with conventions (below) and checks in CI and in the release workflow.

**A deployment is a chart version, and the chart pins one explicit tag per service.** There is no implicit "latest" or "current" version: the chart requires a tag for each service, so a chart version names an exact, reproducible set of images, and rolling back is deploying the previous chart. A service release alone changes nothing on the cluster until a chart release pins it.

**Each service's version lives in a `VERSION` file next to its code.** Local builds default to it, the release workflow checks the tag against it, and a test checks it is valid SemVer. A tag is a claim about the tree, so the tree must agree before anything is published.

**Tags are `<component>-vX.Y.Z`; images carry only the version tag, are never moved, and `latest` does not exist.** What runs is always reproducible.

**A release is a tag on main, and the workflow refuses anything else.** Before pushing it checks the tag format, the version in the tree, the changelog section, that the commit is on `main`, and that CI passed for that exact commit.

**A chart release is refused unless every image it pins exists and is public.** This enforces the order (release the services, then the chart) and keeps a deploy from failing on an image pull.

**GHCR images are public.** The GitHub token publishes without extra credentials, and a cluster pulls without a Secret. A new package starts out private, so the workflow fails with the exact click path instead of publishing something no cluster can pull.

**`linux/amd64` only.** That is the only architecture in use; adding arm64 would double the build time of the ML images.

**Release tags are protected by a ruleset with no bypass.** A published version must never change, and the realistic way to break that is a careless force push or tag deletion by the one person with access. Blocking deletion and updates for the four tag patterns makes that impossible by accident. The price is that recovering from a release that failed before publishing means disabling the ruleset for a minute, which is deliberate friction. The protection was tested on a throwaway tag (a delete and a move were both rejected) before it was trusted.

## Backups

**Backups are verified at write time and restore-tested weekly, not just taken.** A dump that cannot be restored is not a backup. Each dump is checked to be a readable archive holding the data of every table before it replaces anything, and a weekly CronJob restores the newest one into a scratch database, so a silently broken backup shows up as a failed job within a week. The same job fails when the newest dump is older than 36 hours, which catches backups that stopped without any error.

**Plain `pg_dump` in a CronJob, not a backup operator.** One small database on one node: a dump of about 10 MB takes a second. An operator, or WAL archiving for point-in-time recovery, adds moving parts the data does not justify. The cost is that recovery is to the last dump (at most a day of items, which the feeds mostly re-supply).

**The backup volume is on the same node as the database, so an off-node copy is part of the design.** `make backup-pull` streams a verified dump to the developer machine. Uploading to cloud storage from the cluster would need credentials inside it, which this project avoids for now.

**The backup volume survives `helm uninstall`** (`helm.sh/resource-policy: keep`), because uninstalling is exactly when someone needs the backups.

**The scripts are tested against a real Postgres, including their failure cases and the destructive restore command.** A restore procedure that has only been read is a hope. `scripts/test-backup.sh` found a real bug in the first version of the scripts (`pg_restore` ignores `PGDATABASE`).

**Every volume claim in the chart has a pod that uses it right away.** k3s's `local-path` class binds a volume only when a pod uses the claim, and `helm --wait` (so `--atomic`) waits for every claim to be bound. The backup volume's first user was the 03:00 CronJob, so the first deploy of the backups hung until its timeout and would have rolled back; a fresh install would have hit the same. A tiny Job that mounts the claim and exits now binds it during the wait (it is a normal resource, because hooks run after the wait, and it removes itself shortly after finishing). The failure was reproduced on a throw-away k3s (the install hung for the whole timeout), the fix was checked there (install in 9 s, upgrades with and without the Job present, uninstall keeping the claim), and `tests/test_chart.py` now fails if any claim has no consumer that exists during the wait.

## Release engineering (in progress)

Still planned (see the issues): deploys started manually from GitHub over Tailscale. The reasoning will be recorded here when it ships.
