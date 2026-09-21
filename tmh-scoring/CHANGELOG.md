# Changelog

## 2.4.0 — labelled sensitivity steps (21 Sep 2026)

**ZERO DIFFERENCES** — nothing in this release executes unless a step is set.
Record: `releases/2.4.0.md`.

New `tmh_scoring/sensitivity.py`: five positions (Much stricter / Stricter /
Standard / More sensitive / Much more sensitive) on each of six compartments,
mapped to `ScoringSettings` values. `settings_for(steps)` is the only call the
app needs.

Triage picks a step; R&D owns what the step means. A run records the STEP and
the `scoring_version`, so re-tuning a step later does not rewrite history, and
Triage has no path to a global default — the only thing it can produce is a
delta from DEFAULTS for one run.

- **Step 0 returns the `DEFAULTS` object itself**, not an equal copy, and the
  module asserts at import that every step-0 preset is empty. "Standard" has to
  be bit-identical to "no sensitivity", or Triage's clear path restores nothing.
- Presets are explicit VALUES, not offsets — an offset is unreadable in an
  audit and cannot express a lumpy measure. `orthographic` is lumpy: its score
  is discrete (0.95 / 0.90 / 0.85), so only gates landing between those values
  mean anything, and the preset rows say so.
- `AVAILABLE_STEPS` is derived from `PRESETS`. **`classes` has no +2** — one
  shared class is already treated as an overlap, so there is nowhere below it
  to go, and the control renders that position disabled rather than offering a
  step that scores as +1 and logs as a change.
- `_validate()` at import refuses a preset that reaches into another
  compartment, sets an unknown field, or sets a field to the value it already
  has.
- New `tools/sensitivity_sweep.py` — replays a band_diff inputs file at every
  offered step and reports what moves. Measured on a 12,000-record Watch sample
  and on audit run 8ccac5ee: every step moves in the direction its label
  promises, the largest effect is fuzzy -2 at 4.6% of Watch rows / 11.8% of
  audit rows, and `visual` moves nothing because its scorer is not wired yet.
- New `tests/test_settings_sensitivity.py`, 28 tests, including a
  monotonic-direction test that catches a transposed preset row.

## 2.3.0 — scoring settings become a per-run object (21 Sep 2026)

No scoring change: **ZERO DIFFERENCES** across 55,526 Watch records and 1,164
audit records, every field, `band_diff.py --impl package` before and after.
Record: `releases/2.3.0.md`.

Every threshold the scorers consult now comes from a `ScoringSettings` object
(`tmh_scoring/settings.py`) instead of a module-level constant. Pass nothing
and you get `DEFAULTS`, built from the same constants the globals held.

This is the mechanism sensitivity needs. Triage must be able to ask "what
would this run look like at a lower sound threshold?" without changing any
other run and without touching a global default — global settings remain R&D's
and Jonathan's alone. The two alternatives were both wrong: editing the package
changes every run past and future, and re-scoring in a patched subprocess
cannot run in a web request and throws away the per-comparison breakdown the
Triage panel exists to show.

- `settings=` added to `assess`, `mark_similarity`, `trade_similarity`,
  `phonetic_score`, `orthographic_score`, `score_word_result`,
  `risk_from_score`, `mark_similarity_components`, `goods_similarity`,
  `assess_risk`, `mark_similarity_tier`. Propagation inside the package is by
  `contextvars`, chosen because a helper that forgets to pass settings on
  inherits the caller's (correct) rather than silently reverting to defaults
  (a wrong answer that looks right).
- Fields are grouped into the six Triage compartments — classes, terms,
  phonetic, orthographic, fuzzy, visual — by `COMPARTMENT_FIELDS`.
- `mark_similarity`'s tier-3 gate is now per axis rather than on the maximum
  of the three. Identical at the defaults; necessary at any other setting, or
  loosening "sounds like" would loosen spelling too.
- `image_scoring` untouched — its thresholds have fields but the scorer is not
  wired into any run yet. Phase 2.
- **Removed** `LEGAL_FORMS_AUDIT` / `LEGAL_FORMS_WATCH`, deprecated in 2.2.0.
  The one remaining caller, `audit_engine/criteria.py`, used the audit pattern
  to LABEL a word structural-or-weak in the ignored-words panel; that question
  now has `ignore_words.is_structural()`, which folds every jurisdiction's
  list into one set. Labelling can safely be the union — it cannot change what
  is searched, only whether a word is offered back to staff.
- Named two bare literals: `word_low_medium_min` (6) and `generic_guard_ratio`
  (0.60). `TOKEN_COVERAGE_FOR_WHOLE` and `SOUNDEX_MIN_LENGTH_RATIO` stay
  importable from `two_layer` but are now aliases of the `DEFAULTS` fields.

