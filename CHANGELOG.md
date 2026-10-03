# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/). The components (`poller`, `ner`, `laya` and the Helm `chart`) are versioned independently with [Semantic Versioning](https://semver.org/), so released entries are grouped per component under `## <component> <version> - <date>` (see [docs/releasing.md](docs/releasing.md)). Repository-only changes (CI, docs, tests) are in the git history.

## [Unreleased]

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
