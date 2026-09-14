#!/usr/bin/env python3
"""The fortnightly job, start to finish, in one resumable orchestrator.

Stage order matters and is not arbitrary:

  1 sync_reviews    pull client/staff decisions OUT of Zoho first, so the
                    reports we are about to render already reflect what the
                    client said last time (ignored results stay ignored).
  2 refresh_supress refresh the Supress map that drives the client-facing
                    Monitoring column - a stale map mislabels cover.
  3 search          the delta run against the UK register.
  4 contact_status  correct Own/Parent/No Contact for this run's accounts
                    BEFORE recipients are resolved.
  5 render          build the HTML reports.
  6 publish         issue fresh 30-day tokens, upload, rotate old links.
  7 results         push Monitoring_Results to Zoho (trigger: []).
  8 reports         build + push Monitoring_Report records. THIS IS THE SEND:
                    the Zoho workflow fires on record create. Runs last so
                    nothing is emailed until the report it links to is live.
  9 schedules       write Latest_Run / Next_Run back to Monitoring_Schedules.

Every stage is idempotent and the state file means a stage that dies part
way (sandbox call limits, API hiccups) resumes rather than repeating work.

    python3 fortnightly.py --run-id <id> --enrolled [--cap 50] [--dry-run]
    python3 fortnightly.py --run-id <id> --from reports --accounts <id> --release
    python3 fortnightly.py --run-id <id> [--accounts a,b] [--dry-run]
    python3 fortnightly.py --run-id <id> --status
    python3 fortnightly.py --run-id <id> --from render   # re-enter a stage

--enrolled takes the account list from the Monitoring_Schedules register
(see enrol.py) instead of the command line: everything whose slot is due,
plus up to --cap accounts that are enrolled but have never had a report,
in the ramp order set by ramp_priority (dead marks first). That cap is the
onboarding ramp - 500 enrolled at 50 a day reaches everyone in ten days,
and after that each account sits on its own two days of the month, a
fortnight apart (enrol._next_send).

An account's FIRST report is withheld for a client-details check and a task
is raised instead; --release re-runs the reports stage and lets it out.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STAGES = ["sync_reviews", "refresh_supress", "search", "contact_status",
          "render", "publish", "results", "reports", "schedules"]
BUDGET = 135          # seconds per invocation; the sandbox kills us at ~170


def _state_path(run_id: str) -> Path:
    return HERE / "_work" / f"fortnightly_{run_id}.json"


def _load(run_id: str) -> dict:
    p = _state_path(run_id)
    return json.loads(p.read_text()) if p.exists() else {"done": [], "notes": {}}


def _save(run_id: str, st: dict) -> None:
    p = _state_path(run_id)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(st, indent=2))


# --------------------------------------------------------------------------
FIRST_REPORT_CAP = 50       # onboarding ramp: first reports per calendar day
RELEASE_FIRST = False       # --release: let held first reports go out


def select_accounts(cap: int = FIRST_REPORT_CAP, log=print) -> list[str]:
    """Which enrolled accounts this run should cover.

    The register is Monitoring_Schedules (enrol.py). Two groups:

      DUE      already had a first report (a Monitoring_Report record
               exists for them) and their slot on the fortnightly wheel has
               come round. Never capped - a client on the service gets their
               report on their day.
      NEW      enrolled, never had a report. Capped at `cap` per calendar
               day, so enrolling 500 clients does not email 500 clients
               tomorrow, and ordered by ramp_priority: dead marks first,
               then expired, renewals inside a year, applications in flight,
               then the rest. Longest-waiting first inside each band.

    "Never had a report" is read from Monitoring_Reports, NOT from the
    schedule's Last_Run_At: enrolment stamps Last_Run_At, so that test put
    every enrolled account in DUE and the cap never engaged.

    Being enrolled is not the same as being reachable: load_clients still
    drops accounts whose Account switch is Disabled, and the send stage
    still drops accounts with no contact anywhere."""
    import enrol
    import ramp_priority
    live = enrol.live_accounts(log=lambda *a: None)
    today = date.today().isoformat()
    had_report = ramp_priority.first_report_accounts()

    due, new, waiting = [], [], {}
    for aid, rec in live.items():
        if aid not in had_report:
            new.append(aid)
            waiting[aid] = rec.get("History_Last_Synced_At") or ""
        elif (rec.get("Next_Run_At") or "")[:10] <= today:
            due.append(aid)
    ranked = ramp_priority.rank(new, waiting)
    taking = [aid for aid, _ in ranked[:max(cap, 0)]]
    bands = {}
    for aid, band in ranked[:max(cap, 0)]:
        bands[band] = bands.get(band, 0) + 1
    log(f"    enrolled {len(live)}: {len(due)} due today, "
        f"{len(new)} awaiting a first report, taking {len(taking)} "
        f"(cap {cap})")
    if taking:
        log("    ramp order: " + ", ".join(
            f"{n} {ramp_priority.BANDS[b]}" for b, n in sorted(bands.items())))
    return due + taking


def _accounts_for(run_id: str, explicit: list[str] | None) -> list[str]:
    if explicit:
        return explicit
    from ledger import Ledger
    led = Ledger()
    try:
        return [r["client_account_id"] for r in led.conn.execute(
            "select client_account_id from run_clients "
            "where run_id=? and status='rendered'", (run_id,))]
    finally:
        led.close()


def stage_sync_reviews(run_id, accounts, dry, log):
    import sync_reviews
    if dry:
        log("    (dry run) would pull review state from Zoho")
        return True
    return sync_reviews.sync(budget_seconds=100, log=log) is not None


def stage_refresh_supress(run_id, accounts, dry, log):
    """Refresh the Supress map AND the derived Monitoring_State in Zoho.

    Monitoring_State is read-only for staff and derived from two things that
    both move: the register status and Supress. If it is only ever written
    by hand it goes stale, and staff read a stale answer to "is this being
    watched". Recomputed every run, and a mark that has since been refused
    or cancelled is auto-suppressed at the same time."""
    import refresh_supress
    if dry:
        log("    (dry run) would refresh Supress + Monitoring_State")
        return True
    refresh_supress.main()
    try:
        import mark_lines, monitoring_state
        if mark_lines.pull(budget_seconds=60, log=lambda *a: None):
            states, suppress, tally = monitoring_state.plan(log=log)
            monitoring_state._push("Trademark", states, f"state_{run_id}", log=log)
            monitoring_state._push("Trademark", suppress, f"suppress_{run_id}",
                                   log=log)
            log(f"    monitoring state: {dict(tally)}")
        else:
            log("    mark cache incomplete - Monitoring_State left for the "
                "next pass")
    except Exception as e:                       # never block the run
        log(f"    ! monitoring state skipped ({str(e)[:90]})")
    return True


def stage_search(run_id, accounts, dry, log):
    import runner
    if dry:
        log(f"    (dry run) would search for {len(accounts) or 'all'} accounts")
        return True
    runner.run(account_ids=accounts or None, run_id=run_id, log=log)
    return True


def stage_contact_status(run_id, accounts, dry, log):
    from clients import refresh_contact_status
    if dry:
        log("    (dry run) would correct Contact_Status")
        return True
    refresh_contact_status(_accounts_for(run_id, accounts), log=log)
    return True


def stage_render(run_id, accounts, dry, log):
    from clients import load_clients
    from ledger import Ledger
    import report as report_mod
    ids = _accounts_for(run_id, accounts)
    if dry:
        log(f"    (dry run) would render {len(ids)} reports")
        return True
    st = _load(run_id)
    done = set(st["notes"].get("rendered", []))
    led = Ledger()
    t0 = time.time()
    try:
        todo = [i for i in ids if i not in done]
        for c in load_clients(account_ids=todo, log=lambda *a: None):
            if time.time() - t0 > BUDGET:
                break
            report_mod.render_client_report(led, c, run_id, date.today(),
                                            HERE / "_out", portal_url="")
            done.add(c.account_id)
    finally:
        led.close()
    st["notes"]["rendered"] = sorted(done)
    _save(run_id, st)
    log(f"    rendered {len(done)}/{len(ids)}")
    return len(done) >= len(ids)


def stage_publish(run_id, accounts, dry, log):
    import publish_reports
    if dry:
        log("    (dry run) would publish + rotate links")
        return True
    publish_reports.publish(run_id, set(accounts) if accounts else None, log=log)
    return True


def stage_results(run_id, accounts, dry, log):
    import zoho_writer
    from ledger import Ledger
    led = Ledger()
    try:
        recs = zoho_writer.build_payloads(led, account_ids=accounts or None,
                                          run_id=run_id)
    finally:
        led.close()
    log(f"    {len(recs):,} result records")
    if dry:
        return True
    zoho_writer.emit_batches(recs, run_id)
    zoho_writer.push_batches_selfclient(run_id, log=log)
    return True


def stage_reports(run_id, accounts, dry, log):
    """THE SEND. Report records trigger the Zoho email workflow on create."""
    import zoho_writer
    from ledger import Ledger
    led = Ledger()
    try:
        recs = zoho_writer.build_report_records(led, run_id,
                                                account_ids=accounts or None)
    finally:
        led.close()
    # Report one is checked by a human before it goes (Jonathan, 14 Sep):
    # the gate is about the client information being right, not the findings.
    # Records are withheld rather than written with Should_Send false,
    # because the Zoho send rule runs on CREATE and a held record could
    # never be released. --release skips the gate for a re-run.
    if not RELEASE_FIRST:
        import ramp_priority
        had_report = ramp_priority.first_report_accounts()
        held = [r for r in recs
                if ((r.get("Client_Account") or {}).get("id") or "") not in had_report]
        if held:
            recs = [r for r in recs if r not in held]
            seen = set()
            for r in held:
                aid = (r.get("Client_Account") or {}).get("id") or ""
                if aid in seen:
                    continue
                seen.add(aid)
                log(f"      HELD (first report, needs a client-details check): "
                    f"{(r.get('Client_Account') or {}).get('name')}")
                if not dry:
                    ramp_priority.create_check_task(
                        aid, (r.get("Client_Account") or {}).get("name") or aid,
                        (r.get("Owner") or {}).get("id"), run_id,
                        r.get("Portal_Link") or "")
            log(f"    {len(seen)} account(s) held for a first-report check; "
                f"{len(held)} record(s) withheld, nothing emailed to them")
    sending = [r for r in recs if r.get("Should_Send")]
    log(f"    {len(recs)} report records, {len(sending)} marked to send")
    if dry:
        for r in sending[:5]:
            log(f"      would send: {r.get('Client_TM_Number')} -> "
                f"{r.get('Email')} ({r.get('Recipient_Via')})")
        return True
    import zoho_client
    ok = 0
    for i in range(0, len(recs), 100):
        for res in zoho_client.upsert("Monitoring_Reports", recs[i:i+100],
                                      duplicate_check_fields=["Report_ID"],
                                      trigger=["workflow"]):
            ok += res.get("code") == "SUCCESS"
    log(f"    pushed {ok}/{len(recs)}")
    return True


def stage_schedules(run_id, accounts, dry, log):
    import schedule_sync
    if dry:
        log("    (dry run) would update Monitoring_Schedules")
        return True
    schedule_sync.sync(run_id, log=log)
    return True


RUNNERS = {s: globals()[f"stage_{s}"] for s in STAGES}


def main():
    a = sys.argv
    if "--run-id" not in a:
        print(__doc__)
        return 1
    run_id = a[a.index("--run-id") + 1]
    accounts = (a[a.index("--accounts") + 1].split(",")
                if "--accounts" in a else None)
    dry = "--dry-run" in a
    global RELEASE_FIRST
    RELEASE_FIRST = "--release" in a
    st = _load(run_id)

    # --enrolled: take the account list from the register rather than the
    # command line, capping first reports. The chosen list is stored in the
    # run state, so every later stage of this run works on the same set even
    # if the register changes underneath it mid-run.
    if "--enrolled" in a and not accounts:
        cap = int(a[a.index("--cap") + 1]) if "--cap" in a else FIRST_REPORT_CAP
        accounts = st["notes"].get("accounts")
        if not accounts:
            accounts = select_accounts(cap)
            if not dry:
                st["notes"]["accounts"] = accounts
                _save(run_id, st)
        else:
            print(f"    resuming with the {len(accounts)} accounts "
                  f"chosen when this run started")
        if not accounts:
            print("  nothing due and nothing awaiting a first report - stop")
            return 0

    if "--status" in a:
        for s in STAGES:
            print(f"  {'DONE' if s in st['done'] else '    '}  {s}")
        return 0
    if "--from" in a:
        frm = a[a.index("--from") + 1]
        st["done"] = [s for s in st["done"] if STAGES.index(s) < STAGES.index(frm)]
        _save(run_id, st)

    print(f"fortnightly {run_id}{' (DRY RUN)' if dry else ''}")
    start = time.time()
    for s in STAGES:
        if s in st["done"]:
            continue
        if time.time() - start > BUDGET:
            print("  budget reached - run again to continue")
            break
        print(f"  [{s}]")
        try:
            finished = RUNNERS[s](run_id, accounts, dry, log=print)
        except Exception as e:                                # noqa: BLE001
            print(f"  ! {s} failed: {str(e)[:200]}")
            print("  stopping - fix, then re-run (stage will resume)")
            return 1
        if finished and not dry:
            st["done"].append(s)
            _save(run_id, st)
        elif not finished:
            print(f"  {s} incomplete - run again to continue")
            break
    else:
        print("  all stages complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
