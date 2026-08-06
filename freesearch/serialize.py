"""Serialize a FreeSearchResult into the exact JSON the results page renders.

The anonymous results page (Jonathan's spec):

    Total Results Flagged   : N
    Number Displayed        : N
    High / Medium / Low     : counts
    Top 5 (registered/active): risk · type · mark text · status   (NO score)
    Disclaimers
    Download full report CTA (this is the lead gate)
    Offer Brand Audit

The gate rule: anonymous sees COUNTS + the top-5 shortlist rows. Ownership /
Companies House detail and the full conflict list unlock on download, which is
where we capture name/email/phone + consent. The score is never shown — only
the band — because the number invites argument and the band sells the audit.
"""
from __future__ import annotations

from .models import CompanyInfo, FreeSearchResult, MarkRecord

TOP_N = 5

INFO_DISCLAIMER = (
    'These results are provided for information purposes only and do not '
    'constitute trademark advice.'
)
LOGO_TAGLINE_DISCLAIMER = (
    'Logo and tagline searches are carried out as part of a Brand Audit, not '
    'this free UK word search.'
)


def _band_counts(records: list[MarkRecord]) -> dict:
    counts = {'High Risk': 0, 'Medium Risk': 0, 'Low Risk': 0,
              'Negligible': 0}
    for r in records:
        counts[r.risk] = counts.get(r.risk, 0) + 1
    return counts


def _is_active(r: MarkRecord) -> bool:
    # r.status is canonical (Registered | Pending | Ended).
    return r.status.strip().lower() in ('registered', 'pending')


def _status_label(r: MarkRecord) -> str:
    # Show the real office status to the client ('Application Published',
    # 'Registered', 'Dead'…); fall back to the canonical if none captured.
    return r.status_display or r.status


def _company_public(c: CompanyInfo | None) -> dict | None:
    """Minimal, non-gated company facts safe for the online shortlist.

    Owner NAME and full address stay behind the gate; status/sector are shown
    because 'held by a dissolved company' is exactly the hook that makes the
    free result feel valuable.
    """
    if c is None:
        return None
    return {
        'status': c.status,
        'is_dissolved': c.is_dissolved,
        'sic_codes': c.sic_codes,
        'locality': c.locality,   # town only — enough to feel specific
    }


def _shortlist_row(r: MarkRecord, *, gated: bool) -> dict:
    row = {
        'risk': r.risk,                 # band only, never the score
        'type': r.mark_type,
        'mark': r.mark_text,
        'status': _status_label(r),     # raw office status for display
    }
    if gated:
        # Post-lead-capture: unlock ownership + provenance.
        row.update({
            'application_number': r.application_number,
            'owner_name': r.owner_name,
            'filing_date': r.filing_date,
            'classes': r.classes,
            'goods_services': r.goods_services,
            'source_url': r.source_url,
            'company': _company_public(r.company),
            'company_name': r.company.name if r.company else '',
        })
    else:
        # Anonymous: a taste of the company signal, no PII / owner name.
        row['company'] = _company_public(r.company)
    return row


def serialize_result(result: FreeSearchResult, *, gated: bool = False) -> dict:
    """Build the results-page payload.

    `gated=False` -> anonymous view (counts + top-5 shortlist, no detail).
    `gated=True`  -> unlocked view after lead capture (full list + ownership).
    """
    conflicts = result.conflicts
    active = [r for r in conflicts if _is_active(r)]

    # Top 5 registered/active for the on-screen shortlist.
    top = active[:TOP_N]

    if gated:
        rows = [_shortlist_row(r, gated=True) for r in conflicts]
    else:
        rows = [_shortlist_row(r, gated=False) for r in top]

    disclaimers = [INFO_DISCLAIMER]
    req = result.request
    if req.image_bytes or req.tagline:
        disclaimers.append(LOGO_TAGLINE_DISCLAIMER)
    disclaimers.append(result.disclaimer())  # UK-only + jurisdiction gap

    return {
        'tenant_id': req.tenant_id,
        'searched_office': result.searched_office,
        'query': {
            'word_marks': list(req.word_marks),
            'tagline': req.tagline,
            'has_logo': bool(req.image_bytes),
            'classes': list(req.classes),
        },
        'summary': {
            'total_flagged': result.total_conflicts,
            'displayed': len(rows),
            'high': _band_counts(conflicts)['High Risk'],
            'medium': _band_counts(conflicts)['Medium Risk'],
            'low': _band_counts(conflicts)['Low Risk'],
            'overall_risk': result.overall_risk,
            'active_count': len(active),
            'truncated': result.truncated,
        },
        'top_results': rows if not gated else rows[:TOP_N],
        'all_results': rows if gated else None,   # full list only when unlocked
        'gated': gated,
        'disclaimers': disclaimers,
        'notes': result.notes,
        'cta': {
            'download_report': {
                'label': 'Download your full report',
                'requires': ['first_name', 'last_name', 'email', 'phone',
                             'consent'],
                'unlocks': 'Full conflict list with ownership, company status '
                           'and goods/services detail.',
            },
            'brand_audit': {
                'label': 'Request a Brand Audit',
                'blurb': 'Covers logos, taglines, social media, marketplaces, '
                         'domains, company registers and international '
                         'registers — plus prior-use risk.',
            },
        },
    }
