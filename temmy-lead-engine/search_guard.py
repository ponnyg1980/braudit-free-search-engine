#!/usr/bin/env python3
"""SHARED SEARCH GUARDS — every route MUST import these. Do not reimplement them.

Two absolute rules, both learned the hard way. They lived inside per-run scripts (.work/rC_claim.py,
rP_claim.py, rN_claim.py) where a newly written route could silently omit them. Centralised here so
that cannot happen again.

────────────────────────────────────────────────────────────────────────────────────────────────
RULE 1 — NEVER RE-SEARCH SOMEONE SEARCHED IN THE LAST 6 MONTHS. EVER. (Jonathan, 30 Jul: "EVER!")
    eligible(aid, log) is the ONLY sanctioned way to decide whether an applicant may be searched.
    It blocks on: suppression, requeue=False (permanently frozen), requeue_after in the future,
    and an in_progress claim less than 24h old.

RULE 2 — VALIDATE THAT RESULTS ACTUALLY MATCH THE TARGET, AND ABORT IF THEY DO NOT.
    On 29 Jul eight Sales-Navigator queries returned the IDENTICAL five generic "trademark"
    profiles: the company_name filter silently was not applied. Real API calls, junk responses,
    0/40 usable, budget spent. At fleet scale (2,000 profile-views/day) that bug wastes 8x as much.
    validate_results() catches it. Jonathan, 30 Jul: "THIS MUST BE BAKED INTO EVERYTHING, we do not
    have a shortage of opportunities" — i.e. when in doubt, DISCARD. Throwing away a good batch
    costs nothing; ingesting a junk batch corrupts the CRM and burns credits.

Usage in any route worker:

    import search_guard as sg

    if not sg.eligible(aid, log):
        continue                                   # RULE 1

    results = linkedin_people_search(company_name="Isern Patentes", ...)
    ok, why, matched = sg.validate_results(results, expect_terms=["isern", "patentes"])
    if not ok:
        sg.abort(f"search validation failed for Isern: {why}")   # RULE 2 — do NOT ingest
"""
import json, os, datetime, re

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "searched_log.json")

# v2 (2026-07-31, Jonathan — "report on Searched, found, sent to cerebrum, email attached, by
# route and by LinkedIn account, each day"). Nothing in the codebase kept a per-day history before
# this: search_budget.json overwrites itself when the date rolls, queues are deleted on push, and
# record() itself never accepted account/synced/email — so those fields only ever existed on the
# ~9-68% of searched_log.json entries some operator happened to hand-merge in. record() is the ONE
# choke point every route already imports (§2b: "Import it. Never reimplement"), so this is also
# the one place a fix reaches every route for free. ACTIVITY_LOG is append-only (one JSON line per
# record() call) precisely because searched_log.json/search_budget.json are NOT — nothing here ever
# overwrites a prior day. See daily_report.py for the reader.
ACTIVITY_LOG = os.path.join(HERE, "activity_log.jsonl")

# A batch must have at least this share of rows plausibly tied to the target, or it is junk.
MIN_MATCH_RATIO = 0.20
# If every row is identical on these keys, the filter was ignored and the same page came back.
IDENTITY_KEYS = ("public_identifier", "profile_url", "provider_id")

# D4 fix (6 Aug 2026, OPEN_DEFECTS.md): the Andermatt Group AG incident — company_name free text
# silently returned Jonathan's own FIRST_DEGREE network, ten unrelated UK people, instead of an
# error. Every row was first-degree; a genuine company/org search returns a MIX of connection
# distances, so "literally everyone is 1st-degree" is itself the filter-ignored tell, the same class
# of signal IDENTITY_KEYS already catches for "the same page came back regardless of filter".
# `linkedin_people_search`'s own schema confirms the live field: `network_distance` is an array of
# `1, 2, or 3` on the REQUEST side; historical raw result files in this repo show the per-row
# connection value coming back in more than one shape across tool versions — a string enum
# ("FIRST_DEGREE"/"THIRD_DEGREE"), a plain int (1/2/3), or null when unknown — so this is checked
# defensively across all of them rather than assuming one exact shape.
DEGREE_KEYS = ("network_distance", "degree", "connection_degree")
FIRST_DEGREE_MIN_ROWS = 3   # same floor as the IDENTITY_KEYS check — don't over-react to a tiny page

