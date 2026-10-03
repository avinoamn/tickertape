# Labelling guide (the rubric used to write the Laya training and gold labels)

Labels are **soft targets**: a probability per option for each of the three questions in `services/laya/questions.py`.
Put the probability on what a careful reader would say; split it when the text is genuinely ambiguous (e.g. 0.7 / 0.3),
and use 1.0 when it is clear. Judge only from the headline, summary and focus ticker the model sees. Never use
knowledge of what happened to the stock afterwards.

The questions ask about the **focus company** (`focus=` in the item). If it is `None`, judge the item as a whole.

## event_type (earnings, guidance, mna, analyst, legal, other)

- `earnings`: reported quarterly/annual results, revenue/EPS beats and misses, SEC Item 2.02.
- `guidance`: outlook raised, cut or reaffirmed. If a headline reports results and an outlook, split between the two
  by which one is the story (e.g. "beats, raises guidance": earnings 0.5, guidance 0.5).
- `mna`: merger, acquisition, divestiture, spin-off, takeover bid, SEC Items 1.01 (acquisition agreements), 2.01.
- `analyst`: rating, price target, upgrade/downgrade, initiation.
- `legal`: lawsuit, investigation, settlement, regulatory action, SEC Items 1.03 bankruptcy, 3.01 delisting notices,
  4.02 restatements, 1.05 cyber incidents.
- `other`: everything else: product news, executive changes (5.02), buybacks, dividends, offerings, macro, 9.01-only exhibits.

## sentiment (pos, neu, neg), for the focus company's stock/business

- `pos`: clearly good news (beat, raise, win, approval, upgrade). `neg`: clearly bad (miss, cut, probe, layoffs due to trouble,
  downgrade). `neu`: routine, mixed or no clear direction (most SEC filings, most "stock moves" explainers with no cause).
- Mixed headlines ("beats but stock falls") split the probability. A fall in the stock alone is `neg` ~0.6, not 1.0.

## action (ignore, log, alert), "alert" means "a human should look now", not trading advice

- `alert`: market-moving or time-sensitive for a **watchlist company** (NVDA, AAPL, MSFT, AMZN, TSLA, META, GOOGL, AMD, JPM, XOM)
  or a very large company: surprise results/guidance, M&A, bankruptcy, major legal/regulatory action, a big
  executive departure, a halt. Alerts should be rare: roughly 10 to 15% of items.
- `log`: relevant company news worth storing but not urgent: ordinary results, analyst moves, product news, most
  material 8-Ks.
- `ignore`: not about a specific company, opinion/lifestyle/evergreen, routine boilerplate filings (9.01-only, 5.07 vote
  results, 3.02 small unregistered sales), tiny unknown companies with nothing material, duplicate-looking noise.

## Buybacks, dividends, insider trades (handled as rules, not new options)

Too rare in our data (about 6 of 1,372 items) to justify new `event_type` options, which would also change the live service.

- Buybacks / dividends / capital returns: `event_type` other; sentiment mostly `pos` (about 0.8), `neu` for routine
  reaffirmations; action `log`. A very large or surprising authorization at a watchlist or mega-cap company (about
  $50B or more, or a big step-up) gets alert about 0.3 to 0.5. Small or routine: log or ignore.
- Insider trades (headline news only, our SEC feed carries 8-Ks, not Form 4): `event_type` other. Open-market buying by a
  CEO or director: sentiment `pos` about 0.6. Selling: mostly `neu` (about 0.7), since most sales are scheduled
  (10b5-1). Action `log`; an unusual cluster of buying or a large unscheduled CEO/founder sale at a watchlist company
  gets alert about 0.3; scheduled or small trades: log or ignore.

## Alert rate (decided with the user: keep as is)

Alert stays rare and mostly a minority probability, so `action` accuracy will rarely have alert as its top class.
Evaluate `action` with Brier and calibration, and report the alert probability mass, not only accuracy.

## Consistency rules

- SEC 8-K items are boilerplate: label from the item codes (2.02 earnings; 1.01 is `mna` only if the text says acquisition,
  otherwise `other`; 5.02 `other`; 8.01 `other`).
- Do not let the company's size alone make something `alert`.
- Probabilities per question are renormalised by the tool, so only relative weights matter.
- These are Claude Code's labels (silver): the gold report measures agreement with them, not ground truth.
