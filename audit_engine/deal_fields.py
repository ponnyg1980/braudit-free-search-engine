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

# The CRM picklist -> the TMH match vocabulary used everywhere else
# (criteria.TMH_TO_SIGNA, word_scoring's criterion types, the Order Form).
#
# 22 Sep 2026, three corrections:
#
#  1. "Exact Match" mapped to "Exact", which is not the vocabulary. Measured:
#     TMH_TO_SIGNA.get("Exact") is None, so Signa received no operator at all,
#     and word_scoring tests `stype == "exact match"`, so "Exact" fell through
#     to the generic contains+fuzzy branch. A criterion declared to the client
#     as one thing and scored as another — the same class of defect as
#     finding G.
#
#  2. "Sounds Like" and "Related Words" were flattened to "Similar To" here.
#     That is right for RECALL and wrong for ASSESSMENT: Signa has no phonetic
#     mode, so TMH_TO_SIGNA degrades them at the Signa boundary, but
#     word_scoring HAS a phonetic method and needs the real label to use it.
#     Degrade at the boundary that cannot cope, not at the reader.
#
#  3. "Equals" is the older picklist label for exact, and "Domain" appears in
#     the word slots by mis-selection. Both were absent, so both silently
#     became "Similar To" through the dict default — which is how a wrong
#     operator reaches a client report without anyone seeing it. Unknown
#     operators now return None and the caller records a warning; nothing
#     defaults in silence.
OPERATOR_TO_MATCH = {
    "Exact Match": "Exact Match", "Exact": "Exact Match", "Equals": "Exact Match",
    "Similar To": "Similar To", "Similar Match": "Similar To",
    "Starts With": "Starts With", "Start With": "Starts With",
    "Contains": "Contains",
    "Sounds Like": "Sounds Like", "Related Words": "Related Words",
}

#: Operators that are real picklist values but mean "this is not a word
#: search". Recorded and skipped rather than guessed at.
OPERATOR_NOT_A_WORD_SEARCH = {"Domain"}


def match_for_operator(op: str) -> tuple[str | None, str]:
    """(match_type, note). match_type None means do not search on this slot.

    Never guesses. An operator nobody has mapped is a data question, not a
    default — the whole reason `Search_Operator_1 = "Exact Match"` could run
    as four broad searches is that an unrecognised value fell through a
    `.get(op, "Similar To")` without a sound.
    """
    raw = (op or "").strip()
    if not raw:
        return "Similar To", ""
    if raw in OPERATOR_NOT_A_WORD_SEARCH:
        return None, f"operator {raw!r} is not a word search - slot skipped"
    hit = OPERATOR_TO_MATCH.get(raw)
    if hit:
        return hit, ""
    return "Similar To", (f"operator {raw!r} is not a recognised match type - "
                          f"searched as Similar To; fix the Deal picklist")
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
    notes: list[str] = []
    for i in range(1, KEYWORD_SLOTS + 1):
        phrase = (deal.get(f"Search_Word_{i}") or "").strip()
        if not phrase:
            continue
        op = deal.get(f"Search_Operator_{i}") or ""
        match, note = match_for_operator(op)
        if note:
            notes.append(f"Search_Operator_{i}: {note}")
        if match is None:
            continue
        out.append((match, phrase))
    _keywords_from_deal_fields.notes = notes
    return out


def _keywords_legacy(deal: dict) -> list[tuple[str, str]]:
    mark = (deal.get("TM_Text") or "").strip()
    if not mark:
        m = re.search(r"Audit\s*-\s*(.+?)\s*\((word|image|logo)", deal.get("Deal_Name") or "", re.I)
        if m:
            mark = m.group(1).strip()
    return [("Similar To", mark)] if mark else []


#: Sources where the operator was CHOSEN by a person on the order form. Only
#: these govern the search (decision I). `legacy` is a guess reconstructed
#: from TM_Text or the Deal name, carries no operator anybody selected, and
#: must never suppress the derived criteria.
DECLARED_SOURCES = {"search_records", "deal_fields"}


