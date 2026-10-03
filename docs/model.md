# The models: NER and Laya

This page explains how the two models in tickertape were chosen, tuned and evaluated, and, just as important, how far the results can be trusted. The commands to reproduce each step are in [`training/README.md`](../training/README.md).

## Summary

| | NER (`ner` service) | Decisions (`laya` service) |
|---|---|---|
| Model | GLiNER `urchade/gliner_medium-v2.1`, used as is | Laya (ModernBERT-large with a typed-answer head), **fine-tuned** here |
| Task | Extract companies, tickers, people, amounts; resolve a focus ticker | Answer three multiple-choice questions per item |
| Tuned by | Post-processing rules, measured on a 50-item labelled sample | Full fine-tune on 901 labelled items, calibrated on 99 |
| Evaluated on | 50 items, focus-ticker accuracy 90% | 150 held-out items, see below |
| Labels written by | An LLM (Claude Code), not a human | An LLM (Claude Code), not a human |

## The honesty note

Both labelled sets were written by an LLM. The same LLM wrote the Laya training labels and the evaluation labels. Every metric on this page therefore measures **agreement with that LLM's labels**, not agreement with reality, and a model that learns the labeller's habits is rewarded. A human-labelled evaluation set is the most valuable improvement still open, and it is on the issue list. Until then, treat differences of a few points as noise.

## Laya

### What it answers

Three questions about the news item and its focus company (definitions in `services/laya/questions.py`): `event_type` (earnings, guidance, mna, analyst, legal, other), `sentiment` (pos, neu, neg) and `action` (ignore, log, alert). Every answer is a probability distribution over the options, so the model can say "70% earnings, 30% guidance".

### Why fine-tune

The public base model was tried first on real items. On the 150-item evaluation set it was *worse than always predicting the training distribution* for `event_type` (accuracy 0.27 against 0.74) and `action` (0.25 against 0.59), and it labelled 53% of items `alert`. Only `sentiment` beat the trivial baseline. So the acceptance test for the fine-tune is to beat both the base model and this "prior" baseline, not just the base model.

### Data

| Set | Items | Source | Used for |
|---|---|---|---|
| train | 901 | Backfilled history plus live items (SEC capped at 300 because filings are boilerplate) | Fine-tuning |
| val | 99 | Same sources | Choosing the best epoch and fitting calibration |
| gold | 150 | **Live items only**, the kind the model sees in production | Evaluation only; never trained or calibrated on |

Splits are by news item (never by question), after removing near-duplicates. History was gathered because the live feeds produce too little in a short time: CNBC headlines from Wayback Machine snapshots of the exact feed URLs (spread across dates) and SEC 8-K filings rebuilt from EDGAR's daily index and submissions API.

The raw text (CNBC, SEC and Yahoo titles and summaries) belongs to its publishers and is **not** included in this repository; it is used only for private training and evaluation. The repository keeps the code, the rubric and the aggregate reports.

### Labels

Each item gets a **soft label**: a probability per option for each question, written following a rubric (`training/labelling_guide.md`): judge only from the headline, summary and focus ticker the model sees, never from what happened to the stock afterwards, and split the probability when the text is genuinely ambiguous. Alerts are deliberately rare in the labels (3 of 1,150 items have `alert` on top; mean probability about 5%), which is why the evaluation looks at alert probability mass and not at alert accuracy.

### Training

`training/finetune_laya.py` is adapted from the single-device fine-tuning script of the [laya](https://github.com/NandhaKishorM/laya) project. It trains all weights at the serving budget (512 tokens of state, 192 for the head), reports validation loss, Brier score and accuracy after every epoch, keeps the best epoch (4 of 4), fits calibration temperatures on the validation set inside Laya's allowed range, reloads the saved folder through `laya.load` to check that probabilities match, and pushes to a private Hugging Face repository. It was run on a free Kaggle notebook with two T4 GPUs (the home server has no GPU). The notebook is `training/laya_finetune_kaggle.ipynb`.

Calibration is fitted per option-count bucket (3 to 5 options, and 6 to 10), replacing the base checkpoint's own temperatures. Laya reads the per-bucket values first, so leaving the old ones in place would have hidden the new fit.

### Evaluation

`training/eval_laya.py` runs a checkpoint over the gold set once, caches the probability of every option, and then compares models from the cache with the same code, so the base and fine-tuned models are scored identically. Metrics, per question:

- **Accuracy** of the top choice against the label's top choice.
- **Brier score** against the soft label (multiclass, lower is better); it rewards good probabilities, not just a good top choice.
- **ECE**, expected calibration error over 10 confidence bins.
- 95% **bootstrap intervals** over items, and paired differences between models.
- A **prior baseline** and the **alert mass**.

Results (full tables in [`training/reports/laya_eval.md`](../training/reports/laya_eval.md)):

| Question | Accuracy ft / base / prior | Brier ft / base / prior |
|---|---|---|
| event type | **0.89** [0.83, 0.94] / 0.27 / 0.74 | **0.076** / 0.929 / 0.339 |
| sentiment | **0.74** [0.67, 0.81] / 0.70 / 0.47 | **0.128** / 0.278 / 0.380 |
| action | **0.80** [0.73, 0.86] / 0.25 / 0.59 | **0.082** / 0.496 / 0.204 |

### Known weaknesses

- **`alert` is effectively off.** The labels almost never contain it, so the model never puts it on top (the base model did for 53% of items, which would drown any reviewer). If real alerts are wanted, the rubric and the `action` labels must change and the model be retrained.
- **Rare event types are weak**: only a handful of gold examples each (M&A 4, analyst 7, legal 10), with M&A recalled 1 of 4 and guidance over-predicted. Intervals are wide.
- **Sentiment** improved on Brier but the accuracy gain over the base model is within noise.
- **Under-confidence on `action`** (mean confidence 0.68 against accuracy 0.80, ECE 0.15), so the dashboard's "low confidence" panel shows a fair share of low values.
- **Small training set** (901), mostly CNBC and SEC. Yahoo-style headlines are scarce in training but common in live data. More labelled data is the first lever for quality.
- The fine-tuned weights are private. A fresh clone uses the public base model unless you train your own.

## NER evaluation

GLiNER is used unmodified. What was tuned is the code around it, measured on 50 items (10 per source) whose focus ticker was labelled from the title and summary only, blind to the pipeline's output (also by an LLM; ambiguous items carry a note). The first measurement gave 76% accuracy, 82% precision and 64% recall. Fixes, each checked against the sample and against a diff over a few hundred stored items:

- Keep an extracted ticker only if it is a real symbol in the SEC list.
- Money and percent entities must contain a digit (or "million", "billion", "percent").
- Company names must start with a capital letter or digit; a trailing "stock" or "shares" is ignored when looking up the name.
- Prefix matching on whole words ("Costco" finds "Costco Wholesale"), accepted if unique or if one SEC match clearly dominates.

Result: 90% accuracy, 89% precision, 89% recall on the same 50 items. Since the fixes were tuned on that sample, this is optimistic. The confidence threshold stays at 0.5 (0.3 to 0.7 scored identically). Remaining errors are known and listed here: brand names that map to a parent company (Burger King is QSR), inverted company names in SEC titles, an unmapped first company letting a later one win, and incidental mentions. The sample file in `training/gold/ner_gold.jsonl` keeps only item ids and labels, not the text.
