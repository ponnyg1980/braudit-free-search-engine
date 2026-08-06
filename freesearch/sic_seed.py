"""SIC empirical seeding — the 'Always/Sometimes class patterns per industry'.

This is the Template SEEDING workstream from Doc 1 (Zoho Field Spec to Alex):
Industry Standard Templates are "seeded from UKIPO analysis, keyed by SIC
codes", and each G&S Classes & Descriptions row carries a frequency field
(Always / Sometimes) "driven by the UKIPO data analysis".

For a SIC code we ask the real register, via Query Runs:
    companies with that SIC -> their applicants -> their trademarks -> the Nice
    classes those marks are filed in -> how often each class appears.

That per-SIC aggregation is HEAVY (~25s each — it crosses the 2.85M-row link
table), so this is an OFFLINE BATCH, not a live call. It writes:
    * freesearch/data/sic_seed.json  — consumed live by sic_engine (instant)
    * freesearch/data/sic_seed.csv   — the Template seed CSV handed to Alex

`sic_engine` then reads the seed and serves real frequencies; it falls back to
the hand-built concordance for any SIC not yet seeded. Refresh the seed
quarterly (or when the register moves materially).

Run:  python -m freesearch.sic_seed 62012 47110 64191 ...     (space-separated)
      python -m freesearch.sic_seed --divisions               (all 2-digit)
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    from nice_classes import NICE_HEADINGS  # type: ignore
except Exception:
    NICE_HEADINGS = {}

DATA_DIR = Path(__file__).resolve().parent / 'data'
SEED_JSON = DATA_DIR / 'sic_seed.json'
SEED_CSV = DATA_DIR / 'sic_seed.csv'

# Frequency thresholds (share of the SIC's marks that use the class).
# 2-band (Doc 1 Template field) + 4-band (client-facing). Calibrated against
# live software (62012) numbers; tune as the seed grows.
from .bands import (TIER_LABELS as LABELS4, tier_for, frequency_for,
                    DROP_BELOW)
MIN_MARKS = 20            # ignore SICs with too little data to be meaningful

# Term seeding: sample this many class specifications and count the terms in
# them. The sample keeps the aggregation fast (~0.6s) while the frequencies
# stay representative. Only the top classes get terms (bounds the query count).
TERM_SAMPLE = 2500
TERM_TOP_CLASSES = 8
TERM_MAX = 25             # keep this many banded terms per class


def _term_band(share: float) -> tuple[str | None, str]:
    """(frequency Always/Sometimes|None, tier a..d) for a term share."""
    if share < 0.02:
        return None, 'd'
    tier = tier_for(share, terms=True)
    return frequency_for(tier), tier


def _band4(share: float) -> str:
    return tier_for(share)


def _freq2(share: float) -> str | None:
    """Doc 1 Template field (Always/Sometimes), or None to drop the row."""
    if share < DROP_BELOW:
        return None
    return frequency_for(tier_for(share))


def _write_json(path: Path, obj) -> None:
    """Write via a temp file + rename.

    The seed is checkpointed inside long batches, so a timeout or Ctrl-C lands
    mid-write sooner or later. rename() is atomic on POSIX, so the seed is
    either the old file or the new one — never a truncated one. (Learned the
    hard way: a killed backfill corrupted the seed and every business type
    silently fell back to the concordance.)
    """
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(path)


def _qr_submit(sql: str, *, base: str, key: str, timeout: int = 90) -> list[dict]:
    """Submit one bounded aggregation and return its rows (fits in preview)."""
    req = urllib.request.Request(
        base.rstrip('/') + '/api/v2/query-runs',
        data=json.dumps({'sql': sql, 'page_size': 100, 'preview_limit': 60}).encode(),
        headers={'Content-Type': 'application/json', 'X-Query-Runs-Key': key},
        method='POST')
    body = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return body.get('preview') or []


def _sic_where(sic: str) -> str:
    """Exact SIC match for a full code; prefix (LIKE 'NN%') for a coarse
    division/group. Coarse keys are 2-4 digits (Jonathan's 'primary SIC')."""
    s = sic.replace("'", "''")
    if len(s) >= 5:
        return f"'{s}' = ANY(co.sic_codes)"
    return f"EXISTS (SELECT 1 FROM unnest(co.sic_codes) x WHERE x LIKE '{s}%')"


def _freq_sql(sic: str) -> str:
    from .bands import CORPUS_WHERE
    return f"""
WITH sic_marks AS (
  SELECT DISTINCT t.id AS trademark_id
  FROM companies co
  JOIN applicants a ON a.company_id = co.id
  JOIN applicant_trademarks apt ON apt.applicant_id = a.id
  JOIN trademarks t ON t.id = apt.trademark_id
  WHERE {_sic_where(sic)} AND {CORPUS_WHERE}
), tot AS (SELECT count(*) AS n FROM sic_marks)
SELECT nc.number AS nice_class,
       count(DISTINCT sm.trademark_id) AS n_marks,
       (SELECT n FROM tot) AS total
FROM sic_marks sm
JOIN nice_class_trademarks nct ON nct.trademark_id = sm.trademark_id
JOIN nice_classes nc ON nc.id = nct.nice_class_id
GROUP BY nc.number
ORDER BY n_marks DESC
""".strip()


def _term_sql(sic: str, nice_class: int) -> str:
    from .bands import CORPUS_WHERE
    return f"""
WITH gs AS (
  SELECT nct.goods_services_description AS d
  FROM nice_class_trademarks nct
  JOIN nice_classes nc ON nc.id = nct.nice_class_id AND nc.number = {int(nice_class)}
  JOIN trademarks t ON t.id = nct.trademark_id
  JOIN applicant_trademarks apt ON apt.trademark_id = t.id
  JOIN applicants a ON a.id = apt.applicant_id
  JOIN companies co ON co.id = a.company_id
  WHERE {_sic_where(sic)} AND {CORPUS_WHERE}
  LIMIT {TERM_SAMPLE}
), n AS (SELECT count(*) AS c FROM gs)
SELECT lower(trim(term)) AS term, count(*) AS n_marks, (SELECT c FROM n) AS sample
FROM gs CROSS JOIN LATERAL unnest(string_to_array(d, ';')) AS term
WHERE length(trim(term)) BETWEEN 4 AND 80
GROUP BY lower(trim(term))
ORDER BY n_marks DESC
LIMIT {TERM_MAX}
""".strip()


def sic_class_terms(sic: str, nice_class: int, *, base: str, key: str) -> list[dict]:
    """Top banded terms for one (SIC, class), from a sampled aggregation."""
    try:
        rows = _qr_submit(_term_sql(sic, nice_class), base=base, key=key,
                          timeout=40)
    except Exception:
        return []
    sample = int(rows[0]['sample']) if rows else 0
    if sample < 10:
        return []
    out = []
    for r in rows:
        share = int(r['n_marks']) / sample
        freq, tier = _term_band(share)
        if freq is None:
            continue
        out.append({'text': r['term'], 'n_marks': int(r['n_marks']),
                    'share': round(share, 3), 'frequency': freq,
                    'band': tier, 'label': LABELS4[tier]})
    return out


def sic_frequencies(sic: str, *, base: str, key: str,
                    with_terms: bool = True) -> dict:
    """Compute one SIC's empirical class bands, and (optionally) the banded
    terms for its top classes. Class query ~25s; each term query ~0.6s."""
    rows = _qr_submit(_freq_sql(sic), base=base, key=key)
    total = int(rows[0]['total']) if rows else 0
    classes = []
    if total >= MIN_MARKS:
        for r in rows:
            n = int(r['n_marks'])
            share = n / total if total else 0
            freq2 = _freq2(share)
            if freq2 is None:
                continue
            b4 = _band4(share)
            classes.append({
                'nice_class': int(r['nice_class']),
                'heading': NICE_HEADINGS.get(int(r['nice_class']), ''),
                'n_marks': n, 'share': round(share, 3),
                'frequency': freq2,          # Always / Sometimes (Doc 1 field)
                'band': b4, 'label': LABELS4[b4],
                'terms': [],
            })
        if with_terms:
            for c in classes[:TERM_TOP_CLASSES]:
                c['terms'] = sic_class_terms(sic, c['nice_class'],
                                             base=base, key=key)
    return {'sic': sic, 'total_marks': total, 'classes': classes}


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

SEED_WORKERS = 8      # Query Runs takes concurrent aggregations happily


def seed(sics: list[str], *, base: str | None = None, key: str | None = None,
         merge: bool = True, with_terms: bool = True,
         workers: int = SEED_WORKERS, skip_done: bool = True) -> dict:
    """Seed a list of SICs and write JSON + CSV. Returns the seed dict.

    Runs the SICs through a thread pool (the aggregations are independent) and
    checkpoints the JSON as it goes, so a long batch can be resumed with
    `skip_done` rather than restarted.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    base = base or os.environ.get('TEMMY_API_BASE_URL', '').strip()
    key = key or os.environ.get('TEMMY_QUERY_RUNS_API_KEY', '').strip()
    if not base or not key:
        raise RuntimeError('TEMMY_API_BASE_URL and TEMMY_QUERY_RUNS_API_KEY required')

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    if merge and SEED_JSON.exists():
        try:
            out = json.loads(SEED_JSON.read_text())
        except Exception:
            out = {}

    todo = [s for s in sics if not (skip_done and s in out)]
    if len(todo) < len(sics):
        print(f'{len(sics) - len(todo)} already seeded, {len(todo)} to go')

    lock = threading.Lock()
    done = [0]
    t0 = time.time()

    def _one(sic):
        try:
            rec = sic_frequencies(sic, base=base, key=key, with_terms=with_terms)
        except Exception as exc:
            with lock:
                done[0] += 1
                print(f'[{done[0]}/{len(todo)}] SIC {sic}: FAILED {exc}', flush=True)
            return
        with lock:
            out[sic] = rec
            done[0] += 1
            print(f'[{done[0]}/{len(todo)}] SIC {sic}: {rec["total_marks"]} marks, '
                  f'{len(rec["classes"])} classes', flush=True)
            if done[0] % 3 == 0:            # checkpoint
                _write_json(SEED_JSON, out)

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(as_completed([pool.submit(_one, s) for s in todo]))

    _write_json(SEED_JSON, out)
    _write_csv(out)
    print(f'wrote {SEED_JSON} and {SEED_CSV} ({len(out)} SICs) '
          f'in {time.time()-t0:.0f}s')
    return out


SEED_TERMS_CSV = DATA_DIR / 'sic_terms.csv'


def _write_csv(seed: dict) -> None:
    """Template seed CSVs for Alex's modules:
      sic_seed.csv  — one row per (SIC, class)  [G&S Classes, class frequency]
      sic_terms.csv — one row per (SIC, class, term)  [specific terms + freq]
    """
    with SEED_CSV.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['sic', 'nice_class', 'heading', 'frequency',
                    'band_label', 'n_marks', 'share', 'sic_total_marks'])
        for sic, rec in seed.items():
            for c in rec['classes']:
                w.writerow([sic, c['nice_class'], c['heading'], c['frequency'],
                            c['label'], c['n_marks'], c['share'],
                            rec['total_marks']])
    with SEED_TERMS_CSV.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['sic', 'nice_class', 'term', 'frequency', 'band_label',
                    'n_marks', 'share'])
        for sic, rec in seed.items():
            for c in rec['classes']:
                for t in c.get('terms', []):
                    w.writerow([sic, c['nice_class'], t['text'], t['frequency'],
                                t['label'], t['n_marks'], t['share']])


def backfill_terms(sics: list[str], *, base: str | None = None,
                   key: str | None = None) -> dict:
    """Second pass: add banded terms to SICs already seeded for classes.

    Split from `seed` because the class aggregation is one query per SIC while
    terms are one per (SIC, class) — so classes land fast and terms fill in
    behind them. Resumable: skips any class that already has terms.
    """
    base = base or os.environ.get('TEMMY_API_BASE_URL', '').strip()
    key = key or os.environ.get('TEMMY_QUERY_RUNS_API_KEY', '').strip()
    out = load_seed()
    n = 0
    for sic in sics:
        rec = out.get(sic)
        if not rec or not rec.get('classes'):
            continue
        todo = [c for c in rec['classes'][:TERM_TOP_CLASSES] if not c.get('terms')]
        if not todo:
            continue
        for c in todo:
            c['terms'] = sic_class_terms(sic, c['nice_class'], base=base, key=key)
        n += 1
        print(f'terms: SIC {sic} ({len(todo)} classes)', flush=True)
        _write_json(SEED_JSON, out)
    _write_csv(out)
    print(f'backfilled terms for {n} SICs')
    return out


def load_seed() -> dict:
    """Load the seed for the live engine (empty dict if not seeded yet).

    A corrupt seed is loud. Swallowing it silently degrades all 242 business
    types to the concordance while everything still "works" — the worst kind of
    failure, because nothing looks broken.
    """
    if not SEED_JSON.exists():
        return {}
    try:
        return json.loads(SEED_JSON.read_text())
    except Exception as exc:
        print(f'WARNING: {SEED_JSON} is unreadable ({exc}). Falling back to the '
              f'concordance for every business type — re-seed to fix.',
              file=sys.stderr)
        return {}


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    _no_terms = '--no-terms' in sys.argv
    _terms_only = '--terms-only' in sys.argv
    if '--divisions' in sys.argv:
        # 2-digit divisions actually used by the LinkedIn industry map.
        from . import industry_data
        used = sorted({d for divs in industry_data.INDUSTRY_SIC.values()
                       for d in divs})
        args = used
    if not args:
        print('usage: python -m freesearch.sic_seed <sic> [...] '
              '[--no-terms | --terms-only]')
        sys.exit(1)
    if _terms_only:
        backfill_terms(args)
    else:
        seed(args, with_terms=not _no_terms)
