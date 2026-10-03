"""Fine-tune the Laya English checkpoint on our labelled news items (step 5). One GPU (Kaggle T4) or CPU.

Adapted from laya's research/scripts/finetune_single_device.py (Apache-2.0, github.com/NandhaKishorM/laya), same RLCD
loss (policy-gradient term over noisy logit samples + soft cross-entropy against the soft labels). Differences:

  * reads train.jsonl and val.jsonl (rows `{state, questions, gold:{q:{label, probabilities}}}`); never sees the gold set;
  * the held-out split is by news item (val.jsonl), not by question, so calibration has no leakage;
  * trains at the SERVING budget (the base config's max_len 512 / head_max_len 192), so truncation at training and in
    the cluster is identical and the saved config needs no restoring;
  * validation loss, Brier and accuracy after every epoch; the best epoch (lowest val soft cross-entropy) is kept;
  * temperatures are fitted on val within laya's load clamp [0.5, 5] (laya.common.TEMP_MIN/MAX) and written both as
    `temperature[0]` and per option-count bucket (`choice:3-5`, `choice:6-10`), which is what laya reads first;
  * the saved checkpoint is reloaded with `laya.load` and checked against the in-memory model before anything is pushed;
  * optional push to a PRIVATE Hub repo (HF_TOKEN from the environment or the Kaggle secret of the same name).

  python training/finetune_laya.py --train train.jsonl --val val.jsonl --output-dir out --hf-repo <user>/<repo>
  python training/finetune_laya.py --train train.jsonl --val val.jsonl --output-dir out --limit 24 --epochs 1 --device cpu   (smoke test)
"""
import argparse
import copy
import gc
import json
import os
import random
import time

os.environ.setdefault("USE_TF", "0")  # before importing transformers

import torch  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from laya.agent import _fix_tokenizer_config  # noqa: E402
from laya.common import (  # noqa: E402
    QTYPES,
    TEMP_MAX,
    TEMP_MIN,
    build_model,
    build_sequence,
    proper_reward,
    render_options,
    temp_bucket,
)
from safetensors.torch import load_file, save_file  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

BASE_MODEL = "convaiinnovations/laya"
BASE_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"  # the revision laya pins and the cluster runs today
MIN_BUCKET = 30  # fewer val decisions than this in an option-count bucket: use the shared temperature instead


def read_jsonl(path, limit=None):
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[:limit] if limit else rows


def build_item(tok, cfg, state, qid, q, gold_q, row):
    """One tokenized decision. Returns None if the options do not fit the budget."""
    crit = q.get("criteria", {})
    keys = list(crit)  # the option order defines the target vector order
    target = [float(gold_q["probabilities"].get(k, 0.0)) for k in keys]
    total = sum(target)
    target = [v / total for v in target] if total > 0 else [1.0 / len(target)] * len(target)
    seq, markers = build_sequence(tok, state, {"t": "choice", "ins": q["instructions"], "crit": crit},
                                  cfg["max_len"], cfg["head_max_len"])
    if len(markers) != len(render_options({"t": "choice", "crit": crit})):
        return None
    return {"ids": seq, "markers": markers, "qtype": QTYPES["choice"], "target": target,
            "label": target.index(max(target)), "qid": qid, "row": row}


def preprocess(tok, cfg, rows):
    items, dropped = [], 0
    for ri, row in enumerate(rows):
        for qid, q in row["questions"].items():
            if qid not in row["gold"]:
                continue
            it = build_item(tok, cfg, row["state"], qid, q, row["gold"][qid], ri)
            if it is None:
                dropped += 1
            else:
                items.append(it)
    return items, dropped


def collate(items, pad_id):
    n, length = len(items), max(len(it["ids"]) for it in items)
    kmax = max(len(it["markers"]) for it in items)
    ids = torch.full((n, length), pad_id, dtype=torch.long)
    att = torch.zeros((n, length), dtype=torch.long)
    mpos = torch.zeros((n, kmax), dtype=torch.long)
    mmask = torch.zeros((n, kmax), dtype=torch.bool)
    target = torch.zeros((n, kmax), dtype=torch.float32)
    for i, it in enumerate(items):
        ids[i, : len(it["ids"])] = torch.tensor(it["ids"])
        att[i, : len(it["ids"])] = 1
        k = len(it["markers"])
        mpos[i, :k] = torch.tensor(it["markers"])
        mmask[i, :k] = True
        target[i, :k] = torch.tensor(it["target"], dtype=torch.float32)
    return {"input_ids": ids, "attention_mask": att, "marker_pos": mpos, "marker_mask": mmask, "target": target,
            "qtype": torch.tensor([it["qtype"] for it in items])}


