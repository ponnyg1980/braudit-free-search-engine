"""Read a Zoho Deal into an AuditRequest, and write run status back.

The Deal is the commercial record and the trigger; everything from the
search onward lives in Supabase. WHERE each input lives on the Deal is the
business of `deal_fields` (one adapter per input, precedence written down
there) — this module only assembles the request and writes status back.

What the Deal contributes, measured on 1964745000119873070 (Coastal
Nutrients, 10 Sep 2026) and 1964745000119447114 (Hailaflo, 13 Sep 2026):

    keywords          deal_fields.keywords       Search_Word_1..5 today
    Vienna codes      deal_fields.vienna_codes   Image_Mark_Information subform
    logo              deal_fields.logo           Image_JPEG on that subform / Logo_Awaited
    jurisdictions     deal_fields.jurisdictions  Trademark_Jurisdictions (ISO codes)
    Classes_Terms (subform)                      classes and the goods text
    channels          deal_fields.channels       Audit_Search_Layers (forms) else Other_Search_Platforms
    Account_Name, Contact_Name                   client and applicant
    Nature_of_Business, SIC_Code
    Search_Domain_1..5                           domain *criteria* — slugs to look up

What it does not contribute: the client's own assets. Those come from the
application form (portfolio) or from triage, never from a Deal field.

Write-back reuses the Braudit_* status fields already on the Deal, with
their existing picklist ACTUAL values (`deal_fields.TRIGGER_STATUS`), so no
Zoho Setup change is needed.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "uk_monitor"))
import zoho_client as zoho  # noqa: E402  (uk_monitor/zoho_client.py — token refresh, retries)

from . import deal_fields  # noqa: E402
from .runner import AuditRequest  # noqa: E402

# kept for callers that imported these names from here
OFFICE_TO_ISO = deal_fields.OFFICE_TO_ISO
OPERATOR_TO_MATCH = deal_fields.OPERATOR_TO_MATCH



def _pipeline_v21(deal_id: str) -> str | None:
    """Pipeline is not returned by API v2 (only v2.1+). One small extra GET."""
    import json as _json
    import urllib.request as _ur
    try:
        req = _ur.Request(f"https://www.zohoapis.com/crm/v2.1/Deals/{deal_id}?fields=Pipeline",
                          headers={"Authorization": f"Zoho-oauthtoken {zoho.access_token()}"})
        with _ur.urlopen(req, timeout=60) as resp:
            data = (_json.loads(resp.read() or b"{}").get("data") or [{}])[0]
        p = data.get("Pipeline")
        return (p.get("name") if isinstance(p, dict) else p) or None
    except Exception as exc:                      # never block a run on this
        print(f"[pipeline] lookup failed for {deal_id}: {exc}", file=sys.stderr)
        return None


def fetch_deal(deal_id: str) -> dict:
    out = zoho.call("GET", f"Deals/{deal_id}")
    data = out.get("data") or []
    if not data:
        raise RuntimeError(f"Deal {deal_id} not found")
    deal = data[0]
    if not deal.get("Pipeline"):
        deal["Pipeline"] = _pipeline_v21(deal_id)
    return deal


def _classes_and_goods(deal: dict) -> tuple[list[int], str]:
    classes, parts = [], []
    for row in deal.get("Classes_Terms") or []:
        for c in row.get("Classes") or []:
            m = re.match(r"\s*(\d+)", str(c))
            if m and int(m.group(1)) not in classes:
                classes.append(int(m.group(1)))
        terms = (row.get("Terms1") or row.get("Terms") or "").strip()
        if terms:
            label = ", ".join(str(c).split(" - ")[0].strip() for c in (row.get("Classes") or []))
            parts.append(f"Class {label}: {terms}")
    if not classes:
        for c in deal.get("Classes") or []:
            m = re.match(r"\s*(\d+)", str(c))
            if m:
                classes.append(int(m.group(1)))
    return classes, "\n".join(parts)


def _jurisdictions(deal: dict) -> list[str]:
    return deal_fields.jurisdictions(deal)[0]


def _criteria(deal: dict) -> tuple[str, list[tuple]]:
    """(mark_text, extra criteria as (match_type, phrase)). Word 1 is the mark."""
    words, _src = deal_fields.keywords(deal)
    if not words:
        raise RuntimeError("Deal has no search words (Search_Word_1 / TM_Text) — nothing to audit")
    return words[0][1], words[1:]


def _channels(deal: dict) -> dict:
    return deal_fields.channels(deal)


def _image_inputs(deal: dict) -> dict:
    """Vienna codes + logo state from the adapter; image search only when the
    Deal asked for it (Braudit_Search_Types has 'image', or Report_Type says
    so) OR codes were entered — a coded logo is an explicit request."""
    codes = deal_fields.vienna_codes(deal)
    lg = deal_fields.logo(deal)
    types = {str(t).lower() for t in (deal.get("Braudit_Search_Types") or [])}
    rt = str(deal.get("Report_Type") or "").lower()
    wants_image = "image" in types or rt in ("image only", "combined") or bool(codes)
    return {
        "vienna_codes": codes if wants_image else [],
        "logo_awaited": lg["state"] == "awaited",
        "logo_ref": (lg["files"][0] if lg["files"] else None),
        "logo_state": lg["state"],
        "wants_image": wants_image,
    }


def request_from_deal(deal: dict, search_date: date | None = None, with_exclusions: bool = True) -> AuditRequest:
    mark, extra = _criteria(deal)
    classes, goods = _classes_and_goods(deal)
    ch = _channels(deal)
    img = _image_inputs(deal)
    account = (deal.get("Account_Name") or {}).get("name") or ""
    contact = (deal.get("Contact_Name") or {}).get("name") or ""
    req = AuditRequest(
        client_name=account or contact or mark,
        mark_text=mark,
        classes=classes,
        jurisdictions=_jurisdictions(deal),
        applicant=account or None,
        goods_text=goods,
        extra_criteria=extra,
        deal_id=str(deal.get("id") or ""),
        nature_of_business=deal.get("Nature_of_Business") or "",
        search_date=search_date or date.today(),
        include_companies=ch["include_companies"],
        include_domains=ch["include_domains"],
        include_serp=ch["include_serp"],
        # an explicit empty selection means "none of these", not "all of them"
        socials=ch["socials"] if ch["socials_selected"] else [],
        marketplaces=ch["marketplaces"] if ch["markets_selected"] else [],
        vienna_codes=img["vienna_codes"],
        logo_awaited=img["logo_awaited"],
        logo_ref=img["logo_ref"],
    )
    if img["logo_ref"] and img["wants_image"]:
        req.logo_url = fetch_logo_data_url(img["logo_ref"]) or None
    if with_exclusions:
        try:
            rows = fetch_exclusions((deal.get("Account_Name") or {}).get("id"))
        except Exception as exc:                       # reported, never fatal
            rows = []
            print(f"[warn] Client_Search_Exclusions unavailable: {exc}", file=sys.stderr)
        req.exclusions, req.zoho_portfolio = deal_fields.exclusions_from_rows(rows)
    return req


def fetch_exclusions(account_id: str | None) -> list[dict]:
    """Active Client_Search_Exclusions for the Deal's Account (the record of
    exclusions). Empty when the Deal has no Account. Never raises — an
    exclusions outage must not stop an audit, but it is reported."""
    if not account_id:
        return []
    out = zoho.call("GET", "Client_Search_Exclusions/search?criteria="
                    + f"((Account:equals:{account_id})and(Active:equals:true))"
                    + "&fields=id,Name,Exclusion_Value,Target_Type,Match_Mode,Active,Reason&per_page=200")
    return out.get("data") or []


MONITORING_PIPELINE = "Monitoring or Representation"
AUDIT_PIPELINE = "Audit and Consultation"


def run_kind(deal: dict) -> str:
    """'monitoring' or 'audit', decided by the Deal's PIPELINE and nothing else
    (Jonathan, 13 Sep 2026: "it is pipeline that defines monitoring and
    representation from audits"). The webhook's kind is ignored."""
    pipeline = deal.get("Pipeline")
    if isinstance(pipeline, dict):
        pipeline = pipeline.get("name") or pipeline.get("display_value")
    if str(pipeline or "").strip().lower() == MONITORING_PIPELINE.lower():
        return "monitoring"
    return "audit"


def deal_summary(deal: dict) -> dict:
    """What a staff member should see before pressing go."""
    mark, extra = _criteria(deal)
    classes, goods = _classes_and_goods(deal)
    img = _image_inputs(deal)
    words, kw_source = deal_fields.keywords(deal)
    juris, juris_source = deal_fields.jurisdictions(deal)
    return {
        "deal": deal.get("Deal_Name"), "stage": deal.get("Stage"), "next_action": deal.get("Next_Action"),
        "client": (deal.get("Account_Name") or {}).get("name"), "contact": (deal.get("Contact_Name") or {}).get("name"),
        "mark": mark, "extra_criteria": extra, "keyword_source": kw_source,
        "classes": classes, "goods_chars": len(goods),
        "jurisdictions": juris, "jurisdiction_source": juris_source, "channels": _channels(deal),
        "vienna_codes": img["vienna_codes"], "logo_state": img["logo_state"], "wants_image": img["wants_image"],
        "domain_criteria": [deal.get(f"Search_Domain_{i}") for i in range(1, 6) if deal.get(f"Search_Domain_{i}")],
        "account_id": (deal.get("Account_Name") or {}).get("id"),
        "legacy_report": bool(deal.get("Excel_Report_Link")),
    }


def validate(deal: dict) -> list[str]:
    """Required-information check, mirroring what the Triage Form asks for.
    Returns the list of problems; empty means the Deal can be researched."""
    problems = []
    words, _ = deal_fields.keywords(deal)
    if not words:
        problems.append("no search words (Search_Word_1 empty and no TM_Text)")
    classes, _goods = _classes_and_goods(deal)
    if not classes:
        problems.append("no Nice classes (Classes_Terms subform empty)")
    if not (deal.get("Account_Name") or {}).get("id"):
        problems.append("no Account linked")
    img = _image_inputs(deal)
    if img["wants_image"] and img["logo_state"] == "none" and not img["vienna_codes"]:
        problems.append("image search requested but no logo attached and no Vienna codes")
    return problems


# ---------------------------------------------------------------------------
# write-back — the Braudit_* fields, their existing picklist ACTUAL values
# ---------------------------------------------------------------------------

def _now() -> str:
    # Zoho wants ISO-8601 with a colon in the offset (+01:00); "%z" gives +0100 → INVALID_DATA
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _update(records: list[dict]) -> None:
    """AUDIT_ZOHO_WRITEBACK=0 turns write-back into a log line (staging, tests)."""
    import os
    if os.environ.get("AUDIT_ZOHO_WRITEBACK", "1") == "0":
        print(f"[zoho write-back suppressed] {records}", file=sys.stderr)
        return
    out = zoho.update("Deals", records)
    bad = [r for r in out if str(r.get("code")) != "SUCCESS"]
    if bad:
        # never silent: a rejected write-back is a defect, not a no-op
        raise RuntimeError(f"Zoho Deal write-back rejected: {bad}")
    print(f"[zoho write-back] {[r.get('code') for r in out]} {[list(x.keys()) for x in records]}", file=sys.stderr)


def mark_running(deal_id: str, run_id: str | None = None) -> None:
    rec = {"id": deal_id, "Latest_Braudit_Run_Status": deal_fields.RUN_STATUS["running"],
           "Braudit_Trigger_Status": deal_fields.TRIGGER_STATUS["sending"],
           "Last_Braudit_Run_At": _now(), "Braudit_Last_Error": ""}
    if run_id:
        rec["Latest_Braudit_Run_ID"] = run_id
    _update([rec])


def mark_done(deal_id: str, run_id: str, status: str, findings: int, new_findings: int,
              triage_url: str | None = None, warnings: list | None = None) -> None:
    zs = deal_fields.RUN_STATUS.get(status, "Unknown")
    trig = deal_fields.TRIGGER_STATUS["complete" if status == "complete" else "partial"]
    rec = {"id": deal_id, "Latest_Braudit_Run_ID": run_id, "Latest_Braudit_Run_Status": zs,
           "Braudit_Trigger_Status": trig,
           "Last_Braudit_Run_At": _now(), "Latest_Finding_Count": findings,
           "Latest_New_Finding_Count": new_findings,
           "Braudit_Last_Error": ("; ".join(warnings or [])[:255]) if status != "complete" else ""}
    if triage_url:
        rec["Brand_Audit_Link"] = triage_url[:450]
    _update([rec])


def send_back_to_setup(deal: dict, problems: list[str]) -> None:
    """Validation failed: the Deal may not sit at 'Send for Research'. Put it
    back to 'Set Up Order', mark the trigger not_ready, record what is
    missing, and give the Deal Owner a task (Jonathan, 13 Sep 2026)."""
    deal_id = str(deal.get("id"))
    missing = "; ".join(problems)
    _update([{"id": deal_id, "Next_Action": "Set Up Order",
              "Braudit_Trigger_Status": deal_fields.TRIGGER_STATUS["not_ready"],
              "Latest_Braudit_Run_Status": "Cancelled",
              "Last_Braudit_Run_At": _now(), "Braudit_Last_Error": ("Missing: " + missing)[:255]}])
    create_owner_task(deal, "Complete audit order information before research",
                      "The audit could not be sent for research. Missing: " + missing
                      + "\nAdd the information in the Deal (Triage Form) and set Next Action Sub Status "
                        "back to 'Send for Research'.")


ADMIN_SUPPORT_USER_ID = "1964745000036444001"   # support@ (Admin Support), same id the Deluge uses


def triage_assignee(deal: dict, kind: str) -> str | None:
    """Who triages a finished run (Jonathan, 13 Sep 2026):
    Audit Deals -> the Deal Owner; Monitoring & Representation -> support@."""
    if kind == "monitoring":
        return ADMIN_SUPPORT_USER_ID
    return (deal.get("Owner") or {}).get("id") or ADMIN_SUPPORT_USER_ID


def create_owner_task(deal: dict, subject: str, description: str,
                      owner_id: str | None = None) -> dict | None:
    """One open Task on the Deal for its Owner (or `owner_id`). Idempotent on
    subject: an open task with the same subject on the same Deal is not duplicated."""
    import os
    deal_id = str(deal.get("id"))
    owner = owner_id or (deal.get("Owner") or {}).get("id")
    rec = {"Subject": subject[:255], "Description": description[:32000], "Status": "Not Started",
           "Priority": "High", "Due_Date": datetime.now().date().isoformat(),
           "What_Id": {"id": deal_id}, "$se_module": "Deals"}
    if owner:
        rec["Owner"] = {"id": owner}
    if os.environ.get("AUDIT_ZOHO_WRITEBACK", "1") == "0":
        print(f"[zoho task suppressed] {rec}", file=sys.stderr)
        return None
    try:
        existing = zoho.call("GET", "Tasks/search?criteria=((What_Id:equals:" + deal_id
                             + ")and(Status:not_equal:Completed))&fields=id,Subject&per_page=50")
        for t in existing.get("data") or []:
            if (t.get("Subject") or "") == rec["Subject"]:
                return {"id": t["id"], "existing": True}
    except RuntimeError:
        pass                                    # 204 / search failure: create anyway
    out = zoho.call("POST", "Tasks", {"data": [rec], "trigger": []})
    return (out.get("data") or [{}])[0]


def create_triage_task(deal: dict, kind: str, run_id: str, triage_url: str,
                       new_findings: int, status: str) -> dict | None:
    """After a run lands in Triage: one task for whoever triages this Deal type.
    Subject carries the run id, so re-runs get their own task and repeats of the
    same run do not."""
    who = triage_assignee(deal, kind)
    label = "monitoring report" if kind == "monitoring" else "audit results"
    subject = f"Triage {label} -- run {run_id[:8]} -- {deal.get('Deal_Name') or deal.get('id')}"
    desc = (f"Run {run_id} finished with status {status}. {new_findings} new result(s) await review.\n"
            f"Open Triage: {triage_url}\n"
            "Nothing reaches the client until you press Send in Triage.")
    return create_owner_task(deal, subject, desc, owner_id=who)


def mark_failed(deal_id: str, error: str) -> None:
    _update([{"id": deal_id, "Latest_Braudit_Run_Status": deal_fields.RUN_STATUS["failed"],
              "Braudit_Trigger_Status": deal_fields.TRIGGER_STATUS["failed"],
              "Last_Braudit_Run_At": _now(), "Braudit_Last_Error": error[:255]}])


def fetch_logo_data_url(ref: dict | None) -> str:
    """The logo held on Image_Mark_Information.Image_JPEG, as a data: URL the
    report builder can embed (report_docx._client_logo_bytes accepts data:).
    Empty string when there is no file or the fetch fails — a report must
    still build without the picture (the panel then reads 'not shown')."""
    fid = (ref or {}).get("file_id")
    if not fid:
        return ""
    try:
        import base64
        raw = zoho.download(f"files?id={fid}")
        if not raw:
            return ""
        name = str((ref or {}).get("name") or "").lower()
        mime = ("image/png" if name.endswith(".png") else
                "image/gif" if name.endswith(".gif") else "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(raw).decode()
    except Exception as exc:                            # reported, never fatal
        print(f"[warn] logo fetch failed for file {fid}: {exc}", file=sys.stderr)
        return ""
