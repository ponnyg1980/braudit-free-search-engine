"""Where each audit input lives in Zoho TODAY — one place, so a move is one edit.

The engine's inputs have moved before (keywords from five Deal fields towards
individual search records; Vienna codes from a text box to a picklist on the
Image subform, and next into their own module) and each move so far has been
absorbed by editing every reader. This module is the adapter layer: nothing
else in `audit_engine` may read `Search_Word_*`, `Image_Mark_Information`,
`Vienna_Codes` or `Trademark_Jurisdictions` directly.

Each reader states the precedence it applies. "Latest write wins" is never
the rule — the rule is written down here, per input.

    keywords(deal)        -> [(match_type, phrase), ...]  word 1 is the mark
    vienna_codes(deal)    -> ["26.4", "29.1", ...]         tidy, de-duplicated
    logo(deal)            -> {"state": attached|awaited|none, "files": [...]}
    jurisdictions(deal)   -> ["GB", "EM", ...]             raw Zoho values;
                                                            sources.normalise maps them

Measured against Deal 1964745000119447114 (Hailaflo, 13 Sep 2026): one
Image_Mark_Information row, Image_JPEG attached, Vienna_Codes empty — the
state the "logo attached, not Vienna-coded" warning exists for.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Keywords (the word criteria)
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. A dedicated search-record source (the "Searches" module Jonathan has
#      described). NOT PRESENT in the production CRM on 13 Sep 2026 — the
#      module list carries no such module and nothing in the estate writes
#      one. When it exists, implement `_keywords_from_search_records` and
#      it takes precedence; the Deal fields become the fallback for legacy
#      Deals only.
#   2. Deals.Search_Word_1..5 + Search_Operator_1..5 — written by the client
#      form and the staff form (tmh_audit_checkout, stage=scope) and edited
#      by staff in the Deal. This is the live source of record today.
#   3. Deals.TM_Text, then the mark parsed from Deal_Name — legacy Deals
#      created before the search fields were populated.
#
# A newer client/staff response DOES override an older one for the same slot
# (the form re-writes Search_Word_1 on every scope save; staff edits in the
# Deal are the final word because they happen after the form). Two sources
# never merge: the first non-empty source in the order above is used whole.

OPERATOR_TO_MATCH = {
    "Exact Match": "Exact", "Similar To": "Similar To", "Similar Match": "Similar To",
    "Starts With": "Starts With", "Start With": "Starts With", "Contains": "Contains",
    "Sounds Like": "Similar To", "Related Words": "Similar To",
}
KEYWORD_SLOTS = 5


def _keywords_from_search_records(deal: dict) -> list[tuple[str, str]]:
    """Placeholder for the search-record module. Returns [] until it exists.

    When the module lands: read the related records for this Deal, ordered by
    their sequence, and return [(match_type, phrase)]. Keep the Deal-field
    reader below as the legacy fallback — do not delete it.
    """
    return []


def _keywords_from_deal_fields(deal: dict) -> list[tuple[str, str]]:
    out = []
    for i in range(1, KEYWORD_SLOTS + 1):
        phrase = (deal.get(f"Search_Word_{i}") or "").strip()
        if not phrase:
            continue
        op = deal.get(f"Search_Operator_{i}") or ""
        out.append((OPERATOR_TO_MATCH.get(op, "Similar To"), phrase))
    return out


def _keywords_legacy(deal: dict) -> list[tuple[str, str]]:
    mark = (deal.get("TM_Text") or "").strip()
    if not mark:
        m = re.search(r"Audit\s*-\s*(.+?)\s*\((word|image|logo)", deal.get("Deal_Name") or "", re.I)
        if m:
            mark = m.group(1).strip()
    return [("Similar To", mark)] if mark else []


def keywords(deal: dict) -> tuple[list[tuple[str, str]], str]:
    """([(match_type, phrase)], source_label). Empty list = nothing to audit."""
    for reader, label in ((_keywords_from_search_records, "search_records"),
                          (_keywords_from_deal_fields, "deal_fields"),
                          (_keywords_legacy, "legacy")):
        words = reader(deal)
        if words:
            return words, label
    return [], "none"


# ---------------------------------------------------------------------------
# Vienna codes (the image criteria)
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. Deals.Image_Mark_Information (subform) — per row: `Vienna_Codes`
#      (multi-select, category.division such as "26.4") and the free-text
#      `Post_S_Img_Class_Div_Sub_Div` staff type when the picklist lacks a
#      section ("26.4.1, 29.1.4"). Both are read; the union is used, because
#      the picklist is coarser than the text and neither is wrong.
#   2. A future Vienna module (planned; not built). When it exists, put its
#      reader FIRST here and leave the subform reader as the fallback for
#      Deals coded before the move. Nothing else changes.
#
# Codes are tidied to WIPO's unpadded form ("26.04.01" -> "26.4.1"); Signa's
# padding happens on the way out in signa.normalise_vienna. Order is kept as
# entered, duplicates dropped.

_VIENNA_RE = re.compile(r"\b(\d{1,2})(?:\.(\d{1,2}))?(?:\.(\d{1,2}))?\b")


def tidy_vienna(code) -> str:
    parts = [p.strip() for p in str(code or "").replace(",", ".").split(".") if p.strip()]
    if not parts or not all(p.isdigit() for p in parts):
        return ""
    return ".".join(str(int(p)) for p in parts)


def _codes_from_text(text: str) -> list[str]:
    out = []
    for m in _VIENNA_RE.finditer(str(text or "")):
        code = ".".join(p for p in m.groups() if p is not None)
        if code:
            out.append(code)
    return out


def _vienna_from_image_subform(deal: dict) -> list[str]:
    out: list[str] = []
    for row in deal.get("Image_Mark_Information") or []:
        raw = list(row.get("Vienna_Codes") or [])
        raw += _codes_from_text(row.get("Post_S_Img_Class_Div_Sub_Div") or "")
        for c in raw:
            t = tidy_vienna(c)
            if t and t not in out:
                out.append(t)
    return out


def vienna_codes(deal: dict) -> list[str]:
    # future: a Vienna-module reader goes first; the subform stays as fallback
    return _vienna_from_image_subform(deal)


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------
#
# The logo file itself lives on the same subform row (`Image_JPEG`, a Zoho
# file-upload field). It is not a public URL, so it cannot feed a reverse
# image search directly; what the engine needs to know is whether a logo is
# HELD, AWAITED (`Logo_Awaited` on the Deal, set by the forms when the client
# says it will follow) or ABSENT — and the file ids, so a later step can
# fetch the bytes. `Logo_Awaited` is only consulted when no file is held.

def logo(deal: dict) -> dict:
    files = []
    for row in deal.get("Image_Mark_Information") or []:
        for f in row.get("Image_JPEG") or []:
            # API v2 returns file_Id / file_Name / original_Size_Byte on a
            # file-upload field (File_Id__s / File_Name__s / Size__s on v2.1)
            files.append({"file_id": f.get("File_Id__s") or f.get("file_Id") or f.get("id"),
                          "name": f.get("File_Name__s") or f.get("file_Name"),
                          "size": f.get("Size__s") or f.get("original_Size_Byte"),
                          "attachment_id": f.get("attachment_Id"),
                          "row_id": row.get("id"), "nickname": row.get("Image_Nickname")})
    if files:
        state = "attached"
    elif str(deal.get("Logo_Awaited") or "").lower() == "true":
        state = "awaited"
    else:
        state = "none"
    return {"state": state, "files": files}


# ---------------------------------------------------------------------------
# Jurisdictions
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. Deals.Trademark_Jurisdictions — stored as ISO/WIPO codes (GB, EM, US,
#      WO ...; the CRM shows country names but the API carries the codes).
#      Written by both forms; staff may edit.
#   2. Deals.Trademark_Search_Platforms — the older "office" picklist; mapped
#      to codes for Deals created before Trademark_Jurisdictions existed.
#   3. ["GB"] — a UK audit is the default, never an empty search.
# `sources.normalise` turns EM/UK/EUTM style aliases into the engine's codes.

OFFICE_TO_ISO = {
    "UK Office": "GB", "EU Office": "EU", "USA Office": "US", "WIPO": "WO",
    "WIPO - All Countries": "WO", "China Office": "CN", "NZ Office": "NZ",
    "New Zealand Office": "NZ", "UAE Office": "AE", "Australia Office": "AU",
    "Saudi Office": "SA", "Canada Office": "CA",
}


def jurisdictions(deal: dict) -> tuple[list[str], str]:
    iso = [j for j in (deal.get("Trademark_Jurisdictions") or []) if j]
    if iso:
        return iso, "Trademark_Jurisdictions"
    out = []
    for p in deal.get("Trademark_Search_Platforms") or []:
        code = OFFICE_TO_ISO.get(p)
        if code and code not in out:
            out.append(code)
    if out:
        return out, "Trademark_Search_Platforms"
    return ["GB"], "default"


# ---------------------------------------------------------------------------
# Channels (which non-register layers to search)
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. Deals.Audit_Search_Layers — written by the client and staff forms
#      (tmh_audit_checkout stage=scope, `search_layers`). Register entries
#      (UKIPO/EUIPO/USPTO/WIPO/Other IPO) are ignored here — jurisdictions
#      decide registers — the rest name the platforms.
#   2. Deals.Other_Search_Platforms + Social_Platforms + Marketplace_Platforms
#      + Search_Engine_Platforms — the older staff-entered picklists, used
#      when Audit_Search_Layers is empty (Deals created in the CRM by hand).
# Measured 13 Sep 2026: form-created Deals carry only Audit_Search_Layers;
# staff-created Deals carry only Other_Search_Platforms.

_LAYER_SOCIAL = {"Facebook": "Facebook", "Instagram": "Instagram", "LinkedIn": "LinkedIn",
                 "Linkedin": "LinkedIn", "TikTok": "TikTok", "YouTube": "YouTube",
                 "X (Twitter)": "X (Twitter)", "X": "X (Twitter)"}
_LAYER_MARKET = {"Amazon": "Amazon", "Etsy": "Etsy", "Temu": "Temu", "TikTok Shop": "TikTok Shop",
                 "eBay": "eBay", "Ebay": "eBay", "Alibaba": "Alibaba"}
_LAYER_COMPANIES = {"Companies House UK", "UK Companies House"}
_LAYER_DOMAINS = {"Domain Registers", "Domain Registrations"}


def channels(deal: dict) -> dict:
    """{socials, marketplaces, include_companies, include_domains, include_serp,
    socials_selected, markets_selected, source}. socials/marketplaces: None = all,
    [] = none — an explicit empty selection is honoured."""
    layers = [str(x) for x in (deal.get("Audit_Search_Layers") or []) if x]
    if layers:
        pool, source = set(layers), "Audit_Search_Layers"
        engines = ["Google Web", "Google Images"]      # the forms always search the web
    else:
        pool = set(deal.get("Other_Search_Platforms") or [])
        pool |= set(deal.get("Social_Platforms") or []) | set(deal.get("Marketplace_Platforms") or [])
        engines = list(deal.get("Search_Engine_Platforms") or
                       (["Google Web", "Google Images"] if "Google" in pool else []))
        source = "Other_Search_Platforms"
    socials = sorted({_LAYER_SOCIAL[p] for p in pool if p in _LAYER_SOCIAL})
    markets = sorted({_LAYER_MARKET[p] for p in pool if p in _LAYER_MARKET})
    return {
        "socials": socials or None, "marketplaces": markets or None,
        "include_companies": bool(pool & _LAYER_COMPANIES),
        "include_domains": bool(pool & _LAYER_DOMAINS),
        "include_serp": bool(engines or socials or markets),
        "socials_selected": bool(socials), "markets_selected": bool(markets),
        "source": source,
    }


# ---------------------------------------------------------------------------
# Exclusions (the client's own assets, declared)
# ---------------------------------------------------------------------------
#
# AUTHORITY: Zoho `Client_Search_Exclusions` is THE record (ratified 11 Sep
# 2026 — "Supabase audit.* is a cache and ipsearch.db is a feeder"). Rows are
# written by both forms (stage=scope `exclusions`), by IP-Search and by staff.
# `Active` is a boolean and means one thing: in force now. Only Active rows
# are read. Rows are matched to the Deal through its Account.
#
# The engine's Supabase `audit.exclusions` / `client_portfolio` hold LEARNED
# rules from triage; they are applied as well, never instead. When triage
# writes a new standing exclusion it must go to Zoho too (open item — today
# triage exclusions stay in Supabase; see the working record).

# Target_Type values as they exist on the module (13 Sep 2026): Domain | Subdomain | URL |
# Profile | Handle | Storefront | Search Result ID | Trademark Reference | Company Number |
# Keyword / Pattern | Platform. Older rows may carry the pre-rename labels; both are read.
_TARGET_TO_KIND = {"Domain": "domain", "Subdomain": "domain", "Website": "domain", "URL": "url",
                   "Trademark": "trademark", "Trademark Reference": "trademark",
                   "Company": "company", "Company Number": "company", "Handle": "handle",
                   "Profile": "handle", "Social": "handle", "Storefront": "handle",
                   "Marketplace": "handle", "Keyword / Pattern": "text", "Search Result ID": "other",
                   "Platform": "other"}


def exclusions_from_rows(rows: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Zoho Client_Search_Exclusions rows -> (engine exclusions, portfolio items).

    engine exclusions: [{"value", "kind", "match_mode", "source_record_id"}]
    portfolio items:   [(kind, value, label)] for domain/handle rows so the
                       SERP/social/marketplace layers auto-exclude them.
    """
    excl, items = [], []
    for r in rows or []:
        if str(r.get("Active")).lower() not in ("true", "1"):
            continue
        val = str(r.get("Exclusion_Value") or r.get("Name") or "").strip()
        if not val:
            continue
        kind = _TARGET_TO_KIND.get(str(r.get("Target_Type") or ""), "other")
        excl.append({"value": val, "kind": kind, "match_mode": r.get("Match_Mode") or "Exact",
                     "source_record_id": str(r.get("id") or ""), "zoho_id": str(r.get("id") or "")})
        if kind in ("domain", "handle"):
            items.append((kind, val, f"Client_Search_Exclusions {r.get('id')}"))
    return excl, items


# ---------------------------------------------------------------------------
# Write-back vocabulary — Zoho picklists take the ACTUAL value, not the label
# ---------------------------------------------------------------------------
# Braudit_Trigger_Status: not_ready | awaiting_payment | awaiting_logo | ready |
#                         sending | sent | partial | failed | complete
# Latest_Braudit_Run_Status: Pending | Running | Completed | Partial | Failed |
#                            Cancelled | Unknown   (label == value)
TRIGGER_STATUS = {"sending": "sending", "complete": "complete", "partial": "partial",
                  "failed": "failed", "ready": "ready", "awaiting_logo": "awaiting_logo",
                  "not_ready": "not_ready"}
RUN_STATUS = {"complete": "Completed", "held": "Partial", "failed": "Failed",
              "running": "Running"}
