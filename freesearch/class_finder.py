"""Keyword -> classes, with the wording real businesses actually filed.

Jonathan, 17 Sep 2026: "we want a client to be able to type what they do as a
keyword and it will display relevant goods and services classes."

WHAT MAKES THIS DIFFERENT FROM THE AI TOOLS. Nothing here is generated. Every
term comes verbatim from `data/class_terms.csv` — the vocabulary of terms on
marks REGISTERED at the UKIPO (see build_class_terms.py) — and every number
beside a term is a count of how many marks in that class used that exact
wording. The tool cannot return a term the IPO has not already accepted,
because it has no way to write one.

BROWSE-ONLY BY DESIGN. This endpoint takes no email, mints no session and
writes nothing. It is the public "look it up" layer on the Goods and Services
Classes page. Keeping a list is the Class Builder's job, and the hand-off is a
URL: /class-builder?classes=30,35 seeds the basket, so nobody has to log in to
browse and nobody loses their selection on the way.

BANDS. The design deck labelled terms ALWAYS 75%+ / MOST 50%+ / SOME 15%+.
No term in the dataset reaches 75% and only 13 of 13,007 pass 30%, because
specifications vary enormously — even "coffee" appears on only 20% of class 30
marks. Those thresholds would have shown every term as "A FEW" and the top
band would never have appeared at all. The thresholds below are set where the
data actually sits, which happens to reproduce the four bands the dataset was
built with (Essential / Most use this / Recommended / Some use this):

    ALWAYS  share >= 30%      13 terms    the class's defining wording
    MOST    share >= 12%     307 terms
    SOME    share >=  4%   2,397 terms
    A FEW   below            10,290 terms

A share is "of the marks registered in that class, this many used this exact
term", so it is a count, not an opinion. Say so wherever it is displayed.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

_CSV = Path(__file__).resolve().parent / 'data' / 'class_terms.csv'

MAX_CLASSES = 12        # a ranked shortlist, not all 45 — the UI lists the rest
MAX_TERMS = 40          # per class, most-registered first
GOODS_MAX = 34          # classes 1-34 are goods, 35-45 are services

# share thresholds -> band label. Ordered high to low; first match wins.
BANDS = ((0.30, 'ALWAYS'), (0.12, 'MOST'), (0.04, 'SOME'), (0.0, 'A FEW'))

# Words that carry no sector signal. A query of nothing but these matches
# half the register, so it is treated as no query at all.
_STOP = {'and', 'or', 'the', 'a', 'an', 'of', 'for', 'to', 'in', 'on', 'with',
         'my', 'our', 'we', 'i', 'sell', 'sells', 'selling', 'make', 'makes',
         'making', 'provide', 'provides', 'services', 'service', 'goods',
         'products', 'product', 'business', 'company', 'ltd', 'limited'}


def band_for(share: float) -> str:
    for floor, label in BANDS:
        if share >= floor:
            return label
    return 'A FEW'


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r'[^a-z0-9]+', (text or '').lower()) if t]


@lru_cache(maxsize=1)
def _rows() -> list[dict]:
    """Every term row, parsed once. ~13k rows, a few MB — fine to hold."""
    out = []
    with _CSV.open(newline='', encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            try:
                n = int(r['nice_class'])
                marks = int(r['n_marks'])
                share = float(r['share'])
            except (TypeError, ValueError, KeyError):
                continue            # a malformed row is skipped, never fatal
            term = (r.get('term') or '').strip()
            if not term or not 1 <= n <= 45:
                continue
            out.append({'n': n, 'term': term, 'marks': marks, 'share': share,
                        'toks': frozenset(_tokens(term))})
    return out


@lru_cache(maxsize=1)
def _by_class() -> dict[int, list[dict]]:
    idx: dict[int, list[dict]] = defaultdict(list)
    for r in _rows():
        idx[r['n']].append(r)
    for n in idx:
        idx[n].sort(key=lambda r: -r['marks'])
    return dict(idx)


def _public(r: dict) -> dict:
    return {'term': r['term'], 'marks': r['marks'],
            'share': round(r['share'], 5), 'band': band_for(r['share'])}


def _label(n: int) -> dict:
    try:
        from . import nice_labels                    # package context (Render)
    except ImportError:                              # bare-script context
        import nice_labels                           # type: ignore
    return {'n': n, 'label': nice_labels.short(n),
            'kind': 'goods' if n <= GOODS_MAX else 'services'}


def terms_for(nice_class: int, limit: int = MAX_TERMS) -> dict:
    """One class's most-registered terms — what the panel shows with no query."""
    rows = _by_class().get(int(nice_class), [])
    out = _label(int(nice_class))
    out['n_terms'] = len(rows)
    out['terms'] = [_public(r) for r in rows[:limit]]
    out['matched'] = 0
    return out


