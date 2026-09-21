"""The two scoring paths, as pure functions over plain values.

WHY THIS MODULE EXISTS
----------------------
Until now there was exactly one caller of each path — `runner._score` for
register rows and `runner._serp_score` for web rows — and the scoring logic
lived inline inside those loops, tangled with the row objects they mutate.

Sensitivity needs a SECOND caller: re-scoring a stored run at different
thresholds, from the rows in the database rather than from a live search.
Writing that as a second implementation is precisely how finding G happened —
two copies of a rule, drifting apart for weeks, disagreeing on 27 of 1,183
real names and nobody noticing. So the logic moves here, both callers use it,
and there is one answer to "what does this row score".

Everything here is a pure function of plain values: no row objects, no
database, no network. Given the same inputs and the same `settings` it returns
the same answer, which is what makes `rescore.py` trustworthy and what lets
`tools/band_diff.py` replay a run years later.

`settings` is a `tmh_scoring.ScoringSettings` or None. None means the shipped
calibration — see tmh-scoring releases 2.3.0 and 2.4.0.
"""
from __future__ import annotations

import re

__all__ = ["score_register_row", "score_web_row", "web_searches", "BANDS", "band_counts"]

#: Report order, worst first. Shared with runner._BAND_ORDER's intent but
#: expressed as a list because the panel also needs it for display.
BANDS = ["High", "Medium/High", "Medium", "Low/Medium", "Low",
         "Result (not live)", "Review - client may be at risk"]


def band_counts(bands) -> dict:
    """{band: count} over an iterable of band strings, in report order.

    Bands with a zero count are omitted rather than shown as 0 — a preview
    saying "Medium/High: 0 -> 0" is noise, and the four bands that matter get
    lost in it.
    """
    out = {}
    for b in bands:
        out[b] = out.get(b, 0) + 1
    return {b: out[b] for b in BANDS if b in out} | {
        b: n for b, n in out.items() if b not in BANDS}


def web_searches(mark_text: str) -> list[dict]:
    """The three criteria every web/marketplace/social row is scored against.

    A web hit has no status and no classes, so only similarity can contribute.
    That is deliberate: it keeps SERP rows below register rows of equal name
    match, because a page mentioning the mark is not a right.
    """
    return [{"type": "Exact", "phrase": mark_text},
            {"type": "Similar To", "phrase": mark_text},
            {"type": "Contains", "phrase": mark_text}]


def score_register_row(*, client_mark: str, cited_mark: str,
                       client_classes, cited_classes,
                       status: str = "", client_goods: str = "",
                       cited_goods: str = "",
                       registration_date=None, expiry_date=None,
                       filing_date=None, client_filing_date=None,
                       idf: dict | None = None, settings=None) -> dict:
    """Score one register row. Returns None for blocking noise.

    None means mark tier 0 — "no meaningful resemblance". Temmy deliberately
    over-returns, blocking in SQL on a substring or a shared leading token and
    letting scoring decide, so a mark sharing four letters and nothing else is
    a blocking artefact rather than a finding. The caller counts them.
    """
    from tmh_scoring.two_layer import assess

    a = assess(
        client_mark=client_mark, cited_mark=cited_mark,
        client_classes=client_classes, cited_classes=cited_classes,
        status=status, client_goods=client_goods, cited_goods=cited_goods or "",
        registration_date=registration_date or None,
        expiry_date=expiry_date or None,
        filing_date=filing_date or None,
        client_filing_date=client_filing_date,
        idf=idf or {},
        settings=settings,
    )
    if a.mark and a.mark.tier <= 0:
        return None

    m, t = a.mark, a.trade
    return {
        # D3: conflict and rights are two numbers and are never merged. The
        # score column carries CONFLICT - the factual similarity finding - and
        # rights travels beside it rather than being averaged into it.
        "score": a.conflict,
        "band": a.priority,
        "explanation": "; ".join(x for x in (
            m.reason if m else "",
            t.reason if t else "",
            "; ".join(a.rights_reasons or []),
        ) if x),
        "assessment": a.as_row(),
        "goods_similarity": ({"band": t.goods_band,
                              "shared_terms": list(t.shared_terms or []),
                              "explanation": t.reason,
                              "evidence": t.evidence} if t else None),
        "components": {
            "kind": "register",
            "mark": {
                "tier": m.tier if m else 0,
                "orthographic": round(float(m.orthographic), 4) if m else 0.0,
                "phonetic": bool(m.phonetic) if m else False,
                "shared_words": list(m.shared_words or []) if m else [],
                "visual": (m.visual if m else None),
                "reason": m.reason if m else "",
                # tmh-scoring 2.5.0. The raw axis measurements, so a row can be
                # re-banded at a different sensitivity from its own breakdown
                # and the panel can say WHICH axis carried it rather than only
                # that something did.
                "axes": (m.axes() if m and hasattr(m, "axes") else None),
            },
            "trade": {
                "tier": t.tier if t else 0,
                "goods_band": t.goods_band if t else "unknown",
                "shared_classes": list(t.shared_classes or []) if t else [],
                "shared_terms": list(t.shared_terms or []) if t else [],
                "evidence": t.evidence if t else "",
                "reason": t.reason if t else "",
            },
            "rights_reasons": list(a.rights_reasons or []),
        },
    }


def score_web_row(*, text: str, url: str = "", mark_text: str = "",
                  searches: list | None = None, settings=None) -> dict:
    """Score one web / marketplace / social row from its title and seller.

    Position is recorded elsewhere, not scored: prominence is context for a
    reviewer, not evidence of rights.
    """
    from tmh_scoring import score_word_result
    from tmh_scoring.word_scoring import mark_similarity_components

    ws = searches if searches is not None else web_searches(mark_text)
    text = (text or "")[:200]

    # An account or listing whose handle/path IS the mark
    # (facebook.com/BluePortalGroup) is the mark used as a name, whatever the
    # page title says. Checked on the FIRST path segment only, so a mark
    # buried deep in a URL does not qualify.
    slug = re.sub(r"[^a-z0-9]", "", (mark_text or "").lower())
    path = re.sub(r"^https?://[^/]+/", "", url or "").lower()
    first = re.sub(r"[^a-z0-9]", "", path.split("/")[0]) if path else ""
    named = bool(slug) and len(slug) >= 5 and slug in first

    out = score_word_result(
        {"status": "Registered", "mark_text": text, "mark_type": "", "classes": ""},
        {"word_searches": ws, "client_classes": []},
        settings,
    )
    sim = (out.get("components") or {}).get("similarity", 0)
    comparisons = mark_similarity_components(text.upper(), ws, settings)

    if named and sim < 3:
        sim = 3
    band = "Medium" if sim >= 3 else "Low/Medium" if sim >= 2 else "Low"
    return {
        "score": sim,
        "band": band,
        "explanation": ("account/handle named after the mark" if named else
                        "title is the mark itself" if sim >= 3 else
                        f"mark appears in title/seller (similarity {sim})" if sim
                        else "found by the query; mark not in the title"),
        "components": {
            "kind": "word",
            "points": dict(out.get("components") or {}),
            "comparisons": comparisons,
            "named_after_mark": named,
        },
    }
