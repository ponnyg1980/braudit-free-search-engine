"""The audit app — webhook, staff triage, client magic link. One process.

    python3 -m audit_engine.webapp --port 8787

    POST /run?deal_id=…&key=…      Zoho workflow webhook (queues a run)
    GET  /                          staff: runs list          (login: a STAFF_TOKENS token)
    GET  /runs/{run_id}             staff: triage — exclude / report / hold, row by row, with reasons
    GET  /r/{token}                 client: the magic link — portfolio, exclusions by group,
                                    results by group, reported, forensic
    GET  /health

Everything the pages show comes from the `audit` schema in Supabase through
`store.py`; every review action records who did it. The engine and the
Zoho write-back live in `worker.py`; this file is the HTTP surface.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import worker
from .store import Store, portfolio_from_urls

_HERE = Path(__file__).resolve().parent
app = FastAPI(title="TMH Audit", docs_url=None, redoc_url=None)

CHANNELS = [("trademark", "Trademarks"), ("company", "Companies"), ("domain", "Domains"),
            ("web", "Web"), ("social", "Social"), ("marketplace", "Marketplaces")]
REASONS = [("own_asset", "Client's own asset"), ("unrelated_goods", "Unrelated goods / services"),
           ("dead_or_parked", "Dead, parked or dormant"), ("duplicate", "Duplicate"),
           ("staff_judgement", "Staff judgement"), ("client_instruction", "Client instruction")]
BAND_ORDER = ["High", "Medium/High", "Medium", "Low/Medium", "Low", "Result (not live)",
              "Not Assessed", "Available"]


# ---------------------------------------------------------------------------
# staff auth — a STAFF_TOKENS entry, kept in an HttpOnly cookie
# ---------------------------------------------------------------------------

def _staff_tokens() -> dict[str, dict]:
    raw = worker._secret("STAFF_TOKENS") or "[]"
    try:
        items = json.loads(raw)
    except Exception:
        items = []
    return {it.get("t"): {"name": it.get("n"), "email": it.get("e")} for it in items if it.get("t")}


def staff(request: Request) -> dict:
    tok = request.cookies.get("tmh_staff") or request.headers.get("X-Staff-Token") or ""
    for known, who in _staff_tokens().items():
        if hmac.compare_digest(tok, known):
            return who
    raise HTTPException(status_code=401, detail="login required")


def _store() -> Store:
    return Store()


def _json(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "hex") and not isinstance(o, (bytes, str)):  # uuid
        return str(o)
    raise TypeError(str(type(o)))


def _dumps(o) -> str:
    return json.dumps(o, default=_json)


# ---------------------------------------------------------------------------
# webhook + health (unchanged contract from worker.py)
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "queued": worker._jobs.qsize()}


@app.post("/run")
async def webhook_run(request: Request, deal_id: str = "", key: str = "", kind: str = "audit"):
    body: dict = {}
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body = await request.json()
    elif ctype:
        form = await request.form()
        body = dict(form)
    deal_id = (body.get("deal_id") or deal_id or "").strip()
    key = request.headers.get("X-Audit-Key") or body.get("key") or key
    expected = worker._secret("AUDIT_WORKER_KEY") or ""
    if not expected or not hmac.compare_digest(str(key), expected):
        raise HTTPException(401, "unauthorised")
    if not deal_id.isdigit():
        raise HTTPException(400, "deal_id required")
    out = worker.enqueue(deal_id, body.get("kind") or kind, "zoho-webhook")
    return JSONResponse(out, status_code=202 if out["queued"] else 200)


# ---------------------------------------------------------------------------
# staff JSON API
# ---------------------------------------------------------------------------

@app.get("/api/runs")
def api_runs(who: dict = Depends(staff)):
    return Response(_dumps({"runs": _store().list_runs(), "queue": dict(worker._state)}),
                    media_type="application/json")


def _search_score_settings(st, run: dict, run_id: str) -> dict:
    """The Search & Score Settings panel's data (18 Sep 2026).

    Read-only. `scored_by` is the package version that produced this run's
    numbers and `package` is what is loaded now; when they differ the panel
    says so, because a breakdown recomputed under a later scorer would be a
    different answer to the one the client was given.
    """
    try:
        import tmh_scoring
        package = str(tmh_scoring.__version__)
    except Exception:
        package = None
    return {"ignored": st.ignored_words(run_id),
            "scored_by": run.get("scoring_version"),
            "package": package}


@app.post("/api/runs/{run_id}/ignored/restore")
async def api_ignored_restore(run_id: str, request: Request, who: dict = Depends(staff)):
    """Put a weak word back. Structural words are refused: un-ignoring "Ltd"
    does not sharpen a search, it floods it."""
    body = await request.json()
    word = str(body.get("word") or "").strip()
    if not word:
        raise HTTPException(400, "word is required")
    n = _store().restore_ignored_word(run_id, word, who["name"])
    if not n:
        raise HTTPException(409, "that word is not on this run's weak list "
                                 "(structural words cannot be restored)")
    return {"restored": word, "rows": n,
            "note": "the weak list acts at search time, so re-run the audit for "
                    "this to take effect"}


@app.post("/api/runs/{run_id}/ignored")
async def api_ignored_add(run_id: str, request: Request, who: dict = Depends(staff)):
    """Add a word to ignore on this run, optionally proposing it as global.

    The proposal is a queue entry, never a write to a global list — global
    settings are R&D's, and the queue carries the evidence they are ruled on.
    """
    body = await request.json()
    word = str(body.get("word") or "").strip()
    if len(word) < 2:
        raise HTTPException(400, "a word of at least two characters is required")
    st = _store()
    out = st.add_ignored_word(run_id, word, who["name"])
    out["evidence"] = st.word_evidence(run_id, word)
    if body.get("propose_global"):
        out["proposal"] = st.propose_global(word, run_id, who["name"],
                                            str(body.get("reason") or ""))
    return out


@app.get("/api/proposals")
def api_proposals(status: str = "proposed", who: dict = Depends(staff)):
    """The global-exclusion queue. Read-only here: ruling happens in R&D."""
    return {"proposals": _store().proposals(status)}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str, who: dict = Depends(staff)):
    st = _store()
    run = st.run(run_id)
    if not run:
        raise HTTPException(404)
    cid = str(run["client_id"]) if run.get("client_id") else None
    from . import pages
    plan = st.plan(run_id)
    from . import images
    results = st.results(run_id)
    for r in results:
        r["image_src"] = images.image_src(r)
    payload = {
        "run": run, "results": results, "plan": plan,
        "portfolio": st.portfolio(cid) if cid else [], "exclusions": st.exclusions(cid) if cid else [],
        "reports": st.reports_for_run(run_id), "me": who,
        "classes": pages.classes_detail(run), "requested": pages.requested_platforms(run, plan),
        "terms": pages.terms_by_channel(plan), "forensic": st.forensic(run_id),
        "settings": _search_score_settings(st, run, run_id),
    }
    return Response(_dumps(payload), media_type="application/json")


@app.get("/i/{run_id}/{name}")
def stored_image(run_id: str, name: str):
    """Our stored copy of a result image (images.py). The path is a run UUID
    plus a result UUID, so it is unguessable without the triage or client
    link that shows it; marked noindex like the client pages."""
    from . import images
    import re as _re
    if not (_re.fullmatch(r"[0-9a-f-]{36}", run_id) and _re.fullmatch(r"[0-9a-f-]{36}\.(png|jpg)", name)):
        raise HTTPException(404)
    f = images.IMAGES_DIR / run_id / name
    if not f.is_file():
        raise HTTPException(404)
    return Response(f.read_bytes(), media_type="image/png" if name.endswith(".png") else "image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400", "X-Robots-Tag": "noindex"})


@app.post("/api/results/review")
async def api_review(request: Request, who: dict = Depends(staff)):
    body = await request.json()
    ids = [str(i) for i in body.get("ids") or []]
    status = body.get("status")
    if status == "note":                        # edit Additional Comments only
        n = _store().set_note(ids, body.get("note") or "", by=who["name"])
        return {"updated": n}
    if status not in ("new", "excluded", "reported", "held", "forensic") or not ids:
        raise HTTPException(400, "ids and a valid status are required")
    st = _store()
    n = st.review(ids, status, reason=body.get("reason"), note=body.get("note"), by=who["name"])
    zoho_out = []
    if status == "excluded":
        # the record of exclusions is Zoho Client_Search_Exclusions (11 Sep ruling)
        from . import zoho_exclusions
        learned = st.learned_for_results(ids)
        by_account: dict = {}
        for e in learned:
            by_account.setdefault((e.get("zoho_account_id"), str(e.get("run_id"))), []).append(e)
        for (acc, run_id), rows in by_account.items():
            zoho_out += zoho_exclusions.upsert(rows, acc, run_id=run_id, by=who["name"])
    return {"updated": n, "zoho": zoho_out}


@app.post("/api/runs/{run_id}/report")
async def api_report(run_id: str, request: Request, who: dict = Depends(staff)):
    body = await request.json()
    st = _store()
    ids = [str(r["id"]) for r in st.results(run_id) if r["review_status"] == "reported"]
    if not ids and not body.get("allow_empty"):
        raise HTTPException(409, "no findings")          # the UI asks "is that okay?" and retries with allow_empty
    s4 = st.section4(run_id)
    if s4["section4_included"] and not s4["section4_approved"]:
        raise HTTPException(409, "Section 4 is included but not yet approved — tick Approve Section 4 first")
    try:
        out = st.create_report(run_id, ids, body.get("title") or "Trademark audit", by=who["name"],
                               expires_days=body.get("expires_days"))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    base = str(request.base_url).rstrip("/")
    out["url"] = f"{base}/r/{out['token']}"
    out["results"] = len(ids)
    return out


@app.post("/api/runs/{run_id}/findings/clear")
def api_clear_findings(run_id: str, who: dict = Depends(staff)):
    """Move every Reported row back to Results (new)."""
    return {"cleared": _store().clear_findings(run_id, who["name"])}


@app.post("/api/runs/{run_id}/forensic")
async def api_forensic(run_id: str, request: Request, who: dict = Depends(staff)):
    """Run forensic reviews on the selected rows, then draft Section 4 from every review on the run."""
    from . import forensic
    body = await request.json()
    ids = {str(i) for i in body.get("ids") or []}
    if not ids:
        raise HTTPException(400, "ids required")
    st = _store()
    run = st.run(run_id)
    if not run:
        raise HTTPException(404)
    rows = [r for r in st.results(run_id) if str(r["id"]) in ids]
    done, failed = [], []
    for r in rows[:40]:                        # a forensic pass is deliberate; 40 rows ≈ 3 minutes
        try:
            rev = forensic.review_one(r, run)
            st.add_forensic(str(r["id"]), rev, who["name"])
            done.append({"id": str(r["id"]), "title": r["title"], **{k: rev[k] for k in ("verdict", "band_suggested", "rationale", "recommendation")}})
        except Exception as exc:
            failed.append({"id": str(r["id"]), "title": r["title"], "error": str(exc)[:200]})
    # draft Section 4 from the latest review per reported row, unless staff have approved a wording
    s4 = st.section4(run_id)
    if not s4["section4_approved"]:
        latest = {}
        for f in st.forensic(run_id):
            latest.setdefault(str(f["result_id"]), f)
        reported = {str(r["id"]) for r in st.results(run_id) if r["review_status"] == "reported"}
        text = forensic.section4_text([f for rid, f in latest.items() if rid in reported], run["mark_text"])
        if text:
            st.set_section4(run_id, text=text)
    return {"reviewed": done, "failed": failed, "section4": st.section4(run_id)}


@app.get("/api/runs/{run_id}/section4")
def api_section4_get(run_id: str, who: dict = Depends(staff)):
    return Response(_dumps(_store().section4(run_id)), media_type="application/json")


@app.post("/api/runs/{run_id}/section4")
async def api_section4_set(run_id: str, request: Request, who: dict = Depends(staff)):
    body = await request.json()
    out = _store().set_section4(run_id, included=body.get("included"), text=body.get("text"),
                                approved=body.get("approved"), by=who["name"])
    return Response(_dumps(out), media_type="application/json")


def _public_base() -> str:
    return (os.environ.get("AUDIT_PUBLIC_BASE")
            or worker._secret("AUDIT_PUBLIC_BASE")
            or "https://audit.thetrademarkhelpline.com").rstrip("/")


def _client_payload_for_token(token: str):
    from . import pages
    st = _store()
    view = st.report_view(token)
    if not view:
        return None, None
    run = st.run(view["run"]["id"])
    payload = pages.client_payload(view, run, st.plan(view["run"]["id"]), st.results(view["run"]["id"]))
    # report_view() returns neither the author nor the token; both belong on
    # the cover, and the client link must be printed in the document itself
    # (Jonathan, 16 Sep) or a printed report has no way back to the full results.
    meta = st.report_meta(token)
    payload["link_url"] = payload["client_url"] = f"{_public_base()}/r/{token}"
    if meta.get("issued_by"):          # the staff member who created the report in triage
        payload.setdefault("cover", {})["prepared_by"] = meta["issued_by"]
    return payload, st


def _docx_response(payload: dict):
    from . import report_docx
    data = report_docx.build(payload)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{report_docx.filename(payload)}"',
                             "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, no-store"})


@app.get("/api/reports/{report_id}/report.docx")
def api_report_docx(report_id: str, who: dict = Depends(staff)):
    """The client report as an editable Word document — staff read it, amend it, then send."""
    rep = _store().report(report_id)
    if not rep:
        raise HTTPException(404)
    payload, _ = _client_payload_for_token(rep["token"])
    if not payload:                      # revoked or expired: build it anyway for staff
        from . import pages
        st = _store()
        raise HTTPException(410, "this report has been revoked; issue a new one to download it")
    return _docx_response(payload)


@app.get("/r/{token}/report.docx")
def client_report_docx(token: str):
    payload, _ = _client_payload_for_token(token)
    if not payload:
        raise HTTPException(404)
    return _docx_response(payload)


def _pdf_response(payload: dict):
    """Same report as PDF: staff who are happy with the view download it and
    send it themselves. Rendered from the Word document so the two match."""
    from . import report_docx, report_pdf
    try:
        data = report_pdf.docx_to_pdf(report_docx.build(payload))
    except Exception as exc:
        raise HTTPException(503, f"PDF conversion unavailable: {exc}")
    name = report_docx.filename(payload).rsplit(".", 1)[0] + ".pdf"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "X-Robots-Tag": "noindex, nofollow", "Cache-Control": "private, no-store"})


@app.get("/api/reports/{report_id}/report.pdf")
def api_report_pdf(report_id: str, who: dict = Depends(staff)):
    rep = _store().report(report_id)
    if not rep:
        raise HTTPException(404)
    payload, _ = _client_payload_for_token(rep["token"])
    if not payload:
        raise HTTPException(410, "this report has been revoked; issue a new one to download it")
    return _pdf_response(payload)


@app.get("/r/{token}/report.pdf")
def client_report_pdf(token: str):
    payload, _ = _client_payload_for_token(token)
    if not payload:
        raise HTTPException(404)
    return _pdf_response(payload)


@app.post("/api/reports/{report_id}/shared")
async def api_report_shared(report_id: str, request: Request, who: dict = Depends(staff)):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    return {"updated": _store().mark_shared(report_id, who["name"], bool(body.get("shared", True)))}


@app.delete("/api/reports/{report_id}")
def api_report_revoke(report_id: str, who: dict = Depends(staff)):
    return {"revoked": _store().revoke_report(report_id, who["name"])}


@app.get("/api/runs/{run_id}/export.xlsx")
def api_export(run_id: str, who: dict = Depends(staff)):
    """The workbook, rebuilt from the stored run — Order Form + builder sheets + detail sheets."""
    import tempfile
    from .export import result_from_run
    from .xlsx_out import build_workbook
    st = _store()
    run = st.run(run_id)
    if not run:
        raise HTTPException(404)
    result = result_from_run(run, st.results(run_id), st.plan(run_id))
    safe = "".join(ch for ch in (run.get("client_name") or run["mark_text"]) if ch.isalnum() or ch in " -_").strip()
    fname = f"{date.today():%Y-%m-%d} - {safe or 'audit'} - TM audit.xlsx"
    with tempfile.TemporaryDirectory() as td:
        path = build_workbook(result, str(_HERE / "template" / "audit_template.xlsx"), f"{td}/{fname}")
        data = Path(path).read_bytes()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/runs/{run_id}/import")
async def api_import(run_id: str, request: Request, who: dict = Depends(staff)):
    """Add results from an uploaded Excel order form (old builder shape or our detail shape)."""
    from .export import result_from_run
    from .imports import parse_workbook, score_imported, to_result_rows
    form = await request.form()
    up = form.get("file")
    if up is None:
        raise HTTPException(400, "file required")
    data = await up.read()
    st = _store()
    run = st.run(run_id)
    if not run:
        raise HTTPException(404)
    label = f"manual import ({getattr(up, 'filename', 'xlsx')})"
    imp = parse_workbook(data, label)
    if not imp.total():
        raise HTTPException(400, "no recognisable result sheets in that workbook")
    base = result_from_run(run, [], [])
    score_imported(imp, base.criteria, base.request)
    rows = to_result_rows(imp, label)
    out = st.add_results(run_id, str(run["client_id"]) if run.get("client_id") else None, rows, by=who["name"])
    out["read"] = {"trademarks": len(imp.tm), "companies": len(imp.companies), "domains": len(imp.domains),
                   "web_social_marketplace": len(imp.serp)}
    return out


@app.post("/api/runs/{run_id}/rerun")
def api_rerun(run_id: str, who: dict = Depends(staff)):
    run = _store().run(run_id)
    if not run or not run.get("zoho_deal_id"):
        raise HTTPException(400, "run has no Deal to re-read")
    return worker.enqueue(run["zoho_deal_id"], "monitoring", who["name"], force=True)


@app.get("/api/deals/{deal_id}/preflight")
def api_preflight(deal_id: str, who: dict = Depends(staff)):
    """What would happen if this Deal were sent for research — before it is.

    Runs the same validate() the worker runs, plus the summary of what the
    engine would search, so a staff member can see the problems and fix
    them in the Deal rather than discover them from a bounced Deal and a
    task. Read-only; nothing is queued and nothing is written to Zoho.
    """
    from . import deal_reader
    if not deal_id.isdigit():
        raise HTTPException(400, "deal_id required")
    try:
        deal = deal_reader.fetch_deal(deal_id)
    except Exception as exc:
        raise HTTPException(404, f"Deal not found or Zoho unavailable: {exc}")
    problems = deal_reader.validate(deal)
    warns = deal_reader.warnings(deal)
    try:
        summary = deal_reader.deal_summary(deal)
    except Exception as exc:                 # a Deal so empty that summary itself fails
        summary = {"error": str(exc)}
    owner = (deal.get("Owner") or {}).get("name")
    return Response(_dumps({
        "deal_id": deal_id, "deal_name": deal.get("Deal_Name"), "owner": owner,
        "pipeline": deal_reader.run_kind(deal), "stage": deal.get("Stage"),
        "next_action": deal.get("Next_Action"),
        "ok": not problems, "problems": problems, "warnings": warns, "summary": summary,
        "legacy_report": bool(deal.get("Excel_Report_Link")),
    }), media_type="application/json")


@app.post("/api/run")
async def api_run_deal(request: Request, who: dict = Depends(staff)):
    body = await request.json()
    deal_id = str(body.get("deal_id") or "").strip()
    if not deal_id.isdigit():
        raise HTTPException(400, "deal_id required")
    return worker.enqueue(deal_id, body.get("kind") or "audit", who["name"], force=bool(body.get("force")))


@app.post("/api/portfolio")
async def api_portfolio_add(request: Request, who: dict = Depends(staff)):
    body = await request.json()
    st = _store()
    if body.get("urls"):
        items = portfolio_from_urls([u for u in str(body["urls"]).replace("\n", ",").split(",") if u.strip()])
        n = st.seed_portfolio(body["client_id"], items, source="staff", by=who["name"])
    else:
        n = st.add_portfolio(body["client_id"], body["kind"], body["value"], body.get("label"), who["name"])
        items = [(body["kind"], body["value"], body.get("label"))]
    # own assets are exclusions too: mirror to Zoho Client_Search_Exclusions (Client-Owned)
    from . import zoho_exclusions
    from .store import _norm_portfolio_value
    values = [_norm_portfolio_value(k, v) for k, v, _ in items]
    rows = st.portfolio_items(body["client_id"], values)
    zoho_out = zoho_exclusions.upsert_portfolio(rows, st.client_account(body["client_id"]), by=who["name"])
    return {"added": n, "zoho": zoho_out}


@app.delete("/api/portfolio/{item_id}")
def api_portfolio_del(item_id: str, who: dict = Depends(staff)):
    return {"removed": _store().remove_portfolio(item_id)}


@app.delete("/api/exclusions/{excl_id}")
def api_exclusion_del(excl_id: str, who: dict = Depends(staff)):
    from . import zoho_exclusions
    n = _store().deactivate_exclusion(excl_id)
    return {"deactivated": n, "zoho": zoho_exclusions.deactivate(excl_id)}


# ---------------------------------------------------------------------------
# pages — HTML lives in pages.py
# ---------------------------------------------------------------------------

from . import pages  # noqa: E402


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return pages.login_page()


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    tok = str(form.get("token") or "")
    for known in _staff_tokens():
        if hmac.compare_digest(tok, known):
            r = RedirectResponse("/", status_code=303)
            r.set_cookie("tmh_staff", tok, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30,
                         secure=request.url.scheme == "https")
            return r
    return HTMLResponse(pages.login_page("That token is not recognised."), status_code=401)


@app.get("/", response_class=HTMLResponse)
def runs_page(request: Request):
    try:
        who = staff(request)
    except HTTPException:
        return RedirectResponse("/login")
    return pages.runs_page(who["name"])


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def triage_page(run_id: str, request: Request):
    try:
        who = staff(request)
    except HTTPException:
        return RedirectResponse("/login")
    return pages.triage_page(run_id, who["name"])


@app.get("/r/{token}", response_class=HTMLResponse)
def report_page(token: str):
    payload, _ = _client_payload_for_token(token)
    if not payload:
        return HTMLResponse(pages.shell("Not found", "", "",
                                        '<div class="pane on"><h2>This link is not valid</h2><p>It may have expired or been replaced. '
                                        'Please contact The Trademark Helpline on 0161 833 5400.</p></div>', ""), status_code=404)
    payload["token"] = token
    html = pages.report_page(payload)
    return HTMLResponse(html, headers={"X-Robots-Tag": "noindex, nofollow, noarchive", "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import uvicorn
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    args = p.parse_args(argv)
    if not worker._secret("AUDIT_WORKER_KEY"):
        print("AUDIT_WORKER_KEY is not set — refusing to start an open webhook", file=sys.stderr)
        return 2
    if not _staff_tokens():
        print("STAFF_TOKENS is empty — nobody could log in", file=sys.stderr)
        return 2
    threading.Thread(target=worker._worker_loop, daemon=True, name="audit-worker").start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
