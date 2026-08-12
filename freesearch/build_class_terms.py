"""Build the verified Nice term vocabulary — one row per (class, term).

WHY THIS EXISTS
---------------
The AI class/term agent (AI_CLASS_AGENT_SPEC.md) must never invent a goods &
services term. It is only ever allowed to SELECT from a list of real terms we
hand it. This script builds that list.

Jonathan, 10 Aug 2026: "Update the csv with all terms for each class but only
on trademarks that have been registered successfully in the last 5 years max,
run that on all 45 classes, we do it once and update it quarterly."

WHAT IT PRODUCES
----------------
`data/class_terms.csv` — class, term, n_marks, class_marks, share, band.

This is a NEW file, deliberately not an overwrite of `data/sic_terms.csv`.
That one is keyed (sic, nice_class, term) and powers the company -> classes
route via SIC codes; it has a different shape and a different job. Two files,
two purposes. Overwriting it would silently break `company_classes()`.

THE CORPUS
----------
    status = 'Registered'                      -- succeeded, not just filed
    registration_date >= today - 5 years       -- current drafting practice
    nice_class_trademarks.active = true        -- not since removed

Registration date, not application date, because the instruction was
"registered successfully in the last 5 years". Note this differs from
bands.py's CORPUS_WHERE, which uses a 3-year APPLICATION window for a
different purpose (what a business type typically files for). Both are
correct for their own job; don't unify them without thinking.

WHY NOT EVERY TERM
------------------
"All terms" would include the long tail of one-off phrasings, typos and
fragments from a single filing. Those are not standard terms and recommending
them would be worse than useless. `--top` (default 300) keeps the terms that
real applicants actually reuse. For most classes that is effectively all of
the usable vocabulary; for the biggest it trims noise.

USAGE
-----
    python3 build_class_terms.py --env ../temmy-access/secrets.env
    python3 build_class_terms.py --classes 9,35,42 --top 100   # spot check
    python3 build_class_terms.py --dry-run                     # no write

Takes roughly 20s per query, so a full 45-class run is ~15-25 min with the
default 4 workers. Re-runnable and idempotent: it writes atomically via a
.tmp file, so an interrupted run never leaves a half-written vocabulary.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
OUT = DATA / 'class_terms.csv'

PAGE = 100          # Query Runs caps preview_limit at 100 and IGNORES its own
                    # page parameter, so we paginate with SQL OFFSET instead.
DEFAULT_TOP = 300
DEFAULT_YEARS = 5
DEFAULT_WORKERS = 4

# Terms that only make sense next to the term before them. Standalone they are
# noise, and an AI shown them would happily recommend "the aforesaid services".
_FRAGMENT = re.compile(
    r'\b(aforesaid|aforementioned|the above|all the foregoing|foregoing|'
    r'included in class|other than|none of the)\b', re.I)

# Bands reused from bands.py's TERM_THRESHOLDS so the wording a client sees is
# the same wherever it comes from.
_BANDS = [(0.30, 'Essential'), (0.12, 'Most use this'),
          (0.04, 'Recommended'), (0.0, 'Some use this')]


def band_for(share: float) -> str:
    for thr, label in _BANDS:
        if share >= thr:
            return label
    return 'Some use this'


def load_env(path: str | None) -> dict:
    """Read secrets.env without importing the lead-engine helper (this script
    ships inside the deployed engine image, which doesn't include it)."""
    cfg = {k: os.environ[k] for k in
           ('TEMMY_API_BASE_URL', 'TEMMY_QUERY_RUNS_API_KEY')
           if os.environ.get(k)}
    if path and Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            cfg.setdefault(k.strip(), v.strip())
    missing = [k for k in ('TEMMY_API_BASE_URL', 'TEMMY_QUERY_RUNS_API_KEY')
               if not cfg.get(k)]
    if missing:
        sys.exit('ERROR: missing ' + ', '.join(missing) +
                 ' (pass --env ../temmy-access/secrets.env)')
    return cfg


def qr(sql: str, cfg: dict, *, timeout: int = 300, retries: int = 3) -> list[dict]:
    """One Query Run. Returns preview rows (max 100).

    Query Runs answers a failed query with a bare 500 and no detail, so a
    retry is worth it before giving up — most 500s here are load, not syntax.
    """
    body = json.dumps({'sql': sql, 'page_size': PAGE,
                       'preview_limit': PAGE}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            cfg['TEMMY_API_BASE_URL'].rstrip('/') + '/api/v2/query-runs',
            data=body,
            headers={'Content-Type': 'application/json',
                     'X-Query-Runs-Key': cfg['TEMMY_QUERY_RUNS_API_KEY']},
            method='POST')
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            return r.get('preview') or []
        except urllib.error.HTTPError as e:
            if attempt == retries - 1:
                raise RuntimeError(f'Query Runs {e.code} after {retries} tries') from e
            time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    return []


def _corpus(years: int) -> str:
    return (f"t.status = 'Registered'\n"
            f"  AND t.registration_date >= (CURRENT_DATE - INTERVAL '{years} years')\n"
            f"  AND nct.active = true")


def class_mark_count(cls: int, cfg: dict, years: int) -> int:
    """How many registered marks in this class — the denominator for share."""
    rows = qr(f"""
SELECT count(DISTINCT t.id) AS n
FROM nice_class_trademarks nct
JOIN nice_classes nc ON nc.id = nct.nice_class_id AND nc.number = {cls}
JOIN trademarks t ON t.id = nct.trademark_id
WHERE {_corpus(years)}
""", cfg)
    return int(rows[0]['n']) if rows else 0


def class_terms(cls: int, cfg: dict, *, years: int, top: int) -> list[dict]:
    """Top `top` terms for one class, paged 100 at a time via SQL OFFSET.

    A specification is one semicolon-delimited string, so the terms are the
    parts: unnest(string_to_array(description, ';')). Length 4-80 drops both
    stray punctuation and whole run-on paragraphs that were never a term.
    """
    out: list[dict] = []
    for off in range(0, top, PAGE):
        rows = qr(f"""
SELECT lower(trim(term)) AS term, count(*) AS n_marks
FROM nice_class_trademarks nct
JOIN nice_classes nc ON nc.id = nct.nice_class_id AND nc.number = {cls}
JOIN trademarks t ON t.id = nct.trademark_id
CROSS JOIN LATERAL unnest(string_to_array(nct.goods_services_description, ';')) AS term
WHERE {_corpus(years)}
  AND length(trim(term)) BETWEEN 4 AND 80
GROUP BY 1
ORDER BY 2 DESC, 1
LIMIT {min(PAGE, top - off)} OFFSET {off}
""", cfg)
        out.extend(rows)
        if len(rows) < PAGE:
            break           # exhausted this class
    return out


def normalise(rows: list[dict]) -> list[tuple[str, int]]:
    """Clean, de-duplicate and drop fragments.

    The raw data contains 'accounting software.' alongside 'accounting
    software' — the same term, split across two rows by a stray full stop.
    Merge them and keep the combined count, otherwise both look half as
    popular as the term really is.
    """
    merged: dict[str, int] = {}
    for r in rows:
        t = (r.get('term') or '').strip().lower()
        t = re.sub(r'\s+', ' ', t)
        t = t.strip(' .,;:-–—')
        if len(t) < 4 or len(t) > 80:
            continue
        if _FRAGMENT.search(t):
            continue
        if not re.search(r'[a-z]', t):
            continue
        merged[t] = merged.get(t, 0) + int(r.get('n_marks') or 0)
    return sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))


