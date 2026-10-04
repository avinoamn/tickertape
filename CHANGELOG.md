# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/). The components (`poller`, `ner`, `laya` and the Helm `chart`) are versioned independently with [Semantic Versioning](https://semver.org/), so released entries are grouped per component under `## <component> <version> - <date>` (see [docs/releasing.md](docs/releasing.md)). Repository-only changes (CI, docs, tests) are in the git history.

## [Unreleased]

## chart 0.4.0 - 2026-10-04

### Added

- Prometheus alert rules (`alerts.enabled`, a `PrometheusRule` rendered only when the Operator CRDs exist) for the poller not succeeding, ner or laya down or restarting, and failed or stale backups and restore tests. Tunable with `alerts.pollerMaxAgeMinutes`, `alerts.restartsPerHour` and `alerts.restoreTestMaxAgeDays`.
- Upgrading from 0.3.1 only adds one `PrometheusRule`: no spec of an existing object changes and no pod restarts. The `deployer` account needs the `prometheusrules` permission first (`k8s/rbac.yaml`, applied by an admin with `make bootstrap`), or the upgrade is denied and rolled back. The alerts are delivered by the Alertmanager of the monitoring stack (`k8s/monitoring/values.yaml`, Secret `alertmanager-discord`); see docs/operations.md, section Alerts.

## chart 0.3.1 - 2026-10-04

### Fixed

- The deploy no longer hangs on the backup volume claim. With storage that binds a volume only when a pod uses it (k3s's `local-path`), the claim stayed `Pending` until the first CronJob run, and `helm --wait` / `--atomic` waits for every claim to be bound, so the first deploy of 0.3.0 hung until its timeout (a fresh install would too). A small Job `postgres-backups-bind` now mounts the claim right away and removes itself shortly after finishing.
- Upgrading from 0.3.0 only adds that Job: no spec of an existing object changes and no pod restarts. Use 0.3.1 rather than 0.3.0 for new installs.

## chart 0.3.0 - 2026-10-04

### Added

- Backups of the database, on by default (`backup.enabled`): a daily CronJob `postgres-backup` writes a `pg_dump` to its own volume `postgres-backups` (checked to be readable and to hold every table before it replaces anything; the newest 14 are kept; the volume survives `helm uninstall`), and a weekly CronJob `postgres-restore-test` restores the newest dump into a scratch database, checks that the tables have data, and fails if the restore fails or the newest dump is older than 36 hours.
- New values under `backup:` (schedule, time zone, retention, volume size, restore-test schedule and age limit, resources).
- Upgrading from 0.2.0 only adds the two CronJobs, a ConfigMap with the scripts and a 1 Gi volume claim: no spec of an existing object changes (only the chart version label) and no pod restarts. The dumps are on the same node as the database, so copy them off the node regularly (`make backup-pull`, see docs/operations.md).

## chart 0.2.0 - 2026-10-04

### Added

- First release of the Helm chart for the `tickertape` namespace: Postgres (StatefulSet and volume), the schema (applied by a hook Job after every install and upgrade), the poller CronJob, ner and laya (Deployments, model-cache volumes, ClusterIP and NodePort Services), Ingresses, and ServiceMonitors (only when the Prometheus Operator CRDs exist). Secrets stay outside the chart and are referenced by name.
- Pins one image tag per service, so a chart version names an exact set of images: `poller` 0.1.1, `ner` 0.1.1, `laya` 0.1.2.

### Changed

- Replaces the plain manifests. The rendered objects are identical to those manifests, so adopting a live install restarts nothing; the first deploy of this version moves the three services from locally built images to the public GHCR images, which restarts them once.

## ner 0.1.1 - 2026-10-04

### Fixed

- The "money words" rule (`million`, `billion`, ...) never matched, because its regular expression had been corrupted (backspace characters instead of word boundaries). MONEY entities without a digit, such as "a billion dollars", were dropped; they are kept now. Focus-ticker resolution is not affected.

### Changed

- Internal only: import ordering from the new lint rules.

## laya 0.1.2 - 2026-10-04

### Changed

- The "Invalid JSON" error in the UI now keeps the original exception as its cause.
- Internal only: import ordering from the new lint rules. The model, the questions and the stored answers are unchanged.

## poller 0.1.1 - 2026-10-04

### Changed

- Internal only: `datetime.UTC` and import ordering from the new lint rules. Behaviour is unchanged.

## laya 0.1.1 - 2026-10-03

### Changed

- Serves the fine-tuned model `avinoamn/tickertape-laya` (pinned by commit) instead of the public base model, with an optional read token for the private repository.

### Fixed

- The stored `confidence` is the probability of the chosen answer. It was Laya's entropy-based value, about 0.1 on average, which made the low-confidence panel meaningless.

## ner 0.1.0 - 2026-10-03

### Added

- First version: GLiNER entity extraction and focus-ticker resolution (SEC company list), worker loop, UI and metrics. Built and run locally on the cluster only; never published to a registry.

## poller 0.1.0 - 2026-10-03

### Added

- First version: feed fetching with conditional requests, normalisation and idempotent inserts, as a CronJob. Built and run locally on the cluster only; never published to a registry.

## laya 0.1.0 - 2026-10-03

### Added

- First version: Laya decisions (event type, sentiment, action) with the public base model, worker loop, UI and metrics. Built and run locally on the cluster only; never published to a registry.