## 2.2.0 — finding G ruled: legal forms are per jurisdiction (19 Sep 2026)

First intended behaviour change since the consolidation. Record:
`releases/2.2.0.md`.

Neither old pattern was adopted. Both applied a legal form outside the country
whose law defines it — the audit engine ate UK/GROUP/HOLDINGS, Watch ate a
bare AS/SA. Now `LEGAL_FORMS_GENERIC` applies everywhere and
`LEGAL_FORMS_BY_JURISDICTION` applies only where the record belongs, so a
British name never meets the Norwegian list. `DESCRIPTIVE_SUFFIXES` is its own
list, off by default.

Measured: 167 of 2,064 audit company rows (8.1%) normalise differently and
**2 bands move**. Both products now produce identical normalisation on all
1,183 ledger names. band_diff is ZERO DIFFERENCES, as expected — the change
is upstream of the scoring functions it replays.

Stripping moved from inside the comparison to Refine:
`normalise_company_name()` returns the compared form AND what was stripped,
and the audit company row carries `compared_as` / `legal_forms_stripped`.

## 2.1.0 — ignore words centralised, mark scorer explains itself (18 Sep 2026)

No scoring change: ZERO DIFFERENCES across 55,526 Watch records and 1,164
audit records, every field, `band_diff.py --impl package` before and after.
Record: `releases/2.1.0.md`.

- New `tmh_scoring/ignore_words.py`. `WEAK_TOKENS` moved in from
  `audit_engine/criteria.py`; both legal-form patterns moved in from
  `audit_engine/companies.py` and `uk_monitor/companies.py`, which had
  **diverged** — kept as `LEGAL_FORMS_AUDIT` and `LEGAL_FORMS_WATCH`, each
  wired to the product that has always used it, so no score moves. See
  finding G.
- `word_scoring.mark_similarity_components()` reports every comparison and
  its points; `_mark_similarity_points()` now selects from that list instead
  of computing its own, so total and breakdown cannot drift.
- Outside the package: `Scoring_Version` / `Threshold_Version` now derive
  from `__version__`, and `audit.runs.scoring_version` records it per run
  (migration 007).

## Unreleased — tests only, no scoring change (17 Sep 2026)

`tests/test_layer2_trade.py` + `tests/fixtures.py`: 32 tests pinning Layer 2
behaviour to D7, D8 and D10, plus 21 `xfail(strict=True)` tests recording where
2.0.0 does not do what a decision says. Scoring files are byte-identical to
2.0.0. Mutation check: ten deliberate breaks (no stemming, Jaccard, idf ignored,
class gate, collapsed evidence label, removed enrichment gate, thresholds,
boilerplate, class rescue) were all caught, and applying the tie-break fix
turns its xfail red, as intended.

**Findings awaiting a ruling** (measured on the Watch ledger, 27,164 results,
24,880 with specification text both sides):

| # | Finding | Decision | Size |
|---|---|---|---|
| A | A RELATED_CLASSES pair lifts two specifications with ZERO shared terms to "related" (tier 2), above a SHARED class with the same text (tier 1). The module says related classes are the fallback when text is missing, yet the class-only fallback never consults them. Applied backwards. | D8, module docstring | 1,566 pairs related on the class pair alone; 1,005 currently Medium+ |
| B | The IPO tail "information, advisory and consultancy services relating to all the aforesaid" survives: patterns lack commas and need "all OF the". Unrelated trades match on it. | D8 | 274 pairs match on it alone; 99 Medium+ |
| G | **RULED 19 Sep, fixed in 2.2.0** — per-jurisdiction lists. ~~The legal-form patterns disagree on 27 of 1,183 real names (2.3%). The audit engine strips UK, GROUP and HOLDINGS, eating words that are load-bearing in British marks ("UK & FRIED CHICKEN" -> "& FRIED CHICKEN"); Watch keeps them but strips a bare AS and SA, which eat ordinary English ("SHOP AS YOU GO LIMITED"). Neither list is simply right. ~~ | ruled 19 Sep | closed: 2 bands moved |
| C | Stemming does not converge for -ings plurals (fittings/fitting), -ing on ss/ll roots (dressing/dress, processing/process, selling/sell) and -ses plurals (houses/house, databases/database); clothes/clothing never meet; "preparation" is filtered only in the plural. The "every rule is idempotent" comment is false for 330 ledger words. | D8 | 183 pairs miss a variant (20 then share nothing); 122 clothes/clothing; 12 preparation-only |
| D | Class-only fallback: two shared classes = one shared class = tier 2. | D7 "multiple rank above one" | not measured |
| E | Band matrix: mark tier 1 x trade tier 4 = Medium, so D10's "tier 0-1 cannot reach a reportable band" holds for the conflict score but not the band; SWIFT COURIERS v RAPID COURIERS in the same trade is Medium. | D9, D10 | 0 Watch rows; 1 row in audit 8ccac5ee |
| F | Shared-term text depends on hash seed (known issue 1). | D8 | 6,086 trade_reason strings |

