"""The term_basket — the one object every class-selection route emits.

Whatever route a client takes to choose goods & services — pick from a list
(2a), describe their business (2b), give a website (2c), point at a competitor
trademark (2d), or a competitor website (2e) — the output that matters
downstream is the same: **Nice specification terms**, grouped by class. An
application needs terms. An audit needs terms. Class numbers alone are not
enough.

So the basket is built once here and the five routes are just different
populators of it. This module is route 2d's populator (from a chosen
trademark) plus the shared model; 2a/2b/2c/2e attach later against the same
shape.

A term is one semicolon-delimited phrase of a Nice specification — the unit a
client keeps or crosses out. `kept` drives that edit UI.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Nice class headings, reused from the audit tool (single source of truth).
_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
try:
    from nice_classes import NICE_HEADINGS  # type: ignore
except Exception:  # pragma: no cover
    NICE_HEADINGS = {}


def split_terms(specification: str) -> list[str]:
    """Split a Nice specification into individual keep/cross-out terms.

    Specifications are semicolon-delimited phrases:
        'Financial services; insurance services; real estate affairs'
        -> ['Financial services', 'insurance services', 'real estate affairs']
    Empty fragments are dropped; surrounding whitespace trimmed.
    """
    if not specification:
        return []
    parts = re.split(r'\s*;\s*', specification.strip().rstrip('.'))
    return [p.strip() for p in parts if p.strip()]


@dataclass
class Term:
    """One specification phrase the client can keep or cross out."""
    text: str
    kept: bool = True


@dataclass
class ClassEntry:
    """One Nice class in the basket, with its terms.

    `class_label` is the short human name ("Cosmetics & cleaning preparations")
    shown in the UI; `heading` is the official full Nice heading. Both are
    stored so a later Audit or Application shows the client the same words they
    saw when they chose — along with each term's Always/Often band.
    """
    nice_class: int
    heading: str = ''
    class_label: str = ''
    terms: list[Term] = field(default_factory=list)
    source: str = ''            # e.g. 'competitor: UK00003246114'

    @property
    def kept_terms(self) -> list[str]:
        return [t.text for t in self.terms if t.kept]

    @property
    def specification(self) -> str:
        """Re-assemble the kept terms into a single specification string."""
        return '; '.join(self.kept_terms)


@dataclass
class TermBasket:
    """Classes + terms chosen for an application/audit, with provenance."""
    entries: list[ClassEntry] = field(default_factory=list)
    source_type: str = 'manual'     # competitor_trademark | owner_trademark |
                                    # sic | website | business_description | manual
    source_ref: str = ''            # trademark number / owner id / url
    source_label: str = ''          # human label, e.g. the competitor's name

    # -- construction --------------------------------------------------------

    def add_class(self, nice_class: int, specification: str = '',
                  source: str = '') -> ClassEntry:
        """Add (or merge into) a class, splitting its specification to terms."""
        nice_class = int(nice_class)
        entry = next((e for e in self.entries if e.nice_class == nice_class),
                     None)
        if entry is None:
            from .nice_labels import short as _short
            entry = ClassEntry(nice_class=nice_class,
                               heading=NICE_HEADINGS.get(nice_class, ''),
                               class_label=_short(nice_class),
                               source=source)
            self.entries.append(entry)
            self.entries.sort(key=lambda e: e.nice_class)
        existing = {t.text.lower() for t in entry.terms}
        for phrase in split_terms(specification):
            if phrase.lower() not in existing:
                entry.terms.append(Term(text=phrase))
                existing.add(phrase.lower())
        return entry

    # -- selection edits (the keep / cross-out UI) ---------------------------

    def set_term(self, nice_class: int, text: str, kept: bool) -> None:
        for e in self.entries:
            if e.nice_class == int(nice_class):
                for t in e.terms:
                    if t.text == text:
                        t.kept = kept

    def drop_class(self, nice_class: int) -> None:
        self.entries = [e for e in self.entries if e.nice_class != int(nice_class)]

    # -- outputs -------------------------------------------------------------

    @property
    def classes(self) -> list[int]:
        return [e.nice_class for e in self.entries]

    def specification_by_class(self) -> dict[int, str]:
        """{class: 'kept; terms; joined'} — what an application filing needs."""
        return {e.nice_class: e.specification for e in self.entries
                if e.kept_terms}

    def to_dict(self) -> dict:
        return {
            'source_type': self.source_type,
            'source_ref': self.source_ref,
            'source_label': self.source_label,
            'classes': self.classes,
            'entries': [asdict(e) for e in self.entries],
        }


# ---------------------------------------------------------------------------
# Route 2d populator: a chosen trademark -> a basket
# ---------------------------------------------------------------------------

def from_trademark_detail(detail: dict, *, source_type: str = 'competitor_trademark',
                          source_label: str = '') -> TermBasket:
    """Populate a basket from a Temmy `get_trademark` detail record.

    Uses `nice_class_trademarks[].number` for the class and
    `goods_services_description` for the terms (the live field names, confirmed
    against TemmyDB). Falls back to the flat `classes` list (no terms) when a
    record carries classes but no per-class specification.
    """
    app_no = str(detail.get('application_number') or '')
    basket = TermBasket(source_type=source_type, source_ref=app_no,
                        source_label=source_label)

    nct = detail.get('nice_class_trademarks') or []
    seen_classes = set()
    if isinstance(nct, list):
        for c in nct:
            if not isinstance(c, dict):
                continue
            num = c.get('number') or c.get('nice_class') or c.get('class')
            if num is None:
                continue
            try:
                num = int(num)
            except (TypeError, ValueError):
                continue
            spec = (c.get('goods_services_description')
                    or c.get('goods_services_text') or '')
            basket.add_class(num, spec, source=f'competitor: {app_no}')
            seen_classes.add(num)

    # Any classes present on the flat list but missing a specification entry.
    for n in (detail.get('classes') or []):
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if n not in seen_classes:
            basket.add_class(n, '', source=f'competitor: {app_no}')

    return basket
