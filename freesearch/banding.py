"""Frequency-banding engine — the shared spine of every class route.

The design principle (Jonathan, 15 Jul 2026): AI (and SIC, and competitor
lookup) never *invents* classes or terms. Instead each route produces a SET OF
REAL TRADEMARKS already on the register, and this module bands their classes
and terms by how often they actually appear across that set. Classes/terms are
therefore always grounded in real filings — hallucination is structurally
impossible because nothing here is generated.

    [identify real marks]  ->  band_marks()  ->  basket

Feeders:
  * SIC          -> companies with the SIC -> their marks   (empirical; Query Runs)
  * Competitor   -> an owner's portfolio                    (standard API, now)
  * Description  -> AI-extracted goods -> marks selling them (Query Runs g/s search)
  * Website      -> as description
  * Existing TM  -> the single chosen mark (trivial band)

BANDS (client-facing labels, configurable — Jonathan to finalise wording):
    Advised      "Definitely select these"        (Always)
    Recommended  "You should probably use these"  (Often)
    Worthwhile   "If in doubt, add these"         (Sometimes)
    Optional     "Add if you think it's relevant" (Rarely)

The band is assigned from the *share* of the mark-set that uses the class (or,
within a class, the term). Thresholds are tuned so a single-mark set puts
everything at 'Advised' (it's all we know), while a broad portfolio spreads
across the bands meaningfully.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    from nice_classes import NICE_HEADINGS  # type: ignore
except Exception:  # pragma: no cover
    NICE_HEADINGS = {}

# Client-facing band labels. Swap the strings to change wording everywhere;
# the keys (a/b/c/d, strongest→weakest) are what the code reasons about.
# Chosen 15 Jul 2026 (Jonathan).
from .bands import TIER_LABELS as DEFAULT_LABELS



# Share-of-set thresholds (fraction of marks in the set that use the item).
from .bands import THRESHOLDS as _TH


def _band_key(share: float) -> str:
    from .bands import tier_for
    return tier_for(share)


def _split_terms(spec: str) -> list[str]:
    if not spec:
        return []
    parts = re.split(r'\s*;\s*', spec.strip().rstrip('.'))
    return [p.strip() for p in parts if p.strip()]


def _classes_and_terms(detail: dict) -> dict[int, list[str]]:
    """Extract {nice_class: [terms]} from one Temmy detail record."""
    out: dict[int, list[str]] = {}
    for c in (detail.get('nice_class_trademarks') or []):
        if not isinstance(c, dict):
            continue
        num = c.get('number') or c.get('nice_class')
        if num is None:
            continue
        try:
            num = int(num)
        except (TypeError, ValueError):
            continue
        out.setdefault(num, [])
        out[num].extend(_split_terms(c.get('goods_services_description') or ''))
    # Fall back to the flat class list (no terms) if no nct present.
    if not out:
        for n in (detail.get('classes') or []):
            try:
                out[int(n)] = []
            except (TypeError, ValueError):
                continue
    return out


def band_marks(details: list[dict], *, labels: dict | None = None,
               min_term_share: float = 0.15) -> dict:
    """Band the classes and terms across a set of real trademark details.

    `details` are Temmy `get_trademark` records. Returns:
        {
          'n_marks': 14,
          'labels': {...},
          'classes': [
             {'nice_class': 36, 'band': 'a', 'label': 'Advised',
              'count': 12, 'share': 0.86,
              'terms': [{'text': 'Banking services', 'band': 'a',
                         'label': 'Advised', 'count': 11}, ...]},
             ...
          ]
        }
    Classes are ordered strongest band first, then by prevalence.
    """
    labels = labels or DEFAULT_LABELS
    details = [d for d in details if isinstance(d, dict)]
    n = len(details)
    if n == 0:
        return {'n_marks': 0, 'labels': labels, 'classes': []}

    class_count: dict[int, int] = {}
    # term counts per class: {class: {term_lower: [count, display_text]}}
    term_count: dict[int, dict[str, list]] = {}

    for d in details:
        ct = _classes_and_terms(d)
        for cls, terms in ct.items():
            class_count[cls] = class_count.get(cls, 0) + 1
            tc = term_count.setdefault(cls, {})
            for t in set(terms):                     # count each term once per mark
                key = t.lower()
                if key not in tc:
                    tc[key] = [0, t]
                tc[key][0] += 1

    classes = []
    for cls, cnt in class_count.items():
        share = cnt / n
        bkey = _band_key(share)
        # term banding within the class, over the marks that used the class
        tc = term_count.get(cls, {})
        terms = []
        for key, (tcnt, disp) in tc.items():
            tshare = tcnt / cnt if cnt else 0
            if tshare < min_term_share and n > 1:
                continue                              # drop long-tail noise
            tbkey = _band_key(tshare)
            terms.append({'text': disp, 'band': tbkey,
                          'label': labels[tbkey], 'count': tcnt,
                          'share': round(tshare, 2)})
        terms.sort(key=lambda t: (-t['count'], t['text'].lower()))
        classes.append({
            'nice_class': cls,
            'heading': NICE_HEADINGS.get(cls, ''),
            'band': bkey,
            'label': labels[bkey],
            'count': cnt,
            'share': round(share, 2),
            'terms': terms,
        })

    _rank = {'a': 3, 'b': 2, 'c': 1, 'd': 0}
    classes.sort(key=lambda c: (-_rank[c['band']], -c['count'], c['nice_class']))
    return {'n_marks': n, 'labels': labels, 'classes': classes}


def to_basket(banding: dict, *, keep_bands=('a', 'b')):
    """Build a term_basket from a banding result.

    By default keeps 'Advised' + 'Recommended' classes selected, with their
    'Advised'/'Recommended' terms kept and weaker terms present-but-crossed-out
    so the client can opt them in. That matches the "definitely / probably /
    if in doubt / optional" intent.
    """
    from .term_basket import TermBasket, Term, ClassEntry
    basket = TermBasket(source_type='banding')
    for c in banding.get('classes', []):
        if c['band'] not in keep_bands:
            continue
        entry = ClassEntry(nice_class=c['nice_class'], heading=c['heading'],
                           source=f"banded ({c['label']}, {c['count']}/{banding['n_marks']})")
        for t in c['terms']:
            entry.terms.append(Term(text=t['text'], kept=t['band'] in keep_bands))
        basket.entries.append(entry)
    basket.entries.sort(key=lambda e: e.nice_class)
    return basket


# ---------------------------------------------------------------------------
# Competitor route (standard API, live now): an owner's portfolio -> banded
# ---------------------------------------------------------------------------

def band_owner_portfolio(client, ipo_identifier, *, max_marks: int = 40,
                         labels: dict | None = None) -> dict:
    """Fetch an owner's trademarks, pull each mark's detail, and band them.

    This is Option 4's core once a competitor company is identified (by AI from
    a website, or typed directly). It runs entirely on the standard API.
    """
    from concurrent.futures import ThreadPoolExecutor
    from . import lookup as lk

    owner = lk.get_owner(client, ipo_identifier)
    if not owner:
        return {'n_marks': 0, 'labels': labels or DEFAULT_LABELS, 'classes': [],
                'owner': None}
    numbers = [t['number'] for t in owner['trademarks'] if t.get('number')][:max_marks]

    def _fetch(num):
        try:
            return client.get_trademark(num)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        details = [d for d in pool.map(_fetch, numbers) if d]

    result = band_marks(details, labels=labels)
    result['owner'] = owner['owner']
    return result


def band_from_numbers(client, numbers, *, labels: dict | None = None) -> dict:
    """Band an arbitrary hand-picked set of trademarks (by application number).

    Lets the team paste a few competitor marks and get suggested classes/terms
    banded across just those. Standard API, parallel detail fetch.
    """
    from concurrent.futures import ThreadPoolExecutor
    if isinstance(numbers, str):
        numbers = re.split(r'[,\s]+', numbers)
    numbers = [str(n).strip() for n in (numbers or []) if str(n).strip()][:40]

    def _fetch(num):
        try:
            return client.get_trademark(num)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        details = [d for d in pool.map(_fetch, numbers) if d]
    return band_marks(details, labels=labels)
