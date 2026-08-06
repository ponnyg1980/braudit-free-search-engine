# Empirical bands — what we say, and what it's based on

## What the client reads

> In your business type: **All use this** · **Most use this** · **Some use this** · **A few have this**

Descriptive, not prescriptive. We report what the register shows for businesses
like theirs. We don't tell them what to file — that's advice, and this is a free
tool. It also happens to be the only claim the data can actually support.

Words and thresholds live in **`bands.py`** and nowhere else. Every other module
imports them; the widget has them injected at build time. If the wording is ever
revisited, that's a one-line change.

| Band | Share of filings | Note |
|---|---|---|
| All use this | ≥ 75% | 3 in 4 or better |
| Most use this | ≥ 50% | a literal majority — "Most" must mean most |
| Some use this | ≥ 15% | a real minority, not noise |
| A few have this | < 15% | |

Concordance rows (no filings counted) say **"Likely (estimate)"**, never
"All use this". An estimate must look like an estimate.

## The corpus

    UK trade marks, status = Registered,
    filed in the last 3 years,
    by "Company or Organisation" applicants.

**362,130 filings.** ~202k of them carry a UK company number, which is what lets
us confirm SIC ↔ business type.

- **Registered only** — a refused mark isn't precedent.
- **Last 3 years** — current practice, not 1990s habits.
- **Organisations only** (Jonathan, 15 Jul). This is doing more work than it
  looks. The 387k individual applicants are where the bulk filers live — one
  person filing a dozen marks with identical boilerplate specs. Excluding them
  removes that noise structurally, with no dedupe heuristics. It also matches
  who TMH actually sells to.
- It made the queries **~4× faster** (25s → 4s), because it shrinks the join.

Deliberately out of scope: individuals, partnerships, foreign applicants. That's
the ~57% of the register with no CH SIC.

## Coverage

**242 / 242 business types are on real filing data. Zero concordance.**
(It was 38 before this pass.) 170 SICs seeded, **275,540 filings** analysed.

## How a business type gets its bands

**120 of 148 SIC codes map to exactly one business type** → the seed answers
directly. Deterministic, free, no model. That's 107 business types finished.

**40 SIC codes are shared.** `62012` covers ten types; a fintech platform and an
AI platform file very differently and the SIC can't separate them. Those need
`classify.py` — and the model's question is only ever *"this is a 62012 company,
which of these ten?"*, never *"which of 242?"*. ~74k filings, ~3,700 batched
calls. Needs `ANTHROPIC_API_KEY`.

Run `python -m freesearch.classify --plan` for the scope without making a call.

## Two things the data taught us

**The concordance was wrong where it mattered.** It said a skincare brand files
class 1 (chemicals). The register says **class 3, 88% — "All use this"**. Real
filings beat a sensible-sounding rule.

**SIC codes are often stale.** Sampling 62012 (software development): one company
filed for *tote bags and luggage*, another for *retail food services*, another
for *recruitment*. Companies register a SIC and then do something else. At scale
this stays a percentage point or two and doesn't move a band — which is why the
Organisation filter plus volume is enough, and why `classify.py` must keep its
"none of these" escape rather than forcing a wrong answer. A shared SIC with a
high `none` rate is a taxonomy gap telling us about itself.

## Refreshing

```bash
python -m freesearch.sic_seed <sic>... [--no-terms|--terms-only]  # classes fast, terms behind
python -m freesearch.build_widget          # regenerate the embedded data
python -m freesearch.build_widget --check  # CI: fails if the widget has drifted
python -m pytest freesearch/tests/test_parity.py -q                # 6,815 assertions
```

**The widget's data is generated, never hand-written.** Every wrong answer it has
ever given — fashion returning nothing, accountant returning skincare, the
3-sector list, the 6-activity list — was a hand-written stub drifting from the
engine, not the engine being wrong. `build_widget.py` exists so that can't happen
again, and `--check` catches it in CI.

The three live routes (your company / competitor mark / competitor portfolio)
need Temmy and have **no stub**. Without the engine they say so. A plausible
invented answer is worse than no answer, because nobody checks it.
