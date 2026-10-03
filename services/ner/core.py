"""NER model + focus-ticker resolution, shared by the worker loop, the Gradio UI and the eval script."""
import json
import os
import re
import threading
from collections import defaultdict
from pathlib import Path

import httpx
import yaml

from common.logging import log

SVC = "ner"
MODEL_ID = os.environ.get("NER_MODEL", "urchade/gliner_medium-v2.1")
THRESHOLD = float(os.environ.get("NER_THRESHOLD", "0.5"))          # spans below this are not in items.entities
STORE_FLOOR = float(os.environ.get("NER_STORE_FLOOR", "0.3"))      # spans down to this score go to items.ner_spans
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SNAPSHOT = Path(__file__).with_name("company_tickers.json")
FEEDS_FILE = Path(os.environ.get("FEEDS_FILE", Path(__file__).with_name("feeds.yaml")))

# model label -> key stored in items.entities
LABELS = {
    "company": "COMPANY",
    "ticker": "TICKER",
    "person": "PERSON",
    "money": "MONEY",
    "percent": "PERCENT",
    "financial metric": "FIN_METRIC",
}
KEY_TO_LABEL = {v: k for k, v in LABELS.items()}

# Trailing tokens dropped when comparing company names ("Apple Inc." == "APPLE INC" == "Apple").
_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "lp",
             "nv", "sa", "ag", "com", "stock", "stocks", "shares"}


def normalize_company(name: str) -> str:
    s = re.sub(r"/[^/]*/", " ", name.lower().replace("&", " and "))  # SEC style "Microsoft Corp /DE/"
    tokens = re.sub(r"[^a-z0-9]+", " ", s).split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def load_watchlist() -> list[str]:
    return [t.upper() for t in yaml.safe_load(FEEDS_FILE.read_text())["watchlist"]]


def load_sec_raw() -> tuple[dict, str]:
    """SEC's company_tickers.json ({"0": {cik_str, ticker, title}, ...}) and its source: live, else the snapshot."""
    ua = os.environ.get("SEC_USER_AGENT")
    if ua:
        try:
            r = httpx.get(SEC_TICKERS_URL, headers={"User-Agent": ua}, timeout=20, follow_redirects=True)
            r.raise_for_status()
            return r.json(), "live"
        except Exception as exc:
            log(SVC, status="warn", error=f"SEC ticker fetch failed, using snapshot: {exc!r}")
    return json.loads(SNAPSHOT.read_text()), "snapshot"


class CompanyIndex:
    """Company name -> ticker from SEC's list (ordered by market cap, so earlier = bigger).

    1. exact match on the normalized name ("Apple" == "Apple Inc.");
    2. otherwise prefix match on whole words ("Costco" -> "Costco Wholesale", "Micron" -> "Micron Technology"),
       accepted when exactly one SEC name starts with it, or when the biggest candidate is in the top TOP_RANK
       and clearly dominates the runner-up (DOMINANCE). Names under 4 letters never prefix-match. Ambiguous
       short names stay unmapped on purpose.
    """

    TOP_RANK = 1500
    DOMINANCE = 5  # 2nd candidate must rank at least 5x lower (= be much smaller) than the 1st

    def __init__(self, raw: dict):
        self.exact: dict[str, str] = {}
        self.by_first: dict[str, list[tuple[tuple[str, ...], str, int]]] = defaultdict(list)
        for rank, e in enumerate(raw.values()):
            norm = normalize_company(e["title"])
            if norm and norm not in self.exact:  # first wins (GOOG/GOOGL)
                self.exact[norm] = e["ticker"]
                tokens = tuple(norm.split())
                self.by_first[tokens[0]].append((tokens, e["ticker"], rank))
        self.valid_tickers = {e["ticker"].upper() for e in raw.values()}

    def __len__(self) -> int:
        return len(self.exact)

    def lookup(self, name: str) -> str | None:
        norm = normalize_company(name)
        if not norm:
            return None
        if norm in self.exact:
            return self.exact[norm]
        tokens = tuple(norm.split())
        cands = [c for c in self.by_first.get(tokens[0], []) if c[0][:len(tokens)] == tokens]
        if len(norm) < 4 or not cands:
            return None
        if len(cands) == 1:
            return cands[0][1]
        # several SEC names start with it: only accept a clearly dominant (much bigger) company, so
        # "Micron" -> Micron Technology but "American" stays unmapped (American Express/Tower/Airlines...)
        if cands[0][2] < self.TOP_RANK and cands[1][2] >= self.DOMINANCE * max(cands[0][2], 1):
            return cands[0][1]
        return None


