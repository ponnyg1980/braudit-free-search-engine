"""Business-type classification — the part SIC cannot do on its own.

THE JOB, AS SCOPED

  "Send Haiku through all successfully registered filings of the last 3 years.
   Categorise filings into the taxonomy of all Business Types, contained in
   sectors. Confirm SIC codes. Get the required data to create the always,
   often etc."                                              — Jonathan, 15 Jul
  "Just only assess Organisations ie. UK Limited companies."

WHAT THE SIC SEED ALREADY DOES, SO HAIKU DOESN'T HAVE TO

  120 of the 148 SIC codes in the taxonomy map to exactly ONE business type.
  For those, the empirical seed already gives real All/Most/Some/A few bands
  straight from the register — deterministic, free, no model involved. 107
  business types are finished before this file runs.

  40 SIC codes are shared by 2+ business types. `62012` covers ten of them —
  a fintech platform and an AI platform file very differently, and the SIC
  cannot tell them apart. That, and only that, is what the model is for.

  So the model's question is never "which of 242 business types is this?" It is
  "this filing is a 62012 company — which of these ten?" A choice from 2-10
  candidates, with the SIC already constraining the field. That is a far easier
  question, and a far cheaper one: ~128k filings rather than ~276k, batched
  ~20 to a call.

WHY THERE IS A "none of these" ESCAPE

  The taxonomy is 242 types; the register is every business in the country.
  Forcing a filing into the nearest type would quietly manufacture evidence for
  a type it isn't. `none` filings are counted and reported — a shared SIC with a
  high `none` rate is telling us the taxonomy has a gap, which is a finding we
  want, not noise to hide.

RUNNING IT

  Needs ANTHROPIC_API_KEY in the environment (alongside the Temmy keys).

      python -m freesearch.classify --plan            # what would run, no calls
      python -m freesearch.classify --sic 62012       # one shared SIC
      python -m freesearch.classify --all             # all 40, resumable
      python -m freesearch.classify --aggregate       # bands -> data/type_seed.json

  Resumable: every classified filing is cached in data/classified/<sic>.jsonl,
  so a re-run only does what's missing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

from . import taxonomy as tx
from .bands import CORPUS_WHERE, tier_for, TIER_LABELS, DROP_BELOW
from .sic_seed import _qr_submit, _sic_where

DATA = Path(__file__).resolve().parent / 'data'
CACHE = DATA / 'classified'
TYPE_SEED = DATA / 'type_seed.json'

MODEL = 'claude-haiku-4-5-20251001'
BATCH = 20                 # filings per model call
WORKERS = 8                # parallel batches (the Anthropic API doesn't serialise)
MAX_PER_SIC = 6000         # more than enough to band; caps cost on huge SICs
MIN_FOR_BANDS = 30         # below this a business type stays on its SIC bands


# ---------------------------------------------------------------------------
# Which SICs need the model at all
# ---------------------------------------------------------------------------

def shared_sics() -> dict[str, list[str]]:
    """{sic: [business types sharing it]} — only these need classifying."""
    by = defaultdict(list)
    for _sector, types in tx.SECTORS.items():
        for name, codes in types.items():
            for c in codes:
                by[str(c)].append(name)
    return {c: v for c, v in by.items() if len(v) > 1}


def unique_sics() -> dict[str, str]:
    by = defaultdict(list)
    for _sector, types in tx.SECTORS.items():
        for name, codes in types.items():
            for c in codes:
                by[str(c)].append(name)
    return {c: v[0] for c, v in by.items() if len(v) == 1}


# ---------------------------------------------------------------------------
# Extract — the filings for one shared SIC
# ---------------------------------------------------------------------------

def fetch_filings(sic: str, *, base: str, key: str, limit: int = MAX_PER_SIC):
    """Registered org filings for a SIC, with the text the model will read.

    One row per trade mark: its wording, the applicant's company name, and its
    class specs joined. The company name matters — "SMITH DENTAL LTD" resolves
    an ambiguity that the goods wording alone often won't.
    """
    # Column names verified against information_schema, not assumed: the mark
    # wording lives on `marks`, not `trademarks`, and the company field is
    # `companies.name`. `companies.business_type` is CH's own free-text label —
    # a useful extra signal for the model, and free.
    #
    # We also carry the applicant identity and a representation flag. That costs
    # nothing extra on this pass and turns the sweep into the gap-targeting lead
    # dataset: who owns the mark, their company number (-> CH address / Cognism
    # contact), and whether they already have an IP representative. The
    # unrepresented owners are the ones who filed themselves and are the most
    # likely to have genuinely missed a class — the warm list.
    sql = f"""