def keywords(deal: dict) -> tuple[list[tuple[str, str]], str]:
    """([(match_type, phrase)], source_label). Empty list = nothing to audit.

    The source label matters as much as the words: see DECLARED_SOURCES.
    """
    for reader, label in ((_keywords_from_search_records, "search_records"),
                          (_keywords_from_deal_fields, "deal_fields"),
                          (_keywords_legacy, "legacy")):
        words = reader(deal)
        if words:
            return words, label
    return [], "none"


def keyword_notes() -> list[str]:
    """Warnings raised while reading the operators on the last call."""
    return list(getattr(_keywords_from_deal_fields, "notes", []) or [])


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


def vienna_codes(deal: dict, assets: list | None = None) -> list[str]:
    """Vienna codes for the Deal: the ASSET first, the Deal subform as fallback.

    Wired 22 Sep 2026, implementing the note that stood here as "future: a
    Vienna-module reader goes first". Until now the engine only ever read the
    Vienna_Codes column on the Deal's own subform, which is populated on 1 of
    313 lines - so the codes held against Image Assets were invisible to every
    search.

    `assets` is what deal_reader fetched by following Image_Mark_Information.
    Image_Asset. The subform fallback is what keeps older Deals working, and
    is why this can go live without a migration.
    """
    from_assets = vienna_from_assets(assets or [])
    return from_assets or _vienna_from_image_subform(deal)


def vienna_from_assets(assets: list) -> list[str]:
    """Codes held on the Image Assets themselves. Pure - no API calls.

    Runs every code through tidy_vienna() exactly as the subform path does.
    That matters: codes taken from a register arrive PADDED ("26.04",
    "02.01.01") while the in-house form is unpadded ("26.4", "2.1.1") - the
    form WIPO publishes and the Zoho picklist stores. signa.normalise_vienna()
    re-pads at the API boundary. Without tidying here the two sources would
    disagree and "26.04" would dedupe as a different code from "26.4".
    """
    out = []
    for a in assets or []:
        for row in a.get("Vienna_Classification") or []:
            if row.get("Removed_On"):
                continue                      # amended away; kept for history
            code = tidy_vienna(row.get("Vienna_Code") or "")
            if code and code not in out:
                out.append(code)
    return out


def logo_from_assets(assets: list) -> list[dict]:
    """The logo files held on Image Assets, in the shape logo() returns."""
    files = []
    for a in assets or []:
        for f in a.get("Image_File") or []:
            files.append({"file_id": f.get("File_Id__s") or f.get("file_Id") or f.get("id"),
                          "name": f.get("File_Name__s") or f.get("file_Name"),
                          "size": f.get("Size__s") or f.get("original_Size_Byte"),
                          "attachment_id": f.get("attachment_Id"),
                          "asset_id": a.get("id"), "nickname": a.get("Name"),
                          "checksum": a.get("Image_File_Checksum"),
                          "source": "image_asset"})
    return files


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

def logo(deal: dict, assets: list | None = None) -> dict:
    """Logo state for the Deal. Asset files first, the Deal subform as fallback.

    An asset's copy is preferred because it is the one that is shared across
    deals, frozen at filing and carries the checksum. The subform copy is still
    read for Deals whose lines were never pointed at an asset.
    """
    inline = []
    for row in deal.get("Image_Mark_Information") or []:
        for f in row.get("Image_JPEG") or []:
            # API v2 returns file_Id / file_Name / original_Size_Byte on a
            # file-upload field (File_Id__s / File_Name__s / Size__s on v2.1)
            inline.append({"file_id": f.get("File_Id__s") or f.get("file_Id") or f.get("id"),
                           "name": f.get("File_Name__s") or f.get("file_Name"),
                           "size": f.get("Size__s") or f.get("original_Size_Byte"),
                           "attachment_id": f.get("attachment_Id"),
                           "row_id": row.get("id"), "nickname": row.get("Image_Nickname"),
                           "source": "deal_subform"})
    # EITHER/OR, never both. Every one of the 273 linked lines still holds its
    # inline copy as well as pointing at an asset, so appending would count the
    # same logo twice and buy two image searches for one mark.
    files = logo_from_assets(assets or []) or inline
    if files:
        state = "attached"
    elif str(deal.get("Logo_Awaited") or "").lower() == "true":
        state = "awaited"
    else:
        state = "none"
    return {"state": state, "files": files}