class FocusResolver:
    """Entities -> focus ticker. No model needed, so the eval script can replay it on stored spans."""

    def __init__(self, watchlist, companies: CompanyIndex, first_company_only: bool = False):
        self.watchlist = set(watchlist)
        self.companies = companies
        self.first_company_only = first_company_only

    @property
    def valid_tickers(self) -> set[str]:
        return self.companies.valid_tickers

    def resolve(self, entities: dict[str, list[str]]) -> str | None:
        # 1. first TICKER entity that is in the watchlist
        for text in entities.get("TICKER", []):
            for token in re.sub(r"[^A-Z0-9.\-]", " ", text.upper()).split():
                if token in self.watchlist:
                    return token
        # 2. first COMPANY that maps to a ticker via SEC's company list
        names = entities.get("COMPANY", [])
        for name in names[:1] if self.first_company_only else names:
            ticker = self.companies.lookup(name)
            if ticker:
                return ticker
        # 3. unknown
        return None


class Extractor:
    """Loads GLiNER once. Inference is serialized with a lock, since the worker and the UI share the model."""

    def __init__(self):
        from gliner import GLiNER  # heavy import, keep it out of module import time

        raw, src = load_sec_raw()
        self.resolver = FocusResolver(load_watchlist(), CompanyIndex(raw))
        log(SVC, status="loading", model=MODEL_ID, sec_tickers=len(self.resolver.companies), sec_source=src)
        self.model = GLiNER.from_pretrained(MODEL_ID)
        self._lock = threading.Lock()
        log(SVC, status="ready", model=MODEL_ID)

    def predict_spans(self, text: str, labels: list[str] | None = None) -> list[dict]:
        """GLiNER spans (start, end, text, label, score) at the entity threshold, for the UI."""
        with self._lock:
            return self.model.predict_entities(text, labels or list(LABELS), threshold=THRESHOLD)

    def extract_batch(self, texts: list[str]) -> list[list[dict]]:
        """Spans per text down to STORE_FLOOR. Use group_entities() to apply the entity threshold."""
        with self._lock:
            return self.model.batch_predict_entities(texts, list(LABELS), threshold=STORE_FLOOR)

    def focus_ticker(self, entities: dict[str, list[str]]) -> str | None:
        return self.resolver.resolve(entities)


_EXCHANGE_PREFIX = re.compile(r"^(NASDAQ(GS|GM|CM)?|NYSE(ARCA|AMERICAN)?|AMEX|OTC)\s*[:\s]\s*", re.I)
_MONEY_WORDS = re.compile(r"\b(thousand|million|billion|trillion)\b", re.I)


def clean_entity(key: str, text: str, valid_tickers: set[str] | None) -> str | None:
    """Drop obvious GLiNER false positives. Returns the (possibly normalized) text, or None to drop it."""
    text = text.strip()
    if not text:
        return None
    if key == "TICKER":
        if valid_tickers is None:
            return text
        token = _EXCHANGE_PREFIX.sub("", text.lstrip("$")).strip()  # "NASDAQ: NVDA" / "$NVDA" -> "NVDA"
        return token if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", token) and token in valid_tickers else None
    if key == "MONEY":
        return text if re.search(r"\d", text) or _MONEY_WORDS.search(text) else None
    if key == "PERCENT":
        return text if re.search(r"\d", text) or "percent" in text.lower() else None
    if key == "COMPANY":
        if not (text[0].isupper() or text[0].isdigit()) or normalize_company(text) in _SUFFIXES:
            return None  # "banks", "humanoid robot startups", "CORP."
    return text


def group_entities(spans: list[dict], threshold: float = THRESHOLD,
                   valid_tickers: set[str] | None = None) -> dict[str, list[str]]:
    """{"COMPANY": [...], ...}: spans scoring >= threshold that pass clean_entity, keys only for labels that
    matched, deduped case-insensitively, text order kept. Spans use the model's label names ("company", ...)."""
    out: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for s in sorted(spans, key=lambda s: s["start"]):
        if s["score"] < threshold:
            continue
        key = LABELS[s["label"]]
        text = clean_entity(key, s["text"], valid_tickers)
        if text and (key, text.lower()) not in seen:
            seen.add((key, text.lower()))
            out.setdefault(key, []).append(text)
    return out


def compact_spans(spans: list[dict]) -> list[dict]:
    """The JSON stored in items.ner_spans (labels as COMPANY/TICKER/... keys)."""
    return [
        {"label": LABELS[s["label"]], "text": s["text"], "score": round(float(s["score"]), 3),
         "start": s["start"], "end": s["end"]}
        for s in sorted(spans, key=lambda s: s["start"])
    ]


def entities_from_stored(spans: list[dict], threshold: float = THRESHOLD,
                         valid_tickers: set[str] | None = None) -> dict[str, list[str]]:
    """Rebuild entities from items.ner_spans at any threshold >= STORE_FLOOR (used by the eval script)."""
    return group_entities([{**s, "label": KEY_TO_LABEL[s["label"]]} for s in spans], threshold, valid_tickers)


def item_text(title: str, summary: str | None) -> str:
    return f"{title}. {summary}" if summary else title
