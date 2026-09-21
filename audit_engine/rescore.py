"""Re-score a stored run at a different sensitivity, without re-searching it.

WHAT THIS IS FOR
----------------
Staff in Triage move a sensitivity control. Every band on the run is now
potentially wrong, and they need to see WHAT that costs before they commit it:
"this moves 43 rows up and 11 down" is a decision; "sensitivity applied" is
not.

WHY IT RE-SCORES RATHER THAN RE-BANDS
-------------------------------------
The original plan was to re-apply the bands to the stored `components`
breakdown, which would have been arithmetic. It does not work, and it is worth
saying why so nobody tries again: `components` carries the scorers' OUTPUTS at
the settings the run was scored with — a mark tier, an orthographic score, a
phonetic boolean — not the raw measurements a different gate would need. The
written and token axes were not stored at all until tmh-scoring 2.5.0. Applying
a new threshold to an old tier is applying it to the answer rather than to the
evidence.

So this re-runs the scorers over the STORED INPUTS instead: the cited mark
text, classes, status, specification and dates that are already on every row.
That is exact rather than approximate, it is the same code path a live run
uses (`scoring_paths`, one implementation, deliberately), and it is fast — the
whole Watch corpus of 55,526 records scores in about 17 seconds, so a single
audit run of a few thousand rows is well under a second.

WHAT IT NEVER DOES
------------------
It does not write to `audit.results`. A preview must be free to be discarded,
and even an applied sensitivity leaves the stored bands alone: the run's own
scoring is a historical fact, and the sensitivity is a lens over it recorded
in `audit.run_sensitivity`. Nothing here touches exclusions or review verdicts
either — a client's judgement must never be collateral damage from a scoring
decision.
"""
from __future__ import annotations

import json

from . import scoring_paths

__all__ = ["rescore_run", "compare", "Comparison"]


def _as_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if not v:
        return []
    try:
        return json.loads(v)
    except Exception:
        return []


def _inputs(store, run_id: str) -> tuple:
    """(run, register rows, web rows, shared kwargs) from the database.

    Mirrors what `runner` had in hand when it scored the run the first time.
    Anything the run did not store cannot be recovered and is passed as the
    same default the runner used, which is why `client_filing_date` is None
    here: `runner._score` is called without it.
    """
    run = store.run(run_id)
    if not run:
        raise LookupError(f"no such run: {run_id}")

    req = _as_dict(run.get("request"))
    crit = _as_list(run.get("criteria"))
    client_mark = (crit[0] or {}).get("phrase", "") if crit else ""
    client_goods = req.get("goods_text") or ""
    mark_text = req.get("mark_text") or run.get("mark_text") or client_mark

    rows = store.results(run_id)
    register = [r for r in rows if r.get("channel") == "trademark"]
    web = [r for r in rows if r.get("channel") != "trademark"]

    # Built from THIS run's own candidate specifications, so a term every mark
    # in the sector uses self-downweights without a hand-kept list going stale
    # (D8). Rebuilt here rather than stored, because it is a pure function of
    # the rows and storing it would be a second copy to drift.
    idf = {}
    if client_goods:
        from tmh_scoring.goods_similarity import build_idf
        idf = build_idf([r.get("goods") for r in register if r.get("goods")])

    shared = dict(client_mark=client_mark,
                  client_classes=run.get("classes"),
                  client_goods=client_goods,
                  client_filing_date=None,
                  idf=idf)
    return run, register, web, shared, mark_text


