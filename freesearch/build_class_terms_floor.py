"""Build the term vocabulary with a MARKS FLOOR instead of a rank cap.

WHY (Jonathan, 23 Sep 2026)
---------------------------
"The most important thing is that we find the closest matches to what the
client has said they do ... BUT it absolutely needs a bigger pool to draw
from, that's an error in design."

build_class_terms.py keeps the TOP 300 terms per class by popularity. Class 9
has 173,730 distinct term strings on marks registered in the last five years;
300 is 0.17% of them. "Money transfer services" (546 marks) and "foreign
exchange services" (347 marks) were cut from class 36, and KJ Beckett lost
26 of its own 78 registered terms to the same cut.

A rank cap is the wrong instrument: it keeps 300 in a small class where
fewer are real and cuts a big class off long before its standard wording
ends. This keeps every term that at least MIN_MARKS separate registered marks
use -- a direct test of "standard wording, not one applicant's phrasing".
At >=3: class 9 ~49k, 35 ~30k, 36 ~17k, 42 ~24k.

Shares are n_marks / class_marks, so an existing term's share and band are
UNCHANGED by widening; the rebuild only ADDS rows (all low-share).

HOW
---
ONE query per class. Query Runs MATERIALISES the full result server-side
(up to 100,000 rows, pages of up to 5,000) and serves it from
GET /api/v2/query-runs/{id}/pages/{n}. Only the PREVIEW is capped at 100.
build_class_terms.py believed the page parameter was ignored and so re-ran
the whole class aggregation for every 100 rows -- the first attempt at this
build was on course for 5.5 hours. Via /pages it is 45 queries.
(temmy-query-runs-privilegedbackup.md documents the endpoint.)
Each finished class is checkpointed, so a crash resumes rather than restarts.

    python3 build_class_terms_floor.py --env secrets.env --out /tmp/ct.csv
    python3 build_class_terms_floor.py --classes 36 --min-marks 3 --out /tmp/c36.csv

Writes to --out ONLY. Nothing reads that file until someone copies it over
data/class_terms.csv, which is a deliberate, separate step.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_class_terms as B  # noqa: E402

PAGE = B.PAGE


def _sub(cls: int, years: int, min_marks: int) -> str:
    return f"""
  SELECT lower(trim(term)) AS term, count(DISTINCT t.id) AS n_marks
  FROM nice_class_trademarks nct
  JOIN nice_classes nc ON nc.id = nct.nice_class_id AND nc.number = {cls}
  JOIN trademarks t ON t.id = nct.trademark_id
  CROSS JOIN LATERAL unnest(string_to_array(nct.goods_services_description, ';')) AS term
  WHERE {B._corpus(years)} AND length(trim(term)) BETWEEN 4 AND 80
  GROUP BY 1 HAVING count(DISTINCT t.id) >= {min_marks}
"""


def _req(method, path, cfg, body=None, timeout=300):
    import urllib.request, urllib.error
    req = urllib.request.Request(
        cfg['TEMMY_API_BASE_URL'].rstrip('/') + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json',
                 'X-Query-Runs-Key': cfg['TEMMY_QUERY_RUNS_API_KEY']},
        method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def fetch_all(sql: str, cfg: dict, *, retries: int = 3) -> list[dict]:
    """Run once, then read every materialised page (5,000 rows each)."""
    for attempt in range(retries):
        try:
            r = _req('POST', '/api/v2/query-runs', cfg,
                     {'sql': sql, 'page_size': 5000, 'preview_limit': 0,
                      'ttl_seconds': 3600})
            qid = r['query_id']
            waited = 0
            while r.get('status') not in ('ready', 'completed', 'succeeded'):
                if r.get('status') in ('failed', 'error'):
                    raise RuntimeError(f"query failed: {r.get('error') or r}")
                time.sleep(3); waited += 3
                if waited > 600:
                    raise TimeoutError('query did not finish in 10 min')
                r = _req('GET', f'/api/v2/query-runs/{qid}', cfg)
            pag = r.get('pagination') or {}
            total, pages = int(pag.get('total') or 0), int(pag.get('total_pages') or 0)
            if total >= 100000:
                raise RuntimeError(f'{total} rows hits the 100k run cap -- raise the floor')
            rows: list[dict] = []
            for n in range(1, pages + 1):
                pg = _req('GET', f'/api/v2/query-runs/{qid}/pages/{n}', cfg)
                rows.extend(pg.get('items') or pg.get('rows') or pg.get('data') or [])   # pages endpoint: 'items'
            try:
                _req('DELETE', f'/api/v2/query-runs/{qid}', cfg)
            except Exception:  # noqa: BLE001 -- expiry cleans up anyway
                pass
            if len(rows) != total:
                raise RuntimeError(f'read {len(rows)} of {total} rows')
            return rows
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return []


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--env', default='../temmy-access/secrets.env')
    p.add_argument('--classes', default='')
    p.add_argument('--years', type=int, default=B.DEFAULT_YEARS)
    p.add_argument('--min-marks', type=int, default=3)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--out', required=True)
    p.add_argument('--checkpoint', default='')
    a = p.parse_args()

    cfg = B.load_env(a.env)
    classes = ([int(x) for x in a.classes.split(',') if x.strip()]
               if a.classes else list(range(1, 46)))
    ck = Path(a.checkpoint or (a.out + '.ckpt.jsonl'))
    done: dict[int, list[dict]] = {}
    if ck.exists():
        for line in ck.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[int(r['cls'])] = r['rows']
    todo = [c for c in classes if c not in done]
    print(f'floor >= {a.min_marks} marks | {len(classes)} classes, '
          f'{len(done)} already checkpointed, {len(todo)} to go | '
          f'{a.workers} workers', flush=True)
    t0 = time.time()

    def one(c):
        t = time.time()
        tot = B.class_mark_count(c, cfg, a.years)
        raw = fetch_all(f"SELECT term, n_marks FROM ({_sub(c, a.years, a.min_marks)}) q "
                        f"ORDER BY n_marks DESC, term", cfg)
        out = []
        for term, n in B.normalise(raw):
            n = min(n, tot) if tot else n
            share = (n / tot) if tot else 0.0
            out.append({'nice_class': c, 'term': term, 'n_marks': n,
                        'class_marks': tot, 'share': round(share, 5),
                        'band': B.band_for(share)})
        return c, out, len(raw), time.time() - t

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, c): c for c in todo}
        for f in as_completed(futs):
            c = futs[f]
            try:
                c, out, nraw, secs = f.result()
            except Exception as exc:  # noqa: BLE001
                print(f'  class {c:>2}: FAILED {type(exc).__name__}: {exc}', flush=True)
                continue
            done[c] = out
            with ck.open('a') as fh:
                fh.write(json.dumps({'cls': c, 'rows': out}) + '\n')
            print(f'  class {c:>2}: {len(out):>6,} terms (raw {nraw:,})  {secs:.0f}s  '
                  f'[{len(done)}/{len(classes)} done, {time.time()-t0:.0f}s]', flush=True)

    missing = [c for c in classes if c not in done]
    if missing:
        print(f'INCOMPLETE -- re-run to resume. Missing classes: {missing}', flush=True)
        sys.exit(2)
    flat = [r for c in sorted(done) for r in done[c]]
    B.write_csv(flat, Path(a.out))
    print(f'DONE {len(flat):,} terms across {len(done)} classes in '
          f'{time.time()-t0:.0f}s -> {a.out}', flush=True)


if __name__ == '__main__':
    main()