# ---------------------------------------------------------------------------
# Domain criteria
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. Deals.Search_Domain_1..5 (UI "Search Domain 1-5") paired with
#      Deals.Domain_Search_Operator_1..5. The naming is inconsistent in the
#      CRM — the value field is Search_Domain, the operator field is
#      Domain_Search_Operator — but they are the matched five-slot pair.
#   2. Deals.Domain_Search_1 / Domain_Search2 — single legacy fields, migrated
#      into the slots on 15 Sep 2026. Read only if the slots are empty, for
#      Deals created after the migration by an old form.
#
# Until 15 Sep 2026 NONE of these were read and domain candidates came from
# the mark alone. That silently dropped every domain a staff member typed that
# a slug could not reach: WYS -> westyorkshiresealants, SFM -> thesourcefm.
#
# SENTINELS. Staff use these fields to say "skip this layer" in words. Read
# naively, "NO SEARCH" becomes an RDAP lookup on nosearch.co.uk which would be
# reported to a client as Available — a clearance statement about a domain
# nobody asked about. They are dropped here, at the boundary.

DOMAIN_SLOTS = 5
_SENTINELS = {"no search", "nosearch", "not required", "notrequired", "not applicable",
              "n/a", "na", "none", "no", "-", "--", "tbc", "n/a - not required"}


def is_sentinel(value) -> bool:
    return str(value or "").strip().lower() in _SENTINELS


def domain_criteria(deal: dict) -> tuple[list[tuple[str, str]], str]:
    """([(match_type, phrase)], source_label) for the domain layer.

    match_type is the TMH vocabulary. On a domain it does not describe how a
    registry matches — RDAP is a lookup, not a search — it describes how many
    candidate names we derive from the phrase. `plan` applies that.
    """
    out: list[tuple[str, str]] = []
    for i in range(1, DOMAIN_SLOTS + 1):
        phrase = (deal.get(f"Search_Domain_{i}") or "").strip()
        if not phrase or is_sentinel(phrase):
            continue
        op = deal.get(f"Domain_Search_Operator_{i}") or ""
        out.append((OPERATOR_TO_MATCH.get(op, "Similar To"), phrase))
    if out:
        return out, "Search_Domain_1..5"

    for field in ("Domain_Search_1", "Domain_Search2"):
        phrase = (deal.get(field) or "").strip()
        if not phrase or is_sentinel(phrase):
            continue
        op = deal.get("Domain_Search_Operator_1") or ""
        pair = (OPERATOR_TO_MATCH.get(op, "Similar To"), phrase)
        if pair not in out:
            out.append(pair)
    return out, ("legacy" if out else "none")


# ---------------------------------------------------------------------------
# Jurisdictions
# ---------------------------------------------------------------------------
#
# AUTHORITY, in order:
#   1. Deals.Trademark_Jurisdictions — a picklist whose DISPLAY values are
#      country names ("United Kingdom", "EU (EUIPO)") and whose ACTUAL values
#      are ISO/WIPO codes (GB, EM, US, WO ...). BOTH arrive here: the API
#      returns the display name for anything picked in the CRM or written by
#      name (the 17 Sep 2026 platform migration wrote 1,221 x "United
#      Kingdom"), while the forms write the code and Zoho hands the code
#      straight back ("GB", "EM"). Until 17 Sep this reader took the list
#      verbatim as codes, so "United Kingdom" matched no office and the deal
#      was searched on WIPO ONLY — Hailaflo missed UK00906993372 Healaflow
#      that way. `_jurisdiction_code` resolves either vocabulary.
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

# display_value -> actual_value for Deals.Trademark_Jurisdictions, snapshotted
# from the field metadata (assets/trademark_jurisdictions.json, 255 options).
# The picklist is a fixed country list, so a snapshot is safe; regenerate it
# if Zoho adds an option (uk_monitor/zoho_find_field.py shows the values).
_JURISDICTION_NAMES: dict[str, str] | None = None


