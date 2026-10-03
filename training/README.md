# Training and evaluation

Tools to evaluate the NER stage and to build, fine-tune and evaluate the Laya model. For the reasoning and the results, read [docs/model.md](../docs/model.md) first; this page is the how-to.

Everything here runs in Docker against the **local dev database** (`make dev-up`, see [docs/development.md](../docs/development.md)), never against a cluster.

**Data policy.** `training/data/` is git-ignored: it holds news headlines and summaries from third-party publishers (CNBC, SEC, Yahoo), used only privately. Never commit it and never upload the gold set anywhere. `training/gold/ner_gold.jsonl` and `training/reports/` are in git and contain only item ids, labels and aggregate numbers.

**Label policy.** All labels (NER sample, Laya training and gold sets) were written by an LLM (Claude Code), not humans. Every report says so; keep saying so if you add numbers.

## NER evaluation (`eval_ner.py`)

The dev database needs items that went through ner (`docker compose up -d --build ner` after a poll).

```sh
make -s eval-ner ARGS="sanity"            # per-source stats, invalid TICKER values, score histogram
make -s eval-ner ARGS="cik"               # SEC 8-K: expected ticker from the CIK in the title
make -s eval-ner ARGS="gold training/gold/ner_gold.jsonl"            # accuracy / precision / recall of focus_ticker
make -s eval-ner ARGS="gold training/gold/ner_gold.jsonl --sweep"    # same, replayed at thresholds 0.3 to 0.9
make -s eval-ner ARGS="diff"              # which focus tickers would change under the current code
```

`gold` reads only `uid` and `gold` (the expected focus ticker) from the file and takes the text from the database by uid, so it scores the items your own database has in common with it. Rows not found are reported and skipped; a fresh database will have few matches, so label your own sample:

```sh
make -s eval-ner ARGS="sample --n 50" > my_gold.jsonl      # template with titles; do not commit it
```

Then replace `"gold": "?"` on each line with the ticker of the company the news is about (`"gold": "NVDA"`), or `null` when no single listed company is the focus (macro news, an unlisted company, several equal companies). Judge from the title and summary, not from the pipeline's output. You can add `"entities": {"COMPANY": [...], "MONEY": [...]}` for entity-level scores. Rows are never used for training.

`items.ner_spans` keeps every GLiNER span down to score 0.3, so thresholds can be tuned offline with `--sweep` without re-running the model.

## Laya: data, labels, fine-tune, evaluation

### 1. Backfill historical items (`backfill.py`)

```sh
SEC_USER_AGENT="Your Name you@example.com" make backfill ARGS="sec --count 500 --per-day 4"
make backfill ARGS="cnbc --per-feed 200 --per-snapshot 6 --since 2019-01-01 --max-fetches 300"
make backfill ARGS="check"        # verifies the SEC uid scheme against a live row
```

Runs in the poller image against the dev database. CNBC items come from Wayback Machine snapshots of the exact feed URLs (random days, at most `--per-snapshot` new items per snapshot so the dates spread out). SEC 8-Ks are rebuilt from EDGAR's daily form index and the `data.sec.gov` submissions API into the same title and summary layout as the live feed (EDGAR answers 403, not 404, for a missing daily index; that is handled). The uid scheme matches the poller, so inserts also dedupe against live rows. Every new row is mirrored to `training/data/backfill.jsonl`, which is how live and backfilled items are told apart. The backfilled rows start as `status = 'new'`: run ner over them (`docker compose up -d --build ner`); laya is not needed for training rows.

### 2. Dataset and labels (`build_dataset.py`, `labelling_guide.md`)

```sh
make dataset ARGS="select"           # dedupe + split by item -> training/data/split.jsonl (gold 150, val about 10%, train)
make dataset ARGS="status"           # labelling progress per split and source
make dataset ARGS="batch --n 100"    # print the next unlabelled items (what the labeller reads)
make dataset ARGS="add" < labels.jsonl   # validate and store labels, one JSON object per line
make dataset ARGS="export"           # train.jsonl / val.jsonl / gold_set.jsonl for the notebook and the evaluation
```

Runs in the laya image so it reuses `build_state` (`services/laya/core.py`) and `QUESTIONS` (`services/laya/questions.py`): training rows, labels and live inference cannot drift apart. The option order in `criteria` defines the target vector order.

