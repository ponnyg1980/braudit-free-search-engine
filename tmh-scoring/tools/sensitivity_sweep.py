#!/usr/bin/env python3
"""What each sensitivity step actually does to real stored results.

A preset table is an assertion until it is measured. This replays a band_diff
inputs file at every offered step of every compartment and reports how many
results change band and in which direction, so that:

  * a step that moves NOTHING is caught before it reaches a control — it would
    log as a change that did not happen, which is the one thing the design
    cannot afford;
  * a step that moves EVERYTHING is caught too — that is not sensitivity, it
    is a different scorer;
  * the Triage preview has calibrated expectations to state, rather than
    discovering the consequence on a client's report.

Usage:
    sensitivity_sweep.py INPUTS.jsonl [--kinds two_layer,v1] [--json OUT]

Inputs are the same files tools/band_diff.py produces with extract-watch /
extract-audit.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tmh_scoring import two_layer, word_scoring                    # noqa: E402
from tmh_scoring.goods_similarity import build_idf                 # noqa: E402
from tmh_scoring.sensitivity import (AVAILABLE_STEPS, STEP_LABELS,  # noqa: E402
                                     settings_for)
from tmh_scoring.settings import COMPARTMENT_LABELS                # noqa: E402

BANDS = ["Result (not live)", "Low", "Low/Medium", "Medium", "Medium/High",
         "High", "Review - client may be at risk"]
_ORDER = {b: i for i, b in enumerate(BANDS)}


def load(path, kinds):
    records, idf_goods = [], {}
    for line in pathlib.Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "idf_group":
            idf_goods[r["group"]] = r["goods"]
        elif r.get("kind") in kinds:
            records.append(r)
    return records, {g: build_idf(v) for g, v in idf_goods.items()}


def band_of(rec, idfs, settings):
    kind = rec["kind"]
    if kind == "two_layer":
        args = dict(rec["args"])
        idf = idfs.get(rec.get("group"))
        if idf is not None:
            args.setdefault("idf", idf)
        return two_layer.assess(settings=settings, **args).priority
    if kind == "v1":
        return word_scoring.score_word_result(
            rec["result"], rec["criteria"], settings=settings)["risk_band"]
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs")
    ap.add_argument("--kinds", default="two_layer,v1")
    ap.add_argument("--json", dest="out")
    a = ap.parse_args()

    kinds = set(a.kinds.split(","))
    records, idfs = load(a.inputs, kinds)
    print(f"{len(records)} records "
          f"({dict(Counter(r['kind'] for r in records))})\n")

    base = [band_of(r, idfs, None) for r in records]
    print(f"baseline bands: {dict(Counter(base))}\n")

    report = {}
    for comp, steps in AVAILABLE_STEPS.items():
        rows = []
        for step in steps:
            if step == 0:
                continue
            s = settings_for({comp: step})
            up = down = 0
            moves = Counter()
            for rec, before in zip(records, base):
                after = band_of(rec, idfs, s)
                if after == before:
                    continue
                moves[f"{before} -> {after}"] += 1
                if _ORDER.get(after, 0) > _ORDER.get(before, 0):
                    up += 1
                else:
                    down += 1
            rows.append({"step": step, "label": STEP_LABELS[step],
                         "moved": up + down, "up": up, "down": down,
                         "pct": round(100 * (up + down) / len(records), 2),
                         "top_moves": moves.most_common(4)})
        report[comp] = rows

        print(f"== {COMPARTMENT_LABELS.get(comp, comp)} ({comp})")
        for r in rows:
            flag = "   <-- MOVES NOTHING" if r["moved"] == 0 else ""
            print(f"   {r['step']:+d} {r['label']:<20} "
                  f"moved {r['moved']:>5} ({r['pct']:>5.2f}%)  "
                  f"up {r['up']:>5}  down {r['down']:>5}{flag}")
            for mv, n in r["top_moves"]:
                print(f"        {n:>5}  {mv}")
        print()

    dead = [(c, r["step"]) for c, rs in report.items() for r in rs if r["moved"] == 0]
    if dead:
        print(f"STEPS THAT MOVE NOTHING ON THIS CORPUS: {dead}")
        print("Either the corpus does not exercise them, or the preset is inert.")
    else:
        print("Every offered step moves at least one result.")

    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(report, indent=2))
        print(f"\n-> {a.out}")


if __name__ == "__main__":
    main()
