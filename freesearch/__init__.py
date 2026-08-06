"""Braudit Free Search — headless, tenant-aware, deterministic.

Public surface:

    from freesearch import FreeSearchRequest, Jurisdictions, run_free_search
    from temmy import TemmyClient

    req = FreeSearchRequest(
        word_marks=('MOMENTUS',),
        classes=(36,),
        jurisdictions=Jurisdictions(trading_now=('GB',),
                                    planning_to_trade=('US', 'DE')),
        tenant_id='tmh',
    )
    result = run_free_search(TemmyClient(api_key=...), req)

    result.overall_risk      # 'Medium Risk'
    result.total_conflicts   # 14
    result.preview()         # first 3 — what an anonymous caller sees
    result.disclaimer()      # honest UK-only gap statement
"""
from .models import (
    FreeSearchRequest,
    FreeSearchResult,
    Jurisdictions,
    MarkRecord,
    WordSearch,
)
from .service import run_free_search

__all__ = [
    'FreeSearchRequest',
    'FreeSearchResult',
    'Jurisdictions',
    'MarkRecord',
    'WordSearch',
    'run_free_search',
]