WITH m AS (
  SELECT DISTINCT t.id, t.application_number AS app_no,
         mk.verbal_element_text AS mark,
         co.name AS company, co.business_type AS ch_type,
         a.name AS applicant, co.number AS company_number,
         EXISTS (SELECT 1 FROM representative_trademarks rt
                 WHERE rt.trademark_id = t.id AND rt.active) AS represented
  FROM companies co
  JOIN applicants a ON a.company_id = co.id
  JOIN applicant_trademarks apt ON apt.applicant_id = a.id
  JOIN trademarks t ON t.id = apt.trademark_id
  LEFT JOIN marks mk ON mk.trademark_id = t.id
  WHERE {_sic_where(sic)} AND {CORPUS_WHERE}
  LIMIT {int(limit)}
)
SELECT m.id, m.app_no, m.mark, m.company, m.ch_type,
       m.applicant, m.company_number, m.represented,
       string_agg(DISTINCT nc.number::text, ',') AS classes,
       left(string_agg(nct.goods_services_description, ' | '), 600) AS spec
FROM m
JOIN nice_class_trademarks nct ON nct.trademark_id = m.id
JOIN nice_classes nc ON nc.id = nct.nice_class_id
GROUP BY m.id, m.app_no, m.mark, m.company, m.ch_type,
         m.applicant, m.company_number, m.represented
""".strip()
    return _qr_fetch_all(sql, base=base, key=key, cap=limit)


def _qr_fetch_all(sql: str, *, base: str, key: str, cap: int) -> list[dict]:
    """Full result set, not just the preview.

    `_qr_submit` (used by the seed aggregations) only reads the ~preview rows,
    which is fine when a query returns a handful of grouped rows — but the sweep
    fetches thousands of individual filings, so it must page through the whole
    result. Uses the Temmy client's submit -> get_page cursor, and always
    deletes the server-side query afterwards.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _dep = _Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
    if str(_dep) not in _sys.path:
        _sys.path.insert(0, str(_dep))
    from temmy import TemmyQueryRunsClient  # type: ignore

    qr = TemmyQueryRunsClient(key, base_url=base.rstrip('/'), timeout_sec=90)
    manifest = qr.submit(sql=sql, page_size=1000, preview_limit=100)
    qid = manifest.get('query_id')
    total = int((manifest.get('pagination') or {}).get('total', 0) or 0)
    rows = list(manifest.get('preview') or [])
    try:
        page = 1
        while len(rows) < min(total, cap):
            items = (qr.get_page(qid, page) or {}).get('items') or []
            if not items:
                break
            rows += items
            page += 1
    finally:
        try:
            qr.delete(qid)
        except Exception:
            pass
    # de-dupe by row id (preview + page 1 can overlap) and cap
    seen, out = set(), []
    for r in rows:
        rid = r.get('id')
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------

PROMPT = """You are categorising UK trade mark filings by the filing company's line of business.

Each filing below was made by a UK limited company whose Companies House SIC code is {sic} ({sic_note}). That SIC is shared by several business types, so your job is only to decide WHICH ONE of these candidates each filing's company is:

{candidates}

Use the company name and the goods/services wording together. The company name is often decisive.

Rules:
- Choose exactly one candidate number per filing, or 0 for "none of these".
- Use 0 honestly. These companies all share one SIC code but some will not be any of the candidates. A wrong forced answer is worse than 0.
- Judge the COMPANY's line of business, not the individual mark. A restaurant that files a T-shirt mark is still a restaurant.

Filings:
{filings}

Reply with one line per filing, exactly "<filing number>:<candidate number>". No other text."""


def _anthropic(prompt: str, *, api_key: str, max_tokens: int = 1500) -> str:
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=json.dumps({'model': MODEL, 'max_tokens': max_tokens,
                         'messages': [{'role': 'user', 'content': prompt}]}).encode(),
        headers={'content-type': 'application/json', 'x-api-key': api_key,
                 'anthropic-version': '2023-06-01'},
        method='POST')
    body = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return ''.join(b.get('text', '') for b in body.get('content', []))