Cosmetic: D8 names the fourth evidence level "classes only — neither"; the code
writes "classes only - no specification text either side".

## 2.0.0 — 17 Sep 2026 — consolidation, NO behaviour change

Moved into one package, imports fixed, nothing else changed. Proven with
`tools/band_diff.py`: every field of every result identical — see
`releases/2.0.0.md`.

| Package file | Came from | sha256 (source) | Edited |
|---|---|---|---|
| `two_layer.py` | `scoring harness (experimental)/two_layer.py` | 53726887bf184140 | imports only |
| `goods_similarity.py` | `tm_monitor/goods_similarity.py` | f882d43c834b9730 | no |
| `risk_model.py` | `vendor/tmh_scoring/risk_model.py` (1.2.0) | 4b15e901e7ad6565 | no |
| `word_scoring.py` | `vendor/tmh_scoring/word_scoring.py` (1.2.0) | 42df380f5fc27976 | no |
| `bands.py` | `vendor/tmh_scoring/bands.py` (1.2.0) | 32ede713b3d14784 | no |
| `image_scoring.py` | `vendor/tmh_scoring/image_scoring.py` (1.2.0) | 6efff625fc182751 | no |
| `term_derivation.py` | `tm_monitor/term_derivation.py` | ff3b507a51e40f90 | no |
| `__init__.py` | `vendor/tmh_scoring/__init__.py` (1.2.0) | 5fdd203b0ac7a1c2 | docstring, version |
| `docs/SCORING DECISIONS - authoritative.md` | `temmy-access/` | df696ee0bf8cfa35 | no |
| `tests/test_layer1_mark.py` | `audit_engine/tests/test_word_scoring_two_layer.py` | fc1e7cc85f36be39 | imports only |
| `tests/test_word_scoring_v1.py`, `tests/test_image_scoring.py` | `vendor/tmh_scoring/tests/` | 87248f3c…, 94ccfecc… | no |

**Two copies had diverged. Why keeping one of each is still no change:**

- `risk_model.py`: vendor 1.2.0 and tm_monitor differ ONLY inside
  `mark_similarity_tier` (vendor has the MOMENTUS generic-guard fix). two_layer
  never calls it — it uses `GENERIC_MARK_WORDS`, `strip_generic`, `_MATRIX`,
  `_shift` and the band names, which are identical. v1 `word_scoring` does call
  it and was already on the vendor copy. Vendor kept.
- `goods_similarity.py`: vendor 1.2.0 has no `stem()`. No live caller imported
  the vendor copy — two_layer and the audit runner both used tm_monitor's.
  tm_monitor kept. NOTE `tmh_scoring.goods_similarity` / `build_idf` at package
  top level are therefore the stemmed versions; in 1.2.0 they were not.

**Not carried over:** `uk_monitor/phonetics.py` (the reconstruction). No live
path used it — the Watch shim preferred the authoritative vendor helpers. It
is also not faithful: `_phonetic_normalise` differs on 98.6% of 12,576 ledger
mark texts and would move the Layer 1 tier on 235 of 27,164 result pairs.
Archived in `docs/archive/`. Closes open decision 3 of the plan.

**Removed:** the Watch shim that synthesised a `tmh_scoring` module, and the
hard-coded `/sessions/ecstatic-bold-ptolemy/...` sys.path inserts.

### Known issues carried unchanged into 2.0.0 (fix in 2.0.1+, with a release record)

1. **Non-deterministic `trade_reason` text.** `goods_similarity` ranks shared
   terms with `sorted(set, key=-weight)`; with no idf (the Watch path) every
   weight is 1.0, so which terms appear after "shared:" depends on Python's
   per-process string hashing. Control on the Watch ledger: the SAME package
   run twice (PYTHONHASHSEED 1 vs 2) gave 6,086 of 27,164 different
   `trade_reason` strings, zero different bands or conflict scores. Bands and
   scores are unaffected. Fix: tie-break on the term, `key=lambda t: (-weight(t), t)`.
2. ~~Layer 2 has no tests.~~ Added — see Unreleased above.
3. **Three image tests fail** where `imagehash` is not installed (pre-existing).
4. **`uk_monitor/zoho_writer.py` hard-codes** `Scoring_Version = "uk_monitor/two-layer"`
   and `Threshold_Version = "bands-1.2.0"` on every Watch Result. Wire both to
   `tmh_scoring.__version__` so a challenged score can be traced to its code.
