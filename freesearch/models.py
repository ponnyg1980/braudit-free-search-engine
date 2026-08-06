"""Canonical record + result types for the Free Search service.

Design note (09 Jul 2026)
------------------------
Braudit's scoring logic in `filters.py` reads *positional spreadsheet row
tuples* — `r[2]` is status, `r[3]` is mark type, `r[5]` is mark text, `r[7]`
is the class string. That coupling is why the audit engine cannot currently
be pointed at the live Temmy API: the scores are correct, but the inputs are
xlsx-shaped.

`MarkRecord` is the neutral shape that breaks that coupling. Two adapters
(`from_xlsx_row`, `from_temmy_item`) feed it, one scoring module consumes it.
The paid audit and the free search then share one scoring vocabulary, which
is the point: a client who sees "Medium Risk" on the free report must see the
same band on the paid audit for the same mark, or the upsell is undermined.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SearchType = Literal['exact match', 'starts with', 'contains', 'similar to']
RiskBand = Literal['High Risk', 'Medium Risk', 'Low Risk', 'Negligible']


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WordSearch:
    """One row of the operator's / client's search intent."""
    type: SearchType
    phrase: str

    def as_dict(self) -> dict:
        """filters.py consumes plain dicts; keep the bridge cheap."""
        return {'type': self.type, 'phrase': self.phrase}


@dataclass(frozen=True)
class Jurisdictions:
    """Query 1 answer: two questions, not one.

    `trading_now` and `planning_to_trade` are different sales signals and are
    stored separately in Zoho. Neither scopes the search — the free tier is
    UK IPO only — they scope the *disclaimer* and the lead score.
    """
    trading_now: tuple[str, ...] = ()
    planning_to_trade: tuple[str, ...] = ()

    @property
    def has_non_uk(self) -> bool:
        codes = set(self.trading_now) | set(self.planning_to_trade)
        return bool(codes - {'GB', 'UK'})


@dataclass(frozen=True)
class FreeSearchRequest:
    """One free search. Up to three marks (word, tagline, image) per item 4."""
    word_marks: tuple[str, ...] = ()
    tagline: str | None = None
    image_bytes: bytes | None = None
    classes: tuple[int, ...] = ()
    jurisdictions: Jurisdictions = field(default_factory=Jurisdictions)
    tenant_id: str = 'tmh'          # white-label: introducer / portal / TMH
    search_types: tuple[SearchType, ...] = ('exact match', 'starts with',
                                            'similar to')

    def word_searches(self) -> list[dict]:
        """Expand every mark across every requested search type."""
        out: list[dict] = []
        phrases = [p for p in (*self.word_marks, self.tagline) if p]
        for phrase in phrases:
            for st in self.search_types:
                out.append({'type': st, 'phrase': phrase})
        return out


# ---------------------------------------------------------------------------
# The neutral record
# ---------------------------------------------------------------------------

@dataclass
class CompanyInfo:
    """Companies House detail for a cited mark's owner.

    TemmyDB links trademark applicants to Companies House records, so a free
    UK search can show *who* holds a conflicting mark, whether they're still
    trading, and what sector they're in — value a bare register lookup can't.
    All fields optional: a private-individual applicant has no company, and a
    freshly-scraped applicant may not be CH-matched yet.
    """
    name: str = ''
    number: str = ''                # Companies House number
    status: str = ''                # Active | Dissolved | Liquidation | ...
    business_type: str = ''         # 'ltd', 'plc', ...
    incorporation_date: str = ''
    sic_codes: list[str] = field(default_factory=list)
    locality: str = ''              # town/city — enough for a shortlist row
    postcode: str = ''
    matched: bool = False           # CH match confidence flag

    @property
    def is_active(self) -> bool:
        return self.status.lower() in ('active', 'active - proposal to strike off')

    @property
    def is_dissolved(self) -> bool:
        s = self.status.lower()
        return 'dissolv' in s or 'liquidat' in s or 'strike' in s


@dataclass
class MarkRecord:
    """A single cited trademark, source-agnostic.

    Field names mirror what the report renderer already expects so the free
    report and the audit report can share templates later.
    """
    mark_text: str = ''
    mark_type: str = ''             # Word | Figurative | Combined | Stylized
    status: str = ''                # CANONICAL: Registered | Pending | Ended
    status_display: str = ''        # raw office status shown to client
                                    # (e.g. 'Application Published', 'Dead')
    classes: list[int] = field(default_factory=list)
    application_number: str = ''
    owner_name: str = ''
    filing_date: str = ''
    goods_services: list[str] = field(default_factory=list)
    image_url: str = ''
    source_url: str = ''
    source: str = ''                # 'temmy' | 'xlsx'
    temmy_id: str | int | None = None

    # Companies House owner detail, when Temmy has linked the applicant.
    # A record can name several applicants; `company` is the first CH-matched
    # one (the one worth showing in a shortlist row). `companies` keeps all.
    company: 'CompanyInfo | None' = None
    companies: list['CompanyInfo'] = field(default_factory=list)

    # ---- scoring outputs, populated by scoring.py -------------------------
    word_score: int = 0
    image_score: int = 0
    risk: str = ''

    @property
    def class_string(self) -> str:
        """The comma-joined form the legacy `touches_classes` helper reads."""
        return ', '.join(str(c) for c in self.classes)

    @property
    def is_figurative(self) -> bool:
        mt = self.mark_type.lower()
        return 'figurative' in mt or 'combined' in mt or 'stylis' in mt \
            or 'styliz' in mt


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class FreeSearchResult:
    """What the endpoint returns. `redacted` drives the email gate.

    The anonymous caller gets `overall_risk`, `total_conflicts` and the first
    `PREVIEW_N` conflicts. The full list unlocks on lead capture — the report
    is the carrot, not the search.
    """
    PREVIEW_N = 3

    request: FreeSearchRequest
    conflicts: list[MarkRecord] = field(default_factory=list)
    overall_risk: str = 'Negligible'
    total_conflicts: int = 0
    searched_office: str = 'UK IPO'
    truncated: bool = False         # True when Temmy paging was cut short
    notes: list[str] = field(default_factory=list)

    def preview(self) -> list[MarkRecord]:
        return self.conflicts[:self.PREVIEW_N]

    def disclaimer(self) -> str:
        """Query 2: the honest gap statement, driven by what they told us."""
        base = ('This free search covers the UK Intellectual Property Office '
                'register only. It does not check social media, online '
                'marketplaces, search engines, domain names, or company '
                'registers — and prior unregistered use can defeat an '
                'application even where the register is clear.')
        j = self.request.jurisdictions
        if not j.has_non_uk:
            return base
        bits = []
        if j.trading_now:
            bits.append('you currently trade in '
                        + ', '.join(j.trading_now))
        if j.planning_to_trade:
            bits.append('you plan to trade in '
                        + ', '.join(j.planning_to_trade))
        prefix = ('You told us ' + ' and '.join(bits) + '. ') if bits else ''
        return (prefix + base + ' A Brand Audit covers the full brand '
                'landscape across the territories that matter to you.')
