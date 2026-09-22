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
    G_S_Scope_Assets -> G_S_Scope_Classes_Terms  classes and the goods text (the sub-form is retired, 17 Sep 2026)
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
    deal["_scope_rows"] = _scope_rows(deal_id)
    return deal


def _scope_rows(deal_id: str) -> list[dict]:
    """The Deal's classes & terms from its G S Scope Asset (Jonathan, 17 Sep
    2026: the Deals Classes_Terms sub-form is retired; the asset is the one
    source of truth). Prefers the checkout's own "Brand Audit - " asset, else
    the first Deal Search asset. Related-list reads, never search (it lags)."""
    try:
        assets = zoho.call("GET", f"Deals/{deal_id}/G_S_Scope_Assets?fields=id,Name,Asset_Type").get("data") or []
    except Exception as exc:
        print(f"[scope] asset lookup failed for {deal_id}: {exc}", file=sys.stderr)
        return []
    assets = [a for a in assets if a.get("Asset_Type") == "Deal Search"]
    if not assets:
        return []
    chosen = next((a for a in assets if str(a.get("Name") or "").startswith("Brand Audit - ")), assets[0])
    try:
        rows = zoho.call("GET", f"G_S_Scope_Assets/{chosen['id']}/G_S_Scope_Classes_Terms"
                         "?fields=Nice_Class_Number_Snapshot,Class_Description_Snapshot,Specific_Terms,Terms_Source,Display_Order").get("data") or []
    except Exception as exc:
        print(f"[scope] class rows failed for asset {chosen['id']}: {exc}", file=sys.stderr)
        return []
    out = []
    for r in rows:
        try:
            n = int(r.get("Nice_Class_Number_Snapshot"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 45:
            out.append({"n": n, "description": r.get("Class_Description_Snapshot") or "",
                        "terms": (r.get("Specific_Terms") or "").strip(), "source": r.get("Terms_Source") or "",
                        "order": r.get("Display_Order") or 0})
    out.sort(key=lambda x: (x["order"] or 999, x["n"]))
    return out


def _classes_and_goods(deal: dict) -> tuple[list[int], str]:
    classes, parts = [], []
    # Source of truth (17 Sep 2026): the G S Scope Asset rows fetch_deal
    # attached. Specific_Terms is "; "-joined, one line per class.
    for row in deal.get("_scope_rows") or []:
        if row["n"] not in classes:
            classes.append(row["n"])
        if row["terms"]:
            parts.append(f"Class {row['n']}: {row['terms']}")
    if classes:
        return classes, "\n".join(parts)
    # Legacy fallback while the retired sub-form still exists on old Deals.
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


def _criteria(deal: dict) -> tuple[str, list[tuple], bool]:
    """(mark_text, declared criteria, declared_governs). Word 1 is the mark.

    Until 22 Sep 2026 this returned `words[1:]` and word 1's OPERATOR was
    discarded on that line — its phrase became `mark_text` and the operator
    the client chose went nowhere. `criteria.build()` then derived four shapes
    from the bare mark. A Deal set to Exact Match ran a class-filtered
    `Contains` on its distinctive stem, which is the opposite of what Exact
    Match is chosen for. Word 1 now travels WITH its operator.

    `declared_governs` is false for the legacy reader, which reconstructs a
    phrase from TM_Text or the Deal name and pairs it with a "Similar To"
    nobody selected. A guess must not be allowed to suppress the derived
    criteria; only a person's choice does that.
    """
    words, src = deal_fields.keywords(deal)
    if not words:
        raise RuntimeError("Deal has no search words (Search_Word_1 / TM_Text) — nothing to audit")
    return words[0][1], list(words), src in deal_fields.DECLARED_SOURCES


def _contact_email(deal: dict) -> str:
    """The client's email for the report cover. The Deal's own Email fields are
    usually blank; the address lives on the linked Contact, so fetch it once."""
    for f in ("App_Contact_Email", "Email"):
        if (deal.get(f) or "").strip():
            return deal[f].strip()
    cid = (deal.get("Contact_Name") or {}).get("id")
    if not cid:
        return ""
    try:
        data = (zoho.call("GET", f"Contacts/{cid}") or {}).get("data") or []
        if data:
            return (data[0].get("Email") or data[0].get("Secondary_Email") or "").strip()
    except Exception as exc:
        print(f"[cover] contact email unavailable for {cid}: {exc}", file=sys.stderr)
    return ""


def _channels(deal: dict) -> dict:
    ch = deal_fields.channels(deal)
    picked = any(deal.get(f) for f in ("Audit_Search_Layers", "Other_Search_Platforms",
                                        "Social_Platforms", "Marketplace_Platforms",
                                        "Search_Engine_Platforms"))
    if not picked:
        # Nothing selected on the Deal → search EVERYTHING (Jonathan, 16 Sep
        # 2026, 02:20: "Social, Domain, Marketplace — historically worse than
        # what we have now, better to find now and add to report"; applies to
        # audit or monitoring run via the new system). This is the interim
        # rule for Deals created before the platform picklists were required;
        # once 'None' is enforced (#39) an empty field blocks instead and this
        # branch stops being reachable. An explicit selection is still honoured
        # exactly as made.
        ch = {"socials": sorted(set(deal_fields._LAYER_SOCIAL.values())),
              "marketplaces": sorted(set(deal_fields._LAYER_MARKET.values())),
              "include_companies": True, "include_domains": True, "include_serp": True,
              "socials_selected": True, "markets_selected": True,
              "source": "default_all (no platforms selected on the Deal)"}
    return ch


def _image_assets(deal: dict) -> list:
    """The Image Assets this Deal's image lines point at.

    Wired 22 Sep 2026. Image_Mark_Information.Image_Asset was added on 18 Sep
    and 273 of 313 lines now point at one, but nothing read it - so the 236
    assets and every Vienna code held against them were invisible to the
    engine, which went on reading the inline copies.

    A Deal has a handful of image lines at most, so this is a handful of GETs.
    Deduplicated, because several lines on one Deal can share an asset.
    """
    seen, out = set(), []
    for row in deal.get("Image_Mark_Information") or []:
        aid = (row.get("Image_Asset") or {}).get("id")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        try:
            rec = (zoho.call("GET", f"Image_Assets/{aid}").get("data") or [{}])[0]
        except Exception as exc:
            print(f"[image asset] {aid}: {exc}", file=sys.stderr)
            continue
        if rec:
            out.append(rec)
    return out


def _image_inputs(deal: dict) -> dict:
    """Vienna codes + logo state from the adapter; image search only when the
    Deal asked for it (Braudit_Search_Types has 'image', or Report_Type says
    so) OR codes were entered — a coded logo is an explicit request."""
    assets = _image_assets(deal)
    codes = deal_fields.vienna_codes(deal, assets)
    lg = deal_fields.logo(deal, assets)
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
    mark, extra, declared = _criteria(deal)
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
        criteria_declared=declared,
        deal_id=str(deal.get("id") or ""),
        nature_of_business=deal.get("Nature_of_Business") or "",
        deal_name=deal.get("Deal_Name") or "",
        contact_name=contact,
        contact_email=_contact_email(deal),
        deal_owner=(deal.get("Owner") or {}).get("name") or "",
        sic_code=str(deal.get("SIC_Code") or "").strip(),
        search_date=search_date or date.today(),
        include_companies=ch["include_companies"],
        include_domains=ch["include_domains"],
        include_serp=ch["include_serp"],
        register_layers=ch.get("register_layers") or [],
        # an explicit empty selection means "none of these", not "all of them"
        socials=ch["socials"] if ch["socials_selected"] else [],
        marketplaces=ch["marketplaces"] if ch["markets_selected"] else [],
        vienna_codes=img["vienna_codes"],
        logo_awaited=img["logo_awaited"],
        logo_ref=img["logo_ref"],
        domain_criteria=deal_fields.domain_criteria(deal)[0],
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
    mark, extra, declared = _criteria(deal)
    classes, goods = _classes_and_goods(deal)
    img = _image_inputs(deal)
    words, kw_source = deal_fields.keywords(deal)
    juris, juris_source = deal_fields.jurisdictions(deal)
    return {
        "deal": deal.get("Deal_Name"), "stage": deal.get("Stage"), "next_action": deal.get("Next_Action"),
        "client": (deal.get("Account_Name") or {}).get("name"), "contact": (deal.get("Contact_Name") or {}).get("name"),
        "mark": mark, "extra_criteria": extra, "keyword_source": kw_source,
        "criteria_declared": declared,
        "operator_notes": deal_fields.keyword_notes(),
        "classes": classes, "goods_chars": len(goods),
        "jurisdictions": juris, "jurisdiction_source": juris_source, "channels": _channels(deal),
        "vienna_codes": img["vienna_codes"], "logo_state": img["logo_state"], "wants_image": img["wants_image"],
        "domain_criteria": deal_fields.domain_criteria(deal)[0],
        "domain_criteria_source": deal_fields.domain_criteria(deal)[1],
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
        problems.append("no Nice classes (no G S Scope Asset class rows)")
    if not (deal.get("Account_Name") or {}).get("id"):
        problems.append("no Account linked")
    img = _image_inputs(deal)
    if img["wants_image"] and img["logo_state"] == "none" and not img["vienna_codes"]:
        problems.append("image search requested but no logo attached and no Vienna codes")

    # Monitoring-only gates (Jonathan, 16 Sep 2026). Frequency is what makes
    # a monitoring Deal a monitoring Deal — without it there is no cadence to
    # report against. And what matters commercially is not the payment record
    # but that the Deal shows as active: a Suspended or Cancelled subscription
    # must never be researched, whatever the payment fields say.
    if run_kind(deal) == "monitoring":
        if not (deal.get("Report_Frequency") or "").strip():
            problems.append("monitoring Deal has no Report Frequency "
                            "(Monthly / Quarterly / 6-Monthly / Annually)")
        sub = (deal.get("Subscription_Status") or "").strip()
        if sub in ("Suspended", "Cancelled"):
            problems.append(f"subscription is {sub} — not researched until the Deal is active")
        # Empty does NOT block (Jonathan, 16 Sep 2026, 01:50): the field is not
        # yet live — ~100 monitoring reports a month are managed by hand off the
        # Stage (Paid / Invoiced / Awaiting Payment) — and blocking on it stalled
        # 97 of the 111 Deals in the backlog. It is a warning instead (see
        # warnings()); re-tighten once Subscription Status is actually managed.
    return problems


def warnings(deal: dict) -> list[str]:
    """Things a staff member should see before pressing go that do NOT stop
    the run. Shown in the preflight panel beside the problems."""
    out = []
    if run_kind(deal) == "monitoring":
        if not (deal.get("Subscription_Status") or "").strip():
            out.append("Subscription Status is empty — the run will go ahead; set it to "
                       "Active (or Pending Start for a first run) when you can")
    if str(_channels(deal).get("source", "")).startswith("default_all"):
        out.append("no search platforms selected on the Deal — the run will search everything "
                   "(Companies House, domains, Google, all socials, all marketplaces)")
    return out


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