def build(classes: list[int], cfg: dict, *, years: int, top: int,
          workers: int) -> list[dict]:
    results: dict[int, list[dict]] = {}

    def one(cls: int):
        t0 = time.time()
        try:
            total = class_mark_count(cls, cfg, years)
            raw = class_terms(cls, cfg, years=years, top=top)
            terms = normalise(raw)
            rows = []
            for term, n in terms:
                share = (n / total) if total else 0.0
                rows.append({'nice_class': cls, 'term': term, 'n_marks': n,
                             'class_marks': total, 'share': round(share, 5),
                             'band': band_for(share)})
            results[cls] = rows
            print(f'  class {cls:>2}: {len(rows):>4} terms '
                  f'(of {total:,} marks)  {time.time()-t0:.0f}s', flush=True)
        except Exception as exc:                       # never lose the whole run
            results[cls] = []
            print(f'  class {cls:>2}: FAILED — {type(exc).__name__}: {exc}',
                  flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, classes))

    flat: list[dict] = []
    for c in sorted(results):
        flat.extend(results[c])
    return flat


def write_csv(rows: list[dict], out: Path) -> None:
    """Atomic — a half-written vocabulary is worse than an old complete one."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + '.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['nice_class', 'term', 'n_marks',
                                          'class_marks', 'share', 'band'])
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--env', default='../temmy-access/secrets.env')
    p.add_argument('--classes', default='', help='e.g. 9,35,42 (default: all 45)')
    p.add_argument('--years', type=int, default=DEFAULT_YEARS)
    p.add_argument('--top', type=int, default=DEFAULT_TOP)
    p.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    p.add_argument('--out', default=str(OUT))
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()

    cfg = load_env(a.env)
    classes = ([int(x) for x in a.classes.split(',') if x.strip()]
               if a.classes else list(range(1, 46)))

    print(f'Building term vocabulary: {len(classes)} classes, '
          f'registered within {a.years} years, top {a.top} each, '
          f'{a.workers} workers')
    t0 = time.time()
    rows = build(classes, cfg, years=a.years, top=a.top, workers=a.workers)

    covered = sorted({r['nice_class'] for r in rows})
    missing = [c for c in classes if c not in covered]
    print(f'\n{len(rows):,} terms across {len(covered)}/{len(classes)} classes '
          f'in {time.time()-t0:.0f}s')
    if missing:
        print(f'⚠ no terms for: {missing}')

    if a.dry_run:
        print('(dry run — nothing written)')
        return
    write_csv(rows, Path(a.out))
    print(f'wrote {a.out}')


if __name__ == '__main__':
    main()