def forward(model, batch, device, use_amp):
    args = [batch[k].to(device) for k in ("input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype")]
    if use_amp:
        with torch.autocast("cuda", dtype=torch.float16):
            return model(*args)
    return model(*args)


@torch.no_grad()
def logits_for(model, items, pad_id, device, use_amp, bs=16):
    model.eval()
    out = []
    for i in range(0, len(items), bs):
        chunk = items[i:i + bs]
        logits, _ = forward(model, collate(chunk, pad_id), device, use_amp)
        logits = logits.float().cpu()
        out += [logits[r, : len(it["markers"])] for r, it in enumerate(chunk)]
    return out


def score(items, logits, temp_for=lambda it: 1.0):
    """Soft cross-entropy, multiclass Brier and top-1 accuracy per question (and overall) at the given temperatures."""
    acc = {}
    for it, z in zip(items, logits):
        p = torch.softmax(z / temp_for(it), -1)
        t = torch.tensor(it["target"])
        row = acc.setdefault(it["qid"], [0, 0.0, 0.0, 0])
        row[0] += 1
        row[1] += float(-(t * torch.log(p.clamp_min(1e-9))).sum())
        row[2] += float(((p - t) ** 2).sum())
        row[3] += int(int(p.argmax()) == it["label"])
    out = {q: {"n": n, "ce": ce / n, "brier": br / n, "acc": ok / n} for q, (n, ce, br, ok) in acc.items()}
    n = sum(v["n"] for v in out.values())
    out["all"] = {"n": n, **{k: sum(v[k] * v["n"] for q, v in out.items()) / n for k in ("ce", "brier", "acc")}}
    return out


def fmt(m):
    return " | ".join(f"{q} ce {v['ce']:.3f} brier {v['brier']:.3f} acc {v['acc']:.2f}" for q, v in m.items())


