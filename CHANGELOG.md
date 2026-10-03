# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/) and the project will use [Semantic Versioning](https://semver.org/) with one version for the whole repository once releases start (first planned release: `0.2.0`).

## [Unreleased]

### Added

- Initial public import of the working system:
  - `poller` (CronJob), `ner` (GLiNER) and `laya` (fine-tuned Laya) services around a Postgres queue.
  - Kubernetes manifests for k3s, a namespace-scoped deploy identity, and a kube-prometheus-stack configuration with four generated Grafana dashboards.
  - Training and evaluation tooling for the Laya fine-tune, the labelling rubric, and the evaluation reports.
  - Documentation for newcomers in `README.md` and `docs/`.

### History before the first release (images built from this code)

- Poller, ner and laya images `0.1.0`: first end-to-end pipeline on a CPU-only k3s node.
- Laya image `0.1.1`: serves the fine-tuned model and stores the probability of the chosen answer as `confidence` (the previous entropy-based value was about 0.1 on average).