def search(query: str, kind: str = 'all', limit: int = MAX_CLASSES,
           terms: int = MAX_TERMS) -> dict:
    """Rank the classes whose registered vocabulary matches `query`.

    Matching is on whole tokens, not substrings: "art" must not drag in
    "cartridges" and "smart". A multi-word query prefers terms carrying EVERY
    token ("dog grooming"), and falls back to any-token so a long description
    still returns something useful rather than nothing.

    A class is ranked by the weight of the marks behind its matching terms,
    which is what puts coffee in class 30 ahead of class 21's coffee cups
    without a hand-kept sector map.
    """
    q = [t for t in _tokens(query) if t not in _STOP]
    if not q:
        return {'ok': True, 'query': query or '', 'matched': False, 'classes': []}

    want = set(q)
    strict, loose = [], []
    for r in _rows():
        hit = want & r['toks']
        if not hit:
            continue
        (strict if hit == want else loose).append(r)
    hits = strict or loose
    exact = bool(strict)

    if kind == 'goods':
        hits = [r for r in hits if r['n'] <= GOODS_MAX]
    elif kind == 'services':
        hits = [r for r in hits if r['n'] > GOODS_MAX]

    grouped: dict[int, list[dict]] = defaultdict(list)
    for r in hits:
        grouped[r['n']].append(r)

    out = []
    for n, rows in grouped.items():
        rows.sort(key=lambda r: -r['marks'])
        c = _label(n)
        c['matched'] = len(rows)
        c['n_terms'] = len(_by_class().get(n, []))
        c['weight'] = sum(r['marks'] for r in rows)
        c['top_share'] = round(max(r['share'] for r in rows), 5)
        c['terms'] = [_public(r) for r in rows[:terms]]
        out.append(c)
    out.sort(key=lambda c: (-c['weight'], c['n']))
    return {'ok': True, 'query': query, 'matched': True, 'exact': exact,
            'classes': out[:limit], 'total_classes': len(out)}


def all_classes() -> list[dict]:
    """The 45, with how many registered terms each one has. The finder lists
    these when there is no query, so the labels live here and never get
    copied into the page's JavaScript to drift."""
    idx = _by_class()
    return [dict(_label(n), n_terms=len(idx.get(n, [])), matched=0)
            for n in range(1, 46)]


def handle(params: dict) -> dict:
    """GET /class-search — `q` to search, `class` for one class's terms,
    `all=1` for the 45 class labels."""
    def _one(key, default=''):
        v = params.get(key, default)
        return (v[0] if isinstance(v, list) and v else
                ('' if isinstance(v, list) else v)) or default

    if str(_one('all')).strip() in ('1', 'true', 'yes'):
        return {'ok': True, 'classes': all_classes()}
    one, q, kind = _one('class'), _one('q'), _one('kind', 'all')
    if str(one).strip().isdigit():
        n = int(str(one).strip())
        if not 1 <= n <= 45:
            return {'ok': False, 'error': 'class must be 1 to 45', 'status': 400}
        return {'ok': True, 'class': terms_for(n)}
    return search(str(q), kind=str(kind or 'all'))


if __name__ == '__main__':           # quick manual check
    import json
    import sys
    r = search(' '.join(sys.argv[1:]) or 'coffee')
    for c in r['classes'][:6]:
        print(f"class {c['n']:>2} {c['label'][:38]:<38} "
              f"{c['matched']:>3} terms  top: "
              + ', '.join(f"{t['term']} ({t['marks']:,} {t['band']})"
                          for t in c['terms'][:2]))
    print(json.dumps({'total_classes': r['total_classes'], 'exact': r.get('exact')}))
