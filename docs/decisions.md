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

**Images use explicit version tags and `imagePullPolicy: IfNotPresent`.** Mutable tags and `latest` make a rollout depend on what the node cached. Until a registry exists, images are built locally and imported into k3s.

**laya has a CPU limit of 2 cores and matching thread counts.** Uncapped it took 3.5 of the node's 4 cores and starved everything else. Torch threads must match the limit or they are throttled.

**Prometheus keeps 365 days with a trimmed scrape set.** The default scrape set produced 86,000 series, which would have filled the volume in about ten days; the dashboards use about 5,000. Postgres-backed panels were never limited by retention.

**Grafana allows anonymous viewing, with an admin login for changes, and reads Postgres through a read-only role.** Viewing should not need a login on a private network, and a dashboard cannot modify data.

**Dashboards are generated from code (`grafana/gen_dashboards.py`) and shipped as labelled ConfigMaps.** Hand-edited JSON drifts and cannot be reviewed; generated output can be checked by a script that runs every query.

## Release engineering (in progress)

Planned and tracked in the issues: a public container registry (GHCR) instead of importing images by hand, a Helm chart for the `tickertape` namespace, SemVer with one version for the whole repository, GitHub Actions for CI and release, and deploys started manually from GitHub over Tailscale. The reasoning will be recorded here as each piece ships.
