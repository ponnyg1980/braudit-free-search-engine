"""Zoho writer — ledger results -> Monitoring_Results upsert payloads.

The ledger stays the system of record; Zoho gets a readable copy for staff
(boundary agreed 21 Aug: writes go to Monitoring_Results ONLY).

This module does not talk to Zoho itself. It builds exact API payloads and
writes them as replayable batch files; the transport is whichever channel is
holding OAuth (today: the Claude Zoho connector replays the batches; the
productionised fortnightly job will use a Zoho self-client, at which point
`push_batches_selfclient()` gets written against the same files).

Contract (ZOHO_MODULE_SPEC.md):
  * upsert on Result_ID (module field is unique, case-insensitive)
  * Risk_Band picklist caps at 25 chars: the ledger's
    "Review - client may be at risk" MUST map to "Review - may be at risk"
  * NEVER write Client_Action on update. It is excluded from the upsert
    payload entirely; the transport sets Client_Action="none" in a second
    call for records whose upsert action came back "insert".
  * Client_Is_Senior set explicitly on every record (picklist defaults do
    not persist via the API): 1 -> Yes, 0 -> No, NULL -> Unknown.
  * Name = "{cited mark} vs {client mark}".
  * Scoring/Threshold versions stamped on every record - a re-score must
    never silently rewrite history.
  * trigger: [] on every call - no workflows fire during bulk pushes.
    (The send workflow will live on the report object, not on results.)
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
from pathlib import Path

from config import OUT_DIR

MODULE = "Monitoring_Results"
BATCH = 100

# Both are derived from the loaded package, never typed by hand (18 Sep 2026).
# They used to read "uk_monitor/two-layer" and "bands-1.2.0" - neither of which
# had meant anything since the 17 Sep consolidation, and neither of which moved
# when the scoring did, so a stored band could not be traced to its scorer.
from _compat import SCORING_PACKAGE_VERSION            # noqa: E402

SCORING_VERSION = f"tmh_scoring/{SCORING_PACKAGE_VERSION}"
THRESHOLD_VERSION = f"bands/{SCORING_PACKAGE_VERSION}"

# Ledger band string -> Zoho picklist value (25-char cap).
SENIORITY_LEDGER = "Review - client may be at risk"
SENIORITY_ZOHO = "Review - may be at risk"
_BAND_MAP = {SENIORITY_LEDGER: SENIORITY_ZOHO}
_BAND_UNMAP = {SENIORITY_ZOHO: SENIORITY_LEDGER}

_TM_ID_CSV = Path(__file__).resolve().parent.parent / "zoho_trademarks.csv"


def band_to_zoho(band: str) -> str:
    return _BAND_MAP.get(band, band)


def _band_of(r) -> str:
    """Mirror of report._band_of - the single banding rule."""
    if (r["priority"] or "") == SENIORITY_LEDGER:
        return SENIORITY_LEDGER
    if not r["live"]:
        return "Result (not live)"
    p = r["priority"] or "Low"
    return p


def _zoho_trademark_ids() -> dict[str, str]:
    """UK number -> Zoho Trademark record id, from the client export."""
    out: dict[str, str] = {}
    with open(_TM_ID_CSV, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            num = (row.get("application_number") or "").strip().upper()
            zid = (row.get("zoho_trademark_id") or "").strip()
            if num and zid:
                out[num] = zid
    return out


def _senior(v) -> str:
    if v is None:
        return "Unknown"
    return "Yes" if int(v) else "No"


def build_payloads(led, account_ids: list[str] | None = None,
                   run_id: str | None = None,
                   include_suppressed: bool = True) -> list[dict]:
    """Ledger results -> list of Zoho Monitoring_Results records."""
    from explain import explain  # deterministic client-facing reason

    q = "select * from results"
    conds, params = [], []
    if account_ids:
        conds.append("client_account_id in (%s)"
                     % ",".join("?" * len(account_ids)))
        params += list(account_ids)
    if run_id:
        conds.append("(first_run_id = ? or last_run_id = ?)")
        params += [run_id, run_id]
    if conds:
        q += " where " + " and ".join(conds)
    rows = led.conn.execute(q, params).fetchall()

    marks = {(m["client_account_id"], m["client_tm_number"]): m
             for m in led.conn.execute("select * from client_marks")}
    tm_ids = _zoho_trademark_ids()

    records = []
    for r in rows:
        if r["suppressed_reason"] and not include_suppressed:
            continue
        m = marks.get((r["client_account_id"], r["client_tm_number"]))
        client_mark_text = (m["mark_text"] if m else "") or r["client_tm_number"]

        rec = {
            "Result_ID": r["result_id"],
            "Name": f"{(r['cited_mark_text'] or r['cited_tm_number'])[:55]}"
                    f" vs {client_mark_text[:55]}"[:120],
            "Client_TM_Number": r["client_tm_number"],
            "Cited_TM_Number": r["cited_tm_number"],
            "Cited_Mark": (r["cited_mark_text"] or "")[:255],
            "Cited_Proprietor": (r["cited_owner"] or "")[:255],
            "Cited_Status": r["cited_status"] or "",
            "Cited_Classes": (r["cited_classes"] or "")[:255],
            "Risk_Band": band_to_zoho(_band_of(r)),
            "Conflict": r["conflict"],
            "Rights_Strength": r["rights"],
            "Mark_Tier": r["mark_tier"],
            "Trade_Tier": r["trade_tier"],
            "Why_Flagged": explain(r),
            "Evidence_Level": (r["evidence_level"] or "")[:255],
            "Client_Is_Senior": _senior(r["client_is_senior"]),
            "Trading_Evidence": (r["trading_note"] or "")[:255],
            "First_Seen": r["first_seen_date"],
            "Last_Seen": r["last_seen_date"],
            "Times_Seen": r["times_seen"],
            "Run_ID": r["first_run_id"],
            "Suppressed_Reason": (r["suppressed_reason"] or "")[:255],
            "Scoring_Version": SCORING_VERSION,
            "Threshold_Version": THRESHOLD_VERSION,
            # Client_Action deliberately absent - see module docstring.
        }
        acct = (r["client_account_id"] or "").strip()
        if acct.isdigit():                       # real Zoho account id
            rec["Client_Account"] = {"id": acct}
        zid = tm_ids.get((r["client_tm_number"] or "").upper())
        if zid:
            rec["Client_Trademark"] = {"id": zid}
        records.append(rec)

    return records


def emit_batches(records: list[dict], label: str) -> Path:
    """Write replayable upsert batch files + manifest.

    Each batch_NNNN.json is the exact argument object for the Zoho
    upsertRecords call (body + path_variables).
    """
    out = OUT_DIR / "zoho_push" / label
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for i in range(0, len(records), BATCH):
        payload = {
            "body": {
                "data": records[i:i + BATCH],
                "duplicate_check_fields": ["Result_ID"],
                "trigger": [],
            },
            "path_variables": {"module": MODULE},
        }
        (out / f"batch_{n:04d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1))
        n += 1
    (out / "manifest.json").write_text(json.dumps({
        "module": MODULE, "records": len(records), "batches": n,
        "duplicate_check_fields": ["Result_ID"],
        "after_push": ("for each record whose upsert action == 'insert', "
                       "set Client_Action='none' via updateRecords (id list), "
                       "trigger: []. Never send Client_Action on updates."),
    }, indent=1))
    return out



# --------------------------------------------------------------------------
# Send-decision engine (developer brief, 22 Aug; decisions confirmed same
# day: threshold default Low+, consolidated digests / per-mark alerts,
# launch default Every run, staff-gated exclusions).
#
# Monitoring cadence never changes: a report record is created for EVERY
# trademark on EVERY completed run. The preference controls only whether
# that record sends an email now, and the record stores the policy and
# threshold AS APPLIED - historical send decisions are never recalculated.

BAND_RANK = {          # higher = more serious
    "Result (not live)": 0, "Low": 1, "Low/Medium": 2, "Medium": 3,
    "Medium/High": 4, "High": 5, SENIORITY_LEDGER: 6,
}
THRESHOLD_MIN_RANK = {
    "Low or higher": 1, "Low/medium or higher": 2, "Medium or higher": 3,
    "Medium/high or higher": 4, "High or higher": 5, "Review only": 6,
}

DEFAULT_PREFERENCE = {
    "notification_policy": "Every run",        # launch default, all clients
    "alert_threshold": "Low or higher",
    "immediate_alerts": True,
    "email_enabled": True,
    # 23 Aug: valid WhatsApp authorisation = results at/above this
    # threshold; default Medium/high+, client-adjustable (some want all).
    "whatsapp_threshold": "Medium/high or higher",
    "whatsapp_authorised": False,
}


def decide_send(results, run_id: str, preference: dict | None,
                first_report: bool) -> dict:
    """The brief's section-7 decision, for one trademark's results.

    A notifiable threat (v1) is a NEW live result at/above the client's
    threshold. "Materially changed" detection (band upgrades, status
    restorations, spec/proprietor changes) needs result history and is a
    recorded follow-up - the ledger keeps first/last_seen per result, so an
    unchanged finding can never re-alert, which is the binding half.
    """
    pref = {**DEFAULT_PREFERENCE, **(preference or {})}
    min_rank = THRESHOLD_MIN_RANK.get(pref["alert_threshold"], 1)

    # the account-level switch beats every preference: a disabled account
    # still gets its report RECORD (so staff can see what would have gone)
    # but nothing is ever sent from it.
    if pref.get("account_switch") == "Disabled":
        visible0 = [r for r in results if not r["suppressed_reason"]]
        bands0 = [_band_of(r) for r in visible0]
        return {"should_send": False,
                "reason": "monitoring reports disabled on the account",
                "report_type": "Fortnightly run",
                "highest_risk": (max(bands0, key=lambda b: BAND_RANK.get(b, 0))
                                 if bands0 else "None"),
                "whatsapp_due": False,
                "whatsapp_threshold": pref["whatsapp_threshold"]}

    visible = [r for r in results if not r["suppressed_reason"]]
    bands = [_band_of(r) for r in visible]
    highest = max(bands, key=lambda b: BAND_RANK.get(b, 0)) if bands else "None"
    threats = [r for r, b in zip(visible, bands)
               if r["first_run_id"] == run_id and r["live"]
               and BAND_RANK.get(b, 0) >= min_rank]

    policy = pref["notification_policy"]
    if not pref.get("email_enabled", True):
        should, reason, rtype = False, "Email disabled by preference", "Fortnightly run"
    elif first_report:
        should, reason, rtype = True, "First report always sends", "First report"
    elif threats and pref.get("immediate_alerts", True):
        should = True
        reason = (f"{len(threats)} new result(s) at/above threshold "
                  f"'{pref['alert_threshold']}'")
        rtype = "Threat alert" if policy != "Every run" else "Fortnightly run"
    elif policy == "Every run":
        should, reason, rtype = True, "Policy: every run", "Fortnightly run"
    elif policy in ("Monthly digest", "Quarterly digest",
                    "Quarterly plus alerts"):
        should, reason, rtype = False, f"Held for {policy.lower()}", "Fortnightly run"
    else:  # Threats only
        should, reason, rtype = False, "No new result met threshold", "Fortnightly run"

    wa_min = THRESHOLD_MIN_RANK.get(pref["whatsapp_threshold"], 4)
    wa_due = bool(pref.get("whatsapp_authorised")) and any(
        r["first_run_id"] == run_id and r["live"]
        and BAND_RANK.get(b, 0) >= wa_min
        for r, b in zip(visible, bands))
    return {"should_send": should, "reason": reason, "report_type": rtype,
            "highest_risk": band_to_zoho(highest) if highest != "None" else "None",
            "policy": policy, "threshold": pref["alert_threshold"],
            "threat_count": len(threats),
            "whatsapp_threshold": pref["whatsapp_threshold"],
            "whatsapp_alert_due": wa_due}


# --------------------------------------------------------------------------
# Monitoring Reports - the send vehicle. One record per trademark per run;
# the Zoho workflow rule fires on record creation and merges these fields
# into the notification email. Counts follow report._bucket exactly.

REPORTS_MODULE = "Monitoring_Reports"

_COUNT_FIELDS = {
    SENIORITY_LEDGER: "Count_Review",
    "High": "Count_High",
    "Medium/High": "Count_Medium_High",
    "Medium": "Count_Medium",
    "Low/Medium": "Count_Low_Medium",
    "Low": "Count_Low",
    "Result (not live)": "Count_Cleared",
}
_ATTENTION_BANDS = {SENIORITY_LEDGER, "High", "Medium/High", "Medium"}


def _now_tz() -> str:
    """Zoho datetime, Europe/London."""
    from datetime import datetime as _dt
    return _dt.now().isoformat()[:19] + "+01:00"


_ATTENTION_MAX = 8


def _attention_summary(items: list[tuple[int, str]]) -> str:
    """The 'what needs your attention' list, as the EMAIL will show it.

    Riskiest first, capped, and every line starts with a bullet. The cap is
    because an uncapped list is a wall of text in an inbox and the field is
    2000 characters; the bullets are because this is a plain-text field
    merged into HTML, where the newlines may or may not survive - with a
    bullet it reads as a list either way. The full list is always in the
    report, which is where the email is sending them."""
    if not items:
        return "No results at Medium risk or above this period."
    ordered = [t for _, t in sorted(items, key=lambda x: -x[0])]
    shown = ordered[:_ATTENTION_MAX]
    out = "\n".join("• " + t for t in shown)
    rest = len(ordered) - len(shown)
    if rest:
        out += (f"\n• and {rest} more, all listed in your report.")
    return out[:1990]


_BAND_RANK_FOR_SORT = {"High": 6, "Review - may be at risk": 5,
                       "Medium/High": 4, "Medium": 3, "Low/Medium": 2,
                       "Low": 1, "Result (not live)": 0, "None": 0}


def build_report_records(led, run_id: str, account_ids: list[str] | None = None,
                         portal_base: str | None = None,
                         period_start: str | None = None,
                         first_report: bool | None = None,
                         email_by_account: dict[str, str] | None = None,
                         preference_by_account: dict[str, dict] | None = None) -> list[dict]:
    """One Monitoring Report record per (account, client trademark)."""
    from datetime import date as _date

    if portal_base is None:
        import json as _json
        from pathlib import Path as _Path
        _h = _Path(__file__).resolve().parent / "_work" / "hosting.json"
        portal_base = (_json.loads(_h.read_text())["base"] if _h.exists()
                       else "https://portal.thetrademarkhelpline.com")

    import tokens as tokens_mod
    from explain import short_reason

    q = ("select * from results where (first_run_id = ? or last_run_id = ?)")
    params: list = [run_id, run_id]
    if account_ids:
        q += " and client_account_id in (%s)" % ",".join("?" * len(account_ids))
        params += list(account_ids)
    rows = led.conn.execute(q, params).fetchall()

    marks = {(m["client_account_id"], m["client_tm_number"]): m
             for m in led.conn.execute("select * from client_marks")}
    tm_ids = _zoho_trademark_ids()

    by_tm: dict[tuple, list] = {}
    for r in rows:
        by_tm.setdefault((r["client_account_id"], r["client_tm_number"]), []).append(r)

    from clients import monitoring_switch, resolve_recipients
    from report import _marks_json, portfolio_alert_text, frequency_phrase

    # The report covers an APPLICANT ACCOUNT, not a trademark: one account
    # can hold several applicants of the same name and gets one report for
    # all of their marks (Jonathan, 9 Sep). So the email is addressed in
    # those terms, and the name is read from the Accounts module rather than
    # a lookup label - a lookup keeps a stale copy of the name through a
    # rename, which is how a report came out headed GREGGS PLC.
    def _account_names(ids: list[str]) -> dict[str, str]:
        import zoho_client as _zc
        out: dict[str, str] = {}
        num = [i for i in ids if i.isdigit()]
        for i in range(0, len(num), 100):
            try:
                d = _zc.call("GET", "Accounts?fields=Account_Name&ids="
                             + ",".join(num[i:i + 100]))
                for rec in (d.get("data") or []):
                    out[rec["id"]] = rec.get("Account_Name") or ""
            except Exception:
                pass
        return out
    # D12. The cadence as a phrase, for ${Monitoring_Reports.Frequency_Phrase}
    # in the email templates and the matching sentence in the report.
    _pd = led.conn.execute("select period_days from runs where run_id=?",
                           (run_id,)).fetchone()
    _period_days = (_pd["period_days"] if _pd else None) or 14
    _accts = sorted({a for a, _ in by_tm})
    switch = monitoring_switch(_accts)
    acct_names = _account_names(_accts)
    # delivery falls back to the parent account when the applicant account
    # that holds the marks has no contact of its own (Jonathan, 3 Sep).
    recipients = resolve_recipients(_accts) if email_by_account is None else {}
    alert_by_acct: dict[str, str] = {}
    for (acct0, tm0), m0 in marks.items():
        alert_by_acct.setdefault(acct0, None)
    for acct0 in list(alert_by_acct):
        rows0 = {k[1]: v for k, v in marks.items() if k[0] == acct0}
        # one paragraph, not two lines: the email template is HTML, where a
        # newline collapses and would run the two sentences together.
        alert_by_acct[acct0] = portfolio_alert_text(
            _marks_json(rows0)).replace("\n", " ")

    def _recipient(acct: str) -> str:
        if email_by_account is not None:
            return (email_by_account or {}).get(acct) or ""
        return recipients.get(acct, {}).get("email", "")

    # D12. The email counts the SAME window as the report it links to -
    # report 1 the last 12 months, report 2 onwards the fortnight - so the
    # client never opens a report that contradicts the email that sent them
    # there. window_for_run/in_window are the single definition, in report.py.
    from report import window_for_run, in_window, window_label
    _win_days, _cutoff, _catch_up = window_for_run(
        led, run_id, _date.today(), _period_days)
    _win_label = window_label(_win_days)

    def _tally(rows):
        """(counts, excluded, total, attention) over the reporting window."""
        counts = {v: 0 for v in _COUNT_FIELDS.values()}
        excluded = total = 0
        attention = []
        for r in rows:
            total += 1
            if r["suppressed_reason"]:
                excluded += 1
                continue
            band = _band_of(r)
            counts[_COUNT_FIELDS.get(band, "Count_Low")] += 1
            if band in _ATTENTION_BANDS:
                attention.append((
                    _BAND_RANK_FOR_SORT.get(band_to_zoho(band), 0),
                    f"{r['cited_mark_text'] or r['cited_tm_number']} "
                    f"({r['cited_tm_number']}) - "
                    f"{band_to_zoho(band)}. {short_reason(r)}"))
        return counts, excluded, total, attention

    # First_Report per ACCOUNT, read from the register rather than passed in
    # on the command line: an account is on its first report when its
    # schedule has never recorded a run. Wrong here means a returning client
    # is greeted as new, so it is derived, not remembered.
    first_by_acct: dict[str, bool] = {}
    if first_report is None:
        try:
            import enrol
            live = enrol.live_accounts(log=lambda *a: None)
            first_by_acct = {a: not (r.get("Last_Run_At"))
                             for a, r in live.items()}
        except Exception:                       # register unreadable -> unset
            first_by_acct = {}

    # tokens that were actually published for this run, by account
    published_links: dict[str, str] = {}
    _links_csv = OUT_DIR / run_id / "report_links.csv"
    if _links_csv.exists():
        import csv as _csv
        with _links_csv.open(encoding="utf-8") as _fh:
            for _row in _csv.DictReader(_fh):
                _url = (_row.get("url") or "").strip()
                if _url:
                    published_links[_row["account_id"]] = _url.rsplit("/", 1)[1]
    missing_links: list[str] = []

    windowed_by_acct: dict[str, list] = {}
    today = _date.today().isoformat()
    records = []
    for (acct, tm), rs in sorted(by_tm.items()):
        m = marks.get((acct, tm))
        mark_text = (m["mark_text"] if m else "") or tm

        in_win = [r for r in rs if in_window(r, _cutoff, _catch_up, run_id)]
        windowed_by_acct.setdefault(acct, []).extend(in_win)
        counts, excluded, total, attention = _tally(in_win)
        new = sum(1 for r in rs if r["first_run_id"] == run_id)

        # THE LINK IN THE EMAIL MUST BE THE LINK THAT WAS PUBLISHED.
        # Minting a token here creates one the host has never heard of:
        # publish_reports uploaded the report at h:sha256(ITS token), so a
        # freshly issued token resolves to nothing and the client gets
        # "this report link is no longer available". Caught on Jonathan's own
        # first report, 9 Sep, before any client email went out.
        token = published_links.get(acct, "")
        if not token:
            token = tokens_mod.issue(led, acct, run_id)
            missing_links.append(acct)
        pref_for_acct = dict((preference_by_account or {}).get(acct) or {})
        pref_for_acct["account_switch"] = switch.get(acct, "Enabled")
        # Decide on the SAME rows the client is shown (Jonathan, 9 Sep).
        # Passing every result ever found meant the template could be chosen
        # by a finding outside the reporting window: Braudit's first email
        # fired the Urgent template - "Action may be needed" - off a
        # Review-band result older than 12 months, while the counts printed
        # beside it read 0 review, 0 high. The subject and the numbers have
        # to come from one set of rows or the email argues with itself.
        decision = decide_send(in_win, run_id, pref_for_acct,
                               bool(first_report))
        import tokens as _t
        rec = {
            "Report_ID": f"{run_id}:{tm}",
            "Name": f"{mark_text[:55]} - {run_id}"[:120],
            "Client_TM_Number": tm,
            "Mark_Text": mark_text[:255],
            "Run_ID": run_id,
            "Report_Date": today,
            "Period_End": today,
            "Count_Excluded": excluded,
            "Count_Total": total,
            "New_This_Report": new,
            "Attention_Summary": _attention_summary(attention),
            "Window_Label": _win_label,
            "Account_Name_Text": (acct_names.get(acct) or "")[:120],
            "Report_Scope": f"for {mark_text} ({tm})"[:120],
            # Sent_At: the workflow fires on record CREATE, so creation time
            # is send time to within seconds. A Zoho field update cannot
            # write "now", which is why this is stamped here and only on the
            # records that are actually going out.
            "Sent_At": (_now_tz() if decision["should_send"]
                        and _recipient(acct) else None),
            "Portal_Link": tokens_mod.report_url(portal_base, token),  # noqa: E501  (token = the PUBLISHED one)
            "Send_Channel": ("Email" if _recipient(acct) else "No contact details"),
            "Scoring_Version": SCORING_VERSION,
            "Threshold_Version": THRESHOLD_VERSION,
            # --- send decision (brief section 7), stored as applied ---
            "Portfolio_Alert": alert_by_acct.get(acct, "") or "",
            # adjective form: the templates all read "your <X> check"
            "Frequency_Phrase": frequency_phrase(_period_days,
                                                 decision["policy"],
                                                 form="adjective"),
            "Should_Send": decision["should_send"],
            "Send_Decision_Reason": decision["reason"][:255],
            "Report_Type": decision["report_type"],
            "Report_Status": ("Queued" if decision["should_send"]
                              else "Held for digest"),
            "Notification_Policy_Used": decision["policy"],
            "Alert_Threshold_Applied": decision["threshold"],
            "Highest_Risk": decision["highest_risk"],
            "WhatsApp_Threshold_Used": decision["whatsapp_threshold"],
            "WhatsApp_Alert_Due": decision["whatsapp_alert_due"],
            "Changed_Results": 0,     # material-change detection: follow-up
            "Email_Status": "Not sent",
            "Token_Reference": _t.fingerprint(token),
            "Token_Expiry": (_dt.date.today()
                             + _dt.timedelta(days=_t.DEFAULT_TTL_DAYS)).isoformat(),
            **counts,
        }
        if period_start:
            rec["Period_Start"] = period_start
        if first_report is not None:
            rec["First_Report"] = bool(first_report)
        elif acct in first_by_acct:
            rec["First_Report"] = first_by_acct[acct]
        email = _recipient(acct)
        if email:
            rec["Email"] = email
        via = recipients.get(acct, {})
        rec["Recipient_Via"] = ("Own contact" if via.get("via") == "own"
                                else f"Parent: {via.get('via_name', '')}"[:120]
                                if via.get("via") == "parent"
                                else "No contact found")
        if acct.isdigit():
            rec["Client_Account"] = {"id": acct}
        zid = tm_ids.get(tm.upper())
        if zid:
            rec["Client_Trademark"] = {"id": zid}
        records.append(rec)

    # ONE EMAIL PER ACCOUNT PER RUN.
    # Report records are per (account, trademark) because that is the unit
    # staff review and the unit Zoho links to a Trademark. The SEND is not:
    # the hosted report covers the client's whole portfolio, and the client
    # agreed a consolidated digest - so a 48-mark client must not receive 48
    # emails. Exactly one record per account keeps Should_Send=true (the
    # highest-risk one, so the subject line matches the worst finding);
    # the rest are recorded with the reason.
    by_acct: dict[str, list] = {}
    for r in records:
        aid = (r.get("Client_Account") or {}).get("id") or ""
        by_acct.setdefault(aid, []).append(r)
    if missing_links:
        # A sendable record with an unpublished token is an email that leads
        # to "this report link is no longer available". Refuse rather than
        # send: publish must run before reports, which is the stage order.
        stuck = [r for r in records
                 if r.get("Should_Send")
                 and (r.get("Client_Account") or {}).get("id") in set(missing_links)]
        if stuck:
            raise RuntimeError(
                f"{len(set(missing_links))} account(s) have no published report "
                f"for run {run_id} ({', '.join(sorted(set(missing_links))[:3])}...): "
                f"run the publish stage first, or these emails would link to nothing")

    for aid, group in by_acct.items():
        sendable = [r for r in group if r.get("Should_Send")]
        if not sendable:
            continue
        sendable.sort(key=lambda r: (-_BAND_RANK_FOR_SORT.get(
            r.get("Highest_Risk") or "None", 0),
            r.get("Client_TM_Number") or ""))
        lead = sendable[0]
        for r in sendable[1:]:
            r["Should_Send"] = False
            r["Send_Reason"] = (
                "consolidated into this account's report for "
                f"{lead.get('Client_TM_Number')}")[:255]
            r["Report_Status"] = "Held for digest"
            # nothing was sent for this one, so it must not carry a send
            # time - Sent_At was stamped before consolidation demoted it.
            r["Sent_At"] = None

        # The one record that is actually emailed speaks for the ACCOUNT,
        # because that is what its link opens: a portfolio-wide report. Left
        # per-mark, a five-mark client would read "20 results" in the email
        # and see 59 in the report two seconds later. The held records keep
        # their per-mark counts - staff review by trademark.
        n_marks = len({r.get("Client_TM_Number") for r in group
                       if r.get("Client_TM_Number")})
        if n_marks > 1:
            a_counts, a_excl, a_total, a_attn = _tally(
                windowed_by_acct.get(aid, []))
            lead.update(a_counts)
            lead["Count_Excluded"] = a_excl
            lead["Count_Total"] = a_total
            lead["Attention_Summary"] = _attention_summary(a_attn)
            lead["Report_Scope"] = (
                f"across your {n_marks} trademarks")[:120]
    return records


# --------------------------------------------------------------------------
# Direct push - the production transport (Zoho self-client, no connector).

def push_batches_selfclient(label: str, log=print) -> dict:
    """Replay every batch file in _out/zoho_push/<label>/ via the direct
    API client. Resumable: done batches are tracked in push_state.json.
    Contract enforced: upsert on the manifest's duplicate-check fields,
    trigger [], Client_Action="none" set for INSERTED records only.
    """
    import zoho_client

    out_dir = OUT_DIR / "zoho_push" / label
    state_f = out_dir / "push_state.json"
    state = (json.loads(state_f.read_text()) if state_f.exists()
             else {"done": [], "inserts": 0, "updates": 0, "errors": []})
    batches = sorted(out_dir.glob("batch_*.json"))

    for bf in batches:
        if bf.name in state["done"]:
            continue
        payload = json.loads(bf.read_text())
        module = payload["path_variables"]["module"]
        body = payload["body"]
        try:
            results = zoho_client.upsert(
                module, body["data"],
                duplicate_check_fields=body["duplicate_check_fields"],
                trigger=body.get("trigger", []))
        except Exception as e:                        # noqa: BLE001
            state["errors"].append({"batch": bf.name, "error": str(e)[:300]})
            state_f.write_text(json.dumps(state))
            log(f"  {bf.name}: ERROR {str(e)[:120]}")
            continue

        ins_ids = [r["details"]["id"] for r in results
                   if r.get("action") == "insert" and r.get("code") == "SUCCESS"]
        n_upd = sum(1 for r in results if r.get("action") == "update")
        n_bad = sum(1 for r in results if r.get("code") != "SUCCESS")
        if n_bad:
            state["errors"].append({"batch": bf.name,
                                    "error": f"{n_bad} record-level failures"})
        if ins_ids and module == MODULE:
            for i in range(0, len(ins_ids), 100):
                zoho_client.update(module, [
                    {"id": rid, "Client_Action": "none"}
                    for rid in ins_ids[i:i + 100]], trigger=[])
        state["inserts"] += len(ins_ids)
        state["updates"] += n_upd
        state["done"].append(bf.name)
        state_f.write_text(json.dumps(state))
        log(f"  {bf.name}: {len(ins_ids)} inserted, {n_upd} updated"
            + (f", {n_bad} FAILED" if n_bad else ""))

    done = len(state["done"]) == len(batches)
    log(f"push {label}: {len(state['done'])}/{len(batches)} batches, "
        f"{state['inserts']} inserts, {state['updates']} updates, "
        f"{len(state['errors'])} errors"
        + (" - COMPLETE" if done else ""))
    return state

if __name__ == "__main__":
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger import Ledger

    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--label", required=True)
    ap.add_argument("--push", action="store_true",
                    help="replay the label's batch files via the self-client")
    a = ap.parse_args()

    if a.push:
        push_batches_selfclient(a.label)
        raise SystemExit(0)

    led = Ledger()
    try:
        recs = build_payloads(
            led,
            account_ids=[s.strip() for s in a.accounts.split(",")] if a.accounts else None,
            run_id=a.run_id)
    finally:
        led.close()
    out = emit_batches(recs, a.label)
    print(f"{len(recs)} records -> {out}")
