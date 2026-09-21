"""Exercise rescore.compare() against a real stored run, read-only.

The local machine has no AUDIT_DB_URL (that lives on the droplet), so this
stands a minimal read-only Store in front of the Supabase Management API. It
touches only `run()` and `results()`, which is all rescore needs, and it
cannot write: _supabase_query refuses anything that is not a select.
"""
import json
import pathlib
import re
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[1] if (HERE.parents[1] / "audit_engine").exists() else pathlib.Path(".")
sys.path.insert(0, str(ROOT))
import audit_engine._paths  # noqa: E402,F401
from audit_engine import rescore  # noqa: E402
from tmh_scoring.sensitivity import AVAILABLE_STEPS  # noqa: E402

TOK = None
for line in (ROOT / "secrets.env").read_text().splitlines():
    m = re.match(r"\s*SUPABASE_ACCESS_TOKEN\s*=\s*(.+)", line)
    if m:
        TOK = m.group(1).strip().strip("'\"")


def q(sql):
    if not re.match(r"(?is)^\s*(select|with)\b", sql):
        raise SystemExit("read-only queries only")
    r = urllib.request.Request(
        "https://api.supabase.com/v1/projects/nwsttrpnoygthbgcadmr/database/query",
        data=json.dumps({"query": sql}).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json",
                 "User-Agent": "tmh-rescore-check"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


class ReadOnlyStore:
    def __init__(self, run_id):
        self._run = q(f"select * from audit.runs where id::text like '{run_id}%'")[0]
        self._rows = q(f"""select id, channel, title, url, status, classes, goods,
                                  dates, detail, band, score, components
                             from audit.results where run_id = '{self._run['id']}' order by id""")

    def run(self, run_id):
        return self._run

    def results(self, run_id):
        return self._rows


rid = sys.argv[1] if len(sys.argv) > 1 else "8ccac5ee"
st = ReadOnlyStore(rid)
print(f"run {st._run['id']}  mark={st._run.get('mark_text')!r}  "
      f"rows={len(st._rows)}  scored_by={st._run.get('scoring_version')}")

t0 = time.time()
base = rescore.compare(st, st._run["id"], {})
print(f"\nstandard re-score took {time.time()-t0:.2f}s over {base['rows']} rows")
print(f"  bands now       : {base['band_before']}")
print(f"  drift vs stored : {base['drift_from_stored']} rows")

for comp, steps in AVAILABLE_STEPS.items():
    for step in steps:
        if step == 0:
            continue
        c = rescore.compare(st, st._run["id"], {comp: step})
        if not c["rows_moved"]:
            continue
        print(f"\n{comp} {step:+d}: {c['rows_moved']} moved "
              f"(up {c['moved_up']}, down {c['moved_down']})")
        print(f"   before {c['band_before']}")
        print(f"   after  {c['band_after']}")
        for m in c["moved"][:3]:
            print(f"     {m['from']} -> {m['to']}")

combo = rescore.compare(st, st._run["id"], {"fuzzy": 1, "terms": -1})
print(f"\ncombined fuzzy+1 / terms-1: {combo['rows_moved']} moved "
      f"(up {combo['moved_up']}, down {combo['moved_down']})")
for line in combo["described"]:
    print("   -", line)
print("   deltas:", combo["deltas"])
