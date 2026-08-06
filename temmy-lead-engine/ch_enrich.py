#!/usr/bin/env python3
"""Companies House enrichment for the Route 0 rep-free base (waterfall step 5, MASTER_CONTROL §2e).

PURPOSE: get a real human NAME and a registered address for applicants we hold, without spending a
single LinkedIn search. CH gives officer names and addresses — not emails — but per §2e a named
officer plus a switchboard is already a working lead.

WHAT IT CAN AND CANNOT DO (measured on the 810 rep-free applicants, 29 Jul):
  · 441 "Company or Organisation" + 26 Partnership  -> CH applies
  · 327 "Individual(s)" (40%)                       -> NOT in CH as companies. Skipped by default;
    an individual self-filer is a person, not a registered entity. `--individuals-officer-search`
    will look them up in the CH officer index instead, but name-only matching is weak — results are
    graded `low` and should never be treated as confirmed.
  · 316 of 810 already carry a company_number in TemmyDB -> direct lookup, no name guessing, `high`.

CONFIDENCE GRADING (never merge a `low` into outreach data unreviewed):
  high   — matched by company_number held in TemmyDB, or a single CH search hit whose postcode
           matches the applicant's TemmyDB address
  medium — single plausible CH search hit, no postcode corroboration
  low    — multiple candidates, or officer-index name match only
  none   — nothing found

RATE LIMIT: CH allows 600 requests / 5 min. Held to ~2/sec with backoff on 429. Resumable — state in
ch_enrich_state.json — so it can be rerun across the sandbox's 45s command limit until COMPLETE.

Usage:
  python3 ch_enrich.py --env ../temmy-access/secrets.env [--max-seconds 35] [--limit N]
                       [--individuals-officer-search]
"""
import sys, os, json, time, argparse, base64, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ghl_push as g
import daily_maintenance as dm

HERE = os.path.dirname(os.path.abspath(__file__))
FACTS = os.path.join(HERE, "route0_facts_cache.json")
STATE = os.path.join(HERE, "ch_enrich_state.json")
OUT = os.path.join(HERE, "ch_enrichment.json")
CH_BASE = "https://api.company-information.service.gov.uk"
MIN_GAP = 0.5          # ~2 req/sec against CH's 600-per-5-min ceiling

_last = [0.0]


def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d


def ch(path, key):
    """One CH GET, rate-limited, with 429 backoff. Returns None on 404."""
    gap = time.time() - _last[0]
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    auth = base64.b64encode((key + ":").encode()).decode()
    req = urllib.request.Request(CH_BASE + path, headers={"Authorization": "Basic " + auth})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                _last[0] = time.time()
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(2 + attempt * 3)
                continue
            return {"_error": f"HTTP {e.code}"}
        except Exception as e:
            return {"_error": str(e)}
    return {"_error": "rate-limited"}


def norm_pc(s):
    return "".join((s or "").upper().split())


_SUFFIX = (" limited", " ltd", " plc", " llp", " lp", " company", " co", " uk",
           " holdings", " group", " (uk)", " int", " international")


def norm_name(s):
    """Company-name key: lowercase, drop legal suffixes and punctuation."""
    x = (s or "").lower().strip()
    for _ in range(3):                       # "X Holdings Ltd" -> "x"
        for suf in _SUFFIX:
            if x.endswith(suf):
                x = x[: -len(suf)].strip()
    return "".join(ch_ for ch_ in x if ch_.isalnum())


def officers(num, key, cap=8):
    d = ch(f"/company/{num}/officers?items_per_page={cap}", key)
    if not d or d.get("_error"):
        return []
    out, seen = [], set()
    for it in (d.get("items") or []):
        if it.get("resigned_on"):
            continue                       # current officers only
        nm = (it.get("name") or "").strip()
        if not nm or nm.lower() in seen:
            continue                       # CH returns one row per APPOINTMENT, so the same
        seen.add(nm.lower())               # person recurs (director + secretary) — dedupe by name
        out.append({"name": nm, "role": it.get("officer_role"),
                    "appointed_on": it.get("appointed_on"),
                    "occupation": it.get("occupation"),
                    "nationality": it.get("nationality")})
    return out


def profile(num, key):
    d = ch(f"/company/{num}", key)
    if not d or d.get("_error"):
        return None
    ro = d.get("registered_office_address") or {}
    return {
        "company_number": d.get("company_number"),
        "company_name": d.get("company_name"),
        "status": d.get("company_status"),
        "created": d.get("date_of_creation"),
        "ceased": d.get("date_of_cessation"),
        "sic": d.get("sic_codes"),
        "address": ", ".join(x for x in [ro.get("address_line_1"), ro.get("address_line_2"),
                                         ro.get("locality"), ro.get("postal_code")] if x),
        "postcode": ro.get("postal_code"),
    }


