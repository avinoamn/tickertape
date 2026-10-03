"""NER post-processing and focus-ticker resolution (no model needed)."""
import pytest


def sec_raw(*entries):
    """SEC company_tickers.json shape; the position in the list is the rank (earlier = bigger company)."""
    return {str(i): {"cik_str": i, "ticker": t, "title": name} for i, (t, name) in enumerate(entries)}


def span(label, text, score=0.9, start=0):
    return {"label": label, "text": text, "score": score, "start": start, "end": start + len(text)}


# --- company names ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Apple Inc.", "apple"),
    ("APPLE INC", "apple"),
    ("Apple", "apple"),
    ("Microsoft Corp /DE/", "microsoft"),
    ("The Walt Disney Company", "walt disney"),
    ("Johnson & Johnson", "johnson and johnson"),
    ("Nvidia stock", "nvidia"),
    ("Corp.", "corp"),        # a name that is only a suffix is kept, so clean_entity can reject it
])
def test_normalize_company(ner_core, name, expected):
    assert ner_core.normalize_company(name) == expected


@pytest.fixture
def index(ner_core):
    return ner_core.CompanyIndex(sec_raw(
        ("MSFT", "MICROSOFT CORP"),
        ("GOOGL", "Alphabet Inc."),
        ("GOOG", "Alphabet Inc."),          # same name: the first (bigger) entry wins
        ("COST", "Costco Wholesale Corp"),
        ("MU", "MICRON TECHNOLOGY INC"),
        ("AXP", "AMERICAN EXPRESS CO"),
        ("AMT", "AMERICAN TOWER CORP"),
    ))


def test_exact_match_ignores_suffix_and_case(index):
    assert index.lookup("Microsoft") == "MSFT"
    assert index.lookup("microsoft corporation") == "MSFT"


def test_first_entry_wins_for_duplicate_names(index):
    assert index.lookup("Alphabet") == "GOOGL"


def test_unique_prefix_matches_whole_words(index):
    assert index.lookup("Costco") == "COST"
    assert index.lookup("Cost") is None        # "cost" is not the first word of any name, so no guess


def test_ambiguous_prefix_stays_unmapped(index):
    # American Express and American Tower both start with it, and neither clearly dominates
    assert index.lookup("American") is None


def test_dominant_prefix_candidate_is_accepted(ner_core):
    before = [(f"F{i}", f"Filler {i} Inc") for i in range(10)]      # ranks 0-9
    after = [(f"G{i}", f"Other {i} Inc") for i in range(60)]        # ranks 11-70
    idx = ner_core.CompanyIndex(sec_raw(*before, ("MU", "Micron Technology"), *after, ("MSLN", "Micron Solutions")))
    assert idx.lookup("Micron") == "MU"   # MU is rank 10, the runner-up rank 71: at least 5x lower


def test_empty_and_unknown_names(index):
    assert index.lookup("") is None
    assert index.lookup("Totally Unknown Holdings") is None


def test_valid_tickers_are_collected(index):
    assert {"MSFT", "GOOG", "GOOGL"} <= index.valid_tickers


# --- focus ticker -----------------------------------------------------------------------------------------------

@pytest.fixture
def resolver(ner_core, index):
    return ner_core.FocusResolver(["NVDA", "AAPL", "MSFT"], index)


def test_watchlist_ticker_wins_over_company(resolver):
    assert resolver.resolve({"TICKER": ["NVDA"], "COMPANY": ["Costco"]}) == "NVDA"


def test_ticker_with_exchange_prefix_is_found(resolver):
    assert resolver.resolve({"TICKER": ["NASDAQ: AAPL"]}) == "AAPL"


def test_company_maps_through_sec_list(resolver):
    assert resolver.resolve({"COMPANY": ["Costco Wholesale"]}) == "COST"


def test_ticker_outside_watchlist_falls_through_to_company(resolver):
    assert resolver.resolve({"TICKER": ["XYZ"], "COMPANY": ["Microsoft"]}) == "MSFT"


