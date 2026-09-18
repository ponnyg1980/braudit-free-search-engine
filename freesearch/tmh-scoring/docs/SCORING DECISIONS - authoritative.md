# TMH scoring — decisions record (AUTHORITATIVE)

Read this before writing any scoring code. It is the output of a long R&D
session and supersedes anything in the deployed Braudit audit tool, in
`tmh_scoring` v1.0/v1.1, or in any earlier draft.

Each decision has the reason attached. The reasons matter more than the
rules — without them the rules get "simplified" back into the mistakes they
were made to fix.

---

## D1. Two layers, and they are independent

```
Layer 1  MARK SIMILARITY   0-4   How alike are the two trademarks?
Layer 2  TRADE SIMILARITY  0-4   How related are the goods/services?
```

**Layer 1 sees ONLY the two mark texts** (plus visual evidence where
available). Not status. Not classes. Not age. Not jurisdiction.

**Why:** the deployed forensic score has no mark-similarity factor at all.
MOMENTUS against BANANA REPUBLIC — both registered word marks, one shared
class, same jurisdiction — scores 9/10 "Very High". It is not measuring
conflict, it is measuring how structurally impressive the cited registration
is. That cannot be the headline number.

---

## D2. Search operators govern RECALL, never assessment

The same mark must score identically however it was found — Similar To,
Contains, Vienna code, Google, Signa, or something invented next year.

**Why:** the deployed scorer awards similarity points *by criterion type*, so
the identical pair scores differently depending on how the operator searched.
Recall ("should this enter the pool?") and assessment ("how similar is it?")
are different jobs.

**Watch out:** BR-013 existed because similarity was scoring ZERO for
'Similar To' hits. Removing operator-dependence must not reintroduce that.
MOMENTUM MORTGAGE / MOMENTOUS vs MOMENTUS must still score — because they
genuinely resemble MOMENTUS, not because a particular search found them.
Keep them as regression fixtures.

---

## D3. Conflict and Rights are TWO numbers, never merged

```
CONFLICT        matrix of Layer 1 x Layer 2   — a factual similarity finding
RIGHTS STRENGTH 0-10                          — can this right cause a problem?
```

**Why:** an expired registration identical to the client's mark is a real
finding AND a negligible threat. One number cannot say both.

```
expired identical mark   conflict 9, rights 0-1
live identical mark      conflict 9, rights 9
```

Rights takes status, jurisdiction, age, recoverability, vulnerability and
trading evidence. **None of those may ever move the conflict score.**

---

## D4. Band vocabulary — FIVE bands plus a non-risk state

```
Low · Low/Medium · Medium · Medium/High · High
```

plus **`Result (not live)`** — reported, counted in "checked and cleared",
carries NO band.

**The half-bands are for AMBIGUITY, not padding.** When the two layers
disagree — a near-identical mark in a completely unrelated trade — the result
lands on a half-band so a human looks at it, rather than the report asserting
something only one axis supports.

**Note the conflict:** the deployed audit tool uses `Negligible / Low /
Medium / High / Very High`. That stays as-is in the deployed tool (do not
break it). The NEW monitoring system uses the five bands above. If the two
are ever merged, this must be resolved first.

---

## D5. A registered mark is a threat REGARDLESS OF AGE

Age never reduces the risk band. What age affects is how hard the mark is to
remove, which belongs in Rights, not Conflict.

---

## D6. Expiry is a gradient, not a switch

| Since expiry | Stage | Rights | Live? |
|---|---|---|---|
| < 6 months | renewable as of right | 7 | **YES** |
| 6–12 months | restorable at discretion | 4 | no |
| 12+ months | beyond restoration | 1 | no |
| 12+ and proprietor still trading | re-filing possible | 2 | no |

**Why:** a mark that lapsed last month can be brought back by simply paying,
and would be if challenged. Calling it "not live" understates it badly.

Expiry derives from filing date where not stated: **filing + 120 months** =
term end, **filing + 126 months** = past the window. Filing date is always
present in Braudit and TemmyDB.

**Renewal resets the clock** — filing + 120 is only the FIRST expiry. A mark
filed 2005 and renewed twice expires 2035. For a live mark return the NEXT
term end; for a lapsed one the MOST RECENT. Validated against real records
(UK00002296257 filed 2002, expiry 2022).

