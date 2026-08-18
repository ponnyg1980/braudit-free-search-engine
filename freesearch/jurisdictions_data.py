"""Jurisdiction picker data for Step 4 / Step 5 of the wizard.

One source of truth, served to the browser as JSON, so the front-end renders
the picker without hard-coding country lists and the back-end validates
against the same codes. Reuses the canonical code list in
`deploy-v2-hotfix/jurisdictions.py` — this module only *groups* it for the UI.

Important product point: none of these selections scope the free search. The
free search is UK IPO only, always. Jurisdictions are captured for profiling
(two questions — trade now vs plan to trade) and to drive the results
disclaimer. See models.Jurisdictions.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Pull the canonical (label, code) list without importing the whole audit app.
_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    import jurisdictions as _j  # type: ignore
    _ALL: list[tuple[str, str]] = list(_j.JURISDICTIONS)
except Exception:  # pragma: no cover - fallback keeps the picker alive
    _ALL = [('United Kingdom (GB)', 'GB'), ('EUIPO — European Union (EU)', 'EU'),
            ('United States (US)', 'US')]

# EU member states, so the picker can show "EU (includes …)" and expand one
# EU tick into the constituent codes for profiling. EUIPO is a single office,
# but clients think in countries — show them what EU covers.
EU_MEMBERS: list[str] = [
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR',
    'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK',
    'SI', 'ES', 'SE',
]

# The "Popular" rail Jonathan specified, in order. Each is a picker chip.
# `covers` is display-only (what the office spans); `code` is the office code
# stored for profiling.
POPULAR: list[dict] = [
    {'code': 'GB', 'label': 'United Kingdom', 'covers': None},
    {'code': 'EU', 'label': 'European Union (EUIPO)', 'covers': EU_MEMBERS},
    {'code': 'US', 'label': 'United States', 'covers': 'all 50 states'},
    {'code': 'CA', 'label': 'Canada', 'covers': None},
    {'code': 'AU', 'label': 'Australia', 'covers': None},
    {'code': 'NZ', 'label': 'New Zealand', 'covers': None},
    {'code': 'AE', 'label': 'Dubai / UAE', 'covers': None},
    {'code': 'SG', 'label': 'Singapore', 'covers': None},
    {'code': 'SA', 'label': 'Saudi Arabia', 'covers': None},
]

# "Other Regions" — regional registers spanning multiple COUNTRIES.
#
# WIPO / Madrid was removed on 18 Aug 2026. It is a filing ROUTE, not a
# territory: nobody trades in Madrid. Leaving it in a "where do you trade"
# answer corrupted planning_to_trade, which feeds the results disclaimer and
# the Zoho record — a lead reading "plans to trade in WIPO" tells a
# salesperson nothing. It now surfaces as a prompt once several territories
# are picked (see MADRID_NOTE), which is the moment it is actually useful.
#
# Benelux, ARIPO and OAPI stay, because those genuinely ARE groups of
# territories rather than routes.
REGIONS: list[dict] = [
    {'code': 'BX', 'label': 'Benelux (BOIP)', 'covers': ['BE', 'NL', 'LU']},
    {'code': 'ARIPO', 'label': 'ARIPO (English-speaking Africa)',
     'covers': 'multiple African states'},
    {'code': 'OAPI', 'label': 'OAPI (French-speaking Africa)',
     'covers': 'multiple African states'},
]

# Codes that are groups/regions rather than single countries — excluded from
# the plain A-Z country list so it stays clean.
_NON_COUNTRY = {'EU', 'WO', 'BX', 'ARIPO', 'OAPI'}


def _clean_label(label: str) -> str:
    """'United Kingdom (GB)' -> 'United Kingdom'. Strip the trailing code."""
    import re
    return re.sub(r'\s*[—-]?\s*\([A-Z]{2,5}\)\s*$', '', label).strip() \
        or label


def all_countries() -> list[dict]:
    """Full A-Z country list for the searchable 'All Countries' picker."""
    out = []
    for label, code in _ALL:
        if code in _NON_COUNTRY:
            continue
        out.append({'code': code, 'label': _clean_label(label)})
    out.sort(key=lambda d: d['label'].lower())
    return out


# Shown once someone has picked enough separate territories for the Madrid
# route to be worth mentioning. Same idea as the EU note: offer the
# information at the moment it helps, rather than as an option in a list of
# places.
MADRID_THRESHOLD = 3
MADRID_NOTE = (
    'Picking several countries? The Madrid System can cover them in a single '
    'international application instead of one filing per country. We will '
    'tell you if that works out cheaper for the territories you have chosen.'
)


def picker_payload() -> dict:
    """Everything the Step 4 / Step 5 picker needs, in one JSON blob."""
    return {
        'uk_only_shortcut': {'code': 'GB', 'label': 'UK Only'},
        'popular': POPULAR,
        'regions': REGIONS,
        'all_countries': all_countries(),
        'eu_members': EU_MEMBERS,
        'madrid': {'threshold': MADRID_THRESHOLD, 'note': MADRID_NOTE},
    }


# Valid codes for request validation (countries + groups we recognise).
VALID_CODES: set[str] = {code for _, code in _ALL} | _NON_COUNTRY | {'NZ'}


def expand_for_profiling(codes: list[str]) -> list[str]:
    """Expand group codes into constituents for richer Zoho profiling.

    A client who ticks 'EU' is planning 27 countries; storing them expanded
    makes downstream territory analytics honest. The office code itself is
    kept too, so we don't lose 'they chose the EUIPO route'.
    """
    out: list[str] = []
    for c in codes:
        c = (c or '').upper().strip()
        if not c:
            continue
        out.append(c)
        if c == 'EU':
            out.extend(EU_MEMBERS)
        elif c == 'BX':
            out.extend(['BE', 'NL', 'LU'])
    # De-dupe, preserve first-seen order.
    seen: dict[str, None] = {}
    for c in out:
        seen.setdefault(c, None)
    return list(seen.keys())