def test_no_focus_is_none(resolver):
    assert resolver.resolve({}) is None
    assert resolver.resolve({"COMPANY": ["Unknown Holdings"]}) is None


def test_first_company_only_ignores_later_companies(ner_core, index):
    r = ner_core.FocusResolver(["NVDA"], index, first_company_only=True)
    entities = {"COMPANY": ["Unknown Holdings", "Costco"]}
    assert r.resolve(entities) is None
    assert ner_core.FocusResolver(["NVDA"], index).resolve(entities) == "COST"


# --- entity cleaning --------------------------------------------------------------------------------------------

VALID = {"NVDA", "AAPL", "MU"}


@pytest.mark.parametrize("key, text, expected", [
    ("TICKER", "NVDA", "NVDA"),
    ("TICKER", "NASDAQ: NVDA", "NVDA"),
    ("TICKER", "$AAPL", "AAPL"),
    ("TICKER", "NYSE MU", "MU"),
    ("TICKER", "ZZZZ", None),            # not a real symbol
    ("TICKER", "nvda", None),            # must be upper case
    ("MONEY", "$5 billion", "$5 billion"),
    ("MONEY", "a billion dollars", "a billion dollars"),   # words count when there is no digit
    ("MONEY", "two million", "two million"),
    ("MONEY", "a lot of money", None),
    ("PERCENT", "12%", "12%"),
    ("PERCENT", "percent", "percent"),
    ("PERCENT", "growth rate", None),
    ("COMPANY", "Nvidia", "Nvidia"),
    ("COMPANY", "3M", "3M"),
    ("COMPANY", "banks", None),
    ("COMPANY", "CORP.", None),
    ("PERSON", "Jensen Huang", "Jensen Huang"),
    ("PERSON", "   ", None),
])
def test_clean_entity(ner_core, key, text, expected):
    assert ner_core.clean_entity(key, text, VALID) == expected


def test_ticker_without_a_symbol_list_is_kept_as_is(ner_core):
    assert ner_core.clean_entity("TICKER", "whatever", None) == "whatever"


# --- grouping ---------------------------------------------------------------------------------------------------

def test_group_entities_applies_threshold_and_dedupes(ner_core):
    spans = [
        span("company", "Nvidia", 0.95, start=0),
        span("company", "NVIDIA", 0.90, start=30),      # same company, different case
        span("company", "Maybe Corp", 0.40, start=50),  # below the 0.5 threshold
        span("money", "$5 billion", 0.80, start=10),
    ]
    assert ner_core.group_entities(spans) == {"COMPANY": ["Nvidia"], "MONEY": ["$5 billion"]}


def test_group_entities_keeps_text_order_and_only_matched_keys(ner_core):
    spans = [span("company", "B Corp", start=20), span("company", "A Corp", start=0)]
    out = ner_core.group_entities(spans)
    assert out == {"COMPANY": ["A Corp", "B Corp"]}
    assert "TICKER" not in out


def test_group_entities_threshold_argument(ner_core):
    spans = [span("company", "Maybe Corp", 0.40)]
    assert ner_core.group_entities(spans, threshold=0.3) == {"COMPANY": ["Maybe Corp"]}


def test_stored_spans_round_trip(ner_core):
    spans = [span("company", "Nvidia", 0.9512, start=0), span("ticker", "NVDA", 0.7, start=20)]
    stored = ner_core.compact_spans(spans)
    assert stored[0] == {"label": "COMPANY", "text": "Nvidia", "score": 0.951, "start": 0, "end": 6}
    assert ner_core.entities_from_stored(stored, valid_tickers={"NVDA"}) == ner_core.group_entities(
        spans, valid_tickers={"NVDA"})


def test_item_text(ner_core):
    assert ner_core.item_text("Headline", "Summary") == "Headline. Summary"
    assert ner_core.item_text("Headline", None) == "Headline"
    assert ner_core.item_text("Headline", "") == "Headline"