def classify_batch(rows, candidates, sic, *, api_key: str) -> dict:
    """-> {row_id: candidate name | None}"""
    listing = '\n'.join(f'{i+1}. {c}' for i, c in enumerate(candidates))
    filings = '\n'.join(
        f'{i+1}. company="{(r.get("company") or "?")[:60]}" '
        f'ch_type="{(r.get("ch_type") or "")[:30]}" '
        f'mark="{(r.get("mark") or "?")[:40]}" '
        f'classes={r.get("classes")} spec="{(r.get("spec") or "")[:220]}"'
        for i, r in enumerate(rows))
    note = 'the SIC these companies share'
    txt = _anthropic(PROMPT.format(sic=sic, sic_note=note, candidates=listing,
                                   filings=filings), api_key=api_key)
    out = {}
    for line in txt.splitlines():
        m = re.match(r'\s*(\d+)\s*:\s*(\d+)', line)
        if not m:
            continue
        fi, ci = int(m.group(1)), int(m.group(2))
        if 1 <= fi <= len(rows):
            out[rows[fi - 1]['id']] = candidates[ci - 1] if 1 <= ci <= len(candidates) else None
    return out


def classify_sic(sic: str, *, base: str, key: str, api_key: str) -> int:
    cands = shared_sics().get(sic)
    if not cands:
        print(f'SIC {sic} is not shared — its bands come straight from the seed')
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f'{sic}.jsonl'
    done = set()
    if path.exists():
        for ln in path.read_text().splitlines():
            try:
                done.add(json.loads(ln)['id'])
            except Exception:
                pass

    fetched = fetch_filings(sic, base=base, key=key)
    total_available = len(fetched)
    rows = [r for r in fetched if r['id'] not in done]
    print(f'SIC {sic}: {len(rows)} to classify, {len(done)} cached, '
          f'{len(cands)} candidates', flush=True)

    # Batches run in parallel. Temmy's Query Runs serialises concurrent
    # aggregations so a pool buys nothing there — the Anthropic API doesn't, so
    # here it's the difference between ~2 hours and ~15 minutes.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    chunks = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    lock = threading.Lock()
    n = [0]

    with path.open('a') as f:
        def _run(chunk):
            try:
                res = classify_batch(chunk, cands, sic, api_key=api_key)
            except Exception as exc:
                with lock:
                    print(f'  batch failed ({exc}) — will retry on next run',
                          flush=True)
                return
            with lock:
                for r in chunk:
                    f.write(json.dumps({
                        'id': r['id'], 'sic': sic, 'bt': res.get(r['id']),
                        'classes': r.get('classes'), 'spec': r.get('spec'),
                        # lead fields — the byproduct that makes gap-selling work
                        'app_no': r.get('app_no'), 'mark': r.get('mark'),
                        'applicant': r.get('applicant'),
                        'company_number': r.get('company_number'),
                        'represented': r.get('represented'),
                    }) + '\n')
                    n[0] += 1
                f.flush()      # cached as we go, so a kill costs one batch
                print(f'  {n[0]}/{len(rows)}', end='\r', flush=True)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(as_completed([pool.submit(_run, c) for c in chunks]))

    # Mark done with the row count we saw, so a resume pass can tell a genuinely
    # complete SIC (skip, no re-fetch) from one that only has a partial cache
    # (re-run, the per-filing cache resumes). An empty/old marker fails the int
    # parse and is treated as incomplete, which is what we want.
    (CACHE / f'{sic}.done').write_text(str(total_available))
    print(f'\nSIC {sic}: classified {n[0]} (cache now covers {total_available})')
    return n[0]


# ---------------------------------------------------------------------------
# Aggregate -> per business type bands
# ---------------------------------------------------------------------------

