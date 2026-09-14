"""Who gets onboarded first, and who has already had report one.

THE SERVICE HERE IS TRADEMARK WATCH, NOT MONITORING. They are two products
and this file only touches one of them:

  Watch       one fortnightly automated report per APPLICANT ACCOUNT, run on
              word mark text, with every mark's results consolidated into a
              single email (config.SERVICE_NAME = "Trademark Watch"). This
              module, fortnightly.py and the rest of uk_monitor are Watch.
  Monitoring  a per-trademark service - word, image and tagline, many
              keywords fed to international registers, domains, socials,
              Companies House and marketplaces. It runs through the audit
              engine for Deals in the "Monitoring or Representation"
              pipeline, and nothing here touches it.

The Zoho modules are called Monitoring_Schedules / Monitoring_Reports /
Monitoring_Results because they were built on 22 Aug 2026, four days before
the service was renamed from "Trademark Monitoring" to "Trademark Watch"
(config.py, Jonathan, 26 Aug). The module names are Watch data despite what
they say. Do not read them as the Monitoring product.

Two questions the fortnightly runner has to answer before it sends anything,
both of which used to be answered wrongly:

1. "Has this account ever had a report?"  The runner used to read
   `Last_Run_At` on Monitoring_Schedules. Enrolment stamps that field, so
   every one of the 120 enrolled accounts looked like an established client
   whose slot had come round — which made the onboarding cap inert and put
   119 first-time clients one command away from a simultaneous send. The
   honest test is whether a Monitoring_Report record exists for the account:
   a report record IS the email (the Zoho workflow fires on create), so a
   record is proof an email went and no record is proof none did.

2. "Which of the waiting accounts go today?"  Ramp order is a commercial
   decision, not a technical one (Jonathan, 14 Sep): clients holding dead
   marks first, then expired, then marks up for renewal inside a year, then
   applications in flight, then everyone else. An account is ranked by its
   best mark — one dead mark is enough to put a client at the front.

The mark data comes from `mark_lines.by_account()`, the same cache the
report's portfolio list is built from. That cache is keyed on Zoho's
Account_URN, which `clients.attributed_marks` rightly distrusts for deciding
what to SHOW a client; here it only decides which day someone is onboarded,
so an occasional mis-attribution costs a place in the queue, not a privacy
breach.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zoho_client                                       # noqa: E402

# Band 1 is served first. Wording follows Jonathan's order exactly.
BANDS = {
    1: "dead, not suppressed",
    2: "expired, not suppressed",
    3: "renewal due within 12 months",
    4: "application in progress",
    5: "everything else",
}
_IN_APPLICATION = {"Pending", "Examination", "Application Published",
                   "Pre-Publication", "Opposed"}
RENEWAL_WINDOW_DAYS = 365


def first_report_accounts() -> set[str]:
    """Account ids that have had at least one Monitoring_Report record.

    A record on this module is an email that went to a client, so this is
    the set of accounts already past report one."""
    out: set[str] = set()
    page = 1
    while True:
        r = zoho_client.call(
            "GET", "Monitoring_Reports?fields=Client_Account,Should_Send"
                   f"&per_page=200&page={page}")
        rows = r.get("data") or []
        for rec in rows:
            aid = (rec.get("Client_Account") or {}).get("id")
            if aid:
                out.add(aid)
        if not (r.get("info") or {}).get("more_records"):
            return out
        page += 1


def _mark_band(rec: dict, today: date) -> int:
    status = (rec.get("Status") or "").strip()
    supressed = (rec.get("Supress") or "").strip().lower() == "yes"
    if status == "Dead" and not supressed:
        return 1
    if status == "Expired" and not supressed:
        return 2
    exp = (rec.get("Expiry_date") or "")[:10]
    if exp:
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            d = None
        # "expiring in the next 12 months" - a date already past belongs to
        # the expired bands above, not here.
        if d and today <= d <= today + timedelta(days=RENEWAL_WINDOW_DAYS):
            return 3
    if status in _IN_APPLICATION:
        return 4
    return 5


def account_bands(today: date | None = None) -> dict[str, int]:
    """{account_id: best band}. Accounts with no marks in the cache land in
    band 5 rather than being dropped - an account we know nothing about
    still gets onboarded, just last."""
    import mark_lines
    today = today or date.today()
    out: dict[str, int] = {}
    for aid, recs in mark_lines.by_account().items():
        out[aid] = min((_mark_band(r, today) for r in recs), default=5)
    return out


def rank(account_ids: list[str], waiting_since: dict[str, str] | None = None,
         today: date | None = None) -> list[tuple[str, int]]:
    """Ramp order: band first, then longest-waiting, so nobody is starved
    inside their band. Returns [(account_id, band)]."""
    bands = account_bands(today)
    waiting_since = waiting_since or {}
    return sorted(((aid, bands.get(aid, 5)) for aid in account_ids),
                  key=lambda t: (t[1], waiting_since.get(t[0]) or "", t[0]))


def create_check_task(account_id: str, account_name: str, owner_id: str | None,
                      run_id: str, report_link: str = "") -> dict | None:
    """The report-one gate: a task asking a human to check the client's
    details before the first report is allowed out.

    Deliberately NOT a held Monitoring_Report record. The Zoho rule "Send
    fortnightly monitoring report email" - the Zoho rule's own name, Watch
    reports being what it sends - runs on CREATE only, so a record
    written with Should_Send false can never be released by flipping the
    flag later - editing it re-fires nothing. Withholding the record is the
    only hold that can be undone, and re-running the reports stage with
    --release is how it is undone."""
    body = {"Subject": f"Check client details before first Watch report - {account_name}"[:255],
            "Status": "Not Started", "Priority": "High",
            "Due_Date": date.today().isoformat(),
            "What_Id": account_id, "$se_module": "Accounts",
            "Description": (
                "This account is due its FIRST fortnightly Trademark Watch report. "
                "Nothing has been emailed.\n\n"
                "Check the client information is correct - name, contact, and "
                "that the marks listed are theirs - then release the report:\n"
                f"    python3 fortnightly.py --run-id {run_id} --from reports "
                f"--accounts {account_id} --release\n\n"
                + (f"Report preview: {report_link}\n" if report_link else "")
                + "Later reports for this account send without a check.")}
    if owner_id:
        body["Owner"] = {"id": owner_id}
    try:
        out = zoho_client.call("POST", "Tasks", {"data": [body]})
        return (out.get("data") or [{}])[0]
    except Exception as exc:                      # a task failure must not
        print(f"[warn] first-report check task failed for "      # stop a run
              f"{account_name}: {exc}", file=sys.stderr)
        return None
