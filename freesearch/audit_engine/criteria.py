"""Criteria generation — what we will actually search, written down.

Two real defects drive the design of this module.

PRACTIVA (Aug 2026). The Order Form declared `Similar To: Practiva`. The
searcher had in fact run something broader and collected 120 rows. At import
the declared criterion was re-applied as a filter and kept 5 of 120 — the
report said "4 results". Nothing errored.

BLUE PORTAL (Sep 2026). `Similar To: Blue Portal` against Signa returns
almost nothing, because `similar` matches the whole phrase and few marks are
that exact phrase. The real conflicts — the BluePort family — are found only
by searching the one-word form. The surrounding field of "X PORTAL" marks is
found only by a stem search.

Hence two rules this module exists to enforce:

  1. Every criterion generated here IS declared on the Order Form by the
     writer. Searched and declared are the same list, always.
  2. A mark is searched in more than one shape — whole phrase, one-word
     form, distinctive stem — because one shape reliably misses conflicts.

Class filtering: applied ONLY to broad stem criteria, never to close
matches. A near-identical mark in a different class is a real finding
(scoring decision D7: class overlap is evidence, not a gate); a bare stem
without a class filter returns tens of thousands of rows.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import _paths  # noqa: F401

# TMH match vocabulary -> Signa's. Signa has no phonetic mode; Sounds Like and
# Related Words degrade to `similar`, whose strategies include fuzzy and
# synonym passes, so they weaken rather than fail.
TMH_TO_SIGNA = {
    "Exact Match": "exact",
    "Starts With": "starts_with",
    "Contains": "contains",
    "Similar To": "similar",
    "Sounds Like": "similar",
    "Related Words": "similar",
}

# Tokens too common to search alone, and the legal forms that are never part
# of a mark. Both now live in the scoring package (18 Sep 2026) so Watch and
# the audit engine cannot drift apart again — which they had already done on
# the legal forms; see tmh_scoring/ignore_words.py.
from tmh_scoring.ignore_words import (                    # noqa: E402
    WEAK_TOKENS as _WEAK_TOKENS,
    LEGAL_FORMS_AUDIT as _STRUCTURAL_RE,
)

MIN_STEM = 4
MAX_CRITERIA = 5          # the contract's per-order limit


@dataclass
class Criterion:
    phrase: str
    match_type: str               # TMH vocabulary — what gets declared
    class_filtered: bool = False  # apply nice_classes to this search?
    rationale: str = ""           # printed in the Order Form remarks column
    origin: str = "mark"          # mark | oneword | stem | tagline | staff — lets each
                                  # channel decide which shapes make sense for it

    @property
    def signa_match(self) -> str:
        return TMH_TO_SIGNA.get(self.match_type, "similar")

    @property
    def key(self) -> tuple:
        return (self.phrase.upper(), self.match_type)


@dataclass
class CriteriaSet:
    criteria: list[Criterion] = field(default_factory=list)
    classes: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Words this build dropped, and why. Until 18 Sep 2026 the weak-token
    #: filter was silent: staff could see the criteria that were searched but
    #: never the words that were removed before searching, so "why was this
    #: not found" had no answer on the screen. Each entry is
    #: {word, kind, source, where, blocked_stem}.
    ignored_words: list[dict] = field(default_factory=list)

    @property
    def class_csv(self) -> str:
        return ",".join(str(c) for c in sorted(self.classes))

    def declared(self) -> list[tuple[str, str, str]]:
        """(match_type, phrase, rationale) rows for the Order Form."""
        return [(c.match_type, c.phrase, c.rationale) for c in self.criteria]


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", text or "") if t]


def _one_word_form(mark: str) -> str | None:
    """'Blue Portal' -> 'BluePortal'. None when the mark is already one word."""
    toks = _tokens(mark)
    return "".join(toks) if len(toks) > 1 else None


def distinctive_stem(mark: str) -> str | None:
    """The searchable whole token of a multi-word mark.

    'Blue Portal' -> 'Portal'. Returns None for a single-word mark, and that
    is deliberate.

    A prefix stem ('Practiva' -> 'Practi') is tempting — it is the shape that
    finds PRACTICAL and PRACTIKA in a raw substring search. But every match
    operator in tmh_scoring is word-boundary anchored (Contains uses \\b...\\b
    so that 'ACE' cannot match 'PALACE'; Starts With requires a separator
    after the phrase). Measured: Contains 'Practi' against 'PRACTICAL MAGIC'
    scores 0, as does Starts With.

    So a prefix criterion would be declared on the Order Form and then
    ignored by the scorer — searched and declared diverging again, in a new
    place. Similar To already covers that family through its token-fuzzy
    pass (PRACTICAL MAGIC 1, PRACTIKA 2, PRACTIV 2, practive8 1), so the
    prefix adds nothing the scorer will honour.
    """
    toks = [t for t in _tokens(mark) if t.lower() not in _WEAK_TOKENS]
    if len(toks) < 2:
        return None
    best = max(toks, key=len)
    return best if len(best) >= MIN_STEM else None


def ignored_in(text: str, *, where: str) -> list[dict]:
    """The words `text` contains that the engine will not search on.

    Two kinds, and they are not interchangeable (see the Search & Score
    Settings spec). STRUCTURAL forms - ltd, plc, llp - are never part of a
    mark and stripping them is not a judgement. WEAK words - group, services,
    global, direct - are usually noise but are occasionally the distinctive
    part of the mark, which is why only these are worth offering back to
    staff to un-ignore. Returns one entry per distinct word, in the order the
    text uses them."""
    out, seen = [], set()
    for tok in _tokens(text or ""):
        low = tok.lower()
        if low in seen:
            continue
        structural = bool(_STRUCTURAL_RE.fullmatch(tok))
        if not structural and low not in _WEAK_TOKENS:
            continue
        seen.add(low)
        out.append({"word": tok,
                    "kind": "structural" if structural else "weak",
                    "source": "package legal-form list" if structural
                              else "package weak-token list",
                    "where": where})
    return out


def build(mark_text: str, classes=None, applicant: str | None = None,
          tagline: str | None = None, extra=None) -> CriteriaSet:
    """Derive the criteria for one word audit.

    `tagline` is searched as an additional word criterion in the same order —
    a tagline is not a separate search type (decision 27 Aug).
    `extra` accepts staff-supplied criteria as (match_type, phrase) pairs;
    these are honoured verbatim and marked as manual.
    """
    mark = (mark_text or "").strip()
    cs = CriteriaSet(classes=sorted({int(c) for c in (classes or [])
                                     if str(c).strip().isdigit() and 1 <= int(c) <= 45}))
    if not mark:
        raise ValueError("mark_text is required to build criteria")

    # What this build will NOT search on, recorded before anything is added
    # so the panel can answer "why was this not found" from the run itself.
    cs.ignored_words = ignored_in(mark, where="mark")
    if tagline:
        have = {e["word"].lower() for e in cs.ignored_words}
        cs.ignored_words += [e for e in ignored_in(tagline, where="tagline")
                             if e["word"].lower() not in have]

    seen: set[tuple] = set()

    def add(phrase, match_type, class_filtered=False, rationale="", origin="mark"):
        phrase = (phrase or "").strip()
        if not phrase:
            return
        c = Criterion(phrase, match_type, class_filtered, rationale, origin)
        if c.key in seen:
            return
        seen.add(c.key)
        cs.criteria.append(c)

    # 1. The mark itself, both shapes. Exact is cheap and unambiguous;
    #    similar is the workhorse.
    add(mark, "Exact Match", False, "the mark as filed")
    add(mark, "Similar To", False, "close variants of the mark")

    # 2. The one-word form. On Blue Portal this is the criterion that finds
    #    the BluePort family; the spaced form finds nothing.
    one_word = _one_word_form(mark)
    if one_word:
        add(one_word, "Similar To", False, "one-word form; catches run-together variants", "oneword")

    # 3. The distinctive stem, class-filtered. This is the surrounding field:
    #    without it an audit reports only near-identical hits and looks empty.
    stem = distinctive_stem(mark)
    if stem and stem.upper() not in {c.phrase.upper() for c in cs.criteria}:
        add(stem, "Contains", True,
            f"distinctive stem, restricted to client classes {cs.class_csv or 'n/a'}", "stem")

    # 4. A tagline travels here, not as its own search type.
    if tagline and tagline.strip():
        add(tagline.strip(), "Similar To", False, "tagline searched as a word mark", "tagline")

    for match_type, phrase in (extra or []):
        # A single-token staff criterion ("Starts With: Coastal") is a field,
        # not a mark: unfiltered it returned 207 COASTAL* companies and 150
        # register hits on one audit. Restrict it to the client's classes,
        # which also keeps it out of company scoring (see companies.py).
        single = len(phrase.split()) == 1 and bool(classes)
        add(phrase, match_type, single,
            "added by staff" + ("; restricted to client classes" if single else ""), "staff")

    if len(cs.criteria) > MAX_CRITERIA:
        dropped = cs.criteria[MAX_CRITERIA:]
        cs.criteria = cs.criteria[:MAX_CRITERIA]
        cs.notes.append(
            "criteria capped at %d; not run: %s"
            % (MAX_CRITERIA, "; ".join(f"{c.match_type}:{c.phrase}" for c in dropped)))

    if stem is None and len(_tokens(mark)) < 2:
        cs.notes.append(
            "single-word mark: no stem criterion. Similar To carries the variant "
            "family via token-fuzzy matching; a prefix criterion would not be "
            "honoured by the scorer (see distinctive_stem).")
    if not cs.classes:
        cs.notes.append("no client classes supplied — any stem criterion runs "
                        "unfiltered and may return a large result set")
    return cs
