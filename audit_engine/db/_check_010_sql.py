"""Migration 010's constraints, tested against the real table.

Every case runs inside `begin; ... rollback;` through the Supabase Management
API, so nothing is left behind — the last line asserts that.

These are the rules the app must not be the only thing enforcing. The app
checks that a clear carries a reason; so does the table, because an endpoint
can be called differently tomorrow and a constraint cannot.
"""
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]

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
                 "User-Agent": "tmh-sql-check"})
    try:
        return "OK", json.loads(urllib.request.urlopen(r, timeout=90).read())
    except urllib.error.HTTPError as e:
        return "ERR", e.read().decode()[:200]


RID = q("select id from audit.runs where id::text like '8ccac5ee%'")[1][0]["id"]


def ins(**kw):
    cols = ", ".join(kw)
    vals = ", ".join("null" if v is None else f"'{v}'" for v in kw.values())
    return (f"insert into audit.run_sensitivity (run_id, {cols}) "
            f"values ('{RID}', {vals})")


CASES = [
    (False, "a clear with no reason",
     ins(version=1, action="cleared", steps="{}", created_by="t")),
    (False, "a clear with a token reason",
     ins(version=1, action="cleared", steps="{}", reason="why", created_by="t")),
    (False, "a clear that still carries steps",
     ins(version=1, action="cleared", steps='{"fuzzy":1}',
         reason="a proper reason here", created_by="t")),
    (False, "an action outside applied/amended/cleared",
     ins(version=1, action="tweaked", steps="{}", created_by="t")),
    (True, "a valid apply",
     ins(version=1, action="applied", steps='{"fuzzy":1}', created_by="t")),
    (False, "two rows in force for one run",
     ins(version=1, action="applied", steps='{"fuzzy":1}', created_by="t")
     + f", ('{RID}', 2, 'amended', '{{\"fuzzy\":2}}', 't')"),
    (True, "a superseded row beside a live one",
     ins(version=1, action="applied", steps='{"fuzzy":1}', created_by="t",
         superseded_at="2026-09-21T10:00:00Z") + "; "
     + ins(version=2, action="amended", steps='{"fuzzy":2}', created_by="t")),
    (False, "a repeated version number",
     ins(version=1, action="applied", steps="{}", created_by="t",
         superseded_at="2026-09-21T10:00:00Z") + "; "
     + ins(version=1, action="amended", steps='{"fuzzy":2}', created_by="t")),
    (True, "a valid clear",
     ins(version=1, action="cleared", steps="{}",
         reason="client disputed the extra rows", created_by="t")),
    (False, "a run id that does not exist",
     "insert into audit.run_sensitivity (run_id, version, action, steps, created_by) "
     "values ('00000000-0000-0000-0000-000000000000', 1, 'applied', '{}', 't')"),
]

fails = []
for want_ok, name, sql in CASES:
    state, _ = q(f"begin; {sql}; rollback;")
    ok = (state == "OK") == want_ok
    print(("PASS  " if ok else "FAIL  ") + name + f"   -> {state}")
    if not ok:
        fails.append(name)

left = q("select count(*) n from audit.run_sensitivity")[1][0]["n"]
print(f"\nrows left behind: {left}")
if left:
    fails.append("a case escaped its rollback")
print(f"FAILURES: {len(fails)}" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
