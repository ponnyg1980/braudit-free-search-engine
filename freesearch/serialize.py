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

from . import viability as vb
from .models import CompanyInfo, FreeSearchResult, MarkRecord

TOP_N = 5

INFO_DISCLAIMER = (
    'These results are provided for information purposes only and do not '
    'constitute trademark advice.'
)
# Reworded 17 Aug 2026. The old text said "Logo and tagline searches are
# carried out as part of a Brand Audit, not this free UK word search" — which
# was untrue of taglines: service.py has always searched the tagline as one of
# its phrases. Only the LOGO is genuinely out of scope here, because we do not
# do image comparison on the free tier. Saying otherwise both undersold the
# free search and misdescribed what we had actually done for the visitor.
LOGO_DISCLAIMER = (
    'Your logo has been recorded but not searched — image comparison is part '
    'of a Brand Audit, not this free UK word search.'
)
TAGLINE_NOTE = (
    'Your tagline was searched as words. A tagline can also be challenged on '
    'how it is used and styled, which is part of a Brand Audit.'
)
# Old name kept so nothing importing it breaks mid-deploy.
LOGO_TAGLINE_DISCLAIMER = LOGO_DISCLAIMER


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


def serialize_result(result: FreeSearchResult, *, gated: bool = False,
                     brand_years: float | None = None,
                     sector_terms=()) -> dict:
    """Build the results-page payload.

    `gated=False` -> anonymous view (counts + top-5 shortlist, no detail).
    `gated=True`  -> unlocked view after lead capture (full list + ownership).

    `brand_years` / `sector_terms` feed the viability opinion and are both
    optional: they come from the guided questions and the chosen class terms,
    and a visitor who skipped that route simply gets the opinion computed from
    the register evidence alone. Nothing is invented to fill the gap — an
    unanswered "how long have you used it?" scores as no evidence of use, not
    as a penalty.
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
    if req.image_bytes:
        disclaimers.append(LOGO_DISCLAIMER)
    if req.tagline:
        disclaimers.append(TAGLINE_NOTE)
    disclaimers.append(result.disclaimer())  # UK-only + jurisdiction gap

    band = _band_counts(conflicts)
    summary = {
        'total_flagged': result.total_conflicts,
        'displayed': len(rows),
        'high': band['High Risk'],
        'medium': band['Medium Risk'],
        'low': band['Low Risk'],
        'overall_risk': result.overall_risk,
        'active_count': len(active),
        'truncated': result.truncated,
    }

    # The on-page opinion. Computed here rather than in the browser so the
    # emailed report, the Zoho record and the screen can never disagree about
    # what we told the visitor — the front end renders this, it does not
    # recalculate it.
    # Pass the ACTIVE marks, not just the counts. D12 turns on whether a live
    # mark is identical to what was searched, and a count of "1 High" cannot
    # tell the difference between a lookalike and the same word.
    opinion = vb.verdict(summary, name=req.word_marks[0] if req.word_marks else '',
                         years=brand_years, sector_terms=sector_terms,
                         marks=[{'mark': r.mark_text, 'status': r.status,
                                 'owner_name': r.owner_name,
                                 'company_name': r.company.name if r.company else ''}
                                for r in active])

    return {
        'tenant_id': req.tenant_id,
        'searched_office': result.searched_office,
        'query': {
            'word_marks': list(req.word_marks),
            'tagline': req.tagline,
            'has_logo': bool(req.image_bytes),
            'classes': list(req.classes),
        },
        'summary': summary,
        'viability': opinion,
        'top_results': rows if not gated else rows[:TOP_N],
        # The FULL flagged list for the emailed/linked report page (Jonathan,
        # 21 Aug: "the reports are supposed to have the full results within
        # them"), WITH ownership (26 Aug). The report is only reachable from
        # the emailed link, i.e. after the address is captured, so it is the
        # unlock — the on-page shortlist still withholds owners and says so.
        # NB the anonymous browser receives this array too (it posts the
        # result into its own session), so treat the gate as a conversion
        # device, not a security control. Everything in it is public UK IPO
        # register data.
        'report_rows': [_shortlist_row(r, gated=True) for r in conflicts],
        'all_results': rows if gated else None,   # full list only when unlocked
        'gated': gated,
        'disclaimers': disclaimers,
        'notes': result.notes,
        # Copy supplied verbatim by Jonathan, 11 Aug (one typo corrected).
        # `lead` decides which of the two gets the primary button; both are
        # always on the page, because telling someone they may not apply is
        # not our call to make, and hiding the audit from a messy result would
        # be the same mistake in the other direction.
        'cta': {
            'lead': opinion['lead'],
            'brand_audit': {
                # TWO OFFERS, ONE PRICE (Jonathan, 31 Aug). The list prices are
                # £149 for the audit and £149 for the consultation, an hour of
                # someone's time each. Both surfaces sell at £99; what differs
                # is the anchor, and that is deliberate:
                #
                #   results screen — audit £149 reduced to £99. Nothing about
                #     £298, because "£298 down to £99" on a page somebody
                #     landed on 30 seconds ago reads as too good to be true.
                #     The consultation is given free later, in the application.
                #
                #   this payload, i.e. the REPORT — they have given their
                #     address, read the findings and already seen £149 → £99,
                #     so the sweetener lands: the free consultation is named,
                #     and the anchor becomes the real package price of £298.
                #
                # If you change one, look at the other. Selling the same thing
                # at two anchors only works while each is true on its own page.
                'heading': 'Claim your international brand audit — plus a '
                           'free consultation',
                'label': 'Book online — just £99',
                'eyebrow': 'Our recommended next step',
                'blurb': "You've already done the hard work. The Brand Audit "
                         'goes beyond this search to cover logos, taglines, '
                         'social media, marketplaces, domains, company '
                         'registers, international and prior-use risk — and it '
                         'comes with up to an hour of free consultation with '
                         'one of our trademark experts, who will talk you '
                         'through the audit report, cover any risks it raises '
                         'and guide you on strategy.',
                'offer': 'Total package normally £298. Book online today for '
                         'just £99 — and if you choose to use us for your '
                         'application we deduct it from your fees.',
            },
            'apply': {
                'label': 'Proceed to application',
                'blurb': 'Ready to go? Start your application and one of our '
                         'team will check the detail with you before anything '
                         'is filed.',
                # Second line so the card holds its own beside the audit card,
                # which carries a price panel. An emphasised card that is
                # visibly emptier than the one next to it reads as the weaker
                # option, which is the opposite of what leading with it means.
                'note': 'We check the classes and wording, file it with the '
                        'UK IPO, and watch it through to registration. If we '
                        'spot a problem before we file, we tell you first.',
            },
            'download_report': {
                'label': 'Email me the full results',
                'requires': ['first_name', 'last_name', 'email', 'phone',
                             'consent'],
                'unlocks': 'Full conflict list with ownership, company status '
                           'and goods/services detail.',
            },
        },
    }
