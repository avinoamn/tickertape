# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the project will use [Semantic Versioning](https://semver.org/) with one version for the whole repository once releases start (first planned release: `0.2.0`).

## [Unreleased]

### Added

- Initial public import of the working system:
  - `poller` (CronJob), `ner` (GLiNER) and `laya` (fine-tuned Laya) services around a Postgres queue.
  - Kubernetes manifests for k3s, a namespace-scoped deploy identity, and a kube-prometheus-stack configuration with four generated Grafana dashboards.
  - Training and evaluation tooling for the Laya fine-tune, the labelling rubric, and the evaluation reports.
  - Documentation for newcomers in `README.md` and `docs/`.

- GitHub Actions CI (ruff, shellcheck, pytest with a Postgres service, dashboard freshness, kubeconform, image builds), Dependabot, `make lint` and `make test`, and an 85-test suite covering NER post-processing, Laya answer handling, the poller (including the conditional-GET path) and the Postgres queue.

- Helm chart `charts/tickertape` replacing the plain manifests for the `tickertape` namespace (rendered output verified identical to the previous manifests, so adopting the live install restarts nothing), `scripts/helm.sh`, a Helm-based `scripts/deploy.sh`, and `make chart-check` (also run in CI). The schema file moved to `charts/tickertape/files/schema.sql`.

- Release workflow (`.github/workflows/release.yml`): a `vX.Y.Z` tag builds and publishes the three images to GHCR, packages the chart and creates a GitHub Release from the changelog, after checking the tag, the chart version, the changelog and the CI result. `docs/releasing.md` describes versioning and the release procedure.

### Fixed

- `ner`: the "money words" rule (`million`, `billion`, ...) never matched because its regular expression had been corrupted (backspace characters instead of word boundaries), so MONEY entities without a digit, such as "a billion dollars", were dropped. Found by the new tests.

### History before the first release (images built from this code)

- Poller, ner and laya images `0.1.0`: first end-to-end pipeline on a CPU-only k3s node.
- Laya image `0.1.1`: serves the fine-tuned model and stores the probability of the chosen answer as `confidence` (the previous entropy-based value was about 0.1 on average).
