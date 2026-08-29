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

TIERED SELECTION (Jonathan, 22 Aug 2026 — the StreetVision review)
------------------------------------------------------------------
The first cut selected on context alone, and a single generic context term
("software") swept in the whole class vocabulary — gaming, music and payment
software on a fleet-camera intelligence product. Jonathan: "better to include
and give option to remove than not include at all... group by Definitely
Needed, Possibly Needed, Unlikely Needed."

So every class row now carries `term_tiers`:

  definite  — terms the visitor's route explicitly produced (exact matches),
              terms whose distinctive words are all accounted for in the
              business description, and the class's top broad anchors
              ("computer software", "software as a service [saas]").
  possible  — terms sharing SOME distinctive vocabulary with the description
              (capped, most-registered first).
  unlikely  — the class's other most-used registered terms, kept small, so
              nothing a fee-earner might expect is silently absent.

`terms` remains the flat list (definite + possible + unlikely) so every
downstream consumer — journey push, Zoho rows, CSV email — is unchanged.
"Distinctive" means tokens that are NOT near-universal in the class's own
vocabulary (in class 9 "software" and "computer" say nothing; "camera",
"image", "detection" say everything).
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

MIN_TERMS = 8      # floor for a class with no description/context signal
ANCHOR_N = 3       # top most-registered terms kept as broad anchors
POSSIBLE_CAP = 40  # most-registered first once the cap bites
UNLIKELY_CAP = 15  # a shortlist, not the whole class vocabulary
SCAN_N = 400       # how deep into the vocabulary scoring looks

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    from nice_classes import NICE_HEADINGS  # type: ignore
except Exception:  # pragma: no cover
    NICE_HEADINGS = {}

_CSV = Path(__file__).resolve().parent / 'data' / 'class_terms.csv'

_STOP = {'and', 'or', 'of', 'the', 'for', 'in', 'to', 'with', 'a', 'an',
         'on', 'by', 'via', 'as', 'at', 'its', 'is', 'are', 'be', 'it',
         'relating', 'connected', 'relation', 'use', 'used', 'using',
         'featuring', 'nature', 'form', 'means', 'namely', 'other',
         'services', 'service'}


def _toks(text: str) -> set[str]:
    """Significant tokens of a phrase, crudely stemmed.

    The 5-character prefix stem is deliberate low tech that makes word
    families agree: image/images/imagery -> "image", analyse/analysing/
    analysis -> "analy", detect/detection -> "detec". The occasional
    collision only ever moves a term up a tier, and every tier is reviewed.
    """
    out = set()
    for w in ''.join(ch if ch.isalnum() else ' '
                     for ch in text.lower()).split():
        if w in _STOP or len(w) < 2:
            continue
        w = w.rstrip('s')
        out.add(w[:5] if len(w) > 5 else w)
    return out


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


@lru_cache(maxsize=64)
def _token_stats(class_no: int) -> tuple[frozenset[str], frozenset[str]]:
    """(generic, common) tokens of a class's own vocabulary.

    generic — near-universal in the class (>=25% of the top 200 terms);
              says nothing about relevance there ("software" in class 9).
    common  — frequent (>=8%) without being universal ("computer" in
              class 9); a term hanging off ONE of these alone is not a
              real description match.
    """
    top = _vocab().get(class_no, [])[:200]
    if not top:
        return frozenset(), frozenset()
    cnt: Counter[str] = Counter()
    for v in top:
        cnt.update(_toks(v))
    g_floor = max(3, int(0.25 * len(top)))
    c_floor = max(3, int(0.08 * len(top)))
    generic = frozenset(t for t, c in cnt.items() if c >= g_floor)
    common = frozenset(t for t, c in cnt.items()
                       if c_floor <= c < g_floor)
    return generic, common


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


def _description_tokens(class_source) -> set[str]:
    """Tokens of the business description and the Q&A answers behind it."""
    if not isinstance(class_source, dict):
        return set()
    texts: list[str] = []
    v = class_source.get('value')
    if isinstance(v, str):
        texts.append(v)
    ans = class_source.get('answers')
    if isinstance(ans, dict):
        for k in ('pitch', 'goods', 'services', 'unique'):
            x = ans.get(k)
            if isinstance(x, str):
                texts.append(x)
            elif isinstance(x, (list, tuple)):
                texts.extend(str(i) for i in x)
    return _toks(' '.join(texts))


