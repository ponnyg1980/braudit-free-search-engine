"""SIC code -> Nice class engine (class-selection route 3).

Deterministic, free, no AI — safe to run for anonymous users. A client who
knows their Companies House SIC code(s) gets a sensible set of Nice classes.

TWO IMPLEMENTATIONS, ONE INTERFACE
----------------------------------
1. **Concordance (this file, live now):** a hand-built map from SIC 2007
   *divisions* (the 2-digit prefix) to the Nice classes that industry
   typically files in, with a confidence band. Principled and defensible, but
   it's a heuristic — it encodes "a chemicals manufacturer files in class 1",
   not "these specific companies actually did."

2. **Empirical frequency (the upgrade, needs Query Runs):** for the given SIC
   code, look at every Temmy applicant whose company carries that SIC and count
   which Nice classes their trademarks actually use, then band by frequency
   (Always / Often / Sometimes / Rarely — the Toolkit "frequency banding"
   idea). This is strictly better and is a drop-in: `map_sic_codes` keeps its
   signature and returns the same shape, just sourced from real filings. It is
   deferred only because `/api/v2/query-runs` is currently unavailable.

Bands: 'primary' (core classes for the activity), 'common' (frequently also
filed), 'sometimes' (plausible, lower confidence).
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

P, C, S = 'primary', 'common', 'sometimes'

# SIC 2007 division (2-digit) -> [(nice_class, band), ...].
# Retail/wholesale (class 35) is added as 'common' to most product divisions
# because manufacturers routinely also file for the selling of their goods.
_DIVISION: dict[str, list[tuple[int, str]]] = {
    # A — Agriculture, forestry & fishing
    '01': [(31, P), (29, C), (5, S)],
    '02': [(31, P), (19, C)],
    '03': [(29, P), (31, C)],
    # B — Mining & quarrying
    '05': [(4, P)], '06': [(4, P)], '07': [(6, P)],
    '08': [(19, P)], '09': [(37, P), (4, C)],
    # C — Manufacturing
    '10': [(29, P), (30, P), (31, C), (35, C)],
    '11': [(32, P), (33, P), (35, C)],
    '12': [(34, P), (35, C)],
    '13': [(24, P), (23, C), (25, C)],
    '14': [(25, P), (35, C)],
    '15': [(18, P), (25, C)],
    '16': [(19, P), (20, C)],
    '17': [(16, P)],
    '18': [(16, P), (40, P)],
    '19': [(4, P)],
    '20': [(1, P), (2, C), (3, C), (5, S)],
    '21': [(5, P)],
    '22': [(17, P), (35, C)],
    '23': [(19, P), (21, C)],
    '24': [(6, P)],
    '25': [(6, P), (8, C)],
    '26': [(9, P), (35, C)],
    '27': [(9, P), (11, C)],
    '28': [(7, P), (35, C)],
    '29': [(12, P), (35, C)],
    '30': [(12, P)],
    '31': [(20, P), (35, C)],
    '32': [(28, S), (14, S), (15, S), (10, S), (21, S)],  # broad catch-all
    '33': [(37, P)],
    # D/E — Utilities, water, waste
    '35': [(39, P), (4, C)],
    '36': [(39, P)],
    '37': [(40, P)],
    '38': [(40, P), (39, C)],
    '39': [(40, P), (37, C)],
    # F — Construction
    '41': [(37, P)], '42': [(37, P)], '43': [(37, P)],
    # G — Wholesale & retail
    '45': [(35, P), (37, C), (12, S)],
    '46': [(35, P)], '47': [(35, P)],
    # H — Transport & storage
    '49': [(39, P)], '50': [(39, P)], '51': [(39, P)],
    '52': [(39, P)], '53': [(39, P)],
    # I — Accommodation & food service
    '55': [(43, P)], '56': [(43, P)],
    # J — Information & communication
    '58': [(16, P), (41, C), (9, C), (42, S)],
    '59': [(41, P), (9, C)],
    '60': [(38, P), (41, C)],
    '61': [(38, P)],
    '62': [(42, P), (9, C)],
    '63': [(42, P), (35, C), (38, S)],
    # K — Financial & insurance
    '64': [(36, P)], '65': [(36, P)], '66': [(36, P)],
    # L — Real estate
    '68': [(36, P)],
    # M — Professional, scientific & technical
    '69': [(45, P), (35, C)],
    '70': [(35, P)],
    '71': [(42, P)],
    '72': [(42, P)],
    '73': [(35, P)],
    '74': [(42, P), (41, C), (45, S)],
    '75': [(44, P)],
    # N — Administrative & support
    '77': [(35, C), (39, S)],
    '78': [(35, P)],
    '79': [(39, P), (43, C)],
    '80': [(45, P)],
    '81': [(37, P), (44, C)],
    '82': [(35, P)],
    # O — Public administration
    '84': [(45, S), (35, S)],
    # P — Education
    '85': [(41, P)],
    # Q — Health & social work
    '86': [(44, P)], '87': [(44, P), (43, C)], '88': [(45, P), (44, C)],
    # R — Arts, entertainment & recreation
    '90': [(41, P)], '91': [(41, P)], '92': [(41, P)], '93': [(41, P)],
    # S — Other service activities
    '94': [(45, P), (35, C)],
    '95': [(37, P)],
    '96': [(44, P), (45, C), (37, S)],
    # T/U — Households / extraterritorial: not trademark-relevant
}

_BAND_RANK = {P: 3, C: 2, S: 1}

# --- Inconclusive SICs (point 8) --------------------------------------------
# Some SIC codes describe no goods or services on their own — they're the
# "n.e.c." / "other" / non-trading dustbin codes companies pick when nothing
# fits, or default into at incorporation. A management consultant advises on
# *something*, and that something is the business; the SIC doesn't say what.
#
# For these, the SIC route is the wrong tool and must SAY SO rather than emit
# meaningless bands. Note this fires even when we have filing data for the code
# (70229 has ~34k filings) — those bands are a mush of every kind of business
# that hid under the code, so they're noise, not signal. Inconclusive is
# checked BEFORE the empirical lookup and suppresses it.
#
# The honest move is to route the client to a tool that reads what they
# actually do: their competitor's trade mark (best), their website, or a
# business description.
INCONCLUSIVE_SIC: dict[str, str] = {
    '70229': 'Management consultancy (other than financial management) — this '
             'covers advice on almost anything, so it doesn’t tell us what you '
             'protect.',
    '82990': 'Other business support service activities not elsewhere '
             'classified — a catch-all that doesn’t identify your goods or '
             'services.',
    '74909': 'Other professional, scientific & technical activities not '
             'elsewhere classified — too general to map to classes.',
    '74990': 'Non-trading / general activity code — nothing specific to protect.',
    '82190': 'Photocopying, document preparation & other office support — too '
             'general to identify your goods or services.',
    '63990': 'Other information service activities not elsewhere classified — a '
             'catch-all with no specific goods or services.',
    '96090': 'Other service activities not elsewhere classified — a catch-all '
             'that doesn’t say what the business does.',
    '46900': 'Non-specialised wholesale trade — we can see you sell wholesale, '
             'but not what, which is what decides the classes.',
    '47190': 'Other retail sale in non-specialised stores — retail is clear, '
             'the goods aren’t.',
    '47990': 'Other retail sale not in stores, stalls or markets — retail is '
             'clear, the goods aren’t.',
    '99999': 'Dormant company — no trading activity recorded.',
    '98000': 'Residents property management / household activity — not a '
             'trading classification.',
    '98100': 'Undifferentiated goods-producing activities of households.',
    '98200': 'Undifferentiated service-producing activities of households.',
}

# The tools that DO read what a business actually does, best first. Returned as
# the route when a client's only SICs are inconclusive.
INCONCLUSIVE_ROUTES = [
    {'tool': 'competitor_mark',
     'label': 'Use a competitor’s trade mark',
     'note': 'The most accurate route — we read the classes and terms a '
             'business like yours has actually registered.'},
    {'tool': 'website',
     'label': 'Give us your website',
     'note': 'We read what you offer and match it to real filings.'},
    {'tool': 'business_description',
     'label': 'Describe your business',
     'note': 'A sentence or two on what you sell is enough to get started.'},
]


def _inconclusive_reason(raw) -> str | None:
    """Reason string if this code is a non-specific catch-all, else None."""
    digits = re.sub(r'\D', '', str(raw or ''))
    if not digits:
        return None
    # exact 5-digit, then coarser prefixes, so 70229 / 7022 / 702 all catch
    for n in (len(digits), 5, 4, 3):
        if digits[:n] in INCONCLUSIVE_SIC:
            return INCONCLUSIVE_SIC[digits[:n]]
    return None


def normalise_sic(code) -> str | None:
    """'62012' / '62.01' / '1' -> the 2-digit division '62' / '01'."""
    digits = re.sub(r'\D', '', str(code or ''))
    if not digits:
        return None
    div = digits[:2].zfill(2) if len(digits) == 1 else digits[:2]
    return div


# Unified band vocabulary. `tier` (a/b/c/d) drives sorting + CSS; `band` is the
# display string. Empirical rows carry a real `frequency` (Always/Sometimes);
# concordance rows do not.
# Concordance rows are a heuristic, so they say so — they must not masquerade
# as "All use this" when no filings were counted.
_CONCORDANCE_TIER = {'primary': ('a', 'Likely (estimate)'),
                     'common': ('b', 'Possible (estimate)'),
                     'sometimes': ('c', 'Less likely (estimate)')}
_TIER_RANK = {'a': 3, 'b': 2, 'c': 1, 'd': 0}
from .bands import TIER_LABELS as _EMP_LABEL, tier_for as _tier_for


def map_sic_codes(codes) -> dict:
    """Map one or more SIC codes to Nice classes.

    Uses the EMPIRICAL seed (real UKIPO filing frequencies per SIC — see
    `sic_seed.py`) when the SIC has been seeded, and falls back to the
    hand-built division concordance otherwise. Per-SIC `method` is recorded so
    the caller knows which it got.

    Each class row: {nice_class, heading, tier(a..d), band(display),
    frequency(Always/Sometimes|None), share|None, from_sic[], source}.
    De-duplicated across the supplied SICs, keeping the strongest tier.
    """
    from .sic_seed import load_seed
    seed = load_seed()

    if isinstance(codes, str):
        codes = re.split(r'[,\s]+', codes)
    codes = [c for c in (codes or []) if str(c).strip()]

    acc: dict[int, dict] = {}
    unmatched: list[str] = []
    methods: set[str] = set()

    from .nice_labels import short as _short

    def _merge(nice_class, tier, band, from_sic, source, frequency=None,
               share=None, terms=None, n_marks=None):
        cur = acc.get(nice_class)
        if cur is None:
            acc[nice_class] = {
                'nice_class': nice_class,
                'class_label': _short(nice_class),      # short, for the UI
                'heading': NICE_HEADINGS.get(nice_class, ''),  # official, full
                'tier': tier, 'band': band, 'frequency': frequency,
                'share': share, 'n_marks': n_marks,
                'from_sic': [from_sic], 'source': source,
                'terms': list(terms or []),
            }
        else:
            if from_sic not in cur['from_sic']:
                cur['from_sic'].append(from_sic)
            if terms and not cur.get('terms'):
                cur['terms'] = list(terms)
            if _TIER_RANK[tier] > _TIER_RANK[cur['tier']]:
                cur.update(tier=tier, band=band, frequency=frequency,
                           share=share, n_marks=n_marks, source=source)

    # Pass 0 — set aside the non-specific catch-all codes. They never drive
    # class recommendations, even if we hold filing data for them.
    inconclusive = []
    specific = []
    for raw in codes:
        reason = _inconclusive_reason(raw)
        if reason:
            inconclusive.append({'sic': str(raw), 'reason': reason})
        else:
            specific.append(raw)

    # Pass 1 — real filings. A SIC with a seeded record wins outright.
    no_data = []
    for raw in specific:
        digits = re.sub(r'\D', '', str(raw))
        # Grain-flexible: try the exact code, then coarser prefixes (4/3/2),
        # so a 5-digit company SIC still hits a seed built at any grain.
        rec = None
        for n in (len(digits), 4, 3, 2):
            cand = seed.get(digits[:n])
            if cand and cand.get('classes'):
                rec = cand
                break
        if not (rec and rec.get('classes')):
            no_data.append(raw)
            continue
        methods.add('empirical')
        for c in rec['classes']:
            # The tier is DERIVED from the share, here, at read time — never
            # read back from the seed. The share is the measurement; the band is
            # an opinion about it. Storing the opinion means a threshold change
            # silently doesn't apply until everything is re-seeded, and records
            # seeded under older wording keep serving it.
            share = c.get('share')
            tier = (_tier_for(share) if share is not None
                    else c.get('band', 'd'))
            _merge(int(c['nice_class']), tier,
                   _EMP_LABEL.get(tier, _EMP_LABEL['d']), str(raw), 'empirical',
                   frequency=c.get('frequency'), share=share,
                   terms=c.get('terms'), n_marks=c.get('n_marks'))

    # Pass 2 — concordance, ONLY where nothing empirical was found at all.
    # A company often carries several SICs; if one of them has real filings we
    # must not dilute that with hand-built guesses from its quieter siblings.
    if not acc:
        for raw in no_data:  # only the SPECIFIC codes reach here
            div = normalise_sic(raw)
            entries = _DIVISION.get(div) if div else None
            if not entries:
                unmatched.append(str(raw))
                continue
            methods.add('concordance')
            for nice_class, band in entries:
                tier, disp = _CONCORDANCE_TIER[band]
                _merge(nice_class, tier, disp, str(raw), 'concordance')

    # Strongest band first, then most-used first within the band (a band is a
    # bucket, so without the share tiebreak the order looks arbitrary).
    classes = sorted(acc.values(),
                     key=lambda d: (-_TIER_RANK[d['tier']],
                                    -(d.get('share') or 0), d['nice_class']))
    method = ('empirical' if methods == {'empirical'}
              else 'mixed' if methods == {'empirical', 'concordance'}
              else 'concordance')

    # Every usable code was a catch-all -> the SIC route can't answer honestly.
    # Hand back the reasons and point at the tools that read what they do.
    if not classes and inconclusive:
        return {
            'input_sics': [str(c) for c in codes],
            'inconclusive': True,
            'inconclusive_sics': inconclusive,
            'message': ('Your SIC code doesn’t describe your goods or services '
                        'specifically enough to suggest classes. That’s common '
                        'and it’s not a problem — it just means we should look '
                        'at what you actually do.'),
            'routes': INCONCLUSIVE_ROUTES,
            'classes': [],
            'method': 'inconclusive',
        }

    return {
        'input_sics': [str(c) for c in codes],
        'unmatched': unmatched,
        # Codes we set aside as too general, so the caller can say "we used your
        # other SIC and skipped 70229 because…". Honest, not silent.
        'skipped_inconclusive': inconclusive,
        'classes': classes,
        'method': method,
    }


def to_basket(mapping: dict, *, primary_only: bool = False):
    """Turn a `map_sic_codes` result into a term_basket.

    Terms are seeded from the Nice class heading (the standard specification for
    that class) so the basket is immediately usable; the client refines them, or
    swaps in sharper terms via the competitor-trademark route. Optionally keep
    only 'primary' classes.
    """
    from .term_basket import TermBasket, ClassEntry, Term
    basket = TermBasket(source_type='sic',
                        source_ref=', '.join(mapping.get('input_sics', [])),
                        source_label='SIC codes')
    keep = ('a',) if primary_only else ('a', 'b')   # Essential (+Recommended)
    for c in mapping.get('classes', []):
        if c.get('tier') not in keep:
            continue
        src = f"sic: {','.join(c.get('from_sic', []))}"
        emp_terms = c.get('terms') or []
        if emp_terms:
            # Real, frequency-banded terms from filings. Keep Always/Recommended
            # terms selected; weaker ones present-but-off for opt-in.
            entry = ClassEntry(nice_class=int(c['nice_class']),
                               heading=c.get('heading', ''), source=src)
            for t in emp_terms:
                entry.terms.append(Term(text=t['text'],
                                        kept=t.get('band') in ('a', 'b')))
            basket.entries.append(entry)
        else:
            basket.add_class(c['nice_class'], c.get('heading', ''), source=src)
    basket.entries.sort(key=lambda e: e.nice_class)
    return basket