def search_company(name, key, want_pc=None):
    """Search CH by name. Returns (profile|None, confidence)."""
    q = urllib.parse.quote((name or "")[:120])
    d = ch(f"/search/companies?q={q}&items_per_page=5", key)
    if not d or d.get("_error"):
        return None, "none"
    items = d.get("items") or []
    if not items:
        return None, "none"
    # NB: CH search items use `title`, not `company_name` — using the wrong key silently yields None
    target = norm_name(name)
    exact = [it for it in items if norm_name(it.get("title")) == target]
    # postcode corroboration is the strongest signal, then an exact normalised-name match
    for pool in (exact or items):
        addr = pool.get("address") or {}
        if want_pc and norm_pc(addr.get("postal_code")) == norm_pc(want_pc) and norm_pc(want_pc):
            p = profile(pool.get("company_number"), key)
            if p:
                p["matched_name"] = pool.get("title")
                return p, "high"
    if exact:
        p = profile(exact[0].get("company_number"), key)
        if p:
            p["matched_name"] = exact[0].get("title")
            if len(exact) > 1:
                p["other_exact_matches"] = [i.get("title") for i in exact[1:4]]
            return p, "medium" if len(exact) == 1 else "low"
        return None, "none"
    if len(items) == 1:
        p = profile(items[0].get("company_number"), key)
        if p:
            p["matched_name"] = items[0].get("title")
            return p, "medium"
        return None, "none"
    p = profile(items[0].get("company_number"), key)
    if p:
        p["matched_name"] = items[0].get("title")
        p["other_candidates"] = [i.get("title") for i in items[1:4]]
        return p, "low"
    return None, "none"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    ap.add_argument("--max-seconds", type=int, default=35)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--individuals-officer-search", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    cfg = g.load_env(g.find_env(a.env))
    key = cfg.get("COMPANIES_HOUSE_API_KEY")
    if not key:
        raise SystemExit("COMPANIES_HOUSE_API_KEY missing from the env file")

    state = load(STATE, {})
    results = load(OUT, {})
    targets = state.get("targets")

    # ---- build the target list once, from TemmyDB ----
    if not targets:
        facts = (load(FACTS, {}) or {}).get("facts") or {}
        aids = sorted(a_ for a_, v in facts.items() if v.get("has_free"))
        if not aids:
            raise SystemExit("no rep-free aids in route0_facts_cache.json — run route0_updates.py")
        targets = {}
        for i in range(0, len(aids), 300):
            lst = ",".join("'" + str(x) + "'" for x in aids[i:i + 300])
            q = f"""SELECT ipo_identifier AS aid, name, kind, company_number,
                           nationality_code
                    FROM applicants WHERE ipo_identifier IN ({lst})"""
            try:
                for r in dm.temmy_runsql(cfg, q):
                    targets[str(r["aid"])] = {"name": r.get("name"), "kind": r.get("kind"),
                                              "company_number": (r.get("company_number") or "").strip(),
                                              "cc": r.get("nationality_code")}
            except Exception as e:
                print(f"  TemmyDB error building targets: {e}")
                break
        # postcodes for corroboration
        for i in range(0, len(aids), 300):
            lst = ",".join("'" + str(x) + "'" for x in aids[i:i + 300])
            q = f"""SELECT ap.ipo_identifier AS aid, ad.postcode, ad.country
                    FROM applicants ap
                    JOIN addresses ad ON ad.addressable_id = ap.id
                                     AND ad.addressable_type = 'Applicant'
                    WHERE ap.ipo_identifier IN ({lst})"""
            try:
                for r in dm.temmy_runsql(cfg, q):
                    if str(r["aid"]) in targets:
                        targets[str(r["aid"])]["postcode"] = r.get("postcode")
            except Exception:
                pass          # postcode is a bonus, not required
        state["targets"] = targets
        json.dump(state, open(STATE, "w"))
        print(f"built target list: {len(targets)} applicants")

    todo = [k for k in targets if k not in results]
    if a.limit:
        todo = todo[:a.limit]
    print(f"targets {len(targets)} · already done {len(results)} · to do {len(todo)}")

    n = 0
    for aid in todo:
        if (time.time() - t0) > a.max_seconds:
            break
        t = targets[aid]
        kind = (t.get("kind") or "").lower()
        rec = {"aid": aid, "applicant_name": t.get("name"), "kind": t.get("kind"),
               "checked": time.strftime("%Y-%m-%d")}

        if t.get("company_number"):
            p = profile(t["company_number"], key)
            if p:
                rec.update({"company": p, "confidence": "high", "source": "temmydb company_number"})
                rec["officers"] = officers(p["company_number"], key)
            else:
                rec.update({"confidence": "none", "source": "company_number not found in CH"})
        elif "individual" in kind:
            if a.individuals_officer_search:
                q = urllib.parse.quote((t.get("name") or "")[:100])
                d = ch(f"/search/officers?q={q}&items_per_page=3", key)
                items = (d or {}).get("items") or []
                rec.update({"confidence": "low", "source": "officer-index name match",
                            "officer_candidates": [{"name": i.get("title"),
                                                    "address": (i.get("address_snippet"))}
                                                   for i in items[:3]]})
            else:
                rec.update({"confidence": "none", "source": "individual — CH company lookup N/A"})
        else:
            p, conf = search_company(t.get("name"), key, t.get("postcode"))
            if p:
                rec.update({"company": p, "confidence": conf, "source": "CH name search"})
                if conf in ("high", "medium"):
                    rec["officers"] = officers(p["company_number"], key)
            else:
                rec.update({"confidence": "none", "source": "CH name search — no match"})

        results[aid] = rec
        n += 1
        if n % 25 == 0:
            json.dump(results, open(OUT, "w"), indent=1)

    json.dump(results, open(OUT, "w"), indent=1)
    done = len(results)
    total = len(targets)
    by_conf = {}
    named = 0
    for r in results.values():
        by_conf[r.get("confidence", "?")] = by_conf.get(r.get("confidence", "?"), 0) + 1
        if r.get("officers"):
            named += 1
    print(f"processed {n} this run · {done}/{total} complete")
    print(f"confidence: {by_conf}")
    print(f"applicants with at least one named officer: {named}")
    print("CH_ENRICH COMPLETE" if done >= total else "CH_ENRICH INCOMPLETE — rerun")


if __name__ == "__main__":
    main()
