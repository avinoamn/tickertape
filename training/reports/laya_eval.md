# Laya evaluation on the gold set

> **The gold labels were written by Claude Code (an LLM), not by a human.** Everything below measures agreement with Claude Code's labels, not ground truth. The same labeller wrote the training labels, so a fine-tuned model learning Claude Code's habits is rewarded here. Spot-check `training/data/gold_set.jsonl` before reading much into small differences.

- Gold items: **150**, live items only, never trained on or used for calibration (cnbc_earnings 25, cnbc_finance 22, cnbc_top_news 23, sec_8k 30, yahoo_ticker 50).
- Date: 2026-10-03. 95% intervals are percentile bootstraps over items (2000 resamples); differences are paired (same resamples).
- Accuracy: top-1 against the gold `label`. Brier: multiclass, against the soft gold distribution (lower is better; always predicting the training prior is the reference). ECE: 10 bins of max probability vs top-1 correctness (lower is better).

- `base`: convaiinnovations/laya @ 55cf4c4ebb4e, laya 0.3.24
- `ft`: avinoamn/tickertape-laya @ b02c4f5ce4fe, laya 0.3.24
- `prior`: every item gets the mean training label distribution (no model).

## Headline

### event_type

| model | accuracy | Brier | ECE | mean confidence |
|---|---|---|---|---|
| base | 0.267 [0.200, 0.340] | 0.929 [0.836, 1.027] | 0.395 [0.323, 0.468] | 0.661 [0.620, 0.703] |
| ft | 0.887 [0.833, 0.940] | 0.076 [0.042, 0.115] | 0.051 [0.031, 0.106] | 0.898 [0.872, 0.921] |
| prior | 0.740 [0.667, 0.807] | 0.339 [0.281, 0.402] | 0.065 [0.005, 0.132] | 0.675 [0.675, 0.675] |

### sentiment

| model | accuracy | Brier | ECE | mean confidence |
|---|---|---|---|---|
| base | 0.700 [0.627, 0.773] | 0.278 [0.228, 0.332] | 0.068 [0.043, 0.147] | 0.672 [0.645, 0.698] |
| ft | 0.740 [0.673, 0.807] | 0.128 [0.092, 0.169] | 0.081 [0.053, 0.150] | 0.775 [0.745, 0.805] |
| prior | 0.467 [0.387, 0.547] | 0.380 [0.330, 0.431] | 0.119 [0.046, 0.199] | 0.586 [0.586, 0.586] |

### action

| model | accuracy | Brier | ECE | mean confidence |
|---|---|---|---|---|
| base | 0.247 [0.180, 0.320] | 0.496 [0.442, 0.550] | 0.257 [0.185, 0.324] | 0.503 [0.491, 0.515] |
| ft | 0.800 [0.733, 0.860] | 0.082 [0.065, 0.100] | 0.152 [0.104, 0.213] | 0.678 [0.662, 0.696] |
| prior | 0.593 [0.513, 0.673] | 0.204 [0.179, 0.230] | 0.101 [0.021, 0.181] | 0.492 [0.492, 0.492] |

## Difference against `base` (paired)

Accuracy: higher is better. Brier and ECE: lower is better. An interval that excludes 0 is a real difference on this gold set (still agreement with Claude Code's labels).

### `ft` minus `base`

| question | accuracy | Brier | ECE |
|---|---|---|---|
| event_type | **0.620 [0.520, 0.713]** | **-0.853 [-0.959, -0.745]** | **-0.344 [-0.411, -0.248]** |
| sentiment | 0.040 [-0.047, 0.120] | **-0.151 [-0.202, -0.098]** | 0.012 [-0.071, 0.084] |
| action | **0.553 [0.453, 0.653]** | **-0.415 [-0.470, -0.358]** | **-0.104 [-0.175, -0.019]** |

## Alert (the `action` question)

Alert is rare in the labels (mean probability 5.7%, 0 of 150 gold items have it on top), so it cannot be scored as an accuracy class. What matters is whether the model floods the dashboard with alerts.

| model | mean P(alert) | items with alert on top | share |
|---|---|---|---|
| gold labels | 5.7% | 0 | 0.0% |
| base | 43.4% | 79 | 52.7% |
| ft | 5.1% | 0 | 0.0% |
| prior | 4.9% | 0 | 0.0% |

## Per-class counts (top-1)

Support is the number of gold items whose top label is the class; the rarer classes have only a handful, so per-class recall is noisy.

### event_type

| class | gold | base predicted / correct | ft predicted / correct | prior predicted / correct |
|---|---|---|---|---|
| earnings | 15 | 62 / 15 | 16 / 12 | 0 / 0 |
| guidance | 3 | 11 / 0 | 7 / 3 | 0 / 0 |
| mna | 4 | 28 / 3 | 1 / 1 | 0 / 0 |
| analyst | 7 | 14 / 3 | 4 / 4 | 0 / 0 |
| legal | 10 | 23 / 7 | 4 / 4 | 0 / 0 |
| other | 111 | 12 / 12 | 118 / 109 | 150 / 111 |

### sentiment

| class | gold | base predicted / correct | ft predicted / correct | prior predicted / correct |
|---|---|---|---|---|
| pos | 57 | 57 / 45 | 38 / 35 | 0 / 0 |
| neu | 70 | 63 / 45 | 88 / 60 | 150 / 70 |
| neg | 23 | 30 / 15 | 24 / 16 | 0 / 0 |

### action

| class | gold | base predicted / correct | ft predicted / correct | prior predicted / correct |
|---|---|---|---|---|
| ignore | 89 | 0 / 0 | 99 / 79 | 150 / 89 |
| log | 61 | 71 / 37 | 51 / 41 | 0 / 0 |
| alert | 0 | 79 / 0 | 0 / 0 | 0 / 0 |

## Caveats

- Silver labels (Claude Code), titles and summaries only, no hindsight; same-story items were labelled alike.
- Small gold set: about ±9 points on accuracy per question at 100 items, a bit better at 150; the rarer event types have few examples.
- Gold items are live items (Yahoo, CNBC, SEC from the poller); training items are mostly backfilled CNBC and SEC, so a gap between train-time and gold-time behaviour is possible.
- Items whose state was truncated by laya: base 0, ft 0.