# D6 fix (6 Aug 2026, OPEN_DEFECTS.md): the Endrel incident — Andermatt's own word mark, "Endrel",
# is ALSO a common Brazilian first name. A bare `keywords="Endrel"` brand search (no location — see
# linkedin_search_step.location_request()'s D5/D6 fix for the other half of this) returned 220
# Brazilians named Endrel, and the OLD blob-matching below (which folded `name` into the exact same
# blob as headline/company/etc.) credited every single one as "brand context found" purely because
# their own NAME happened to contain the word mark: `validate_results(rows, ["Andermatt Group AG",
# "Endrel"])` returned PASS, 6/6 rows matched, 100% — a false pass, RULE 2 not doing its job.
#
# Fields excluded from "non-name" brand-context credit. `name` is the obvious one. `public_identifier`
# and `profile_url` are included too — NOT in the OPEN_DEFECTS.md fix note verbatim, but required for
# the fix to actually hold: LinkedIn generates both as slugs FROM the person's own name (see this
# file's own andermatt_good fixture below: "erich-frank" derived from "Erich Frank") — crediting them
# as independent context would silently readmit the exact Endrel bug through a different field. This
# is the same "when in doubt, DISCARD" philosophy this file already states (Jonathan, 30 Jul) applied
# to a fix that would otherwise not fully work — flagged here rather than silently narrowed to match
# the fix note's literal wording.
NAME_DERIVED_KEYS = ("name", "public_identifier", "profile_url")
NON_NAME_CONTEXT_KEYS = ("headline", "company", "company_name", "current_company", "summary", "title")


def _degree_of(row):
    for k in DEGREE_KEYS:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


