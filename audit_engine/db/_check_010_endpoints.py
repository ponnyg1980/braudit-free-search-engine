"""The sensitivity endpoints, end to end, against a fake store.

The database semantics — the clear-needs-a-reason constraint, one live row per
run, no duplicate versions — are proven separately and directly against the
real table in a rolled-back transaction (db/_check_010_sql.py). This file is
the other half: that the HTTP layer refuses what it should, measures what it
claims to measure, and records the event log in the order it says it does.

The fake store reproduces the SQL's supersede-and-insert semantics rather than
just appending, so a bug in the endpoint's use of it shows up here rather than
on the droplet.
"""
import json
import os
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]   # temmy-access
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AUDIT_DB_URL", "postgresql://unused/unused")
os.environ.setdefault("AUDIT_STAFF_TOKEN", "test-token")

import audit_engine._paths  # noqa: E402,F401

TOK = None
for line in (ROOT / "secrets.env").read_text().splitlines():
    m = re.match(r"\s*SUPABASE_ACCESS_TOKEN\s*=\s*(.+)", line)
    if m:
        TOK = m.group(1).strip().strip("'\"")


def q(sql):
    r = urllib.request.Request(
        "https://api.supabase.com/v1/projects/nwsttrpnoygthbgcadmr/database/query",
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json",
                 "User-Agent": "tmh-endpoint-check"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


RUN = q("select id from audit.runs where id::text like '8ccac5ee%'")[0]["id"]
ROWS = q(f"""select id, channel, title, url, status, classes, goods, dates, detail,
                    band, score, components
               from audit.results where run_id = '{RUN}' order by id""")
RUNROW = q(f"select * from audit.runs where id = '{RUN}'")[0]


class FakeStore:
    """Mirrors the SQL's semantics, not just its interface."""

    def __init__(self):
        self.events = []

    def run(self, run_id):
        return RUNROW if str(run_id) == str(RUN) else None

    def results(self, run_id):
        return ROWS

    def ignored_words(self, run_id):
        return []

    def sensitivity(self, run_id):
        live = [e for e in self.events if e["superseded_at"] is None]
        if not live or not live[0]["steps"]:
            return None
        return live[0]

    def sensitivity_history(self, run_id):
        return sorted(self.events, key=lambda e: -e["version"])

    def save_sensitivity(self, run_id, steps, by, comparison=None, reason=None,
                         action=None):
        live = next((e for e in self.events if e["superseded_at"] is None), None)
        prior = (live or {}).get("steps") or {}
        version = int((live or {}).get("version") or 0) + 1
        if action is None:
            action = "cleared" if not steps else ("amended" if live else "applied")
        if action == "cleared" and len((reason or "").strip()) < 10:
            raise AssertionError("the database would have refused this")
        if action == "cleared" and steps:
            raise AssertionError("the database would have refused this")
        if live:
            live["superseded_at"] = "now"
        cmp_ = comparison or {}
        e = {"id": f"fake-{version}", "version": version, "action": action,
             "steps": dict(steps), "prior_steps": prior,
             "deltas": cmp_.get("deltas") or {}, "rows_moved": cmp_.get("rows_moved"),
             "moved_up": cmp_.get("moved_up"), "moved_down": cmp_.get("moved_down"),
             "band_before": cmp_.get("band_before"), "band_after": cmp_.get("band_after"),
             "reason": reason, "created_by": by, "created_at": "2026-09-21T12:00:00",
             "scoring_version": cmp_.get("scoring_version"), "superseded_at": None}
        self.events.insert(0, e)
        return {"id": e["id"], "version": version, "action": action,
                "steps": dict(steps), "prior_steps": prior}


from audit_engine import webapp  # noqa: E402

STORE = FakeStore()
webapp._store = lambda: STORE
webapp.app.dependency_overrides[webapp.staff] = lambda: {"name": "tester"}

from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(webapp.app)
U = f"/api/runs/{RUN}/sensitivity"

fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        fails.append(name)


# --- preview ---------------------------------------------------------------
r = c.post(U + "/preview", json={"steps": {"fuzzy": 1}})
d = r.json()
check("preview returns a measured consequence",
      r.status_code == 200 and d["rows_moved"] > 0 and d["rows"] == len(ROWS),
      f"{r.status_code} {str(d)[:160]}")
check("preview stores nothing", STORE.events == [])
check("preview explains itself in words", bool(d.get("described")), str(d)[:120])

r = c.post(U + "/preview", json={"steps": {"fuzzy": -2}})
d2 = r.json()
check("preview surfaces rows that would leave the report",
      d2["dropped"] > 0, str(d2)[:160])

# --- validation ------------------------------------------------------------
check("unknown compartment is refused",
      c.post(U, json={"steps": {"vibes": 1}}).status_code == 400)
check("out-of-range step is refused",
      c.post(U, json={"steps": {"fuzzy": 9}}).status_code == 400)
check("a step a compartment does not offer is refused",
      c.post(U, json={"steps": {"classes": 2}}).status_code == 400)
check("steps that are not an object are refused",
      c.post(U, json={"steps": "loose"}).status_code == 400)
r = c.post(U, json={"steps": {}})
check("apply with nothing set points at Clear instead",
      r.status_code == 400 and "DELETE" in r.json()["detail"], str(r.json())[:140])
check("a run that does not exist is a 404",
      c.post("/api/runs/00000000-0000-0000-0000-000000000000/sensitivity",
             json={"steps": {"fuzzy": 1}}).status_code == 404)

# --- clear before anything is applied --------------------------------------
check("clearing a standard run is refused as a conflict",
      c.request("DELETE", U, json={"reason": "a perfectly good reason"}).status_code == 409)

# --- apply / amend / clear -------------------------------------------------
r = c.post(U, json={"steps": {"fuzzy": 1}, "reason": "client sells near-identical goods"})
d = r.json()
check("apply is accepted and versioned",
      r.status_code == 200 and d["version"] == 1 and d["action"] == "applied",
      str(d)[:160])
check("apply records the measured consequence, not the browser's claim",
      d["comparison"]["rows_moved"] > 0)
check("the run now reads as non-standard", STORE.sensitivity(RUN) is not None)

r = c.post(U, json={"steps": {"fuzzy": 2, "terms": -1}, "reason": "still too quiet"})
d = r.json()
check("amend is a new version, not an update",
      d["version"] == 2 and d["action"] == "amended" and len(STORE.events) == 2,
      str(d)[:160])
check("amend carries what it replaced", d["prior_steps"] == {"fuzzy": 1}, str(d)[:160])

check("clear without a reason is refused",
      c.request("DELETE", U, json={}).status_code == 400)
check("clear with a token reason is refused",
      c.request("DELETE", U, json={"reason": "no"}).status_code == 400)

r = c.request("DELETE", U, json={"reason": "client disputed the extra rows on the call"})
d = r.json()
check("clear is accepted, versioned and empty",
      r.status_code == 200 and d["version"] == 3 and d["action"] == "cleared"
      and d["steps"] == {}, str(d)[:160])
check("clear keeps what was in force, so it can be put back",
      d["prior_steps"] == {"fuzzy": 2, "terms": -1}, str(d)[:160])
check("clear reports what it cost", d["comparison"]["rows_moved"] > 0)
check("the run reads as standard again", STORE.sensitivity(RUN) is None)
check("nothing was deleted — three events remain",
      len(STORE.sensitivity_history(RUN)) == 3)

# --- the panel payload -----------------------------------------------------
settings = webapp._search_score_settings(STORE, RUNROW, RUN)
sens = settings["sensitivity"]
check("the panel is told the controls by the package, not by the app",
      len(sens["controls"]) == 6, str([x["compartment"] for x in sens["controls"]]))
check("the image compartment is offered but disabled",
      [x for x in sens["controls"] if x["compartment"] == "visual"][0]["enabled"] is False)
check("classes shows four positions, not five",
      len([x for x in sens["controls"] if x["compartment"] == "classes"][0]["steps"]) == 4)
check("the panel gets the history", len(sens["history"]) == 3)

# --- the lens over the payload --------------------------------------------
import copy  # noqa: E402

rows = copy.deepcopy(ROWS)
check("no lens when the run is at standard",
      webapp._apply_lens(STORE, RUN, rows) is None)
check("a standard run's bands are untouched",
      all("band_standard" not in r for r in rows))

c.post(U, json={"steps": {"fuzzy": -2}, "reason": "client only wants close calls"})
rows = copy.deepcopy(ROWS)
lens = webapp._apply_lens(STORE, RUN, rows)
check("the lens reports itself", bool(lens) and lens.get("steps") == {"fuzzy": -2},
      str(lens)[:140])
check("the lens moved rows", lens["rows_moved"] > 0, str(lens)[:140])
check("every row carries what it was scored as",
      all("band_standard" in r for r in rows))
check("the stored rows are not touched",
      all("band_standard" not in r for r in ROWS))
moved = [r for r in rows if r["band"] != r["band_standard"]]
check("moved rows equal the count the lens reported", len(moved) == lens["rows_moved"])
dropped = [r for r in rows if r["band"] == "Not a finding at this sensitivity"]
check("rows that stop being findings are labelled, not removed",
      len(dropped) > 0 and len(rows) == len(ROWS),
      f"{len(dropped)} labelled, {len(rows)} rows")
check("bands on screen are readable band strings",
      all(isinstance(r["band"], str) and r["band"] for r in rows))

c.request("DELETE", U, json={"reason": "reverting for the record check"})
rows = copy.deepcopy(ROWS)
check("clearing removes the lens", webapp._apply_lens(STORE, RUN, rows) is None)


class BrokenStore(FakeStore):
    def sensitivity(self, run_id):
        raise RuntimeError("database unavailable")


check("a lens that cannot be applied is reported, not swallowed",
      (webapp._apply_lens(BrokenStore(), RUN, copy.deepcopy(ROWS)) or {}).get("error"))

print(f"\nFAILURES: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