**OPEN:** restoration window is currently 12 months (fail-safe). Jonathan's
rule of filing + 126 months implies 6. Needs his confirmation.

---

## D7. Class overlap is EVIDENCE, not a gate

The deployed pipeline does `if not touches_classes(...): continue` — results
are discarded before scoring. On Woodcross that dropped **124 of 246 rows**.

**Why it's wrong:** Nice classes organise the register, they do not measure
commercial similarity. Different class plus closely related goods is a real
conflict. Class 25 clothing against class 35 retail-of-clothing is the
classic case.

Multiple shared classes rank above one. Class overlap feeds Layer 2; it never
gates.

---

## D8. Goods/services from specification TEXT, not class numbers

Class 35 alone covers advertising, business management and retail — a
clothing retailer and a management consultancy both sit in it and do not
compete.

Deterministic term overlap, **not** embeddings, because the output must name
the shared terms: "both cover mattress, duvets, linen" is defensible in a
client report; a cosine of 0.73 is not.

- Overlap coefficient, not Jaccard — specification lengths vary enormously
  and a narrow spec inside a broad one is a real conflict.
- IDF computed per run from the candidate set, so common sector words
  self-downweight.
- **Stemming is required and must converge.** `mattresses`→`mattress` while
  `mattress`→`mattres` is a bug — they never meet. Words ending `ss` keep it;
  `-ing` strips the doubled consonant so `bedding`→`bed`.
