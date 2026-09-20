# Changelog

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