def rescore_run(store, run_id: str, settings=None) -> dict:
    """{result_id: band} for every row of the run, scored at `settings`.

    A row that scores as noise (mark tier 0) is reported as None rather than
    dropped. It was kept on the run when it was first scored, so silently
    losing it here would make the counts disagree with the screen.
    """
    run, register, web, shared, mark_text = _inputs(store, run_id)
    ws = scoring_paths.web_searches(mark_text)
    out = {}

    for r in register:
        dates = _as_dict(r.get("dates"))
        scored = scoring_paths.score_register_row(
            cited_mark=r.get("title") or "",
            cited_classes=r.get("classes"),
            status=r.get("status") or "",
            cited_goods=r.get("goods") or "",
            registration_date=dates.get("registration"),
            expiry_date=dates.get("expiry"),
            filing_date=dates.get("filing"),
            settings=settings,
            **shared,
        )
        out[str(r["id"])] = scored["band"] if scored else None

    for r in web:
        detail = _as_dict(r.get("detail"))
        text = f"{r.get('title') or ''} {detail.get('seller') or ''}".strip() \
            or (r.get("url") or "")
        scored = scoring_paths.score_web_row(
            text=text, url=r.get("url") or "", mark_text=mark_text,
            searches=ws, settings=settings)
        out[str(r["id"])] = scored["band"]

    return out


class Comparison(dict):
    """The answer to "what would this sensitivity do", ready for the screen."""


def compare(store, run_id: str, steps: dict | None) -> Comparison:
    """Score the run twice — at the standard calibration and at `steps`.

    BOTH sides are recomputed. Comparing against the bands stored on the rows
    would fold two different questions together: "what does this sensitivity
    change" and "has the scorer moved since this run was scored". The second is
    worth knowing, so it is reported separately as `drift_from_stored` rather
    than smuggled into the first.
    """
    from tmh_scoring import __version__ as scoring_version
    from tmh_scoring.sensitivity import deltas_for, describe, normalise_steps, settings_for

    steps = normalise_steps(steps)
    standard = rescore_run(store, run_id, None)
    override = rescore_run(store, run_id, settings_for(steps)) if steps else dict(standard)

    stored = {str(r["id"]): r.get("band") for r in store.results(run_id)}

    order = {b: i for i, b in enumerate(scoring_paths.BANDS)}


    def rank(b):
        # Unscored (noise) and unknown bands sort below everything, so a row
        # appearing or disappearing reads as a move rather than a crash.
        return -1 if b is None else len(order) - order.get(b, len(order))

    moved, up, down, drift = [], 0, 0, 0
    dropped = added = 0
    for rid, before in standard.items():
        after = override.get(rid)
        if stored.get(rid) != before:
            drift += 1
        if after == before:
            continue
        # A row can leave the findings entirely, or arrive in them. At a
        # stricter mark gate a result falls to tier 0 — "no meaningful
        # resemblance" — and stops being a finding at all; at a looser one
        # something that was blocking noise becomes one. Counting those as
        # "moved down" and "moved up" would be true and useless: staff need to
        # know that twelve rows are about to DISAPPEAR from the report, which
        # is a different conversation from twelve rows changing colour.
        if after is None:
            dropped += 1
            direction = "dropped"
        elif before is None:
            added += 1
            direction = "added"
        else:
            direction = "up" if rank(after) > rank(before) else "down"
            up += direction == "up"
            down += direction == "down"
        moved.append({"result_id": rid, "from": before, "to": after,
                      "direction": direction})

    return Comparison({
        "steps": steps,
        "deltas": deltas_for(steps),
        "described": describe(steps),
        "scoring_version": scoring_version,
        "rows": len(standard),
        "rows_moved": len(moved),
        "moved_up": up,
        "moved_down": down,
        # Rows that stop being findings, and rows that start being findings.
        # Kept out of up/down so the headline cannot hide them.
        "dropped": dropped,
        "added": added,
        "band_before": scoring_paths.band_counts(
            b for b in standard.values() if b is not None),
        "band_after": scoring_paths.band_counts(
            b for b in override.values() if b is not None),
        # Rows whose stored band no longer matches what the current package
        # gives at the standard calibration. Not caused by this sensitivity —
        # the run was scored by an older release, or the rows changed. Shown
        # so nobody reads a package upgrade as the effect of their slider.
        "drift_from_stored": drift,
        "moved": moved[:200],
        "moved_truncated": max(0, len(moved) - 200),
    })
