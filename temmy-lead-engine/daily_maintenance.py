#!/usr/bin/env python3
"""
Temmy Lead Engine — daily maintenance (DETERMINISTIC, no LLM / zero tokens).
1) status monitor: read each prospect's Current TM Number from Cerebrum, query TemmyDB for the live
   status, and push any change back to the Cerebrum field + tag (fires the next workflow).
2) opt-out sync: any prospect flagged DND (opted out) in Cerebrum -> tag 'opted out' + add to suppression
   list so the matching pipeline never re-processes them.
Run via launchd/cron.  Flags: --dry-run  |  --env <path>
"""
import sys, os, re, json, time, urllib.request, urllib.error, ghl_push as g
HERE=os.path.dirname(os.path.abspath(__file__)); UK=re.compile(r'^UK\d{11}$')
def load(env): return g.load_env(env)
def temmy_runsql(cfg, sql):
    base=cfg["TEMMY_API_BASE_URL"]; qr=cfg["TEMMY_QUERY_RUNS_API_KEY"]
    r=urllib.request.Request(base+"/api/v2/query-runs",data=json.dumps({"sql":sql}).encode(),
        headers={"Content-Type":"application/json","X-Query-Runs-Key":qr},method="POST")
    d=json.loads(urllib.request.urlopen(r,timeout=90).read().decode()); rid=d["query_id"]; tp=(d.get("pagination") or {}).get("total_pages",1)
    rows=[]
    for p in range(1,tp+1):
        rr=urllib.request.Request(f"{base}/api/v2/query-runs/{rid}/pages/{p}",headers={"X-Query-Runs-Key":qr})
        rows+=(json.loads(urllib.request.urlopen(rr,timeout=90).read().decode()).get("items") or [])
    return rows
def chunks(x,n):
    for i in range(0,len(x),n): yield x[i:i+n]

