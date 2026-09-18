"""Jurisdiction → source resolution.

A jurisdiction is where the client trades or wants protection. A source is
something we can actually call. They are not the same list and conflating
them is what made the old `Trademark_Search_Platforms` field wrong — six of
its ten "offices" (China, UAE, Saudi, Canada, Australia, New Zealand) were
jurisdictions, not platforms.

The rule, per Jonathan 2026-09-09:

    GB                          → Temmy (our own TemmyDB copy of UKIPO)
    jurisdiction Signa covers   → Signa, that office directly
    everything else             → Signa WO — Madrid designations pick it up

The report states WHERE EACH RESULT CAME FROM, per row, and does not carry
"not covered" statements. WIPO catches the gaps, and a source label on every
row is a truthful account of the search without implying a completeness we
would then have to qualify.
"""
from __future__ import annotations

from dataclasses import dataclass

from .signa import LIVE_JURISDICTIONS, OFFICE_NAMES

TEMMY = "temmy"
SIGNA = "signa"

# Regional systems and legacy aliases that arrive in Trademark_Jurisdictions
# or on older Deals. EM is the OHIM-era designator for the EU trade mark.
_ALIASES = {
    "UK": "GB", "UKIPO": "GB",
    "EM": "EU", "EUTM": "EU", "EUIPO": "EU",
    "WIPO": "WO", "IR": "WO", "MADRID": "WO",
    "USA": "US", "USPTO": "US",
}


@dataclass(frozen=True)
class Source:
    """One register we will actually query."""
    provider: str          # temmy | signa
    jurisdiction: str      # the code passed to the provider
    label: str             # printed in the report's Source column
    requested_for: tuple   # which requested jurisdictions this source serves

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.jurisdiction}"


def normalise(code: str) -> str:
    if not code:
        return ""
    key = str(code).strip().upper()
    return _ALIASES.get(key, key)


def resolve(jurisdictions, prefer_temmy_for_gb: bool = True,
            layers=None) -> list[Source]:
    """Map requested jurisdictions onto the sources that will be queried.

    Two inputs, because they answer two questions (Jonathan, 17 Sep 2026):

      `jurisdictions`  WHICH COUNTRIES the client wants searched
      `layers`         WHICH PLATFORMS to go to — the register entries in
                       Audit_Search_Layers (UKIPO, EUIPO, USPTO, WIPO,
                       Other IPO). Before this was read, a client ticking
                       EUIPO on the order form got no EU search at all.

    Selecting a country adds its direct office automatically; selecting an
    office explicitly adds it even where the country was not listed. The two
    are unioned rather than one overriding the other, because a staff member
    naming a register and a client naming a country are both asking for it.

    WIPO is added to every INTERNATIONAL audit as a backup, not only where a
    country is otherwise unreachable: Signa bills it per search rather than
    per country, so it costs nothing extra to catch designations a national
    register has not yet published. Its duplicates are filtered downstream in
    favour of the local office.

    Returns one Source per distinct register, each remembering which of the
    client's requested jurisdictions it serves, so the report can say why a
    register was searched.
    """
    from . import offices as off

    requested = [normalise(j) for j in (jurisdictions or []) if normalise(j)]
    if not requested:
        requested = ["GB"]          # a UK audit is the sane default, never an empty search

    direct: dict[str, list[str]] = {}
    via_wipo: list[str] = []

    for j in requested:
        if j in LIVE_JURISDICTIONS:
            direct.setdefault(j, []).append(j)
        else:
            via_wipo.append(j)

    # Registers named explicitly on the Deal. "Other IPO" is not an office —
    # it is a request that WIPO cover a register we have no direct feed for.
    wants_wipo = False
    for layer in (layers or []):
        name = str(layer or "").strip().lower()
        if name == off.OTHER_IPO:
            wants_wipo = True
            via_wipo.append("Other IPO")
            continue
        office = off.for_layer(name)
        if not office:
            continue                      # a platform layer, not a register
        if office.code == "WO":
            wants_wipo = True
        else:
            direct.setdefault(office.code, [])
            if office.code not in direct[office.code]:
                direct[office.code].append(office.code)

    # Anything not directly reachable is picked up through Madrid.
    if via_wipo:
        direct.setdefault("WO", []).extend(via_wipo)

    # ...and on an international audit WIPO runs anyway, as the backup.
    international = any(j != "GB" for j in direct if j != "WO")
    if wants_wipo or international:
        direct.setdefault("WO", []).extend(
            j for j in requested if j not in direct.get("WO", []))

    sources: list[Source] = []
    for juris, serves in direct.items():
        if juris == "GB" and prefer_temmy_for_gb:
            sources.append(Source(TEMMY, "GB", "UKIPO (Temmy)", tuple(serves)))
        else:
            office = "EM" if juris == "EU" else juris
            label = f"{OFFICE_NAMES.get(office, office)} (Signa)"
            sources.append(Source(SIGNA, juris, label, tuple(serves)))

    # Deterministic order: UK first, then EU/US, then the rest alphabetically,
    # with WIPO last because it is the catch-all.
    rank = {"GB": 0, "EU": 1, "US": 2}
    sources.sort(key=lambda s: (s.jurisdiction == "WO", rank.get(s.jurisdiction, 5),
                                s.jurisdiction))
    return sources


def unrecognised(jurisdictions) -> list[str]:
    """Requested jurisdictions that are not jurisdiction codes at all.

    A code we have no direct feed for (JP, say) is NOT a fault: WIPO covers it
    through Madrid and that is the documented design above. A value that is
    not a code at all - a display label like "United Kingdom" or "EU (EUIPO)"
    arriving where an ISO code was expected - IS a fault, and until 17 Sep
    2026 the two were indistinguishable here: both fell down the same branch
    and became a WIPO search, silently and billably, while the run went on
    reporting the jurisdiction as "United Kingdom". Nine runs went out that
    way between 16 and 17 Sep before the name->code map in deal_fields closed
    the hole upstream. This is the backstop, and the shape of the value is the
    test - an ISO/WIPO jurisdiction is two letters and nothing else is.
    """
    out = []
    for j in jurisdictions or []:
        code = normalise(j)
        if not code or code in LIVE_JURISDICTIONS:
            continue
        if len(code) == 2 and code.isalpha():
            continue                    # a real code, reached through Madrid
        out.append(str(j))
    return out


def coverage_note(sources: list[Source]) -> str:
    """One line for the report header: which registers were searched."""
    return "Registers searched: " + ", ".join(s.label for s in sources)
