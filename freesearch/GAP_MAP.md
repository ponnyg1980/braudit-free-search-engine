# Shared-SIC gap map (40 SICs, 20-filing probe each)

The 40 SIC codes shared by 2+ business types — the only ones Haiku must
disambiguate. Probed 20 filings each (~800 filings, ~£0.30).

## The headline

High "none of these" is **not** a taxonomy gap. Pulling the actual unmatched
companies and free-form naming them shows no coherent missing type — they are
a scattered long tail (bags, recruitment, charity, memorials…) of companies
that registered under a broad tech/retail SIC and then did something else.
Stale registration, not a missing category.

**Implication:** we do *not* need to expand the taxonomy before the sweep.
The pipeline already handles these correctly — the "none of these" escape
excludes them, so business-type bands are built only from confident matches.

## Map

| SIC | none% | filings | candidates | read |
|-----|------:|--------:|:----------:|------|
| 96090 | 100% | 4,536 | 4 | vague → routed (point 8) |
| 88990 | 75% | 531 | 2 | stale-SIC noise (excluded by design) |
| 62012 | 70% | 7,579 | 10 | stale-SIC noise (excluded by design) |
| 43210 | 70% | 396 | 2 | stale-SIC noise (excluded by design) |
| 74100 | 65% | 2,218 | 4 | stale-SIC noise (excluded by design) |
| 56302 | 65% | 648 | 2 | stale-SIC noise (excluded by design) |
| 93110 | 60% | 507 | 2 | stale-SIC noise (excluded by design) |
| 90030 | 60% | 1,774 | 2 | stale-SIC noise (excluded by design) |
| 63110 | 60% | 1,383 | 2 | stale-SIC noise (excluded by design) |
| 45200 | 60% | 392 | 2 | stale-SIC noise (excluded by design) |
| 70229 | 55% | 34,038 | 7 | vague → routed (point 8) |
| 43320 | 55% | 107 | 2 | stale-SIC noise (excluded by design) |
| 86900 | 50% | 3,671 | 6 | stale-SIC noise (excluded by design) |
| 62020 | 50% | 4,848 | 2 | stale-SIC noise (excluded by design) |
| 47760 | 50% | 525 | 3 | stale-SIC noise (excluded by design) |
| 94990 | 45% | 666 | 4 | stale-SIC noise (excluded by design) |
| 93290 | 45% | 1,175 | 4 | stale-SIC noise (excluded by design) |
| 85590 | 45% | 2,893 | 8 | stale-SIC noise (excluded by design) |
| 96020 | 40% | 6,213 | 4 | mostly clean, some noise |
| 90010 | 40% | 859 | 2 | mostly clean, some noise |
| 88910 | 40% | 122 | 2 | mostly clean, some noise |
| 59200 | 40% | 810 | 2 | mostly clean, some noise |
| 46900 | 40% | 4,319 | 2 | vague → routed (point 8) |
| 25990 | 40% | 403 | 3 | mostly clean, some noise |
| 14190 | 40% | 749 | 7 | mostly clean, some noise |
| 96040 | 35% | 835 | 2 | mostly clean, some noise |
| 20420 | 35% | 796 | 6 | mostly clean, some noise |
| 11070 | 35% | 843 | 2 | mostly clean, some noise |
| 69102 | 30% | 341 | 2 | mostly clean, some noise |
| 56103 | 30% | 1,514 | 3 | mostly clean, some noise |
| 85510 | 25% | 563 | 2 | clean |
| 73110 | 25% | 9,768 | 3 | clean |
| 93130 | 20% | 761 | 2 | clean |
| 82301 | 20% | 393 | 4 | clean |
| 66220 | 20% | 377 | 4 | clean |
| 32500 | 20% | 488 | 2 | clean |
| 32409 | 20% | 552 | 2 | clean |
| 47910 | 15% | 26,056 | 5 | clean |
| 10890 | 10% | 1,817 | 4 | clean |
| 56101 | 0% | 1,539 | 2 | clean |

Total filings under shared SICs: **128,005**

## What this means for the £8 sweep

- **Green light.** No taxonomy expansion needed first. The nones are stale
  registrations the classifier already discards.
- Business-type bands (from the sweep) will be **cleaner** than the raw SIC
  bands, because they exclude the mis-registered companies a shared SIC
  sweeps in.
- Only genuinely vague SICs (70229, 96090, 46900) are routed away by point 8;
  everything else classifies usefully on the majority that fit.