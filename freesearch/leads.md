# leads.py — readable copy

_(Same file, rendered. Code of record is `freesearch/leads.py`.)_

---

Gap leads — the by-product of the classification sweep.

THE IDEA (Jonathan, 16 Jul)

  While classifying the register into business types, we can see which classes
  and terms most companies in a sector protect. Any owner who lacks the ones
  their peers overwhelmingly hold is either (a) deliberately narrow, or (b) has
  a gap they didn't notice. Either way that's a conversation — and a reason to
  send them the sector report.

  Two openers Jonathan drafted:
    Analyst    — "70% of companies in your sector protect X; you don't. Was
                  that deliberate, or did it get missed on application?"
    Engagement — "Do you currently offer <top missing terms>?"

WHAT MAKES A LEAD (and the guardrails that keep it honest)

  * Company-level, never per-mark. A prolific filer is one lead and one
    data-point, not six. "All companies in your sector" means companies, so we
    dedupe to the company before we count anything.
  * Unrepresented first. An owner who filed through an IP firm already has an
    adviser and is a harder, touchier approach; an owner who filed themselves
    is the one who most plausibly missed a class. Represented owners are kept
    but flagged, so the team can choose.
  * Confidence-tagged. The "70% of your sector" claim is only as good as our
    sector assignment. If a SIC classified cleanly (restaurants: ~0% "none of
    these"), the cohort is trustworthy. If it was murky (software: ~55% none),
    we can still ask the Engagement question ("do you offer X?") but should not
    assert the Analyst statistic. Every lead carries the confidence so the team
    picks the right opener.
  * We report, we don't advise. The lead says what the register shows and asks
    a question. It never tells the owner they must file — that's their call
    (classes now, terms at audit/application), and if in doubt they talk to us.
    This is the same anti-SkyKick line the whole product holds: anchor to what
    the business actually does.

INPUT   data/classified/<sic>.jsonl  (written by the sweep)
OUTPUT  data/leads.csv + data/leads.json

    python -m freesearch.leads                       # build from whatever's cached
    python -m freesearch.leads --threshold 0.7 --min-companies 40

---

## The code

```python
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from .nice_labels import short as nice_short

DATA = Path(__file__).resolve().parent / 'data'
CACHE = DATA / 'classified'
LEADS_CSV = DATA / 'leads.csv'
LEADS_JSON = DATA / 'leads.json'

SECTOR_STANDARD = 0.70     # a class "most of your sector protects"
MIN_COMPANIES = 40         # below this a sector is too small to generalise
CONF_OK = 0.60             # >= this share of the SIC classified -> trust the stat


def _load() -> list[dict]:
    rows = []
    if not CACHE.exists():
        return rows
    for path in sorted(CACHE.glob('*.jsonl')):
        for ln in path.read_text().splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows


def _classes(rec) -> set[int]:
    return {int(c) for c in str(rec.get('classes') or '').split(',')
            if c.strip().isdigit()}


def build(threshold: float = SECTOR_STANDARD,
          min_companies: int = MIN_COMPANIES) -> dict:
    rows = _load()
    if not rows:
        print('No classified filings cached yet — run the sweep first '
              '(python -m freesearch.classify --all).')
        return {'sectors': {}, 'leads': []}

    # --- confidence per SIC: what fraction of its filings we could place ------
    sic_total, sic_none = defaultdict(int), defaultdict(int)
    for r in rows:
        sic_total[r['sic']] += 1
        if not r.get('bt'):
            sic_none[r['sic']] += 1
    sic_conf = {s: 1 - sic_none[s] / t for s, t in sic_total.items() if t}

    # --- collapse marks -> companies, within each business type ---------------
    # company key: prefer the CH number; fall back to applicant name.
    # {bt: {company_key: {'classes': set, 'name', 'company_number',
    #                     'represented', 'marks': [(app_no, mark)], 'sic'}}}
    bt_co: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        bt = r.get('bt')
        if not bt:
            continue
        key = (r.get('company_number') or '').strip() or (r.get('applicant') or '').strip().lower()
        if not key:
            continue
        co = bt_co[bt].setdefault(key, {
            'name': r.get('applicant'), 'company_number': r.get('company_number'),
            'represented': False, 'classes': set(), 'marks': [], 'sic': r['sic'],
        })
        co['classes'] |= _classes(r)
        co['represented'] = co['represented'] or bool(r.get('represented'))
        if r.get('app_no'):
            co['marks'].append({'app_no': r['app_no'], 'mark': r.get('mark')})

    # --- per business type: the classes most companies protect ----------------
    sectors = {}
    for bt, cos in bt_co.items():
        n = len(cos)
        if n < min_companies:
            continue
        cnt = defaultdict(int)
        for co in cos.values():
            for cl in co['classes']:
                cnt[cl] += 1
        standard = {cl: c / n for cl, c in cnt.items() if c / n >= threshold}
        if not standard:
            continue
        # confidence = min over the SICs that fed this business type
        sics = {co['sic'] for co in cos.values()}
        conf = min((sic_conf.get(s, 0) for s in sics), default=0)
        sectors[bt] = {
            'n_companies': n,
            'confidence': round(conf, 2),
            'reliable': conf >= CONF_OK,
            'standard_classes': {int(cl): round(sh, 3) for cl, sh in
                                 sorted(standard.items(), key=lambda x: -x[1])},
            # exemplar companies: biggest holders of each standard class, for
            # the "[top company] [second] [third]" message slots
            'exemplars': _exemplars(cos, standard),
        }

    # --- the leads: companies missing >=1 standard class ----------------------
    leads = []
    for bt, sec in sectors.items():
        std = set(sec['standard_classes'])
        for co in bt_co[bt].values():
            missing = std - co['classes']
            if not missing:
                continue
            miss = sorted(missing, key=lambda cl: -sec['standard_classes'][cl])
            leads.append({
                'business_type': bt,
                'confidence': sec['confidence'],
                'reliable': sec['reliable'],
                'suggested_opener': 'analyst' if sec['reliable'] else 'engagement',
                'applicant': co['name'],
                'company_number': co['company_number'],
                'represented': co['represented'],
                'marks': co['marks'],
                'classes_held': sorted(co['classes']),
                'classes_missing': [
                    {'nice_class': cl, 'label': nice_short(cl),
                     'sector_share': sec['standard_classes'][cl],
                     'exemplars': sec['exemplars'].get(cl, [])}
                    for cl in miss],
            })

    # unrepresented first, then by how much of the sector they're missing
    leads.sort(key=lambda l: (l['represented'], -len(l['classes_missing']),
                              -l['confidence']))
    return {'sectors': sectors, 'leads': leads}


def _exemplars(cos: dict, standard: dict, top: int = 3) -> dict[int, list[str]]:
    """For each standard class, the named companies that hold it and file the
    most marks — the recognisable names for the '[top company]' message slots."""
    out = {}
    for cl in standard:
        holders = [(len(co['marks']), co['name']) for co in cos.values()
                   if cl in co['classes'] and co['name']]
        holders.sort(key=lambda x: -x[0])
        out[int(cl)] = [name for _n, name in holders[:top]]
    return out


def write(result: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    LEADS_JSON.write_text(json.dumps(result, indent=1))
    with LEADS_CSV.open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['business_type', 'confidence', 'opener', 'applicant',
                    'company_number', 'represented', 'marks',
                    'classes_held', 'missing_class', 'missing_label',
                    'sector_share', 'exemplars'])
        for l in result['leads']:
            marks = '; '.join(m['mark'] or m['app_no'] for m in l['marks'])
            for m in l['classes_missing']:
                w.writerow([l['business_type'], l['confidence'],
                            l['suggested_opener'], l['applicant'],
                            l['company_number'], 'Y' if l['represented'] else 'n',
                            marks, ','.join(map(str, l['classes_held'])),
                            m['nice_class'], m['label'], m['sector_share'],
                            '; '.join(m['exemplars'])])
    print(f'wrote {LEADS_CSV} and {LEADS_JSON}')


def summary(result: dict) -> None:
    leads, secs = result['leads'], result['sectors']
    unrep = [l for l in leads if not l['represented']]
    reliable = [l for l in unrep if l['reliable']]
    print(f'sectors with a 70% standard : {len(secs)}')
    print(f'total gap leads             : {len(leads):,}')
    print(f'  unrepresented             : {len(unrep):,}  <- the warm list')
    print(f'  ...of those, reliable stat: {len(reliable):,}  (Analyst opener; '
          f'rest use Engagement)')


if __name__ == '__main__':
    args = sys.argv[1:]
    thr = float(args[args.index('--threshold') + 1]) if '--threshold' in args else SECTOR_STANDARD
    mc = int(args[args.index('--min-companies') + 1]) if '--min-companies' in args else MIN_COMPANIES
    res = build(threshold=thr, min_companies=mc)
    if res['leads']:
        write(res)
    summary(res)
```