- Splits are by news item, never by question. Gold (150 items) comes from live items only and is never trained on. SEC filings are capped at 300 of the training items.
- A label line is `{"id": 12, "e": {"earnings": 0.8, "other": 0.2}, "s": {"neu": 1}, "a": {"log": 0.7, "alert": 0.3}}` for event_type, sentiment and action. Omitted options are 0, each question is renormalised, unknown options are rejected.
- The rubric is `labelling_guide.md` (buybacks, dividends and insider trades are rules there, not extra options). Write label lines to a file and redirect it into `add`; very long heredocs break some shells.
- Alert is rare in the labels, so `action` is evaluated with Brier and ECE and alert mass, not accuracy alone. The rarer event types have few gold examples, so reports use bootstrap intervals.

### 3. Evaluation (`eval_laya.py`)

```sh
make eval-laya ARGS="predict --name base --model convaiinnovations/laya"    # about 15 min on CPU, resumable
HF_TOKEN=<read token> make eval-laya ARGS="predict --name ft --model <hf-user>/<repo>"   # a private repo needs the token
python training/eval_laya.py report base ft --out training/reports/laya_eval.md
```

`predict` runs a checkpoint over `training/data/gold_set.jsonl` in the laya image and caches the probability of every option in `training/data/preds/<name>.jsonl` (with a `.meta.json` recording model, revision and library version). `report` is standard-library Python: it compares the cached predictions (the first name is the reference) and writes the markdown: accuracy, multiclass Brier against the soft labels, ECE (10 bins), percentile-bootstrap 95% intervals, paired differences against the reference, a "prior" baseline (the mean training label distribution, ignoring the text), alert probability mass, and per-class counts. Base and fine-tuned models are scored by exactly the same code, from the predicted probabilities.

### 4. Fine-tune on Kaggle (`finetune_laya.py`, `laya_finetune_kaggle.ipynb`)

`finetune_laya.py` is adapted from laya's single-device script (Apache-2.0). It reads `train.jsonl` and `val.jsonl`, trains at the serving budget (`max_len` 512, `head_max_len` 192, so nothing has to be restored afterwards), reports validation loss, Brier and accuracy every epoch and keeps the best epoch, fits calibration temperatures on `val` inside laya's load clamp [0.5, 5] (shared, and per option-count bucket, replacing the base checkpoint's `temperature_by_options`), reloads the saved folder through `laya.load` and compares probabilities, then pushes to a **private** Hugging Face repository.

A GPU is needed (the laya authors recommend a free Kaggle notebook with two T4s; one is used):

1. On kaggle.com create a **private** dataset with exactly three files: `training/data/train.jsonl`, `training/data/val.jsonl`, `training/finetune_laya.py`. **Never upload `gold_set.jsonl`.**
2. Import `training/laya_finetune_kaggle.ipynb` as a notebook. Settings: accelerator GPU T4 x2, Internet on, the dataset added as input, and a Kaggle secret `HF_TOKEN` holding a Hugging Face **write** token.
3. In the second cell set `HF_REPO = "<hf-user>/<repo>"` and run all cells. The last cell prints the per-epoch validation losses, the fitted temperatures and the commit hash of the pushed repository.

A local smoke test without a GPU is possible for the head only (full training needs about 7 GB of memory): `docker compose run --rm -T --no-deps --user root -v "$PWD:/work" -w /work -e PYTHONPATH=/work --entrypoint python laya training/finetune_laya.py --train training/data/train.jsonl --val training/data/val.jsonl --output-dir training/data/ft_smoke --limit 24 --epochs 1 --device cpu --freeze-encoder`.

### 5. Roll out and iterate

To use a new model, set `LAYA_MODEL` and `LAYA_REVISION` (the pushed commit hash) in `k8s/laya.yaml`, and for a private repository create the `hf-read-token` Secret ([docs/operations.md](../docs/operations.md#changing-or-rolling-back-the-laya-model)). To retrain with more data: label more items with `make dataset`, `export`, re-upload `train.jsonl` and `val.jsonl`, run the notebook with a new `HF_REPO` or a new commit, evaluate with `make eval-laya` under a new `--name`, and compare with `report`.

## Reports

- [`reports/laya_eval.md`](reports/laya_eval.md): base against fine-tuned against the prior baseline on the 150-item gold set.
- [`reports/laya_eval_base.md`](reports/laya_eval_base.md): the base model alone, which showed that it is below the prior on two of three questions.
