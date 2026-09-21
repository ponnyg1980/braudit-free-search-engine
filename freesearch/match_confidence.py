"""How much should a human trust this resolver match? A SIGNAL, never a gate.

Jonathan, 21 Sep 2026, approving the design: the question staff actually have
in front of a resolver-created Lead is "is this really them, before I ring
it?" — and until now nothing on the record answered it.

WHY THIS IS NOT A GATE. The obvious fix for the corroboration hole was to feed
the visitor's Nice classes into `contact_resolver._corroborate`. That was
written, tested and reverted: `_corroborate` compares tokens against the
CANDIDATE'S TITLE, and a company name almost never contains its own class
label, so "Ruby Rebel Ltd" was REJECTED for a Ruby Rebel search. Gating on
classes swaps "accepts everything" for "rejects almost everything". Scoring
has no such failure mode — a weak score costs nothing, it just tells the truth.

TWO INDEPENDENT SIGNALS, deliberately kept apart so the evidence string can
say which one is weak:

  NAME   how much of the searched brand appears in the matched company name.
         "Ruby Rebel" -> "Ruby Rebel Ltd"            = full   (2 of 2)
         "Black Flock" -> "Flock Development Ltd"    = partial(1 of 2)

  TRADE  do the visitor's classes agree with the classes businesses on that
         SIC code actually register in (`data/sic_terms.csv`)? Three states,
         and "unknown" is the common one: the file maps only 61 SIC codes, so
         most candidates have no mapping at all. Unknown must never be
         reported as disagreement.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data' / 'sic_terms.csv'

# Words that carry no identifying weight in a company name. "Ruby Rebel" vs
# "Ruby Rebel Ltd" must score as a FULL match, not 2-of-3.
_NOISE = {
    'ltd', 'limited', 'plc', 'llp', 'lp', 'uk', 'the', 'and', 'co', 'company',
    'group', 'holdings', 'services', 'solutions', 'international', 'trading',
    'org', 'inc', 'llc', 'cic', 'cyf', 'cyfyngedig',
}


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r'[a-z0-9]+', (s or '').lower())
            if t not in _NOISE and len(t) > 1]


def _fold(t: str) -> str:
    """Crude singular fold so a possessive or plural is not treated as a
    different word. "McDonald's" tokenises to `mcdonald`, the company is
    `mcdonalds`; without this they score as no overlap at all."""
    return t[:-1] if len(t) > 3 and t.endswith('s') else t


@lru_cache(maxsize=1)
def _sic_to_classes() -> dict[str, frozenset[int]]:
    """SIC code -> the Nice classes businesses on that code actually register."""
    out: dict[str, set[int]] = {}
    try:
        with open(DATA, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                sic = (row.get('sic') or '').strip()
                try:
                    n = int(row.get('nice_class') or 0)
                except (TypeError, ValueError):
                    continue
                if sic and 1 <= n <= 45:
                    out.setdefault(sic, set()).add(n)
    except OSError:
        return {}
    return {k: frozenset(v) for k, v in out.items()}


def assess(search_term: str, matched_name: str | None,
           sic_codes=None, classes=None, step: str | None = None) -> tuple[str, str]:
    """(confidence, evidence). Confidence is one of the five picklist values;
    evidence is the one-line human explanation that goes beside it."""
    want = _tokens(search_term)
    cand = _tokens(matched_name or '')
    got = {_fold(t) for t in cand}
    hit = [t for t in want if _fold(t) in got]

    if not matched_name:
        return 'Unverified', 'No company name returned to compare.'
    if not want:
        return 'Unverified', 'Nothing in the search term to compare.'

    # ALWAYS name the company we matched, whatever the verdict. The point of
    # this field is that a human can judge before ringing, and they cannot do
    # that from a band alone — "Ruby Rebel" scoring a full token match against
    # "Rebel ruby and Wild rose" is exactly the case where the band looks fine
    # and the name tells you to look twice.
    extra = len(cand) - len(hit)
    if len(hit) == len(want):
        name_state = 'full'
        name_note = f'Name: all {len(want)} word(s) matched "{matched_name}"'
        name_note += (f', which carries {extra} further word(s).'
                      if extra >= 2 else '.')
    elif hit:
        name_state, name_note = 'partial', (
            f'Name: {len(hit)} of {len(want)} words matched '
            f'({", ".join(hit)}) against "{matched_name}".')
    else:
        name_state, name_note = 'none', f'Name: no overlap with "{matched_name}".'

    # --- trade ---------------------------------------------------------------
    cls = {int(c) for c in (classes or []) if str(c).strip().isdigit()}
    sics = [str(s).strip() for s in (sic_codes or []) if str(s).strip()]
    mapping = _sic_to_classes()
    known = {s: mapping[s] for s in sics if s in mapping}

    if not cls or not sics:
        trade_state, trade_note = 'unknown', 'Trade: no classes or no SIC to compare.'
    elif not known:
        trade_state, trade_note = 'unknown', (
            f'Trade: SIC {", ".join(sics)} not in our SIC-to-class data, '
            'so the trade could not be checked.')
    else:
        overlap = sorted(cls & set().union(*known.values()))
        if overlap:
            trade_state = 'agree'
            trade_note = (f'Trade: class {", ".join(map(str, overlap))} '
                          f'matches SIC {", ".join(known)}.')
        else:
            trade_state = 'conflict'
            trade_note = (f'Trade: classes {sorted(cls)} do not match SIC '
                          f'{", ".join(known)}, which registers in '
                          f'{sorted(set().union(*known.values()))[:6]}.')

    # --- WHAT KIND OF THING DID WE MATCH? ------------------------------------
    #
    # `serper_search` returns ORGANIC WEB RESULTS — page titles, not businesses.
    # Proved on the 21 Sep re-scoring run: "Ruby Rebel" matched
    #   "Ruby Rebel: Virgin Atlantic Kicks off 40th Birthday..."
    # which is a press release about an AIRCRAFT named Ruby Rebel. Both words
    # matched, so on name and trade alone it scored "Good" — the exact false
    # confidence this field exists to prevent. All five bad scores in that run
    # came from this step; every `serper_places` match was at least a real
    # business listing.
    #
    # A page title proves only that a web page mentions the brand, so it can
    # never read Strong or Good however well the name matches.
    if step == 'serper_search':
        note = (' Source: matched a web page title, not a business listing, '
                'so this may not be a company at all.')
        band = ('Check - matched a web page, not a business'
                if name_state != 'none' else 'Unverified')
        return band, (f'{name_note} {trade_note}{note}')[:255]

    if name_state == 'none':
        conf = 'Unverified'
    elif name_state == 'partial':
        conf = 'Weak - partial name match'
    elif trade_state == 'agree':
        conf = 'Strong - name and trade agree'
    elif trade_state == 'conflict':
        conf = 'Check - name matches, trade does not'
    else:
        conf = 'Good - name matches, trade unknown'

    # Zoho's Leads.Match_Evidence is text(255) — the module's textarea quota is
    # already full, so this cannot be a long field. Truncate on a word boundary
    # rather than let Zoho reject or silently clip the write.
    evidence = f'{name_note} {trade_note}'
    if len(evidence) > 255:
        evidence = evidence[:252].rsplit(' ', 1)[0] + '...'
    return conf, evidence