- Strip IPO boilerplate ("information, advisory and consultancy services
  relating to all of the aforesaid…").

**Evidence level must be named on every row:** `specification` /
`classes only — cited specification missing` / `classes only — client
specification missing` / `classes only — neither`. The two gaps have
different remedies.

---

## D9. Generic trade words carry no similarity weight

SWIFT COURIERS vs RAPID COURIERS must NOT score as similar. Two couriers both
called "… Couriers" have not collided.

Two places this bites, both found by testing:
1. Shared-word detection must exclude generic vocabulary.
2. **Token-level fuzzy matching must too** — otherwise the shared token
   "COURIERS" scores a perfect 1.00.

Guard: re-check similarity on the generic-stripped forms. **Only apply the
guard where generic words were actually present** — MOMENTUS vs MOMENTUM
MORTGAGE contains none, and its lower stripped ratio is real similarity, not
boilerplate.

---

## D9a. THREE independent axes: looks like, sounds like, spelled like

Jonathan, 17 Sep 2026: *"There is not one way to score, there are several
ways to score... We have always said there are 3 independent ways to score,
looks like, sounds like, spelled like. Any one can be a high score."*

Each axis is measured on its own and the STRONGEST carries the mark. They are
never averaged — averaging lets two quiet axes talk a loud one down.

| axis | measure | function |
|---|---|---|
| looks like | logo comparison, pHash → CLIP (D13) | `visual_decision` |
| sounds like | syllables, rhyme-weighted | `phonetic_score()` |
| spelled like | edit distance | `orthographic_score()` |

**Spelled-like must be EDIT DISTANCE, not a sequence ratio.** The ratio is
biased by length in exactly the wrong direction — one letter changed scores
0.857 on VETSURE/VETSURA (7 chars) but 0.952 on a 21-character mark. The
short mark is the more confusable one and scored lowest, which left VETSURE
against VETSURA at tier 2. Jonathan: *"vetsure and vetsura have one letter of
difference, that would make it high risk."* Counting edits removes the bias:
one edit → 0.95, two → 0.90 (0.85 on a short mark), transpositions count as
one slip. VETSURE/VETSURA and MOMENTUS/MOMENTUM now read tier 3, "spelled
alike — 1 letter different".

**A shared word is NOT one of the axes.** A token scoring 1.00 says something
about one word, not about either mark as a whole, so it stays behind the
`token_is_whole` coverage guard. Folding it into the axis gate sent 149 rows
up a band on the shared word "RENTAL" alone (measured, then reverted).

**A run-together word is still a shared word.** Brands concatenate constantly,
and a whitespace split cannot see inside: "Rental Passport" scored tier 2
against Rental Closet while "RentalPassport" scored tier 0 and was discarded
as noise before triage — the same mark, spaces removed, gone. `_segment()`
opens a token on a case boundary (RentalPassport → Rental, Passport) or where
the other mark's own word sits at the start or end of it.

The boundary must be real, and **what remains must itself be at least three
letters**: PARENTAL ends with RENTAL and leaves "PA", which is a coincidence
of spelling, not the other half of a compound. Without that guard SKY
PARENTAL ALERT was credited with sharing the client's distinctive word. A
shorter prefix must not match a longer word either — Rent is not Rental.

Measured on 835 distinct cited marks: rows discarded as noise fell from 4 to
2, and the two that remain (RentLondonFlat, RENTELECTRIC) are correct — they
contain "Rent", not "Rental".

---

## D9b. Phonetic similarity is SYLLABLE-based, and it is a score

Soundex is wrong for trademarks. It keeps the first letter plus three
consonant codes and truncates — fine for the short surnames it was designed
for, useless for a mark of more than one word: RENTAL CLOSET and RENTALHEALTH
both code to **R534**, because the code stopped listening after "Rental".
Measured 16 Sep 2026 on a live Rental Closet audit, that declared eight
unrelated marks "phonetically equivalent" and carried them to Medium/High.

Jonathan's rule, and the reason: *"I tend to only hear 'sounds like' when
it's 2 to 3 syllables and they tend to rhyme."* There is no hard threshold —
it is an observation about when the ear actually confuses two brands, so it
is a decaying weight, not a cut-off.

- Split each mark into syllables (vowel groups with their onset consonants).
- Compare the DISTINCTIVE part only, so SWIFT COURIERS and RAPID COURIERS do
  not rhyme their way to a match on the shared generic tail (D9). Scores 0.20.
- Rhyme — a matching final syllable — is what lifts the score.
- Two or three syllables is where the signal is strongest. Four damps to
  0.75, five or more to 0.6; very different syllable counts damp again. A
  long mark can still be flagged, it just cannot carry tier 3 on sound alone.

**It is a SCORE, never a switch.** Jonathan, 16 Sep 2026: *"We are creating a
scoring engine not a decision, we just want to flag and score."* The old
boolean forced tier 3 on its own; the score now competes with the other axes.

Measured on 987 real rows: 33 moved, 28 down from an overstated band, 5 up.
Medium/High fell from 8 to 1. Rental Closet against RENTALHEALTH went from
"phonetically equivalent" (tier 3) to "slight resemblance" (tier 1).

---

## D10. Enrichment is gated on Layer 1

Only fetch missing specifications where mark similarity ≥ 2.

**Why:** with mark tier 0–1, even a perfect trade match tops out at 4 on the
matrix — it cannot reach a reportable band. Measured across 875 real rows:
**only 13.3% ever need a lookup.**

---

## D11. Trading evidence is the tie-breaker

Two results can sit at the same band on mark and trade alone. What separates
them is whether the proprietor is demonstrably trading (Companies House).
Active trading moves the band up one step; dissolved or dormant moves it
down. No company means the factor is skipped, never counted against them.

---

## D12. Seniority — the client may be the one at risk

If the cited mark was filed BEFORE the client's, the client is not the senior
party. That is not a weaker opportunity, it is a different situation:
**"Review — client may be at risk"**, not a risk band.

Nothing in the deployed system expresses this.

---

## D13. Image scoring (deferred to v2, but decided)

pHash pre-filter → CLIP, and:

- **pHash alone must NOT declare "identical".** It is nearly colour-blind — a
  red logo and an identical blue one hash to distance ~2. Require a mean-RGB
  check too; otherwise fall through to CLIP.
- **Preserve aspect ratio.** Resize proportionally and pad onto 224×224.
  Squashing distorts a wide mark; centre-cropping cuts its ends off. The
  deployed code squashes.
- **Use the DEPLOYED thresholds: identical ≥0.92, similar ≥0.85, weak ≥0.78.**
  They are calibrated on real client data ("Friars cited marks, presumed
  unrelated, cosine mean 0.55 max 0.69"). My 0.95/0.90/0.85 came from
  synthetic shapes and would under-report badly.
- Flatten transparency onto **white** — logos are usually transparent PNGs
  and compositing onto black inverts them.
- `method` must distinguish `unavailable` from `unrelated`. Never treat a
  failed decode as clearance.
- **An ended mark with an identical logo is NOT automatically Medium.** It is
  `Result (not live)` with high similarity noted.

---

## D14. The AI layer — bounded, once, stored

Code scores everything. The model reviews only what the code flags as
ambiguous (half-bands, signals disagreeing) or what the client selects.

Four constraints, all required:
1. **Runs once per result; the output is stored**, not regenerated.
2. **Stored with provenance** — model name, prompt version, date.
3. **Bounded** — may move a result one band with a written reason. It can
   never turn a Low into a High.
4. **Never client-facing unreviewed** where it changed something. Always
   closes with: if in any doubt, book an appointment.

**Why not let it score everything:** cost across thousands of results,
non-reproducibility when a client challenges a score, and it reintroduces
exactly what we are removing from Braudit.

---

## D15. Fail closed, never silently

- A run that cannot be validated is HELD, not sent. The canary: search for
  the client's own mark; if their own registration does not come back, the
  run is broken. This caught a real export with all 304 mark-text cells empty
  which would otherwise have gone out as a clean "no results" report.
- A query that errors must raise, never return an empty list. "No conflicts"
  and "the query broke" must never look the same.
- Where a field cannot be obtained, say so against that field rather than
  leaving it blank — otherwise an uncollected field is indistinguishable from
  an empty one. That is how an empty `filing_date` column went unnoticed
  across every export.

---

## D16. Known-bad patterns in the deployed code — do not copy

- `pipeline/filters.py` lines ~1567–1629: Google/Domain/Social risk depends
  on whether the string `led` appears in the text (44.35 / 40.76 / 63
  constants, leftovers from the STEALTH client). Companies House results are
  hardcoded Low Risk. **These are placeholders, not scoring**, and the LLM is
  told to use the risk grade as its first filter — so prototype numbers
  become polished AI commentary.
- `score_record` "Classes and terms" searches the cited specification for the
  client's **brand name**, then writes "recital differs from client goods".
  That statement is false — it never compared the client's goods.
- `risk_from_score` treats only `ended` as dead. `Expired`, `Lapsed`,
  `Cancelled`, `Withdrawn`, `Refused` all score as live. 27 of 190 rows on
  one real report.

---

## D17. Blocking must never be narrower than the scorer

Recall and assessment are one judgement split across two systems, and the
scorer can only judge what the blocking hands it. If the model would rate a
pair at tier 3, the candidate must reach it. Anything else is a false
negative the report presents as a clean search.

**The case that proved it (17 Sep 2026).** A UK audit for **Hailaflo** never
returned **Healaflow** (UK00906993372, registered, shared class 5). The two
SQL nets in `temmy_source.candidates_for_term` were a substring match and the
first four characters — HAIL against HEAL. They diverge at character two, so
neither net fired. Put the pair in front of the scorer and it reads tier 3,
*"spelled alike — 2 letters different"*. It was found only because a staff
member had added a broad "Contains: Flow" criterion by hand.

Any near-miss differing inside the first four characters was invisible.
VETSURE/VETSURA is caught because it diverges at character seven. A vowel
swap at the front of a word — which is precisely how a competitor files
around a mark — was not.

**The rule.** Blocking carries a third net: trigram similarity at a floor of
**0.25**, for terms of five characters or more. Not pg_trgm's 0.30 default —
HAILAFLO/HEALAFLOW measures **0.267**, so the default would still have missed
it. Below five characters the trigram space is too crowded to mean anything
and the substring net already covers it.

**Truncation must be by similarity, never by database id.** The candidate cap
exists to stop a runaway query, not to choose results. Ordering is: substring
matches first, then by trigram similarity descending, then id. If the cap is
reached, what survives is the closest — and the run says so in its warnings.

Measured on TemmyDB, 3.02M marks:

| term | before | after | Healaflow returned |
|---|---|---|---|
| Hailaflo | 117 | 555 | **yes** (was no) |
| Haila | 163 | 1,070 | — |
| Flow | 3,000 *(capped)* | 5,988 | — |

**Class overlap is NOT used to narrow the SQL.** Filtering the query by the
client's classes cut Hailaflo's 523 trigram candidates to 80 and cost 17
seconds, but D7 settles this: class overlap is evidence, never a gate. The
filtering happens in scoring, where it can be weighed.

**Where this is implemented.** The rule belongs here; the implementation
belongs with each data source, because it is a query against a particular
register's store. `temmy_source` satisfies it with the trigram net above.
**UK Watch satisfies it since 17 Sep 2026** in both of its blockers:
`uk_monitor/index.py` `DeltaIndex` (fortnightly, in memory - pg_trgm's
measure reproduced exactly, padded words + Jaccard, so the in-memory 0.25 is
the SQL 0.25; HAILAFLO/HEALAFLOW measures 0.267 there too) and
`uk_monitor/prescreen.py` `TrigramNet` (the all-time first-report sweep,
inverted over each fuzzy term's rarest trigrams so 2.9M rows cost ~30 s).
Measured on the cached 15 Jul-14 Aug delta (113,863 records, 720 cohort
terms): 55,313 candidates became 75,864 (+37%), of which Watch's precision
guard passed 1,953 (VITAL/VITL, VELOUR/VELOURA, WELLINGTON/TELLINGTON).
Tests: `uk_monitor/tests/test_d17_recall.py`.

**Open for ruling - Watch's precision guard is narrower than the scorer.**
`uk_monitor/precision.shared_basis` drops a pair with no shared distinctive
token unless the whole-string ratio is >= 0.85 (`STRONG_WHOLE_RATIO`, and
`_close` at 0.85). The scorer's tier-2 "similar" floor is 0.78. The real
client mark is **Hailaflo** (eight letters - the Deal, not the HAILAFLOW of
the fixture), and Hailaflo/Healaflow is 3 edits, ratio 0.82: `mark_similarity`
rates it tier 2, the trigram net now surfaces it, and the guard drops it
before scoring. The audit engine has no such guard and reports from tier 1,
so an audit for Hailaflo now returns Healaflow and Watch does not. On the
same delta, 373 net-added pairs sit in the 0.78-0.85 band the guard drops -
VITAL/VIDAL and GRIDMASTER/WINDMASTER among them, WELLINGTON/HILLINGTON too,
so the guard is doing real work there. Options: (a) guard = scorer floor
(0.78) and let Layer 2 carry the weight, accepting Hillington-class noise;
(b) keep 0.85 and accept Watch and audits disagree on this class. Pinned as
`xfail(strict=True)` in `test_d17_recall.py` until ruled.
A GIN trigram index on `marks.verbal_element_text` would make it faster —
`pg_trgm` is installed, the index is not — but the floor and the ordering
are the decision, not the index.

Two tests, and both are needed: the scoring suite asserts *Hailaflo and
Healaflow score tier 3*; the audit engine asserts *a search for Hailaflo
returns UK00906993372*. Either alone passes while the gap is open.


## G — the two legal-form patterns (raised 18 Sep 2026, tmh-scoring 2.1.0)

Moving the company-name legal-form strippers into the package (they had been
one regex in `audit_engine/companies.py` and another in
`uk_monitor/companies.py`) revealed that they had drifted apart in both
directions, and had presumably been drifting since each was written.

    only the audit pattern    HOLDINGS, GROUP, UK, (UK), L.L.C
    only the Watch pattern    CORPORATION, CORP, GMBH, SARL, BV, NV, AB, AS,
                              SA, SL, SPA, SRL, PTY, CIC, LLC

They disagree on 27 of 1,183 distinct real names from the Watch ledger. The
audit engine strips words that carry meaning in British marks — "UK & FRIED
CHICKEN" becomes "& FRIED CHICKEN", "HIFU CLINICS UK" becomes "HIFU CLINICS"
— which argues Watch is right. But Watch strips a bare AS and SA, which eat
ordinary English words ("SHOP AS YOU GO LIMITED"), which argues it is not.

2.1.0 keeps BOTH, named `LEGAL_FORMS_AUDIT` and `LEGAL_FORMS_WATCH` and wired
to the product that has always used each, so the move changed no score. A
third, `LEGAL_FORMS_COMMON`, holds only the forms both agree on and is used by
neither product yet.

Ruling needed on which list is authoritative, or on a third list built from
the evidence. Unifying will move scores on both sides, so it is a versioned
release with its own band_diff, not an edit.