def aggregate() -> dict:
    """Turn the classified filings into All/Most/Some/A few bands per type.

    Also derives the characteristic TERMS per business type from what companies
    actually wrote in their specs. This is the point Jonathan made about the
    design cohort: class 42 reads "IT/software" but a graphic designer's class
    42 says "graphic design; website design; logo design". The class number is
    ambiguous; the description disambiguates, so the report must carry both.
    """
    import re
    per_type_classes: dict[str, Counter] = defaultdict(Counter)
    per_type_terms: dict[str, Counter] = defaultdict(Counter)
    per_type_total: Counter = Counter()
    none_rate: dict[str, list] = defaultdict(lambda: [0, 0])   # sic -> [none, all]

    for path in sorted(CACHE.glob('*.jsonl')):
        for ln in path.read_text().splitlines():
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            sic = rec.get('sic')
            none_rate[sic][1] += 1
            bt = rec.get('bt')
            if not bt:
                none_rate[sic][0] += 1
                continue
            per_type_total[bt] += 1
            for c in str(rec.get('classes') or '').split(','):
                if c.strip().isdigit():
                    per_type_classes[bt][int(c)] += 1
            # terms: split the spec on ; and | , count each once per filing so a
            # long spec doesn't dominate. Kept short and generic (drop the
            # boilerplate-length outliers).
            seen = set()
            for t in re.split(r'[;|]', (rec.get('spec') or '').lower()):
                t = t.strip()
                if 3 < len(t) < 45 and t not in seen:
                    seen.add(t)
                    per_type_terms[bt][t] += 1

    out = {}
    for bt, total in per_type_total.items():
        if total < MIN_FOR_BANDS:
            continue
        classes = []
        for cls, cnt in per_type_classes[bt].most_common():
            share = cnt / total
            if share < DROP_BELOW:
                continue
            t = tier_for(share)
            classes.append({'nice_class': cls, 'n_marks': cnt,
                            'share': round(share, 3), 'band': t,
                            'label': TIER_LABELS[t]})
        # top characteristic terms (share of the type's companies that used it)
        terms = []
        for term, cnt in per_type_terms[bt].most_common(20):
            share = cnt / total
            if share < 0.03:
                break
            terms.append({'text': term, 'share': round(share, 3),
                          'band': tier_for(share, terms=True)})
        out[bt] = {'total_marks': total, 'classes': classes,
                   'terms': terms, 'method': 'classified'}

    TYPE_SEED.write_text(json.dumps(out, indent=1))
    print(f'wrote {TYPE_SEED} ({len(out)} business types)')
    print('\ntaxonomy gaps — shared SICs where many filings matched no candidate:')
    for sic, (none, tot) in sorted(none_rate.items(),
                                   key=lambda x: -(x[1][0] / max(x[1][1], 1))):
        if tot and none / tot > 0.25:
            print(f'   SIC {sic}: {none}/{tot} ({none/tot:.0%}) unmatched '
                  f'-> candidates may be missing a type')
    return out


def plan() -> None:
    from .sic_seed import load_seed
    seed = load_seed()
    sh, uq = shared_sics(), unique_sics()
    m = lambda c: (seed.get(c) or {}).get('total_marks', 0)
    print(f'{len(uq)} SICs -> one business type each: '
          f'{sum(m(c) for c in uq):,} filings, already banded from the seed (no model)')
    print(f'{len(sh)} SICs shared: {sum(m(c) for c in sh):,} filings need classifying\n')
    tot = 0
    for c, v in sorted(sh.items(), key=lambda x: -m(x[0])):
        n = min(m(c), MAX_PER_SIC)
        tot += n
        print(f'   SIC {c}: {m(c):>6,} filings (cap {n:>5,}) -> {len(v)} candidates')
    print(f'\n{tot:,} filings to classify ≈ {tot//BATCH:,} model calls at {BATCH}/call')


if __name__ == '__main__':
    base = os.environ.get('TEMMY_API_BASE_URL', '').strip()
    key = os.environ.get('TEMMY_QUERY_RUNS_API_KEY', '').strip()
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()

    if '--plan' in sys.argv:
        plan()
        sys.exit(0)
    if '--aggregate' in sys.argv:
        aggregate()
        sys.exit(0)
    if not api_key:
        print('ANTHROPIC_API_KEY is not set — add it next to the Temmy keys in '
              'temmy-access/secrets.env and re-run. Use --plan to see the scope '
              'without making any calls.')
        sys.exit(2)
    if '--sic' in sys.argv:
        classify_sic(sys.argv[sys.argv.index('--sic') + 1],
                     base=base, key=key, api_key=api_key)
    elif '--all' in sys.argv:
        def _complete(sic):
            done = CACHE / f'{sic}.done'
            cache = CACHE / f'{sic}.jsonl'
            if not done.exists() or not cache.exists():
                return False
            try:
                expected = int(done.read_text().strip())
            except ValueError:
                return False                  # old empty marker -> re-run
            cached = sum(1 for _ in cache.open())
            return cached >= expected
        for sic in sorted(shared_sics()):
            if _complete(sic):
                continue                      # genuinely complete — don't re-fetch
            classify_sic(sic, base=base, key=key, api_key=api_key)
        aggregate()
    else:
        print(__doc__.split('RUNNING IT')[1])
