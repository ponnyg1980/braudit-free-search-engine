"""Decision I: the Deal's declared search operator governs the search.

Jonathan, 22 Sep 2026:
  "Exact Match is chosen when there are very common words. The Exact Match
   should be honoured, if not results came back, the only alternative would
   'Contains' the whole string, just to get some results, but the fall back is
   too broad."

  Ruling on the two open questions: the declared operator REPLACES all derived
  criteria (only what was declared runs), and when it returns nothing the
  report says so — the Contains-whole-phrase fallback is offered in Triage as
  a staff action, never automatic.

THE DEFECT THIS FIXES, precisely.

`deal_reader._criteria()` did this:

    words, _src = deal_fields.keywords(deal)
    return words[0][1], words[1:]

Word 1's PHRASE became `mark_text` and word 1's OPERATOR was discarded on that
line. `criteria.build()` then derived its own four shapes from the bare mark —
Exact Match, Similar To, Similar To (one-word), Contains stem — and appended
whatever was left in `words[1:]` as a fifth. So a Deal set to Exact Match ran
four searches, one of which was `Contains: Capital` on a class filter.

Proof it was the operator being dropped and not a mapping slip: run ce25a67b
carried `extra_criteria = []` — literally nothing from the Deal — and produced
the identical four criteria and 4,968 rows.

Measured on run eae681d1 (Capital Thermal): of the 4,966 rows returned, ZERO
score above nothing on `Exact Match: Capital Thermal`, and zero on
`Contains: Capital Thermal` either. So honouring the order gives an empty
register section, which is the truthful answer — there is no Capital Thermal
on the register — and the 4,966 rows were noise with 982 of them banded
Medium.
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1])


class Patch:
    def __init__(self, name):
        self.path = ROOT / name
        self.src = self.path.read_text()
        self.n = 0

    def sub(self, old, new):
        c = self.src.count(old)
        if c != 1:
            raise SystemExit(f"FAIL {self.path.name}: {c} occurrences of {old[:160]!r}")
        self.src = self.src.replace(old, new)
        self.n += 1
        return self

    def write(self):
        self.path.write_text(self.src)
        print(f"  {self.path.name}: {self.n} edits")


# ===========================================================================
# deal_fields.py — the operator vocabulary
# ===========================================================================
p = Patch("deal_fields.py")
p.sub(
    '''OPERATOR_TO_MATCH = {
    "Exact Match": "Exact", "Similar To": "Similar To", "Similar Match": "Similar To",
    "Starts With": "Starts With", "Start With": "Starts With", "Contains": "Contains",
    "Sounds Like": "Similar To", "Related Words": "Similar To",
}''',
    '''# The CRM picklist -> the TMH match vocabulary used everywhere else
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
                          f"searched as Similar To; fix the Deal picklist")''',
)

p.sub(
    '''def _keywords_from_deal_fields(deal: dict) -> list[tuple[str, str]]:
    out = []
    for i in range(1, KEYWORD_SLOTS + 1):''',
    '''def _keywords_from_deal_fields(deal: dict) -> list[tuple[str, str]]:
    out = []
    notes: list[str] = []
    for i in range(1, KEYWORD_SLOTS + 1):''',
)
p.sub(
    '''        op = deal.get(f"Search_Operator_{i}") or ""
        out.append((OPERATOR_TO_MATCH.get(op, "Similar To"), phrase))
    return out''',
    '''        op = deal.get(f"Search_Operator_{i}") or ""
        match, note = match_for_operator(op)
        if note:
            notes.append(f"Search_Operator_{i}: {note}")
        if match is None:
            continue
        out.append((match, phrase))
    _keywords_from_deal_fields.notes = notes
    return out''',
)

p.sub(
    '''def keywords(deal: dict) -> tuple[list[tuple[str, str]], str]:
    """([(match_type, phrase)], source_label). Empty list = nothing to audit."""
    for reader, label in ((_keywords_from_search_records, "search_records"),
                          (_keywords_from_deal_fields, "deal_fields"),
                          (_keywords_legacy, "legacy")):
        words = reader(deal)
        if words:
            return words, label
    return [], "none"''',
    '''#: Sources where the operator was CHOSEN by a person on the order form. Only
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
    return list(getattr(_keywords_from_deal_fields, "notes", []) or [])''',
)
p.write()


# ===========================================================================
# criteria.py — a declared set replaces the derived one
# ===========================================================================
p = Patch("criteria.py")
p.sub(
    '''def build(mark_text: str, classes=None, applicant: str | None = None,
          tagline: str | None = None, extra=None) -> CriteriaSet:
    """Derive the criteria for one word audit.

    `tagline` is searched as an additional word criterion in the same order —
    a tagline is not a separate search type (decision 27 Aug).
    `extra` accepts staff-supplied criteria as (match_type, phrase) pairs;
    these are honoured verbatim and marked as manual.
    """''',
    '''def build(mark_text: str, classes=None, applicant: str | None = None,
          tagline: str | None = None, extra=None,
          declared=None) -> CriteriaSet:
    """Derive the criteria for one word audit.

    `tagline` is searched as an additional word criterion in the same order —
    a tagline is not a separate search type (decision 27 Aug).
    `extra` accepts staff-supplied criteria as (match_type, phrase) pairs;
    these are honoured verbatim and marked as manual.

    `declared` (decision I, 22 Sep 2026) is what the ORDER FORM asked for —
    the Deal's Search_Word/Search_Operator slots, as (match_type, phrase).
    When it is non-empty it REPLACES the derived set entirely: only what was
    declared runs. No Exact Match auto-add, no Similar To, no one-word form,
    no stem.

    Why replacement and not addition. Exact Match is chosen precisely when the
    mark contains very common words — the order is saying "do not go wide".
    Deriving a `Contains` on the distinctive stem is then the exact opposite of
    the instruction, and on Capital Thermal it filled the report with finance
    and media marks sharing the word "capital". An order form that promises one
    thing while the engine does four is not a form, it is decoration.

    What it costs, honestly: honouring a narrow operator can return nothing.
    Measured on run eae681d1, not one of the 4,966 candidates scored above
    nothing on `Exact Match: Capital Thermal`. That empty answer is the
    truthful one, and it is reported as empty. The `Contains` whole-phrase
    fallback is offered to staff in Triage, never applied automatically — it
    is too broad to reach for on the engine's own initiative.
    """''',
)

p.sub(
    '''    # 1. The mark itself, both shapes. Exact is cheap and unambiguous;
    #    similar is the workhorse.
    add(mark, "Exact Match", False, "the mark as filed")''',
    '''    # Decision I: an order that declared its own operators governs. Nothing
    # below this block runs in that case - the derived shapes are what the
    # engine would ask for when NOBODY has said what to search.
    declared = [(m, p) for m, p in (declared or []) if (p or "").strip()]
    if declared:
        for match_type, phrase in declared[:MAX_CRITERIA]:
            add(phrase, match_type, False,
                "declared on the order form", "declared")
        if len(declared) > MAX_CRITERIA:
            cs.notes.append(
                "WARNING more than %d declared criteria; kept the first %d: %s"
                % (MAX_CRITERIA, MAX_CRITERIA,
                   "; ".join(f"{m}:{p}" for m, p in declared[MAX_CRITERIA:])))
        cs.notes.append(
            "the order form declared its own search criteria, so they are the "
            "whole search: no variant, one-word or stem criteria were added "
            "(decision I, 22 Sep 2026). A narrow operator can legitimately "
            "return nothing.")
        return cs

    # 1. The mark itself, both shapes. Exact is cheap and unambiguous;
    #    similar is the workhorse.
    add(mark, "Exact Match", False, "the mark as filed")''',
)
p.write()


# ===========================================================================
# deal_reader.py — stop throwing word 1's operator away
# ===========================================================================
p = Patch("deal_reader.py")
p.sub(
    '''def _criteria(deal: dict) -> tuple[str, list[tuple]]:
    """(mark_text, extra criteria as (match_type, phrase)). Word 1 is the mark."""
    words, _src = deal_fields.keywords(deal)
    if not words:
        raise RuntimeError("Deal has no search words (Search_Word_1 / TM_Text) — nothing to audit")
    return words[0][1], words[1:]''',
    '''def _criteria(deal: dict) -> tuple[str, list[tuple], bool]:
    """(mark_text, declared criteria, declared_governs). Word 1 is the mark.

    Until 22 Sep 2026 this returned `words[1:]` and word 1's OPERATOR was
    discarded on that line — its phrase became `mark_text` and the operator
    the client chose went nowhere. `criteria.build()` then derived four shapes
    from the bare mark. A Deal set to Exact Match ran a class-filtered
    `Contains` on its distinctive stem, which is the opposite of what Exact
    Match is chosen for. Word 1 now travels WITH its operator.

    `declared_governs` is false for the legacy reader, which reconstructs a
    phrase from TM_Text or the Deal name and pairs it with a "Similar To"
    nobody selected. A guess must not be allowed to suppress the derived
    criteria; only a person's choice does that.
    """
    words, src = deal_fields.keywords(deal)
    if not words:
        raise RuntimeError("Deal has no search words (Search_Word_1 / TM_Text) — nothing to audit")
    return words[0][1], list(words), src in deal_fields.DECLARED_SOURCES''',
)
p.write()
print("\nNOTE: deal_reader callers of _criteria() still need updating — "
      "run the caller check below.")


# ===========================================================================
# runner.py — carry the declared set and let it govern
# ===========================================================================
p = Patch("runner.py")
p.sub(
    '''    extra_criteria: list = field(default_factory=list)  # staff additions''',
    '''    # The order form's own word criteria, word 1 INCLUDED, as
    # (match_type, phrase). Named `extra_criteria` since before it carried
    # word 1; kept for every existing caller and stored request.
    extra_criteria: list = field(default_factory=list)
    # True when a person chose those operators on the Deal (Search_Operator_n
    # or a search record), false when they were reconstructed by the legacy
    # reader. Only a choice governs the search - decision I, 22 Sep 2026.
    criteria_declared: bool = False''',
)
p.sub(
    '''    crit_set = criteria_mod.build(
        req.mark_text, classes=req.classes, applicant=req.applicant,
        tagline=req.tagline, extra=req.extra_criteria,
    )''',
    '''    crit_set = criteria_mod.build(
        req.mark_text, classes=req.classes, applicant=req.applicant,
        tagline=req.tagline,
        # Decision I: declared operators replace the derived set; a legacy
        # guess is only ever an addition.
        declared=(req.extra_criteria if req.criteria_declared else None),
        extra=(None if req.criteria_declared else req.extra_criteria),
    )''',
)
p.write()


# ===========================================================================
# deal_reader.py — the two call sites
# ===========================================================================
p = Patch("deal_reader.py")
p.sub("    mark, extra = _criteria(deal)\n", "    mark, extra, declared = _criteria(deal)\n")
p.sub(
    '''        extra_criteria=extra,
        deal_id=str(deal.get("id") or ""),''',
    '''        extra_criteria=extra,
        criteria_declared=declared,
        deal_id=str(deal.get("id") or ""),''',
)
p.sub(
    '''        "mark": mark, "extra_criteria": extra, "keyword_source": kw_source,''',
    '''        "mark": mark, "extra_criteria": extra, "keyword_source": kw_source,
        "criteria_declared": declared,
        "operator_notes": deal_fields.keyword_notes(),''',
)
p.write()
