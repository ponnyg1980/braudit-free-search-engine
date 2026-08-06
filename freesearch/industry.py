"""Industry picker resolver — LinkedIn industry -> off-the-shelf classes+terms.

Pick an industry (the ~430 LinkedIn list people already know) and get the real,
frequency-banded Nice classes and terms for it — no company lookup, no SIC
typing. The industry maps to SIC division(s) (industry_data.py); the empirical
engine (sic_engine + seed) turns those into classes+terms.

Empirical where the division is seeded (see sic_seed --divisions), concordance
otherwise. Same output shape as the SIC/company routes, so the picker and the
class-assistant render it identically.
"""
from __future__ import annotations

from . import industry_data, sic_engine


def industry_classes(name: str) -> dict | None:
    """Resolve an industry name to classes + terms + basket."""
    sics = industry_data.sic_for_industry(name)
    if not sics:
        return None
    mapping = sic_engine.map_sic_codes(sics)
    return {
        'industry': name,
        'sic_divisions': sics,
        **mapping,
        'basket': sic_engine.to_basket(mapping).to_dict(),
    }


def search_industries(query: str, *, limit: int = 20) -> dict:
    """Type-ahead over the industry list."""
    q = (query or '').strip().lower()
    names = industry_data.all_industries()
    if not q:
        hits = names[:limit]
    else:
        starts = [n for n in names if n.lower().startswith(q)]
        contains = [n for n in names if q in n.lower() and n not in starts]
        hits = (starts + contains)[:limit]
    return {'results': hits}