def _is_first_degree(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return int(value) == 1
    s = str(value).strip().upper()
    return s in ("1", "FIRST_DEGREE", "FIRST-DEGREE", "1ST_DEGREE", "FIRST") or "FIRST" in s


def load_log(path=LOG):
    try:
        return json.load(open(path))
    except Exception:
        return {}


def eligible(aid, log, now=None):
    """RULE 1. True only if this applicant may be searched right now."""
    now = now or datetime.datetime.now()
    e = log.get(str(aid))
    if not e:
        return True
    if not isinstance(e, dict):
        return False                      # legacy string entry = already searched, treat as blocked
    if e.get("outcome") == "suppressed" or e.get("requeue") is False:
        return False
    if e.get("outcome") == "in_progress":
        try:
            age = (now - datetime.datetime.fromisoformat(
                str(e.get("claimed", "2000-01-01T00:00:00")).replace("Z", ""))).total_seconds()
        except Exception:
            age = 1e9
        if age < 24 * 3600:
            return False                  # another worker holds the claim
    ra = e.get("requeue_after")
    if ra and str(ra)[:10] > now.date().isoformat():
        return False                      # inside the 6-month cool-off
    return True


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _tokens(term):
    """Meaningful tokens only — generic words match everything and would defeat the check."""
    stop = {"limited", "ltd", "llp", "plc", "the", "and", "group", "holdings", "company", "co",
            "uk", "international", "services", "partners", "associates", "trademark", "trademarks",
            "patent", "patents", "ip", "law", "legal", "attorneys", "attorney"}
    return [t for t in re.split(r"[^a-z0-9]+", (term or "").lower()) if t and t not in stop
            and len(t) > 2]


def validate_results(results, expect_terms, min_ratio=MIN_MATCH_RATIO, brand_terms=None):
    """RULE 2. Returns (ok, reason, matched_rows).

    `results`   list of profile dicts (name/headline/company/profile_url...).
    `expect_terms` strings the batch SHOULD relate to — firm name, brand, applicant name.
    `brand_terms` (D6 fix) — optional subset of `expect_terms` that are word-marks/brand names, as
        opposed to a person's own identity name. Tokens from `brand_terms` are credited ONLY when
        they appear in a NON-name-derived field (NON_NAME_CONTEXT_KEYS) — a brand token found only
        in `name`/`public_identifier`/`profile_url` does not count (the Endrel bug: a word mark that
        is coincidentally also a common personal name). Tokens NOT in `brand_terms` keep the old
        behaviour (name field counts) — this matters for individual searches, where
        Temmy_Lead_Engine_Design.md's own scoring is "Brand+Name+Location" and the returned row's
        `name` genuinely matching the searched person IS the correct corroboration, not a
        coincidence, since the query itself was first_name/last_name (see linkedin_search_step.py's
        individual_search_plan()/officer_individual_plan(), which pass the person's own name as an
        ordinary expect_term and only the word mark as a brand_term). Left at the default (None),
        this function's behaviour is UNCHANGED from before D6 — every existing caller in this
        codebase keeps working exactly as before; the fix is enforced by construction on the one new
        caller that actually runs a bare-keywords brand search
        (linkedin_search_step.validate_brand_search(), the sanctioned entry point — never call this
        function directly for a brand search).

    Fails on: empty results, all-identical rows (the 29 Jul filter-ignored bug), all-FIRST_DEGREE
    rows (the 6 Aug D4 filter-ignored bug — see DEGREE_KEYS above), too few rows plausibly tied to
    any expected term, or (D6) a brand token that only ever shows up in a name-derived field.
    """
    rows = list(results or [])
    if not rows:
        return False, "empty result set", []

    # the 29 Jul signature: every row identical, i.e. the same page returned regardless of filter
    for k in IDENTITY_KEYS:
        vals = [str(r.get(k) or "") for r in rows if r.get(k)]
        if len(vals) >= 3 and len(set(vals)) == 1:
            return False, f"all {len(vals)} rows share the same {k} — filter was ignored", []

    # D4, 6 Aug: every row FIRST_DEGREE = the identifier filter was ignored and LinkedIn served
    # "your own network" instead. Only fires when degree is KNOWN for every row (unknown/missing
    # degree on some rows is not evidence either way — never claim "all" from a partial signal).
    degrees = [_degree_of(r) for r in rows]
    if len(rows) >= FIRST_DEGREE_MIN_ROWS and all(d is not None for d in degrees) \
       and all(_is_first_degree(d) for d in degrees):
        return (False,
                f"all {len(rows)} rows are FIRST_DEGREE connections — filter was ignored, this is "
                "your own network (D4, 6 Aug)", [])

    wanted = set()
    for term in (expect_terms or []):
        wanted.update(_tokens(term))
    if not wanted:
        # nothing distinctive to check against — do not silently pass, say so
        return False, "no distinctive tokens in expect_terms; cannot validate", []

    brand_wanted = set()
    for term in (brand_terms or []):
        brand_wanted.update(_tokens(term))

    matched = []
    for r in rows:
        # D6: brand tokens are checked against the non-name-derived fields ONLY; everything else
        # (a person's own identity name, an org's own legal name) may still be credited from the
        # full blob including `name` — see brand_terms' docstring above for why the split matters.
        full_blob = _norm(" ".join(str(r.get(f) or "") for f in
                                   ("name", "headline", "company", "company_name",
                                    "current_company", "summary", "title", "profile_url",
                                    "public_identifier")))
        context_blob = _norm(" ".join(str(r.get(f) or "") for f in NON_NAME_CONTEXT_KEYS)) \
            if brand_wanted else full_blob
        hit = False
        for t in wanted:
            if t in brand_wanted:
                if t in context_blob:
                    hit = True
                    break
            elif t in full_blob:
                hit = True
                break
        if hit:
            matched.append(r)

    ratio = len(matched) / len(rows)
    if ratio < min_ratio:
        return (False,
                f"only {len(matched)}/{len(rows)} rows ({ratio:.0%}) reference any of "
                f"{sorted(wanted)[:6]} — below the {min_ratio:.0%} floor, treating as junk",
                matched)
    return True, f"{len(matched)}/{len(rows)} rows matched ({ratio:.0%})", matched


class SearchAborted(RuntimeError):
    pass


def abort(reason):
    """Stop this target cleanly. Preferred over ingesting doubtful data — there is no shortage of
    opportunities, so a discarded batch costs nothing and a junk batch costs trust."""
    raise SearchAborted(reason)


def claim(aid, log, route, path=LOG):
    """D3 fix (6 Aug, OPEN_DEFECTS.md): this call overwrites outcome/claimed/route with no backup —
    that's exactly why a later `release-lock` on an abandoned run couldn't undo it exactly, only
    approximate it. Now stashes what it's about to overwrite into `_pre_claim` first, so
    release_claim() below can restore precisely rather than guess. `_new: True` marks a key that had
    NO prior entry at all (nothing to restore — release should delete it outright, not fabricate a
    "restored" state that never existed)."""
    key = str(aid)
    prior = log.get(key)
    had_prior = isinstance(prior, dict)
    stash = {"outcome": (prior or {}).get("outcome"), "claimed": (prior or {}).get("claimed"),
             "route": (prior or {}).get("route"), "_new": not had_prior}
    base = dict(prior) if had_prior else {}
    base.pop("_pre_claim", None)   # never nest a stash inside a stash (re-claiming an in-progress key)
    log[key] = {**base, "outcome": "in_progress",
               "claimed": datetime.datetime.now().isoformat(timespec="seconds"),
               "route": route, "_pre_claim": stash}
    json.dump(log, open(path, "w"), indent=1)
    return log


def release_claim(aid, log, path=LOG):
    """D3 fix (6 Aug): undo what claim() did to this aid — the other half of the fix, called by
    route_runner.py's `release-lock` for every key an abandoned run's plan claimed.

    Only acts on entries still `outcome == "in_progress"` — if something else (a normal finalize,
    a different run) already recorded a real outcome or re-claimed it since, this leaves it alone
    rather than clobbering a later, real result.

    Prefers the exact stash claim() now leaves in `_pre_claim`:
      - `_new: True`  -> nothing existed before this claim -> delete the entry outright.
      - otherwise     -> restore exactly the outcome/claimed/route this claim overwrote.
    Falls back to OPEN_DEFECTS.md D3's documented best-effort rule for legacy claims made before
    this fix landed (no `_pre_claim` to restore from — cannot be undone exactly, by design, per the
    defect's own note: "claim() overwrites outcome without preserving it"):
      - entry has NO prior-history signal (last_searched / public_identifier / requeue_after all
        absent) -> delete the entry;
      - entry HAS prior-history signal -> strip outcome/claimed/route, keep everything else.

    Returns True if it changed the log, False if there was nothing to do."""
    key = str(aid)
    e = log.get(key)
    if not isinstance(e, dict) or e.get("outcome") != "in_progress":
        return False

    pre = e.get("_pre_claim")
    if isinstance(pre, dict):
        if pre.get("_new"):
            del log[key]
        else:
            restored = {k: v for k, v in e.items() if k != "_pre_claim"}
            for f in ("outcome", "claimed", "route"):
                if pre.get(f) is None:
                    restored.pop(f, None)
                else:
                    restored[f] = pre[f]
            log[key] = restored
    else:
        has_history = any(e.get(f) for f in ("last_searched", "public_identifier", "requeue_after"))
        if not has_history:
            del log[key]
        else:
            log[key] = {k: v for k, v in e.items() if k not in ("outcome", "claimed", "route")}
    json.dump(log, open(path, "w"), indent=1)
    return True


def record(aid, log, route, outcome, tier=None, public_identifier=None,
           cooloff_days=182, path=LOG, account=None, synced=None, email_found=None):
    """Write the outcome AND the 6-month cool-off. `found`+high is frozen permanently.

    v2 (31 Jul): optional `account` (which LinkedIn seat searched), `synced` (bool — landed in
    Cerebrum), `email_found` (bool — enrichment succeeded; deliberately NOT the address itself,
    to keep this a metrics field rather than a second copy of PII). All three are additive —
    omitting them (the old call shape) behaves exactly as before. When given, they are merged onto
    the searched_log.json entry AND appended to ACTIVITY_LOG, which is what daily_report.py reads.
    """
    frozen = (outcome == "found" and tier == "high") or outcome == "suppressed"
    ra = None if frozen else (datetime.date.today() +
                              datetime.timedelta(days=cooloff_days)).isoformat()
    # the claim this resolves is fulfilled now, not abandoned -- drop claim()'s _pre_claim stash
    # (D3 fix, 6 Aug) rather than let it linger forever as dead weight on a normally-completed entry.
    prior = {k: v for k, v in log.get(str(aid), {}).items() if k != "_pre_claim"}
    entry = {**prior,
            "last_searched": datetime.date.today().isoformat(),
            "outcome": outcome, "tier": tier,
            "public_identifier": public_identifier,
            "requeue": not frozen, "requeue_after": ra, "route": route}
    if account is not None:
        entry["account"] = account
    if synced is not None:
        entry["synced"] = synced
    if email_found is not None:
        entry["email_found"] = email_found
    log[str(aid)] = entry
    json.dump(log, open(path, "w"), indent=1)
    _log_activity(aid, route, outcome, tier, account, synced, email_found)
    return log


def _log_activity(aid, route, outcome, tier, account, synced, email_found):
    """Append one line to ACTIVITY_LOG. Best-effort: a logging failure must never break the
    sacred searched_log.json write above, so this is deliberately swallow-and-continue."""
    try:
        now = datetime.datetime.now()
        line = {"ts": now.isoformat(timespec="seconds"), "date": now.date().isoformat(),
               "aid": str(aid), "route": str(route) if route is not None else None,
               "outcome": outcome, "tier": tier, "account": account,
               "synced": bool(synced) if synced is not None else None,
               "email_found": bool(email_found) if email_found is not None else None}
        with open(ACTIVITY_LOG, "a") as f:
            f.write(json.dumps(line) + "\n")
    except Exception as e:
        print(f"  ! activity_log append failed (non-fatal): {e}")


if __name__ == "__main__":
    log = load_log()
    today = datetime.date.today().isoformat()
    elig = sum(1 for k in log if eligible(k, log))
    print(f"searched_log entries : {len(log)}")
    print(f"eligible to search   : {elig}")
    print(f"blocked by cool-off  : {len(log) - elig}")
    # self-test of RULE 2 against the real 29 Jul failure shape
    junk = [{"public_identifier": "same-guy", "name": "Trademark Person"} for _ in range(5)]
    ok, why, _ = validate_results(junk, ["Isern Patentes y Marcas"])
    print(f"RULE 2 vs 29-Jul junk: {'BLOCKED' if not ok else 'PASSED (BUG!)'} — {why}")
    good = [{"public_identifier": f"p{i}", "name": "Pepe Isern",
             "headline": "Partner at Isern Patentes y Marcas"} for i in range(4)]
    ok2, why2, _ = validate_results(good, ["Isern Patentes y Marcas"])
    print(f"RULE 2 vs good batch : {'PASSED' if ok2 else 'BLOCKED (BUG!)'} — {why2}")
    # self-test of RULE 2 against the real 6 Aug D4 failure shape (Andermatt Group AG) — ten
    # unrelated, all-FIRST_DEGREE rows, string-enum degree field (as seen in this repo's own
    # historical raw result files).
    andermatt_junk = [{"public_identifier": f"jonathan-contact-{i}", "name": f"Person {i}",
                       "headline": "Unrelated UK professional", "network_distance": "FIRST_DEGREE"}
                      for i in range(10)]
    ok3, why3, _ = validate_results(andermatt_junk, ["Andermatt Group", "Andermatt"])
    print(f"RULE 2 vs D4 (6 Aug) : {'BLOCKED' if not ok3 else 'PASSED (BUG!)'} — {why3}")
    # numeric-degree shape (network_distance: 1/2/3, matching linkedin_people_search's own request
    # schema) must be caught the same way, not just the string-enum shape above.
    andermatt_junk_numeric = [{"public_identifier": f"jonathan-contact-{i}", "name": f"Person {i}",
                              "network_distance": 1} for i in range(10)]
    ok4, why4, _ = validate_results(andermatt_junk_numeric, ["Andermatt Group"])
    print(f"RULE 2 vs D4 (numeric): {'BLOCKED' if not ok4 else 'PASSED (BUG!)'} — {why4}")
    # a GENUINE mixed-degree org result (the working Andermatt call, D4's own example) must NOT be
    # blocked by the new check — real company searches return a mix of connection distances.
    andermatt_good = [
        {"public_identifier": "erich-frank", "name": "Erich Frank",
         "headline": "Director Business Development bei Andermatt Group AG", "network_distance": 2},
        {"public_identifier": "ralph-blunschi", "name": "Ralph Blunschi",
         "headline": "Head of ICT bei Andermatt Service AG", "network_distance": 3},
        {"public_identifier": "martin-gunter", "name": "Martin Günter",
         "headline": "CEO bei Andermatt Biocontrol Suisse AG", "network_distance": 3},
        {"public_identifier": "amy-blu", "name": "Amy Blu Breytenbach",
         "headline": "Group Marketing Manager - Andermatt Group", "network_distance": 2},
    ]
    ok5, why5, _ = validate_results(andermatt_good, ["Andermatt Group"])
    print(f"RULE 2 vs D4 real good: {'PASSED' if ok5 else 'BLOCKED (BUG!)'} — {why5}")

    # self-test of RULE 2 against the real D6 failure shape (6 Aug 2026, OPEN_DEFECTS.md): "Endrel"
    # is Andermatt's word mark AND a common Brazilian first name. Bare keywords="Endrel" (no
    # location) returned 220 unrelated Brazilians; here modelled as 10 for brevity, each one's own
    # `name` AND LinkedIn-slug `public_identifier` containing "endrel" purely by coincidence, with a
    # headline/company that has nothing to do with Andermatt at all.
    endrel_junk = [{"public_identifier": f"endrel-silva-{i}", "name": f"Endrel Silva {i}",
                   "headline": "Vendedor na Loja Brasileira", "company": "Magazine Popular",
                   "network_distance": 2} for i in range(10)]
    ok6_old, why6_old, _ = validate_results(endrel_junk, ["Andermatt Group AG", "Endrel"])
    print(f"RULE 2 vs D6 Endrel, OLD call (no brand_terms — pre-D6 behaviour, still PASSES): "
         f"{'PASSED' if ok6_old else 'BLOCKED'} — {why6_old}")
    ok6, why6, _ = validate_results(endrel_junk, ["Andermatt Group AG", "Endrel"],
                                    brand_terms=["Endrel"])
    print(f"RULE 2 vs D6 Endrel, brand_terms=['Endrel']: "
         f"{'BLOCKED' if not ok6 else 'PASSED (BUG!)'} — {why6}")

    # a brand token found via a GENUINE non-name field (headline/company actually reference the
    # brand) must still PASS even with brand_terms set — D6 narrows the credit source, it does not
    # forbid brand matches outright.
    endrel_real = [{"public_identifier": f"p{i}", "name": f"Person {i}",
                    "headline": "Sales Manager at Endrel Group AG", "network_distance": 2}
                   for i in range(4)]
    ok7, why7, _ = validate_results(endrel_real, ["Endrel"], brand_terms=["Endrel"])
    print(f"RULE 2 vs D6 genuine brand-in-headline: {'PASSED' if ok7 else 'BLOCKED (BUG!)'} — {why7}")

    # D5's individual-search path: the design scores "Brand+Name+Location" — the returned candidate's
    # own `name` matching the person actually searched for is the CORRECT corroboration (confirms the
    # first_name/last_name filter worked), not a coincidence, and must not be blocked just because
    # brand_terms is present for the word-mark token in the same call. Modelled as
    # linkedin_search_step.py would call it: expect_terms=[word_mark, applicant_name],
    # brand_terms=[word_mark] only.
    individual_good = [{"public_identifier": "erich-frank", "name": "Erich Frank",
                        "headline": "Freelance consultant", "network_distance": 1}]
    ok8, why8, _ = validate_results(individual_good, ["Endrel", "Erich Frank"],
                                    brand_terms=["Endrel"])
    print(f"RULE 2 vs D5 individual name-field match (not a brand term): "
         f"{'PASSED' if ok8 else 'BLOCKED (BUG!)'} — {why8}")