def main():
    dry="--dry-run" in sys.argv
    env=sys.argv[sys.argv.index("--env")+1] if "--env" in sys.argv else None
    cfg=g.load_env(g.find_env(env))
    key=cfg["CEREBRUM_API_KEY"]; loc=cfg["CEREBRUM_LOCATION_ID"]; base=cfg.get("CEREBRUM_API_BASE",g.DEFAULT_BASE)
    byid,idtype,fields=g.get_field_map(base,key,loc)
    n2id={g.norm(f['name']):f['id'] for f in fields}; idname={f['id']:f['name'] for f in fields}
    F_TM=n2id.get(g.norm("Current TM Number")); F_ST=n2id.get(g.norm("Current TM Status")); F_LU=n2id.get(g.norm("Last Temmy Update"))
    F_AID=n2id.get(g.norm("IPO Applicant ID"))  # v3 (2026-07-24): applicant-level suppression join key
    # 1. collect prospects (search returns tags + customFields + dnd)
    prospects=[]
    def sit():
        page=1
        while True:
            d=g.api(base,key,"POST","/contacts/search",body={"locationId":loc,"page":page,"pageLimit":100,
                  "filters":[{"field":"tags","operator":"contains","value":"temmy prospect"}]})
            cs=d.get("contacts") or []
            if not cs: break
            for c in cs: yield c
            if len(cs)<100: break
            page+=1
    for c in sit():
        cf={f.get("id"):(f.get("value") or "") for f in (c.get("customFields") or [])}
        prospects.append({"id":c.get("id"),"tags":[t for t in (c.get("tags") or [])],
                          "tm":(cf.get(F_TM) or "").upper(),"status":cf.get(F_ST) or "",
                          "dnd":bool(c.get("dnd")),"aid":str(cf.get(F_AID) or "").strip()})
    tms=sorted({p["tm"] for p in prospects if UK.match(p["tm"])})
    # 2. live status from TemmyDB
    live={}
    for ch in chunks(tms,500):
        inlist=",".join("'"+t+"'" for t in ch)
        for r in temmy_runsql(cfg, f"SELECT t.application_number app, t.status st FROM trademarks t WHERE t.application_number IN ({inlist})"):
            live[(r['app'] or '').upper()]=r['st']
    changes=[{"id":p["id"],"tm":p["tm"],"old":p["status"],"new":live[p["tm"]],
              "old_tags":[t for t in p["tags"] if t.lower().startswith("current tm status: ")]}
             for p in prospects if p["tm"] in live and live[p["tm"]] and live[p["tm"]].lower()!=p["status"].lower()]
    optouts=[p["id"] for p in prospects if p["dnd"] and "opted out" not in [t.lower() for t in p["tags"]]]
    # ── v3 (2026-07-24): APPLICANT-LEVEL SUPPRESSION ──────────────────────────────
    # Rule (MASTER_CONTROL §2.3): one opt-out closes the WHOLE applicant. Signals:
    #  (a) any contact of an aid has DND or a suppression tag (do-not-contact / opted out /
    #      tmh account: closed / tmh account: do not call), OR
    #  (b) an LLM route queued an aid in suppression_pending.json (Zoho account Closed /
    #      Do Not Call, director request, Accounts-data evidence).
    # Fan-out: EVERY contact sharing the aid gets DND + tags; searched_log entry frozen.
    SUPP_TAGS={"do-not-contact","opted out","tmh account: closed","tmh account: do not call"}
    pend_f=f"{HERE}/suppression_pending.json"; app_f=f"{HERE}/suppression_applicants.json"
    pending=json.load(open(pend_f)) if os.path.exists(pend_f) else []
    appsupp=json.load(open(app_f)) if os.path.exists(app_f) else {}
    today=time.strftime("%Y-%m-%d")
    by_aid={}
    for p in prospects:
        if p["aid"]: by_aid.setdefault(p["aid"],[]).append(p)
    for p in prospects:  # signal (a)
        if p["aid"] and p["aid"] not in appsupp and (p["dnd"] or SUPP_TAGS & {t.lower() for t in p["tags"]}):
            appsupp[p["aid"]]={"reason":"contact opt-out (fan-out from contact "+p["id"]+")","source":"cerebrum","date":today}
    for e in pending:    # signal (b)
        aid=str(e.get("aid") or "").strip()
        if aid and aid not in appsupp:
            appsupp[aid]={"reason":e.get("reason","account closed / do not call"),"source":e.get("source","zoho"),"date":e.get("date",today)}
    fanout=[p for aid in appsupp for p in by_aid.get(aid,[])
            if not (p["dnd"] and "do-not-contact" in {t.lower() for t in p["tags"]})]
    print(f"prospects: {len(prospects)} | tm numbers checked: {len(tms)} | STATUS CHANGES: {len(changes)} | OPT-OUTS: {len(optouts)} | APPLICANTS SUPPRESSED: {len(appsupp)} | FAN-OUT CONTACTS TO CLOSE: {len(fanout)}"
          + (f" | NO 'IPO Applicant ID' FIELD FOUND — fan-out inert" if not F_AID else ""))
    for c in changes[:15]: print(f"  status change {c['tm']}: '{c['old']}' -> '{c['new']}'")
    today=time.strftime("%Y-%m-%d")
    if dry:
        print("DRY-RUN: nothing written"); return
    budget=float(sys.argv[sys.argv.index("--max-seconds")+1]) if "--max-seconds" in sys.argv else 3600.0
    donef=f"{HERE}/maint_applied.json"
    done=set(json.load(open(donef))) if os.path.exists(donef) else set()
    t0=time.time(); applied=0
    for c in changes:
        if c["id"] in done: continue
        g.api(base,key,"PUT",f"/contacts/{c['id']}",body={"customFields":[{"id":F_ST,"value":c["new"]},{"id":F_LU,"value":today}]})
        if c["old_tags"]: g.api(base,key,"DELETE",f"/contacts/{c['id']}/tags",body={"tags":c["old_tags"]})
        # v2 (2026-07-20): status lives in the custom field ONLY — no status tag re-added.
        # Legacy `current tm status:` tags are deleted above as contacts churn through.
        done.add(c["id"]); applied+=1
        if applied%5==0: json.dump(sorted(done),open(donef,"w"))
        if time.time()-t0>budget:
            json.dump(sorted(done),open(donef,"w")); print(f"budget hit — applied {applied} this run; re-run to continue."); return
    supp=set(json.load(open(f"{HERE}/suppression.json"))) if os.path.exists(f"{HERE}/suppression.json") else set()
    for cid in optouts:
        g.api(base,key,"POST",f"/contacts/{cid}/tags",body={"tags":["opted out"]}); supp.add(cid)
    # v3: applicant fan-out — close every sibling contact of a suppressed aid
    fanned=0
    for p in fanout:
        try:
            if not p["dnd"]:
                g.api(base,key,"PUT",f"/contacts/{p['id']}",body={"dnd":True})
            g.api(base,key,"POST",f"/contacts/{p['id']}/tags",body={"tags":["do-not-contact","opted out"]})
            supp.add(p["id"]); fanned+=1
        except Exception as ex:
            print(f"  fan-out FAILED for contact {p['id']} (aid {p['aid']}): {ex} — will retry next run")
    # freeze suppressed applicants in searched_log (never re-search, never requeue)
    slog_f=f"{HERE}/searched_log.json"
    try:
        slog=json.load(open(slog_f))
    except Exception:
        slog=None
    if isinstance(slog,dict):
        touched=False
        for aid in appsupp:
            rec=slog.get(aid)
            if isinstance(rec,dict) and rec.get("outcome")!="suppressed":
                rec.update({"outcome":"suppressed","requeue":False,"requeue_after":None,
                            "suppressed_date":appsupp[aid].get("date",today)}); touched=True
        if touched: json.dump(slog,open(slog_f,"w"))
    json.dump(appsupp,open(app_f,"w"),indent=1)
    if pending: json.dump([],open(pend_f,"w"))  # consumed — record lives in suppression_applicants.json
    json.dump(sorted(supp),open(f"{HERE}/suppression.json","w"))
    if os.path.exists(donef): os.remove(donef)
    print(f"applied {applied} status pushes, {len(optouts)} opt-outs suppressed, "
          f"{len(appsupp)} applicants suppressed, {fanned} sibling contacts closed by fan-out")

if __name__=="__main__": main()