def fit_temperature(pairs):
    """LBFGS on log T, soft-target cross-entropy, clamped to laya's load range."""
    kmax = max(len(z) for z, _ in pairs)
    Z = torch.full((len(pairs), kmax), -1e4)
    T = torch.zeros((len(pairs), kmax))
    for i, (z, t) in enumerate(pairs):
        Z[i, : len(z)] = z
        T[i, : len(t)] = torch.tensor(t, dtype=torch.float32)
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(T * torch.log_softmax(Z / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    return float(torch.clamp(log_t.exp(), TEMP_MIN, TEMP_MAX).item())


def hf_token():
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    try:
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        return None


def model_card(base, revision, log, hf_private):
    best = log["epochs"][log["best_epoch"] - 1]
    return f"""---
license: apache-2.0
base_model: {base}
library_name: laya
tags: [laya, finance, news, decisions]
---
# Laya fine-tuned on market-news decisions (tickertape)

Fine-tune of `{base}` (revision `{revision[:12]}`) for three typed `choice` questions asked about a news item
(`event_type`, `sentiment`, `action`; see the tickertape project). Load it like any laya checkpoint:
`laya.load("<this repo>")`.

- Training data: {log['train_items']} decisions from {log['train_rows']} news items (CNBC, SEC 8-K, Yahoo Finance headlines), validation {log['val_items']} decisions.
  The data is third-party text and is **not** included.
- **Labels were written by Claude Code (an LLM), not by humans**, as soft probability distributions per option.
- {log['epochs_run']} epochs run, best epoch {log['best_epoch']} by validation soft cross-entropy ({best['val']['all']['ce']:.3f}).
  Temperatures fitted on the validation split: {json.dumps(log['temperature_by_options'])}.
- `max_len` 512 / `head_max_len` 192, same as the base checkpoint.
- Not trading advice: `action=alert` only means that a human should look.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--output-dir", default="laya_finetuned")
    ap.add_argument("--base-model", default=BASE_MODEL, help="Hub id or a local checkpoint directory")
    ap.add_argument("--base-revision", default=BASE_REVISION)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--lr-encoder", type=float, default=2.5e-5)
    ap.add_argument("--lr-head", type=float, default=1.0e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--limit", type=int, default=None, help="use only the first N rows of train and val (smoke test)")
    ap.add_argument("--hf-repo", default=None, help="push the result to this Hub repo (private unless --public)")
    ap.add_argument("--public", action="store_true")
    ap.add_argument("--freeze-encoder", action="store_true",
                    help="debug only: train the head alone (a CPU smoke test of the full model needs ~7 GB)")
    ap.add_argument("--no-verify", action="store_true", help="skip the reload-through-laya check")
    args = ap.parse_args()

    device = torch.device("cuda", 0) if (args.device == "auto" and torch.cuda.is_available()) or args.device == "cuda" \
        else torch.device("cpu")
    use_amp = device.type == "cuda"
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    print("device", device, flush=True)

    model_dir = args.base_model
    if not os.path.isdir(model_dir):
        model_dir = snapshot_download(args.base_model, revision=args.base_revision,
                                      allow_patterns=["encoder/*", "tokenizer/*", "model.safetensors", "rl_agent_config.json"])
    _fix_tokenizer_config(model_dir)
    with open(os.path.join(model_dir, "rl_agent_config.json")) as f:
        base_cfg = json.load(f)
    cfg = copy.deepcopy(base_cfg)  # training copy; the saved config starts from base_cfg
    cfg["gradient_checkpointing"] = use_amp
    cfg["max_tokens_per_batch"] = 4096
    print("serving budget kept: max_len", cfg["max_len"], "head_max_len", cfg["head_max_len"], flush=True)

    tok = AutoTokenizer.from_pretrained(os.path.join(model_dir, "tokenizer"))
    model = build_model(cfg, encoder_dir=os.path.join(model_dir, "encoder"))
    model.load_state_dict(load_file(os.path.join(model_dir, "model.safetensors")), strict=True)
    if use_amp:
        model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.head_checkpointing = True
    model.to(device)

    train_rows, val_rows = read_jsonl(args.train, args.limit), read_jsonl(args.val, args.limit)
    train_items, d1 = preprocess(tok, cfg, train_rows)
    val_items, d2 = preprocess(tok, cfg, val_rows)
    if not train_items or not val_items:
        raise SystemExit("no training or validation items produced")
    print(f"train {len(train_items)} decisions ({len(train_rows)} rows, {d1} dropped), "
          f"val {len(val_items)} decisions ({len(val_rows)} rows, {d2} dropped)", flush=True)

    bs, group_size = args.batch_size, 4
    enc_params = [p for n, p in model.named_parameters() if "encoder." in n]
    head_params = [p for n, p in model.named_parameters() if "encoder." not in n]
    groups = [{"params": head_params, "lr": args.lr_head}]
    if args.freeze_encoder:
        for p in enc_params:
            p.requires_grad_(False)
    else:
        groups.insert(0, {"params": enc_params, "lr": args.lr_encoder})
    optimizer = torch.optim.AdamW(groups, weight_decay=0.01)
    steps_per_epoch = (len(train_items) + bs - 1) // bs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=steps_per_epoch * args.epochs, eta_min=1e-6)
    scaler = torch.amp.GradScaler("cuda", enabled=True) if use_amp else None

    def half_state():
        return {k: (v.detach().to("cpu", torch.float16).clone() if v.is_floating_point() else v.detach().cpu().clone())
                for k, v in model.state_dict().items()}  # noqa: F821 (closure over `model`, which is deleted only after training)

    log = {"epochs": [], "train_items": len(train_items), "train_rows": len(train_rows), "val_items": len(val_items),
           "val_rows": len(val_rows), "base_model": args.base_model, "base_revision": args.base_revision}
    best_state, best_ce, best_epoch = None, float("inf"), 0
    base_val = score(val_items, logits_for(model, val_items, tok.pad_token_id, device, use_amp))
    print("val before training (T=1):", fmt(base_val), flush=True)
    log["val_before"] = base_val

    for epoch in range(args.epochs):
        t0 = time.time()
        model.train()
        random.seed(args.seed + epoch)
        random.shuffle(train_items)
        sigma = 0.4 + (0.1 - 0.4) * (epoch / max(1, args.epochs - 1))
        epoch_loss, n_batches = 0.0, 0
        optimizer.zero_grad(set_to_none=True)
        for b in range(0, len(train_items), bs):
            batch = collate(train_items[b:b + bs], tok.pad_token_id)
            logits, _act = forward(model, batch, device, use_amp)
            logits = logits.float()
            mask = batch["marker_mask"].to(device)
            k = mask.sum(-1, keepdim=True).float()
            target = batch["target"].to(device)
            eps = torch.randn((group_size,) + logits.shape, device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            z = logits.detach().unsqueeze(0) + eps
            q = torch.softmax(z.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                r = proper_reward(q, target.unsqueeze(0), batch["qtype"].to(device), mask, w_sph=0.75, w_rps=1.0)
                adv = r - r.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -(((z - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            loss = -(adv * logp).mean() - (target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            if scaler:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            epoch_loss += loss.item()
            n_batches += 1
        val = score(val_items, logits_for(model, val_items, tok.pad_token_id, device, use_amp))
        improved = val["all"]["ce"] < best_ce
        if improved:
            best_ce, best_state, best_epoch = val["all"]["ce"], half_state(), epoch + 1
        log["epochs"].append({"epoch": epoch + 1, "train_loss": epoch_loss / max(1, n_batches), "val": val,
                              "seconds": round(time.time() - t0), "best": improved})
        print(f"epoch {epoch + 1}/{args.epochs} train loss {epoch_loss / max(1, n_batches):.4f} | val {fmt(val)}"
              f"{'  <- best' if improved else ''} ({time.time() - t0:.0f}s)", flush=True)
    log["epochs_run"] = args.epochs
    log["best_epoch"] = best_epoch

    # Calibrate the best epoch on val (by item, never trained on).
    model.load_state_dict(best_state, strict=True)
    val_logits = logits_for(model, val_items, tok.pad_token_id, device, use_amp)
    buckets = {}
    for it, z in zip(val_items, val_logits):
        buckets.setdefault(temp_bucket(it["qtype"], len(it["markers"])), []).append((z, it["target"]))
    shared = fit_temperature([p for v in buckets.values() for p in v])
    by_options = {b: fit_temperature(v) for b, v in buckets.items() if len(v) >= MIN_BUCKET}
    print("temperatures: shared", round(shared, 3), {b: round(t, 3) for b, t in by_options.items()}, flush=True)

    def temp_for(it):
        return by_options.get(temp_bucket(it["qtype"], len(it["markers"])), shared)

    log["val_best_T1"] = score(val_items, val_logits)
    log["val_best_calibrated"] = score(val_items, val_logits, temp_for)
    log["temperature"], log["temperature_by_options"] = shared, by_options
    print("val best, T=1       :", fmt(log["val_best_T1"]), flush=True)
    print("val best, calibrated:", fmt(log["val_best_calibrated"]), "(fitted on this same split)", flush=True)

    # Save: the base config (serving budget intact) plus the new temperatures. The notebook also drops the inherited
    # temperature_by_options, which take precedence over `temperature` and would mask the fit.
    out = args.output_dir
    os.makedirs(out, exist_ok=True)
    save_file({k: v.contiguous() for k, v in best_state.items()}, os.path.join(out, "model.safetensors"))
    model.encoder.config.save_pretrained(os.path.join(out, "encoder"))
    tok.save_pretrained(os.path.join(out, "tokenizer"))
    saved = copy.deepcopy(base_cfg)
    saved["fine_tuned"] = True
    saved["temperature"] = [shared] + list(base_cfg.get("temperature", [1.0, 1.0, 1.0]))[1:]
    saved["temperature_by_options"] = by_options
    saved["training"] = {"fine_tuned_from": f"{args.base_model}@{args.base_revision[:12]}", "epochs_run": args.epochs,
                         "best_epoch": log["best_epoch"], "train_decisions": len(train_items),
                         "labeler": "claude-code", "recipe": "RLCD, single device"}
    with open(os.path.join(out, "rl_agent_config.json"), "w") as f:
        json.dump(saved, f, indent=2)
    with open(os.path.join(out, "training_log.json"), "w") as f:
        json.dump(log, f, indent=2)
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
        f.write(model_card(args.base_model, args.base_revision, log, not args.public))
    print("saved", out, flush=True)
    del model, optimizer, scheduler, scaler, best_state, enc_params, head_params  # make room for the reload below
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()

    if not args.no_verify:  # the saved folder must load through laya and agree with the in-memory model
        import laya
        agent = laya.load(out)
        answers = {ri: agent.predict(val_rows[ri]["state"], val_rows[ri]["questions"])["answers"]
                   for ri in range(min(8, len(val_rows)))}
        worst, n = 0.0, 0
        for it, z in zip(val_items, val_logits):
            if it["row"] not in answers:
                continue
            expected = torch.softmax(z / temp_for(it), -1)
            got = answers[it["row"]][it["qid"]]["probabilities"]
            opts = list(val_rows[it["row"]]["questions"][it["qid"]]["criteria"])
            worst = max(worst, max(abs(float(expected[i]) - float(got[o])) for i, o in enumerate(opts)))
            n += 1
        print(f"verify: laya.load({out!r}) answered {n} decisions; max |p_laya - p_trainer| = {worst:.4f}; "
              f"temperatures in effect {agent.temperature} {agent.temperature_by_options}", flush=True)
        if worst > 0.05:
            raise SystemExit("verify failed: the saved checkpoint does not reproduce the trained model's probabilities")

    if args.hf_repo:
        from huggingface_hub import HfApi
        token = hf_token()
        if not token:
            raise SystemExit("--hf-repo needs HF_TOKEN (environment variable or Kaggle secret); nothing was pushed")
        api = HfApi(token=token)
        api.create_repo(args.hf_repo, private=not args.public, exist_ok=True)
        info = api.upload_folder(folder_path=out, repo_id=args.hf_repo, repo_type="model",
                                 commit_message="Fine-tuned laya (tickertape)")
        print("pushed", args.hf_repo, "private" if not args.public else "PUBLIC", info, flush=True)


if __name__ == "__main__":
    main()