def _select_tiers(class_no: int, context: list[str],
                  desc: set[str]) -> dict[str, list[str]]:
    vocab = _vocab().get(class_no, [])
    if not vocab:
        return {'definite': [], 'possible': [], 'unlikely': []}
    ctx = [c.strip().lower() for c in context if c and c.strip()]
    generic, common = _token_stats(class_no)
    # Context terms feed the match bag ONLY through their distinctive words:
    # "computer software" as context must not make "computer games" look
    # possible in class 9 — that was the StreetVision failure mode.
    bag = set(desc)
    for c in ctx:
        bag |= _toks(c) - generic
    exact = set(ctx)

    definite: list[str] = []
    possible: list[str] = []
    unlikely: list[str] = []
    seen: set[str] = set()

    def put(tier: list[str], v: str) -> None:
        tier.append(v)
        seen.add(v.lower())

    # No signal at all (self-picked class, no description).
    #
    # Changed 28 Aug 2026 (Jonathan): we used to pad this out with the class's
    # most-registered terms as "possible". That reads as advice when it is
    # really just a popularity list — and a generic spec makes almost every
    # mark in the class look like a conflict, which is exactly the wrong
    # answer for an audit. So return ONLY the official heading anchors and
    # flag needs_context, which the review screen turns into a one-line
    # "tell us what you sell" prompt. One sentence, and the description-aware
    # path below does the real work.
    if not bag and not ctx:
        for i, v in enumerate(vocab):
            if v.lower() in seen:
                continue
            if i < ANCHOR_N:
                put(definite, v)
            else:
                break
        return {'definite': definite, 'possible': possible,
                'unlikely': unlikely, 'needs_context': True}

    # 1. The route's own terms that ARE registered vocabulary — strongest
    #    possible signal, searched over the FULL vocabulary, never capped.
    lookup = {v.lower(): v for v in vocab}
    for c in ctx:
        hit = lookup.get(c)
        if hit and hit.lower() not in seen:
            put(definite, hit)

    # 2. Score the class's most-registered terms against the description.
    for i, v in enumerate(vocab[:SCAN_N]):
        vl = v.lower()
        if vl in seen:
            continue
        # A vocabulary term quoted inside a context phrase counts as spoken.
        if any(vl in c for c in ctx if len(vl) > 3):
            put(definite, v)
            continue
        vt = _toks(vl)
        core = vt - generic
        if not core:
            # All-generic phrasing ("computer software"). The top few are
            # the broad anchors every software spec carries; the rest only
            # earn a place if the description touches their words at all.
            if i < ANCHOR_N:
                put(definite, v)
            elif vt & bag and len(possible) < POSSIBLE_CAP:
                put(possible, v)
            elif len(unlikely) < UNLIKELY_CAP:
                put(unlikely, v)
            continue
        matched = core & bag
        r = len(matched) / len(core)
        # A lone match on a merely-common word ("computer" in class 9) is
        # coincidence, not relevance — that alone doesn't earn "possible".
        weak = len(matched) == 1 and matched <= common
        if r >= 0.67 and not weak:
            put(definite, v)
        elif matched and not weak and len(possible) < POSSIBLE_CAP:
            put(possible, v)
        elif len(unlikely) < UNLIKELY_CAP:
            put(unlikely, v)

    return {'definite': definite, 'possible': possible, 'unlikely': unlikely}


def build_application_scope(classes, class_source) -> list[dict]:
    """One row per chosen class: official heading + UKIPO-vocabulary terms.

    Shape is what journey/index.ts's zohoScopeBlock forwards to the Deluge
    function: [{n, heading, terms, term_tiers}]. Everything in `terms`
    exists verbatim in data/class_terms.csv — nothing invented, nothing
    copied raw. `terms` = definite + possible + unlikely, in that order, so
    consumers that ignore tiers still get a sensibly ranked list.
    """
    desc = _description_tokens(class_source)
    rows = []
    for raw in classes or []:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= 45:
            continue
        tiers = _select_tiers(n, _context_terms(class_source, n), desc)
        rows.append({
            'n': n,
            'heading': NICE_HEADINGS.get(n, ''),
            'terms': tiers['definite'] + tiers['possible'] + tiers['unlikely'],
            'term_tiers': tiers,
            # True when the class was picked with no description to work
            # from: the UI asks for one line about the business rather than
            # presenting a popularity list as if it were advice.
            'needs_context': bool(tiers.get('needs_context')),
        })
    return rows
