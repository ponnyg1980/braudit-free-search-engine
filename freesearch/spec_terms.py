"""Application-grade goods & services selections for the G S Scope capture.

WHY THIS EXISTS (Jonathan, 19 Aug 2026)
---------------------------------------
"The specific terms in G S Scope Classes Terms are not what we would put in
an application — these should reflect Descriptions from the UKIPO, a number
of specific terms selected, based upon the terms we know already exist
within those classes from the UKIPO."

Two consequences, both handled here:

1. Every term written to `Specific_Terms` must come VERBATIM from
   `data/class_terms.csv` — the vocabulary of terms on marks REGISTERED at
   the UKIPO in the last five years (see build_class_terms.py). The class
   tools' own terms are used as *context* to pick relevant vocabulary rows,
   never written directly: a long spec phrase copied from a competitor's
   registration, or a model paraphrase, is dropped unless it is itself a
   known vocabulary term.

2. The class description stored beside the terms is the OFFICIAL Nice/UKIPO
   class heading (`nice_classes.NICE_HEADINGS`), not our short UI label.

Selection order per class: context-matched vocabulary terms first (exact
case-insensitive match, then substring containment either way), then top-up
by real usage share ("Most use this" before "Recommended" before "Some use
this") until MIN_TERMS is reached, capped at MAX_TERMS. A class with no
context (self-selected) simply gets the most-used registered terms — the
same starting point a fee-earner drafts from.
"""
from __future__ import annotations

import csv
import sys
from functools import lru_cache
from pathlib import Path

MIN_TERMS = 8    # top up to at least this many when the vocabulary allows
MAX_TERMS = 15   # a readable draft selection, not the whole class

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    from nice_classes import NICE_HEADINGS  # type: ignore
except Exception:  # pragma: no cover
    NICE_HEADINGS = {}

_CSV = Path(__file__).resolve().parent / 'data' / 'class_terms.csv'


@lru_cache(maxsize=1)
def _vocab() -> dict[int, list[str]]:
    """class -> vocabulary terms, ordered by registered-usage share (desc).

    The CSV is already written in share order per class; keep that order so
    'coffee' beats 'coffee beverages with milk' when topping up.
    """
    out: dict[int, list[str]] = {}
    try:
        with _CSV.open(newline='', encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                try:
                    n = int(row['nice_class'])
                except (KeyError, ValueError):
                    continue
                term = (row.get('term') or '').strip()
                if term:
                    out.setdefault(n, []).append(term)
    except OSError:  # missing corpus file: selections degrade to empty
        return {}
    return out


def _context_terms(class_source, class_no: int) -> list[str]:
    """Pull whatever terms the visitor's route produced for this class."""
    if not isinstance(class_source, dict):
        return []
    found: list[str] = []
    at = class_source.get('agent_terms')
    if isinstance(at, dict):
        v = at.get(str(class_no), at.get(class_no))
        if isinstance(v, (list, tuple)):
            found.extend(str(t) for t in v)
    tb = class_source.get('term_basket')
    entries = tb.get('entries') if isinstance(tb, dict) else None
    for e in entries or []:
        if not isinstance(e, dict) or e.get('nice_class') != class_no:
            continue
        for t in e.get('terms') or []:
            if isinstance(t, str):
                found.append(t)
            elif isinstance(t, dict):
                txt = t.get('text') or t.get('term')
                if txt:
                    found.append(str(txt))
    return found


def _select(class_no: int, context: list[str]) -> list[str]:
    vocab = _vocab().get(class_no, [])
    if not vocab:
        return []
    ctx = [c.strip().lower() for c in context if c and c.strip()]
    picked: list[str] = []
    seen: set[str] = set()

    # 1. Exact matches — the route's term IS a known registered term.
    exact = {v.lower(): v for v in vocab}
    for c in ctx:
        hit = exact.get(c)
        if hit and hit.lower() not in seen:
            picked.append(hit)
            seen.add(hit.lower())

    # 2. Token-subset match — a vocabulary term qualifies when EVERY
    #    significant word in it appears somewhere in the context ("retail
    #    services relating to the sale of food and beverages" selects
    #    "retail services relating to food" but NOT "...relating to
    #    clothing"), plus plain substring both ways so "coffee" still
    #    selects "coffee beans". Words are compared with a trailing-s strip
    #    so beverages/beverage agree.
    if len(picked) < MAX_TERMS:
        stop = {'and', 'or', 'of', 'the', 'for', 'in', 'to', 'with', 'a',
                'an', 'on', 'by', 'relating', 'connected', 'relation'}

        def toks(text: str) -> set[str]:
            return {w.rstrip('s') for w in
                    ''.join(ch if ch.isalnum() else ' ' for ch in text).split()
                    if w not in stop and len(w) > 1}

        bag: set[str] = set()
        for c in ctx:
            bag |= toks(c)
        for v in vocab:
            vl = v.lower()
            if vl in seen:
                continue
            vt = toks(vl)
            subset = vt and vt <= bag
            substr = any((vl in c or c in vl) for c in ctx)
            if subset or substr:
                picked.append(v)
                seen.add(vl)
                if len(picked) >= MAX_TERMS:
                    break

    # 3. Top-up with the most-used registered terms so a thin or
    #    self-selected class still gets a real draft selection.
    if len(picked) < MIN_TERMS:
        for v in vocab:
            if v.lower() not in seen:
                picked.append(v)
                seen.add(v.lower())
                if len(picked) >= MIN_TERMS:
                    break
    return picked[:MAX_TERMS]


def build_application_scope(classes, class_source) -> list[dict]:
    """One row per chosen class: official heading + UKIPO-vocabulary terms.

    Shape is what journey/index.ts's zohoScopeBlock forwards to the Deluge
    function: [{n, heading, terms}]. Everything in `terms` exists verbatim
    in data/class_terms.csv — nothing invented, nothing copied raw.
    """
    rows = []
    for raw in classes or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= 45:
            continue
        rows.append({
            'n': n,
            'heading': NICE_HEADINGS.get(n, ''),
            'terms': _select(n, _context_terms(class_source, n)),
        })
    return rows