def _jurisdiction_names() -> dict[str, str]:
    global _JURISDICTION_NAMES
    if _JURISDICTION_NAMES is None:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent / "assets" / "trademark_jurisdictions.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8")).get("map") or {}
        except Exception:
            raw = {}
        # Case-insensitive on the name; the code side is upper-cased once.
        _JURISDICTION_NAMES = {str(k).strip().lower(): str(v).strip().upper()
                               for k, v in raw.items() if k and v}
        # The names the engine sees most, in case the snapshot is missing.
        _JURISDICTION_NAMES.setdefault("united kingdom", "GB")
        _JURISDICTION_NAMES.setdefault("eu (euipo)", "EM")
        _JURISDICTION_NAMES.setdefault("united states", "US")
        _JURISDICTION_NAMES.setdefault("wipo international", "WO")
    return _JURISDICTION_NAMES


def _jurisdiction_code(value) -> str:
    """'United Kingdom' -> 'GB'; 'GB' -> 'GB'; 'NONE' / blank -> ''."""
    v = str(value or "").strip()
    if not v:
        return ""
    code = _jurisdiction_names().get(v.lower())
    if code is None:
        code = v.upper()          # already a code, or an alias sources.normalise knows
    return "" if code == "NONE" else code


def jurisdictions(deal: dict) -> tuple[list[str], str]:
    iso = []
    for j in deal.get("Trademark_Jurisdictions") or []:
        code = _jurisdiction_code(j)
        if code and code not in iso:
            iso.append(code)
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


def _is_register_layer(value: str) -> bool:
    from . import offices as off
    return off.is_register_layer(value)


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
        # The register entries (UKIPO, EUIPO, USPTO, WIPO, Other IPO) are NOT
        # channels — they say which platform to fetch a jurisdiction from, and
        # sources.resolve() needs them. Passing them through here rather than
        # dropping them is what makes a ticked EUIPO actually search the EU.
        "register_layers": sorted(p for p in pool if _is_register_layer(p)),
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
                   "Platform": "other",
                   "Owner Name": "owner"}     # audit.match_kind = owner (added to Zoho 16 Sep 2026)

# Zoho Reason <-> audit.exclusion_reason. Zoho is the record; this is the ONE
# place the two vocabularies meet (source-of-truth doc, work item 4).
REASON_TO_ENGINE = {"Client-Owned": "own_asset", "Authorised Partner": "own_asset",
                    "Authorised Reseller": "own_asset", "Legitimate Use": "unrelated_goods",
                    "Unrelated Goods": "unrelated_goods", "Dead or Parked": "dead_or_parked",
                    "Duplicate": "duplicate", "Known False Positive": "staff_judgement",
                    "Previously Reviewed": "staff_judgement", "Ignore": "client_instruction"}
ENGINE_TO_REASON = {"own_asset": "Client-Owned", "unrelated_goods": "Unrelated Goods",
                    "dead_or_parked": "Dead or Parked", "duplicate": "Duplicate",
                    "staff_judgement": "Previously Reviewed", "client_instruction": "Ignore"}
# Zoho Platform <-> audit.channel ("All" / blank = every channel = NULL)
PLATFORM_TO_CHANNEL = {"Trademark Register": "trademark", "Company Register": "company",
                       "Domain": "domain", "Social": "social", "Marketplace": "marketplace",
                       "SERP": "web", "All": None, "": None}
CHANNEL_TO_PLATFORM = {v: k for k, v in PLATFORM_TO_CHANNEL.items() if v}


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
        if kind in PORTFOLIO_KINDS:
            items.append((kind, val, f"Client_Search_Exclusions {r.get('id')}"))
    return excl, items


#: What audit.client_portfolio can actually hold - the portfolio_kind enum.
#: Jonathan, 21 Sep 2026: a client's own trademark was excluded correctly but
#: never appeared in Portfolio, while their website and company number did. The
#: cause was this promotion being limited to domain and handle, so a Trademark
#: Reference exclusion had nowhere to go - the company number only showed
#: because triage had added it separately, and the website because the Account's
#: Website field seeds it. Portfolio is the client's declared estate; if they
#: have told us they own it, it belongs here whatever kind it is.
PORTFOLIO_KINDS = frozenset({"trademark", "company", "domain", "handle", "url"})


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
