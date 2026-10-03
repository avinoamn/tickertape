# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/). The components (`poller`, `ner`, `laya` and the Helm `chart`) are versioned independently with [Semantic Versioning](https://semver.org/), so released entries are grouped per component under `## <component> <version> - <date>` (see [docs/releasing.md](docs/releasing.md)). Repository-only changes (CI, docs, tests) are in the git history.

## [Unreleased]

### Added

- Initial public import of the working system:
  - `poller` (CronJob), `ner` (GLiNER) and `laya` (fine-tuned Laya) services around a Postgres queue.
  - Kubernetes manifests for k3s, a namespace-scoped deploy identity, and a kube-prometheus-stack configuration with four generated Grafana dashboards.
  - Training and evaluation tooling for the Laya fine-tune, the labelling rubric, and the evaluation reports.
  - Documentation for newcomers in `README.md` and `docs/`.

- GitHub Actions CI (ruff, shellcheck, pytest with a Postgres service, dashboard freshness, kubeconform, image builds), Dependabot, `make lint` and `make test`, and an 85-test suite covering NER post-processing, Laya answer handling, the poller (including the conditional-GET path) and the Postgres queue.

- Helm chart `charts/tickertape` replacing the plain manifests for the `tickertape` namespace (rendered output verified identical to the previous manifests, so adopting the live install restarts nothing), `scripts/helm.sh`, a Helm-based `scripts/deploy.sh`, and `make chart-check` (also run in CI). The schema file moved to `charts/tickertape/files/schema.sql`.

- Release workflow (`.github/workflows/release.yml`) with independent versions: `poller-vX.Y.Z`, `ner-vX.Y.Z` and `laya-vX.Y.Z` tags publish one image to GHCR, `chart-vX.Y.Z` packages the chart (after checking that the images it pins exist) and each creates a GitHub Release from the changelog. Per-service `VERSION` files; the chart pins an explicit image tag per service and no longer has an `appVersion`. `docs/releasing.md` describes versioning and the release procedure.

### Fixed

- `ner`: the "money words" rule (`million`, `billion`, ...) never matched because its regular expression had been corrupted (backspace characters instead of word boundaries), so MONEY entities without a digit, such as "a billion dollars", were dropped. Found by the new tests.

### History before the first release (images built from this code)

- Poller, ner and laya images `0.1.0`: first end-to-end pipeline on a CPU-only k3s node.
- Laya image `0.1.1`: serves the fine-tuned model and stores the probability of the chosen answer as `confidence` (the previous entropy-based value was about 0.1 on average).
