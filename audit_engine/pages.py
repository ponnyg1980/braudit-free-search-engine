"""HTML for the staff triage screen and the client magic link.

Both are laid out like the fortnightly monitoring report (uk_monitor):
white top bar with the TMH logo, dark hero with the client and mark, a tab
bar with badges, one white pane per tab, results tables with the same
columns the client already knows. The live stylesheet is inlined from
uk_monitor/_design/report_theme.css so the two products stay in step.

What each audience sees is decided here, on purpose:

  staff    the class headings AND the client's own terms per class; every
           platform requested; the literal search terms per channel; each
           country with the register and the platform used to search it;
           the engine's explanation and which criterion found each row.
  client   the official class headings only (never the specification);
           the word mark, tagline and logo searched — nothing about how;
           the countries searched (not the registers or providers); the
           social and shopping platforms searched (not the queries).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from .nice import HEADINGS, SHORT, country

from . import vienna as _vienna

_HERE = Path(__file__).resolve().parent
_THEME = _HERE.parent / "uk_monitor" / "_design" / "report_theme.css"
_LOGO_SRC = _HERE.parent / "uk_monitor" / "_design" / "SAMPLE_report_reskin.html"

CHANNELS = [("trademark", "Trademarks"), ("company", "Companies"), ("domain", "Domains"),
            ("web", "Web"), ("social", "Social"), ("marketplace", "Marketplaces")]
REASONS = [("own_asset", "Client's own asset"), ("unrelated_goods", "Unrelated goods / services"),
           ("dead_or_parked", "Dead, parked or dormant"), ("duplicate", "Duplicate"),
           ("staff_judgement", "Staff judgement"), ("client_instruction", "Client instruction")]


def _theme() -> str:
    try:
        return _THEME.read_text()
    except Exception:
        return ""


def _logo() -> str:
    try:
        m = re.search(r'<img src="(data:image/svg\+xml;base64,[^"]+)"', _LOGO_SRC.read_text())
        return m.group(1) if m else ""
    except Exception:
        return ""


EXTRA_CSS = """
.pane{padding:22px 24px}.tabs{flex-wrap:wrap}.tick td{font-weight:400}#box-scroll,[id^=box-],#findings,#all-box{overflow-x:auto}.kv{display:grid;grid-template-columns:170px 1fr;gap:6px 14px;font-size:13px}
.kv b{color:#617383;font-weight:600}.kv .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:900px){.two{grid-template-columns:1fr}}
.tick{width:100%}.tick td{padding:7px 10px;font-size:13px}.tick th{font-size:10.5px}
.abtn{font-size:12px;padding:5px 9px}.abtn.r{background:#fff;border-color:#E51652;color:#E51652}.abtn.r:hover{background:#E51652;color:#fff}
.abtn.g{background:#fff;border-color:#1a56b0;color:#1a56b0}.abtn.g:hover{background:#1a56b0;color:#fff}
.abtn.h{background:#fff;border-color:#8A5C11;color:#8A5C11}.abtn.h:hover{background:#8A5C11;color:#fff}
.picker{background:#FBFCFD;border:1px solid #E6E9ED;border-radius:9px;padding:8px;margin-top:6px;display:grid;gap:6px}
.picker select,.picker input{padding:6px 8px;border:1px solid #d8dce2;border-radius:7px;font:inherit;width:100%}
.bulk{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.bulk select,.bulk input{padding:6px 8px;border:1px solid #d8dce2;border-radius:7px;font:inherit}
.row-ex td{opacity:.6}.rev{font-size:12px;color:#3f4c58}.rev .st{font-size:11px;padding:2px 8px}
.st.s-new{border-color:#1a56b0;color:#1a56b0}.st.s-excluded{border-color:#96A2AC;color:#617383}.st.s-reported{border-color:#17704A;color:#17704A}
.st.s-held{border-color:#8A5C11;color:#8A5C11}.st.s-forensic{border-color:#4F3391;color:#4F3391}
.logo-thumb{max-height:80px;max-width:200px;border:1px solid #E6E9ED;border-radius:8px;background:#fff;padding:4px}
/* the shared logo panel — present on every surface, including when empty */
.logo-panel{display:flex;gap:14px;align-items:flex-start}
.logo-frame{flex:0 0 118px;height:118px;border:1px solid #E6E9ED;border-radius:10px;background:#fff;
  display:flex;align-items:center;justify-content:center;padding:8px;box-sizing:border-box}
.logo-frame img{max-width:100%;max-height:100%;object-fit:contain}
.logo-frame.empty{border-style:dashed;background:#FAFBFC;color:#96A0AC;font-size:11.5px;
  text-align:center;line-height:1.4;padding:10px}
.logo-body{min-width:0;flex:1}
.logo-state{font-weight:600;font-size:12.5px}
.logo-state.none,.logo-state.awaited{color:#96A0AC}
.logo-line{color:#5B6672;font-size:12.5px;margin:3px 0 7px}
.vienna-list{margin:0;padding:0;list-style:none;font-size:12.5px}
.vienna-list li{padding:3px 0;border-top:1px solid #F0F2F5;display:flex;gap:10px}
.vienna-list li:first-child{border-top:0}
.vienna-code{flex:0 0 62px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#C4123F}
.vienna-note{color:#96A0AC;font-size:11.5px;margin-top:2px}
.logo-warn{margin-top:7px;font-size:12px;color:#8A5A00;background:#FFF8E6;border:1px solid #F3E2B3;
  border-radius:7px;padding:6px 9px}
.plat{display:inline-block;margin:2px 4px 2px 0;padding:3px 9px;background:#EEF1F4;border-radius:6px;font-size:12px}
.q{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#2D455A}
.subtabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}.subtabs .chip{padding:5px 11px}
.hd-note{font-size:12.5px;color:#617383;margin:0 0 12px}
#t input[type=checkbox]{width:15px;height:15px}
.login{max-width:380px;margin:80px auto;background:#fff;border:1px solid #E6E9ED;border-radius:14px;padding:24px}
.login input{width:100%;padding:9px;border:1px solid #d8dce2;border-radius:7px;font:inherit;margin:8px 0 12px}
.overlay{position:fixed;inset:0;background:rgba(29,29,27,.45);display:flex;align-items:center;justify-content:center;z-index:50}
.modal{background:#fff;border-radius:14px;padding:22px 24px;max-width:760px;width:94%;max-height:86vh;overflow:auto;box-shadow:0 18px 50px rgba(0,0,0,.25)}
.modal h3{margin:0 0 8px}.modal .list{max-height:46vh;overflow:auto;border:1px solid #E6E9ED;border-radius:9px;margin:10px 0}
.modal .list label{display:flex;gap:10px;padding:8px 10px;border-bottom:1px solid #EEF1F4;font-size:13px;cursor:pointer}.modal .list label:last-child{border-bottom:0}
.modal .btns{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
.s4{border:1px solid #E6E9ED;border-radius:14px;padding:16px 18px;background:#FBFCFD;margin:0 0 18px}.s4 textarea{width:100%;min-height:150px;padding:10px;border:1px solid #d8dce2;border-radius:8px;font:inherit;font-size:13px}
.s4 label{display:inline-flex;gap:6px;align-items:center;margin-right:16px;font-size:13px}.s4 .ok{color:#17704A;font-weight:700}
.s4-client{border:1px solid #E6E9ED;border-left:5px solid #2D455A;border-radius:10px;padding:16px 18px;margin-top:18px;background:#FBFCFD;white-space:pre-wrap;font-size:13.5px;line-height:1.55}
.notebox{width:100%;min-height:54px;padding:6px 8px;border:1px solid #d8dce2;border-radius:7px;font:inherit;font-size:12.5px}
.fx{font-size:12.5px;line-height:1.5}.fx b.v-conflict{color:#8f1d1d}.fx b.v-no_conflict{color:#17704A}.fx b.v-unsure{color:#8A5C11}
.clamp{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer;overflow-wrap:anywhere}
.clamp.open{-webkit-line-clamp:unset;display:block}.clamp:hover{background:#FBFCFD}
td .lnk{overflow-wrap:anywhere;display:inline-block;min-width:160px;max-width:260px}.thumb{max-height:56px;max-width:90px;border-radius:5px;border:1px solid #E6E9ED}
.wide table{min-width:1600px}
.scrollx{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.wide-cols{min-width:1500px}table.wide-cols th,table.wide-cols td{max-width:260px;vertical-align:top}
table.wide-cols td{overflow-wrap:normal}table.wide-cols td .clamp,table.wide-cols td .lnk{overflow-wrap:anywhere}
table.wide-cols td .tmno{white-space:nowrap}
@media print{.scrollx{overflow:visible}table.wide-cols{min-width:0;font-size:8pt}table.wide-cols th,table.wide-cols td{padding:4px 5px}}.wide th,.wide td{max-width:280px;vertical-align:top}
.tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px;font-size:12.5px;color:#617383}
@media print{.clamp{-webkit-line-clamp:unset;display:block;overflow:visible}.tabs,.head-actions,.filters,.bulk,.abtn,input[type=checkbox]{display:none!important}.pane{display:block!important;page-break-after:always}}
"""


def shell(title: str, hero: str, tabs: str, panes: str, script: str, phone: str = "0161 833 5400",
          staff_name: str | None = None) -> str:
    right = (f'<span class="ph">Signed in as <b>{esc(staff_name)}</b> · <a href="/">All runs</a></span>' if staff_name
             else f'<span class="ph">Questions about this report? Call <a href="tel:{phone.replace(" ", "")}">{phone}</a></span>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>{_theme()}{EXTRA_CSS}</style></head><body>
<div class="topbar"><div class="in"><a href="https://www.thetrademarkhelpline.com" target="_blank" rel="noopener"><img src="{_logo()}" alt="The Trademark Helpline" height="38"></a>{right}</div></div>
{hero}
<div class="wrap"><div class="tabs">{tabs}</div>{panes}</div>
<script>{script}</script></body></html>"""


def esc(s) -> str:
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# One column specification, three audiences
# ---------------------------------------------------------------------------
# Staff triage, the client link and the Word report all describe the same
# result. Defining the columns once is the only way they stay identical: the
# client sees exactly the fields staff see, minus the staff-only ones
# (score, which criterion found it, review state, actions).
#
#   (label, path, kind)   path walks dicts with dots: "dates.filing"
#   kind: text | strong | mono | link | link_to:<path> | image | yesno | title | clamp
RESULT_COLUMNS = {
    "trademark": [
        ("Mark found", "title", "strong"),
        ("Number", "external_ref", "mono"),
        ("Status", "status", "text"),
        ("Type", "detail.mark_type", "text"),
        ("Filed", "dates.filing", "text"),
        ("Registered", "dates.registration", "text"),
        ("Expires", "dates.expiry", "text"),
        ("Their classes", "classes", "text"),
        ("Their goods and services", "goods", "clamp"),
        ("Proprietor", "owner", "text"),
        ("Country", "platform", "text"),
        ("Image", "image_src", "image"),
    ],
    "company": [
        ("Company", "title", "strong_link_to:url"),
        ("Company number", "external_ref", "mono"),
        ("Status", "status", "title"),
        ("Incorporated", "dates.incorporated", "text"),
        ("Dissolved", "dates.dissolved", "text"),
        ("Activity", "detail.sic_sectors", "clamp"),
        ("SIC codes", "detail.sic_codes", "text"),
        ("Previous names", "detail.previous_names", "clamp"),
        ("Companies House", "url", "link"),
    ],
    "domain": [
        ("Domain name", "external_ref", "strong_link_to:url"),
        ("Registered", "detail.registered", "yesno"),
        ("Registrar", "owner", "text"),
        ("Liveness", "status", "text"),
        ("Created", "dates.created", "text"),
        ("Expires", "dates.expires", "text"),
        ("Final URL", "url", "link"),
        ("Redirects to", "detail.redirects_to", "text"),
    ],
    "web": [
        ("Platform", "platform", "text"),
        ("Type", "kind", "text"),
        ("Title", "title", "clamp"),
        ("URL", "url", "link"),
        ("Snippet", "detail.snippet", "clamp"),
        ("Source domain", "detail.source_domain", "text"),
        ("Position", "detail.position", "text"),
        ("Price", "detail.price", "text"),
        ("Seller", "detail.seller", "text"),
        ("Image", "image_src", "image"),
    ],
}
RESULT_COLUMNS["social"] = RESULT_COLUMNS["web"]
RESULT_COLUMNS["marketplace"] = RESULT_COLUMNS["web"]


def columns_json() -> str:
    return json.dumps({k: [list(c) for c in v] for k, v in RESULT_COLUMNS.items()})


def dig(row: dict, path: str):
    cur = row
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


# ---------------------------------------------------------------------------
# derived facts about a run — shared by both pages
# ---------------------------------------------------------------------------

def classes_detail(run: dict) -> list[dict]:
    goods = (run.get("request") or {}).get("goods_text") or ""
    terms: dict[int, str] = {}
    for m in re.finditer(r"Class\s+([\d,\s]+):\s*(.+?)(?=\nClass\s+[\d,\s]+:|\Z)", goods, re.S):
        for n in re.findall(r"\d+", m.group(1)):
            terms[int(n)] = m.group(2).strip()
    out = []
    for n in run.get("classes") or []:
        out.append({"n": n, "short": SHORT.get(n, ""), "heading": HEADINGS.get(n, ""), "terms": terms.get(n, "")})
    return out


def requested_platforms(run: dict, plan: list[dict]) -> dict:
    req = run.get("request") or {}
    cov = run.get("coverage") or []
    if not cov:
        cov = [{"jurisdiction": j, "country": country(j), "register": "", "provider": ""} for j in run.get("jurisdictions") or []]
    by_ch: dict[str, list[str]] = {}
    tlds: set[str] = set()
    for p in plan:
        ch = p["channel"]
        if p["platform"] not in by_ch.setdefault(ch, []):
            by_ch[ch].append(p["platform"])
        if ch == "domain":
            q = p["query"]
            tld = q[q.index("."):] if "." in q else ""
            if tld:
                tlds.add(tld)
    return {
        "registers": cov,
        "countries": list(dict.fromkeys(c["country"] for c in cov)),
        "companies": bool(req.get("include_companies")) or bool(by_ch.get("company")),
        "domains": bool(req.get("include_domains")) or bool(by_ch.get("domain")),
        "tlds": sorted(tlds),
        "search_engines": by_ch.get("web", []),
        "socials": by_ch.get("social", []),
        "marketplaces": by_ch.get("marketplace", []),
        "tagline": req.get("tagline") or "",
        "logo_url": req.get("logo_url") or "",
        "logo_searched": bool(req.get("logo_url") or req.get("vienna_codes")),
        "vienna": req.get("vienna_codes") or [],
        # the shared logo panel — same definition on triage, client page and
        # both Word reports, so they cannot drift (audit_engine/vienna.py)
        "logo": _vienna.logo_block(req, audience="staff"),
        "logo_client": _vienna.logo_block(req, audience="client"),
    }


def terms_by_channel(plan: list[dict]) -> list[dict]:
    """[{channel, platform, kind, query, status}] grouped for display."""
    groups: dict[tuple, list] = {}
    for p in plan:
        groups.setdefault((p["channel"], p["platform"]), []).append(
            {"kind": p["kind"], "query": (p.get("declared_as") if p["channel"] == "trademark" and p.get("declared_as") else p["query"]),
             "status": p["status"], "rationale": p.get("rationale") or ""})
    order = {c: i for i, (c, _) in enumerate(CHANNELS)}
    return [{"channel": c, "platform": pl, "queries": q}
            for (c, pl), q in sorted(groups.items(), key=lambda kv: (order.get(kv[0][0], 9), kv[0][1]))]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def client_reason(row: dict, mark: str, classes: list[int]) -> str:
    """Plain-English 'why we flagged it' for the client — no scores, no queries."""
    ch, title = row.get("channel"), row.get("title") or ""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", mark.lower()) if len(w) > 2]
    tnorm, mnorm = _norm(title), _norm(mark)
    if ch == "trademark":
        if tnorm == mnorm:
            rel = "This mark is identical to yours"
        elif mnorm and mnorm in tnorm:
            rel = "This mark contains yours"
        else:
            shared = [w for w in words if re.search(r"\b" + re.escape(w), title.lower())]
            rel = (f"This mark shares the word ‘{shared[0]}’ with yours" if shared
                   else "This mark resembles yours")
        theirs = {int(n) for n in re.findall(r"\d+", row.get("classes") or "")}
        shared_cl = sorted(theirs & set(classes))
        cls = (f", in {len(shared_cl)} of your classes ({', '.join(map(str, shared_cl))})" if shared_cl
               else ", in classes you have not applied for")
        st = (row.get("status") or "").lower()
        if "regist" in st:
            live = "It is registered and in force."
        elif "applic" in st or "pending" in st or "publish" in st or "exam" in st:
            live = "It is an application that has not yet been registered."
        elif st:
            live = "It is no longer live on the register."
        else:
            live = ""
        return f"{rel}{cls}. {live}".strip()
    if ch == "company":
        st = (row.get("status") or "").lower()
        return (f"A company with a similar name is {'active' if st == 'active' else st or 'recorded'} at Companies House."
                + (" A company name is not a trademark, but an active business trading under it can matter." if st == "active" else ""))
    if ch == "domain":
        st = row.get("status") or ""
        if st in ("unknown", "", "rdap unavailable") or (row.get("band") == "Not Assessed"):
            return "We could not confirm this domain name's registration status at the time of the search."
        return {"live site": "This domain name is registered and points to a live website.",
                "redirects": "This domain name is registered and redirects to another site.",
                "not registered": "This domain name is available."}.get(st, f"This domain name is registered ({st or 'no website found'}).")
    plat = row.get("platform") or "the web"
    if mnorm and mnorm in tnorm:
        return f"Found on {plat}: your mark appears in the name or title."
    return f"Found on {plat} by a search for your mark."


_BAND_CLASS = {"High": "b-high", "Medium/High": "b-mhigh", "Medium": "b-med", "Low/Medium": "b-lmed", "Low": "b-low"}


def band_class(b: str) -> str:
    return _BAND_CLASS.get(b or "", "b-cleared")


# ---------------------------------------------------------------------------
# staff: login and runs list
# ---------------------------------------------------------------------------

def login_page(error: str = "") -> str:
    return f"""<!doctype html><meta charset=utf-8><title>TMH Audit — sign in</title><style>{_theme()}{EXTRA_CSS}</style>
<div class="login"><img src="{_logo()}" height="38" alt="TMH"><h2 style="margin:14px 0 4px">Audit &amp; Monitoring</h2>
<p class="note">Enter your staff token.</p>{f'<p style="color:#8f1d1d;font-weight:700">{esc(error)}</p>' if error else ''}
<form method="post" action="/login"><input name="token" type="password" autofocus><button class="btn">Sign in</button></form></div>"""


def runs_page(staff_name: str) -> str:
    hero = """<div class="rpt-head"><div class="in"><div><p class="eyebrow">TMH Audit &amp; Monitoring</p><h1>Runs</h1>
<p class="sub">Every audit and monitoring run, newest first. A run appears here a minute or two after Zoho's <i>Send for Research</i>, or when you start one below.</p>
<div class="head-actions"><input id="deal" placeholder="Zoho Deal ID" style="padding:9px 12px;border-radius:9px;border:0;min-width:220px"> <a class="btn" onclick="checkDeal()">Check Deal</a> <a class="btn" id="runbtn" onclick="runDeal()" style="opacity:.45;pointer-events:none">Send for Research</a></div></div>
<div class="head-meta" id="queue"></div></div></div>"""
    panes = """<div id="preflight" style="display:none;margin:0 0 18px;padding:16px 18px;border-radius:12px;border:1px solid #E6E9ED;background:#fff"></div>
<div class="pane on"><table id="runs"><thead><tr><th>When</th><th>Client</th><th>Mark</th><th>Kind</th><th>Status</th><th>Results</th><th>New</th><th>Excluded</th><th>Reported</th><th>Reports</th></tr></thead><tbody></tbody></table></div>"""
    script = """
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let checked=null;
function enableRun(on){const b=document.getElementById('runbtn');b.style.opacity=on?'1':'.45';b.style.pointerEvents=on?'auto':'none'}
document.getElementById('deal').addEventListener('input',()=>{checked=null;enableRun(false);document.getElementById('preflight').style.display='none'});
async function checkDeal(){const id=document.getElementById('deal').value.trim();if(!id)return;
 const box=document.getElementById('preflight');box.style.display='block';box.innerHTML='<span class="note">Checking the Deal…</span>';
 const r=await fetch('/api/deals/'+id+'/preflight');if(!r.ok){box.innerHTML='<b style="color:#8f1d1d">'+esc(await r.text())+'</b>';return}
 const p=await r.json();checked=p;const s=p.summary||{};
 const li=(p.problems||[]).map(x=>'<li>'+esc(x)+'</li>').join('');
 const wl=(p.warnings||[]).map(x=>'<li>'+esc(x)+'</li>').join('');
 const ch=s.channels||{};const layers=[ch.include_companies?'Companies House':null,ch.include_domains?'Domains':null,ch.include_serp?'Google':null,
   ...(ch.socials||[]),...(ch.marketplaces||[])].filter(Boolean);
 const crit=(s.extra_criteria||[]).map(c=>esc(c[0])+': <b>'+esc(c[1])+'</b>').join(' · ');
 const dom=(s.domain_criteria||[]).map(c=>esc(c[0])+': <b>'+esc(c[1])+'</b>').join(' · ');
 box.innerHTML=`<div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start">
  <div><b style="font-size:15px">${esc(p.deal_name)}</b> <span class="pill">${esc(p.pipeline)}</span> <span class="pill">${esc(p.stage||'')}</span>
   <div class="note">Owner ${esc(p.owner||'—')} · Next Action ${esc(p.next_action||'—')}${p.legacy_report?' · <b>has a legacy Excel report</b>':''}</div></div>
  <div style="text-align:right">${p.ok?'<b style="color:#17704a">Ready to send for research</b>':'<b style="color:#8f1d1d">Not ready — '+p.problems.length+' problem'+(p.problems.length>1?'s':'')+'</b>'}</div></div>
  ${li?'<ul style="margin:10px 0 6px 18px;color:#8f1d1d">'+li+'</ul>':''}
  ${wl?'<ul style="margin:6px 0 6px 18px;color:#8a5a00">'+wl+'</ul>':''}
  <div class="kv" style="margin-top:12px"><b>Mark</b><span><b>${esc(s.mark||'—')}</b> <span class="note">(${esc(s.keyword_source||'')})</span></span>
   <b>Extra criteria</b><span>${crit||'—'}</span><b>Domain criteria</b><span>${dom||'—'}</span>
   <b>Classes</b><span>${(s.classes||[]).join(', ')||'—'}</span><b>Registers</b><span>${(s.jurisdictions||[]).join(', ')||'—'} <span class="note">(${esc(s.jurisdiction_source||'')})</span></span>
   <b>Layers</b><span>${layers.join(', ')||'<i>registers only</i>'}</span>
   <b>Logo</b><span>${esc(s.logo_state||'none')}${(s.vienna_codes||[]).length?' · Vienna '+s.vienna_codes.join(', '):''}${s.wants_image&&!(s.vienna_codes||[]).length?' · <b style="color:#8a5a00">image requested, not Vienna-coded</b>':''}</span></div>
  ${(s.domain_criteria||[]).length&&!ch.include_domains?'<div class="note" style="color:#8a5a00;margin-top:8px">Domain criteria are typed but the Domain Registers layer is not ticked — they will not be searched.</div>':''}`;
 enableRun(p.ok);}
async function load(){const r=await fetch('/api/runs');if(r.status==401){location='/login';return}const d=await r.json();
const tb=document.querySelector('#runs tbody');tb.innerHTML='';
for(const x of d.runs){tb.insertAdjacentHTML('beforeend',`<tr><td style="white-space:nowrap">${(x.created_at||'').slice(0,16).replace('T',' ')}</td>
<td><b>${esc(x.client_name||'')}</b></td><td><a href="/runs/${x.id}"><b>${esc(x.mark_text)}</b></a><span class="tmno">${(x.classes||[]).join(', ')} · ${(x.jurisdictions||[]).join(', ')}</span></td>
<td>${x.kind}</td><td><span class="st s-${x.status}">${x.status}</span>${x.hold_reason?'<span class="meta">'+esc(x.hold_reason)+'</span>':''}</td>
<td>${x.total}</td><td><b>${x.new_count}</b></td><td>${x.excluded_count}</td><td>${x.reported_count}</td><td>${x.reports}</td></tr>`)}
const q=Object.entries(d.queue||{}).filter(([k,v])=>['queued','reading','running'].includes(v.status));
document.getElementById('queue').innerHTML=q.map(([k,v])=>`<span class="pill">Deal ${k} — ${v.status}${v.mark?' ('+esc(v.mark)+')':''}</span>`).join('');}
async function runDeal(){const id=document.getElementById('deal').value.trim();if(!id)return;
const r=await fetch('/api/run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({deal_id:id})});
alert(r.ok?'Queued. The run takes 1–3 minutes; this page refreshes itself.':'Could not queue: '+await r.text());load()}
load();setInterval(load,15000);"""
    return shell("TMH Audit — runs", hero, "", panes, script, staff_name=staff_name)


# ---------------------------------------------------------------------------
# staff: triage
# ---------------------------------------------------------------------------

def triage_page(run_id: str, staff_name: str) -> str:
    hero = """<div class="rpt-head"><div class="in"><div><p class="eyebrow" id="h-eyebrow">Trademark audit</p><h1 id="h-title">…</h1>
<p class="sub" id="h-sub"></p><div class="head-actions"><a class="btn ghost" onclick="issue()">Issue report</a><a class="btn ghost" onclick="rerun()">Re-run as monitoring</a><a class="btn ghost" id="h-export" target="_blank">Export workbook</a><a class="btn ghost" onclick="document.getElementById('imp').click()">Import results from Excel</a><input type="file" id="imp" accept=".xlsx" style="display:none" onchange="importXlsx(this)"><a class="btn ghost" id="h-deal" target="_blank" style="display:none">Open the Deal</a></div></div>
<div class="head-meta" id="h-meta"></div></div></div>"""
    tabs = ('<div class="tab on" data-pane="summary" role="tab">Summary</div>'
            + "".join(f'<div class="tab" data-pane="{c}" role="tab">{n} <span class="badge" id="b-{c}"></span><span class="n" id="n-{c}"></span></div>' for c, n in CHANNELS)
            + '<div class="tab hot" data-pane="findings" role="tab">Findings <span class="badge" id="b-findings"></span><span class="n" id="n-findings"></span></div>'
            + '<div class="tab" data-pane="excluded" role="tab">Excluded <span class="n" id="n-excluded"></span></div>'
            + '<div class="tab" data-pane="forensic" role="tab">Forensic review <span class="n" id="n-forensic"></span></div>'
            + '<div class="tab" data-pane="excl" role="tab">Portfolio &amp; exclusions <span class="n" id="n-excl"></span></div>'
            + '<div class="tab" data-pane="plan" role="tab">Search plan</div>'
            + '<div class="tab" data-pane="settings" role="tab">Search &amp; Score Settings</div>')
    reasons = "".join(f'<option value="{k}">{v}</option>' for k, v in REASONS)
    channel_panes = "".join(f"""<div class="pane" id="pane-{c}">
 <div id="banner-{c}"></div>
 <div class="filters"><span><label>Status&nbsp;</label><select class="fstatus" data-ch="{c}"><option value="new">New</option><option value="">All</option><option value="excluded">Excluded</option><option value="reported">Reported</option><option value="held">Held</option><option value="forensic">Forensic</option></select></span>
 <span><label>Risk&nbsp;</label><span class="chip on c-high" data-ch="{c}" data-g="high">High</span><span class="chip on c-medium" data-ch="{c}" data-g="medium">Medium</span><span class="chip on c-low" data-ch="{c}" data-g="low">Low</span><span class="chip on c-cleared" data-ch="{c}" data-g="cleared">Not live / cleared</span></span>
 <span><input class="fq" data-ch="{c}" placeholder="filter text" style="padding:6px 9px;border:1px solid #d8dce2;border-radius:7px"></span>
 <span><label><input type="checkbox" class="fwide" data-ch="{c}"> every column</label></span></div>
 <div class="banner todo bulk" id="bulk-{c}" style="display:none"><b><span class="nsel">0</span> selected</b>
  <select class="breason">{reasons}</select><input class="bnote" placeholder="note (optional)" size=28>
  <a class="abtn r" onclick="bulk('{c}','excluded')">Exclude</a><a class="abtn g" onclick="bulk('{c}','reported')">Report</a><a class="abtn h" onclick="bulk('{c}','held')">Hold</a><a class="abtn p" onclick="bulk('{c}','new')">Reset</a></div>
 <div id="box-{c}"></div></div>""" for c, _ in CHANNELS)
    panes = f"""
<div id="lens-banner"></div>
<div class="pane on" id="pane-summary">
 <div class="cards"><div class="card"><p class="eyebrow">This run</p><div class="tot"><span class="big" id="s-total">0</span><span class="lbl">results found across<br>every channel searched</span></div><div class="bar" id="s-bar"></div><table class="counts" id="s-counts"></table></div>
 <div class="card"><p class="eyebrow">Review progress</p><table class="counts" id="s-review"></table><p class="note" style="margin-top:10px">Exclude with a reason and it is learned for every later run. Mark rows <b>Reported</b> and press <i>Issue report</i> to create the client's link.</p></div></div>
 <div class="two" style="margin-top:18px">
  <div><h3>What we searched for</h3><div class="kv" id="s-request"></div></div>
  <div><h3>Where we searched</h3><div id="s-where"></div></div></div>
 <h3 style="margin-top:22px">Classes and terms</h3><p class="hd-note">Official Nice class heading for reference, then the specific goods and services on this application.</p>
 <table class="tick" id="s-classes"></table>
 <h3 style="margin-top:22px">Criteria of record</h3><p class="hd-note">What the registers were actually searched with. Declared and searched are the same list.</p><div id="s-criteria"></div>
 <div id="s-warn"></div></div>
{channel_panes}
<div class="pane" id="pane-excl"><div class="two">
 <div><h3>Client portfolio</h3><p class="hd-note">Assets we hold as the client's. Matched automatically at ingest, never reported as conflicts.</p><div id="portfolio"></div>
  <div class="formcard" style="margin-top:12px"><textarea id="purls" rows="3" placeholder="Add the client's own URLs — website, socials, storefront (one per line)" style="width:100%;padding:8px;border:1px solid #d8dce2;border-radius:7px;font:inherit"></textarea><br><a class="btn sec" onclick="addUrls()">Add to portfolio</a></div></div>
 <div><h3>Learned exclusions</h3><p class="hd-note">Every exclusion made in triage, applied before anyone looks at the next run.</p><div id="exclusions"></div></div></div></div>
<div class="pane" id="pane-findings">
 <div id="f-banner"></div>
 <div class="filters"><a class="btn" onclick="openForensic()">Run Forensic Reviews</a><a class="btn sec" onclick="clearFindings()">Clear all Findings</a><span class="note">Forensic review is requested from here. Verdicts advise — they never change a score or move a row.</span></div>
 <div id="findings-box"></div>
 <div class="s4" id="s4">
  <h3 style="margin:0 0 4px">Section 4. Personal Recommendations (without liability)</h3>
  <p class="hd-note">Drafted automatically from forensic reviews of the Findings; edit freely. A report will not issue with Section 4 included until it is approved. Any edit withdraws approval.</p>
  <textarea id="s4-text" placeholder="Section 4 text — populated when forensic reviews run, or write it here."></textarea>
  <div style="margin-top:10px;display:flex;gap:16px;align-items:center;flex-wrap:wrap"><a class="btn sec" onclick="s4Set({{text:document.getElementById('s4-text').value}})">Save text</a>
  <label><input type="checkbox" id="s4-inc" onchange="s4Set({{included:this.checked}})"> Include Section 4 in the report</label>
  <label><input type="checkbox" id="s4-app" onchange="s4Set({{approved:this.checked}})"> Approve Section 4</label><span id="s4-status" class="note"></span></div>
 </div></div>
<div class="pane" id="pane-excluded"><p class="hd-note">Everything set aside on this run, with the reason. Sending a row back to Results does not remove the learned exclusion — do that on the Portfolio &amp; exclusions tab.</p><div id="excluded-box"></div></div>
<div class="pane" id="pane-forensic"><p class="hd-note">Every forensic verdict returned on this run, newest first.</p><div id="forensic-box"></div></div>
<div class="pane" id="pane-settings">
 <h3>Search &amp; Score Settings</h3>
 <p class="hd-note">What this run refused to search on, and how each result got the score it did. Sensitivity below is saved on <b>this report only</b> and is recorded. Global settings are set in R&amp;D.</p>
 <div id="ss-version"></div>
 <h3 style="margin-top:22px">Words we did not search on</h3>
 <p class="hd-note">These are words dropped from the MARK before searching. Company legal forms &mdash; Ltd, GmbH, AS &mdash; are handled separately, per result and per that record&rsquo;s own jurisdiction; open a company result below to see what was removed from it.</p>
 <p class="hd-note"><b>Structural</b> words are never part of a mark &mdash; stripping &ldquo;Ltd&rdquo; is not a judgement, and putting it back would only flood the search. <b>Weak</b> words are usually noise but are occasionally the mark itself, which is why they are the ones worth questioning &mdash; DIRECT LINE is the obvious case. <b>Industry</b> words are noise for one client and the whole point for another.</p>
 <div id="ss-ignored"></div>
 <div class="formcard" style="margin-top:12px">
  <input id="ss-word" placeholder="a word to ignore on this search &mdash; courier, bakery, plumbing"
         style="width:min(340px,100%);padding:8px;border:1px solid #d8dce2;border-radius:7px;font:inherit">
  <input id="ss-why" placeholder="why (optional)"
         style="width:min(280px,100%);padding:8px;border:1px solid #d8dce2;border-radius:7px;font:inherit;margin-left:6px">
  <label style="margin-left:10px"><input type="checkbox" id="ss-global"> propose for the global list</label>
  <a class="btn sec" style="margin-left:8px" onclick="addIgnored()">Add</a>
  <div class="hd-note" style="margin-top:8px">Adding affects this search. Proposing does <b>not</b> change
   anything globally &mdash; it queues the word with the evidence from this run for R&amp;D to rule on.</div>
  <div id="ss-addmsg"></div>
 </div>
 <h3 style="margin-top:22px">Sensitivity</h3>
 <p class="hd-note">How hard this run looks, per compartment. This is saved <b>on this report only</b> and
  changes nothing globally &mdash; global calibration is set in R&amp;D. Nothing here re-scores the stored
  results either: it is a lens over them, and it is recorded so the change can be explained later.</p>
 <p class="hd-note">Move a control, then <b>Preview</b>. You will be told exactly what it costs before anything is saved.</p>
 <div id="ss-sens"></div>
 <div id="ss-sens-preview"></div>
 <div class="formcard" style="margin-top:12px">
  <input id="ss-sens-why" placeholder="why (optional when applying, required to clear)"
         style="width:min(420px,100%);padding:8px;border:1px solid #d8dce2;border-radius:7px;font:inherit">
  <a class="btn sec" style="margin-left:8px" onclick="previewSensitivity()">Preview</a>
  <a class="btn" style="margin-left:6px" onclick="applySensitivity()">Apply to this report</a>
  <a class="btn sec" style="margin-left:6px;color:#8A5C11;border-color:#E0C79A" onclick="clearSensitivity()">Clear</a>
  <div class="hd-note" style="margin-top:8px">Clearing returns this report to the standard calibration.
   Your <b>exclusions and review verdicts are not affected</b> &mdash; clearing only undoes the scoring lens.</div>
  <div id="ss-sens-msg"></div>
 </div>
 <div id="ss-sens-history"></div>
 <h3 style="margin-top:22px">Criteria of record</h3>
 <p class="hd-note">What the registers were actually searched with, after the words above were removed.</p>
 <div id="ss-criteria"></div>
 <h3 style="margin-top:22px">How a result scored</h3>
 <p class="hd-note">Pick a result to see which compartment did the work. A comparison that scored nothing is shown too &mdash; &ldquo;we tried sounds-like and it scored zero&rdquo; is usually the answer.</p>
 <div><select id="ss-pick" style="padding:7px;border:1px solid #d8dce2;border-radius:7px;font:inherit;max-width:100%"></select></div>
 <div id="ss-breakdown" style="margin-top:12px"></div>
</div>
<div class="pane" id="pane-plan"><h3>Search plan</h3><p class="hd-note">Every query, for every platform, in the order it ran — registers take criteria, everything else takes the literal string shown. This is what the report cites.</p><div id="plan"></div></div>
<div class="contact"><b>Reports for this run</b><br><span class="note">A report is created here and goes nowhere until you send it. Read it as the client will, amend the Word version if you need to, then mark it as sent.</span><div id="reports" class="note" style="margin-top:10px"></div></div>"""
    script = (_TRIAGE_JS.replace("__RUN__", run_id)
              .replace("__SHARED__", _SHARED_JS.replace("__COLS__", columns_json())))
    return shell("TMH Audit — triage", hero, tabs, panes, script, staff_name=staff_name)


_TRIAGE_JS = r"""
const RUN='__RUN__';let D=null;const sel={};const state={};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const BC={'High':'b-high','Medium/High':'b-mhigh','Medium':'b-med','Low/Medium':'b-lmed','Low':'b-low'};const bc=b=>BC[b]||'b-cleared';
const BO={'High':0,'Medium/High':1,'Medium':2,'Low/Medium':3,'Low':4,'Result (not live)':5,'Not Assessed':6,'Available':7};
const GROUP=b=>['High','Medium/High'].includes(b)?'high':['Medium','Low/Medium'].includes(b)?'medium':b==='Low'?'low':'cleared';
const CH={trademark:'Trademarks',company:'Companies',domain:'Domains',web:'Web',social:'Social',marketplace:'Marketplaces'};
const REASON_LABEL={own_asset:"Client's own asset",unrelated_goods:'Unrelated goods / services',dead_or_parked:'Dead, parked or dormant',duplicate:'Duplicate',staff_judgement:'Staff judgement',client_instruction:'Client instruction'};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));document.querySelectorAll('.pane').forEach(x=>x.classList.remove('on'));t.classList.add('on');document.getElementById('pane-'+t.dataset.pane).classList.add('on')});
const KIND_NOTE={structural:'never part of a mark',weak:'usually noise, occasionally the mark',industry:'noise for this client only',client:'from the client\'s exclusions'};
function renderSettings(){
 const S=D.settings||{},ig=S.ignored||[];
 const same=S.scored_by&&S.package&&S.scored_by===S.package;
 document.getElementById('ss-version').innerHTML=
  !S.scored_by?'<div class="banner todo"><b>Scored before the version was recorded.</b> Runs from before 18 Sep 2026 do not say which code produced their numbers, so the breakdown below is recomputed under the scorer loaded now and may not be the answer the client was given.</div>'
  :`<div class="kv"><b>Scored by</b><span>tmh_scoring ${esc(S.scored_by)}</span><b>Loaded now</b><span>${esc(S.package||'—')}${same?'':' <b style="color:#8A5C11">— different from the version that scored this run</b>'}</span></div>`;
 const byKind={};for(const w of ig)(byKind[w.kind]=byKind[w.kind]||[]).push(w);
 document.getElementById('ss-ignored').innerHTML=ig.length
  ?Object.keys(byKind).map(k=>`<h4 style="margin:14px 0 6px">${esc(k[0].toUpperCase()+k.slice(1))} <span class="tmno">${esc(KIND_NOTE[k]||'')}</span></h4>`
     +'<table class="tick"><tr><th>Word</th><th>Where</th><th>Why it was dropped</th><th style="width:90px"></th></tr>'
     +byKind[k].map(w=>`<tr><td><b>${esc(w.word)}</b></td><td>${esc(w.where_applied)}</td><td class="why">${esc(w.source)}${w.restored_by?' &middot; <b class="ok">restored by '+esc(w.restored_by)+'</b>':''}</td>`
        +`<td>${w.kind==='structural'?'<span class="tmno">always</span>'
             :w.restored_by?'<span class="tmno">restored</span>'
             :`<a class="abtn p" onclick="restoreWord('${esc(w.word)}')">Restore</a>`}</td></tr>`).join('')
     +'</table>').join('')
  :'<p class="empty">Nothing was dropped from this mark — or this run predates 18 Sep 2026, when the filter was silent and recorded nothing.</p>';
 document.getElementById('ss-criteria').innerHTML=(D.run.criteria||[]).map(c=>`<span class="plat q">${esc(c.match_type)}: ${esc(c.phrase)}${c.class_filtered?' [classes]':''}</span>`).join(' ')||'<p class="empty">None recorded.</p>';
 const scored=(D.results||[]).filter(r=>r.components);
 const pick=document.getElementById('ss-pick');
 pick.innerHTML=scored.length?scored.slice().sort((a,b)=>(b.score||0)-(a.score||0)).slice(0,80)
   .map(r=>`<option value="${r.id}">[${esc(CH[r.channel]||r.channel)}] ${esc((r.title||r.url||'').slice(0,70))} — ${esc(r.band||'')}</option>`).join('')
   :'<option>no breakdown recorded on this run</option>';
 pick.onchange=()=>showBreakdown(pick.value);
 showBreakdown(scored.length?pick.value:null);
 document.getElementById('n-excluded');
}
function lensMark(x){
 // Only where the sensitivity actually moved THIS row. Saying "adjusted" on
 // every row would train people to stop reading it.
 if(x.band_standard===undefined||x.band_standard===x.band)return '';
 return `<span class="meta" title="scored as ${esc(x.band_standard||'—')} at the standard calibration">was ${esc(x.band_standard||'—')}</span>`;
}
function renderLensBanner(){
 // Every band on this screen is read through the run's sensitivity, so this
 // banner is not optional and not dismissable. A number on a report that
 // somebody adjusted, shown without saying so, is the worst outcome this
 // feature could have.
 const L=D.lens,el=document.getElementById('lens-banner');
 if(!el)return;
 if(!L||L.error||!L.steps){el.innerHTML=L&&L.error?'<div class="banner todo"><b>Sensitivity could not be applied to this screen.</b> The bands below are as they were scored. '+esc(L.error)+'</div>':'';return}
 const names=Object.keys(L.steps).map(k=>k+' '+(L.steps[k]>0?'+':'')+L.steps[k]).join(', ');
 el.innerHTML=`<div class="banner todo"><b>Bands on this page are shown at an adjusted sensitivity</b> &mdash; ${esc(names)},
   set by ${esc(L.created_by||'?')}${L.reason?': '+esc(L.reason):''}.
   ${L.rows_moved} result(s) differ from how they were scored; each is marked.
   <span class="tmno">Stored results are unchanged. Clear it on the Search &amp; Score Settings tab.</span></div>`;
}
function sensSteps(){
 const out={};
 document.querySelectorAll('#ss-sens select[data-comp]').forEach(s=>{
   const v=parseInt(s.value,10); if(v) out[s.dataset.comp]=v;
 });
 return out;
}
function bandRow(before,after){
 const keys=[];for(const k in (before||{}))keys.push(k);for(const k in (after||{}))if(keys.indexOf(k)<0)keys.push(k);
 if(!keys.length)return '';
 return '<table class="tick"><tr><th>Band</th><th>Now</th><th>Would be</th><th>Change</th></tr>'
  +keys.map(k=>{const a=(before||{})[k]||0,b=(after||{})[k]||0,d=b-a;
     return `<tr><td><b>${esc(k)}</b></td><td>${a}</td><td>${b}</td>`
      +`<td>${d===0?'<span class="tmno">—</span>':(d>0?'+':'')+d}</td></tr>`}).join('')
  +'</table>';
}
function renderSensitivity(){
 const S=(D.settings||{}).sensitivity||{},cs=S.controls||[],live=S.live;
 const box=document.getElementById('ss-sens'); if(!box)return;
 if(!cs.length){box.innerHTML='<p class="empty">Sensitivity is unavailable &mdash; the scoring package loaded here does not offer it.</p>';return}
 box.innerHTML=(live?`<div class="banner todo"><b>This report is not at the standard calibration.</b>
    ${esc((live.reason||'').trim()||'No reason was recorded.')}
    <span class="tmno">&mdash; set by ${esc(live.created_by||'?')} (v${live.version||1}, tmh_scoring ${esc(live.scoring_version||'?')})</span></div>`:'')
  +'<table class="tick"><tr><th style="width:210px">Compartment</th><th>Setting</th><th>What it moves</th></tr>'
  +cs.map(c=>{
     const opts=c.steps.map(s=>`<option value="${s.step}"${s.step===c.current?' selected':''}>${esc(s.label)}</option>`).join('');
     const cur=c.steps.filter(s=>s.step===c.current)[0]||{};
     const ch=Object.keys(cur.changes||{}).map(k=>esc(k)+' '+cur.changes[k]).join(', ');
     return `<tr><td><b>${esc(c.label)}</b>${c.enabled?'':' <span class="tmno">not wired yet</span>'}</td>`
      +`<td><select data-comp="${esc(c.compartment)}"${c.enabled?'':' disabled'} style="padding:6px;border:1px solid #d8dce2;border-radius:7px;font:inherit">${opts}</select></td>`
      +`<td class="why">${ch?esc(ch):'<span class="tmno">nothing &mdash; the shipped calibration</span>'}</td></tr>`}).join('')
  +'</table>';
 const h=S.history||[];
 document.getElementById('ss-sens-history').innerHTML=h.length
  ?'<h4 style="margin:18px 0 6px">History</h4><p class="hd-note">Every change, newest first. Nothing is overwritten &mdash; a clear ends the history, it does not delete it.</p>'
   +'<table class="tick"><tr><th>When</th><th>What</th><th>Setting</th><th>Moved</th><th>Who</th><th>Why</th></tr>'
   +h.map(e=>{const st=e.steps||{},names=Object.keys(st).map(k=>k+' '+(st[k]>0?'+':'')+st[k]).join(', ');
      return `<tr><td>${esc(String(e.created_at||'').slice(0,16).replace('T',' '))}</td>`
       +`<td><b>${esc(e.action)}</b></td><td>${names?esc(names):'<span class="tmno">standard</span>'}</td>`
       +`<td>${e.rows_moved==null?'<span class="tmno">—</span>':e.rows_moved+(e.dropped?'':'')}</td>`
       +`<td>${esc(e.created_by||'')}</td><td class="why">${esc(e.reason||'')}</td></tr>`}).join('')
   +'</table>'
  :'';
}
async function previewSensitivity(){
 const msg=document.getElementById('ss-sens-msg'),out=document.getElementById('ss-sens-preview');
 const steps=sensSteps();
 if(!Object.keys(steps).length){out.innerHTML='';msg.innerHTML='<p class="empty">Every compartment is at Standard &mdash; nothing to preview. To return this report to standard, use Clear.</p>';return}
 msg.innerHTML='<p class="hd-note">Scoring&hellip;</p>';
 const r=await fetch('/api/runs/'+RUN+'/sensitivity/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({steps:steps})});
 const d=await r.json();
 if(!r.ok){msg.innerHTML='<p class="empty">'+esc(d.detail||'could not preview that')+'</p>';out.innerHTML='';return}
 msg.innerHTML='';
 const drop=d.dropped?`<b style="color:#8A5C11">${d.dropped} would stop being findings altogether</b> and leave this report. `:'';
 const add=d.added?`${d.added} would become findings that are not on the report now. `:'';
 out.innerHTML=`<div class="banner todo" style="margin-top:10px">
   <b>${d.rows_moved} of ${d.rows} results would change band.</b>
   ${d.moved_up} up, ${d.moved_down} down. ${drop}${add}
   ${d.drift_from_stored?`<div class="hd-note" style="margin-top:6px">Separately, ${d.drift_from_stored} result(s) already score differently from what is stored, because this run was scored by an earlier version of the scorer. That is not caused by this change.</div>`:''}
   <div class="hd-note" style="margin-top:6px">${(d.described||[]).map(esc).join('<br>')}</div>
  </div>`+bandRow(d.band_before,d.band_after);
}
async function applySensitivity(){
 const steps=sensSteps(),msg=document.getElementById('ss-sens-msg');
 if(!Object.keys(steps).length){msg.innerHTML='<p class="empty">Every compartment is at Standard. Use Clear to return this report to standard and record why.</p>';return}
 const names=Object.keys(steps).map(k=>k+' '+(steps[k]>0?'+':'')+steps[k]).join(', ');
 if(!confirm('Apply to this report only:\n\n  '+names+'\n\nThis is saved on this report and recorded. It changes nothing globally and does not touch your exclusions.'))return;
 const r=await fetch('/api/runs/'+RUN+'/sensitivity',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({steps:steps,reason:document.getElementById('ss-sens-why').value.trim()})});
 const d=await r.json();
 if(!r.ok){msg.innerHTML='<p class="empty">'+esc(d.detail||'could not apply that')+'</p>';return}
 const c=d.comparison||{};
 alert(`Saved as version ${d.version}. ${c.rows_moved||0} result(s) changed band`+(c.dropped?`, ${c.dropped} left the report`:'')+'.');
 load();
}
async function clearSensitivity(){
 const msg=document.getElementById('ss-sens-msg');
 const why=document.getElementById('ss-sens-why').value.trim();
 const S=(D.settings||{}).sensitivity||{},live=S.live;
 if(!live){msg.innerHTML='<p class="empty">This report is already at the standard calibration.</p>';return}
 if(why.length<10){msg.innerHTML='<p class="empty">Clearing needs a reason of at least 10 characters. It is recorded, and it is what makes the change explainable later.</p>';return}
 const st=live.steps||{},names=Object.keys(st).map(k=>k+' '+(st[k]>0?'+':'')+st[k]).join(', ');
 if(!confirm('Clear the sensitivity on this report?\n\nIn force now: '+(names||'standard')
   +'\n\nEvery result returns to the standard calibration, so bands on this report will move.'
   +'\n\nYour exclusions and review verdicts are NOT affected.'
   +'\n\nThis is recorded against your name with the reason you gave, and the setting above is kept in the history so it can be put back.'))return;
 const r=await fetch('/api/runs/'+RUN+'/sensitivity',{method:'DELETE',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({reason:why})});
 const d=await r.json();
 if(!r.ok){msg.innerHTML='<p class="empty">'+esc(d.detail||'could not clear that')+'</p>';return}
 const c=d.comparison||{};
 alert(`Cleared. ${c.rows_moved||0} result(s) returned to their standard band.`);
 load();
}
async function restoreWord(word){
 if(!confirm(`Put "${word}" back into the search?\n\nThe weak list acts when the search is built, so this takes effect on the next run of this audit, not on the results below.`))return;
 const r=await fetch('/api/runs/'+RUN+'/ignored/restore',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({word:word})});
 const d=await r.json();
 if(!r.ok){alert(d.detail||'could not restore that word');return}
 alert(`"${word}" restored. ${d.note}`); load();
}
async function addIgnored(){
 const w=document.getElementById('ss-word').value.trim();
 const why=document.getElementById('ss-why').value.trim();
 const g=document.getElementById('ss-global').checked;
 const msg=document.getElementById('ss-addmsg');
 if(w.length<2){msg.innerHTML='<p class="empty">Type a word first.</p>';return}
 const r=await fetch('/api/runs/'+RUN+'/ignored',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({word:w,reason:why,propose_global:g})});
 const d=await r.json();
 if(!r.ok){msg.innerHTML='<p class="empty">'+esc(d.detail||'could not add that word')+'</p>';return}
 const ev=d.evidence||{};
 msg.innerHTML=`<div class="banner todo" style="margin-top:8px"><b>&ldquo;${esc(w)}&rdquo; added to this search.</b>
   It appears in ${ev.rows_touched||0} result(s) on this run, ${ev.rows_medium_plus||0} of them Medium or above.
   ${d.proposal?'Queued for R&amp;D as a global exclusion with that evidence.':''}</div>`;
 document.getElementById('ss-word').value='';document.getElementById('ss-why').value='';
 document.getElementById('ss-global').checked=false; load();
}
function showBreakdown(id){
 const box=document.getElementById('ss-breakdown');
 const r=(D.results||[]).find(x=>String(x.id)===String(id));
 if(!r||!r.components){box.innerHTML='<p class="empty">No breakdown recorded for this result. Runs before 18 Sep 2026 stored only the total and the sentence.</p>';return}
 const c=r.components;
 // Refine (tmh-scoring 2.2.0): what the scorer was actually handed. The name
 // on the report is the real one; this is the one it was compared as, and the
 // legal forms that came out to get there — per the record's own jurisdiction,
 // so a British name never meets the Norwegian list.
 const d=r.detail||{},ca=d.compared_as,lf=d.legal_forms_stripped||[];
 const refine=ca?`<div class="banner" style="margin-bottom:12px;background:#f3f6f9;border-left:3px solid #cbd6df;padding:10px 12px">
   <b>Compared as</b> &ldquo;${esc(ca)}&rdquo;${ca!==(r.title||'')?` &mdash; the record says &ldquo;${esc(r.title||'')}&rdquo;`:''}
   ${lf.length?`<br><span class="tmno">legal forms removed: ${lf.map(esc).join(', ')}${d.jurisdiction?' &middot; '+esc(d.jurisdiction)+' list':''}</span>`
              :'<br><span class="tmno">no legal forms removed</span>'}</div>`:'';
 if(c.kind==='register'){
  const m=c.mark||{},t=c.trade||{};
  box.innerHTML=refine+`<div class="two">
   <div><h4>Mark similarity</h4><table class="tick">
    <tr><th>Compartment</th><th style="width:110px">Result</th></tr>
    <tr><td>Tier</td><td><b>${m.tier??'—'}</b></td></tr>
    <tr><td>Spelled like (orthographic)</td><td>${m.orthographic??'—'}</td></tr>
    <tr><td>Sounds like (phonetic)</td><td>${m.phonetic?'<b>yes</b>':'no'}</td></tr>
    <tr><td>Shared distinctive words</td><td>${(m.shared_words||[]).map(esc).join(', ')||'—'}</td></tr>
    <tr><td>Visual</td><td>${esc(m.visual||'—')}</td></tr></table>
    <p class="hd-note">${esc(m.reason||'')}</p></div>
   <div><h4>Trade similarity</h4><table class="tick">
    <tr><th>Compartment</th><th style="width:110px">Result</th></tr>
    <tr><td>Tier</td><td><b>${t.tier??'—'}</b></td></tr>
    <tr><td>Goods band</td><td>${esc(t.goods_band||'—')}</td></tr>
    <tr><td>Shared classes</td><td>${(t.shared_classes||[]).join(', ')||'—'}</td></tr>
    <tr><td>Shared terms</td><td class="why">${(t.shared_terms||[]).slice(0,8).map(esc).join(', ')||'—'}</td></tr>
    <tr><td>Evidence</td><td>${esc(t.evidence||'—')}</td></tr></table>
    <p class="hd-note">${esc(t.reason||'')}</p></div></div>
   ${(c.rights_reasons||[]).length?'<h4 style="margin-top:14px">Rights</h4><p class="hd-note">'+c.rights_reasons.map(esc).join(' &middot; ')+'</p>':''}`;
 }else{
  const pts=c.points||{},comps=c.comparisons||[];
  box.innerHTML=refine+`<div class="two">
   <div><h4>Points by compartment</h4><table class="tick"><tr><th>Compartment</th><th style="width:90px">Points</th></tr>`
   +Object.keys(pts).map(k=>`<tr><td>${esc(k.replace(/_/g,' '))}</td><td><b>${pts[k]}</b></td></tr>`).join('')
   +`</table></div>
   <div><h4>Every comparison made</h4><table class="tick"><tr><th>Method</th><th style="width:70px">Points</th><th>What it found</th></tr>`
   +(comps.length?comps.map(x=>`<tr><td>${esc(String(x.method).replace(/_/g,' '))}</td><td>${x.points?'<b>'+x.points+'</b>':'0'}</td><td class="why">${esc(x.reason)}</td></tr>`).join('')
     :'<tr><td colspan=3 class="why">no comparisons recorded</td></tr>')
   +`</table></div></div>`;
 }
}
async function load(){const r=await fetch('/api/runs/'+RUN);if(r.status==401){location='/login';return}D=await r.json();render()}
function render(){const run=D.run,req=run.request||{},rp=D.requested;
 document.getElementById('h-eyebrow').textContent=(run.kind==='monitoring'?'Monitoring run':'Trademark audit')+' · '+esc(run.client_name||'');
 document.getElementById('h-title').textContent=run.mark_text;
 document.getElementById('h-sub').innerHTML=`Classes ${(run.classes||[]).join(', ')} · ${rp.countries.join(', ')} · run ${(run.finished_at||'').slice(0,16).replace('T',' ')} · <span class="st s-${run.status}" style="border-color:#cbd6df;color:#fff">${run.status}</span>${run.hold_reason?' · <b>'+esc(run.hold_reason)+'</b>':''}`;
 if(run.zoho_deal_id){const a=document.getElementById('h-deal');a.style.display='';a.href='https://crm.zoho.com/crm/org628887698/tab/Potentials/'+run.zoho_deal_id}
 document.getElementById('h-export').href='/api/runs/'+RUN+'/export.xlsx';
 renderLensBanner();
 document.getElementById('h-meta').innerHTML=`<span class="pill">Search ${(run.finished_at||'').slice(0,10)}</span>`+rp.registers.map(c=>`<span class="pill">${esc(c.country)} register</span>`).join('');
 // summary counts
 const all=D.results;const bands={};for(const x of all)bands[x.band||'—']=(bands[x.band||'—']||0)+1;
 document.getElementById('s-total').textContent=all.length;
 const order=Object.keys(bands).sort((a,b)=>(BO[a]??9)-(BO[b]??9));const SC={'High':'s-high','Medium/High':'s-mhigh','Medium':'s-med','Low/Medium':'s-lmed','Low':'s-low'};
 document.getElementById('s-bar').innerHTML=order.map(b=>`<span class="${SC[b]||'s-cleared'}" style="width:${(100*bands[b]/all.length).toFixed(1)}%"></span>`).join('');
 document.getElementById('s-counts').innerHTML=order.map(b=>`<tr><td><span class="dot ${SC[b]||'s-cleared'}"></span>${esc(b)}</td><td>${bands[b]}</td></tr>`).join('');
 const rs={};for(const x of all)rs[x.review_status]=(rs[x.review_status]||0)+1;
 document.getElementById('s-review').innerHTML=['new','excluded','reported','held','forensic'].map(s=>`<tr><td><span class="st s-${s}">${s}</span></td><td>${rs[s]||0}</td></tr>`).join('');
 document.getElementById('s-request').innerHTML=`<b>Word mark</b><span><b>${esc(run.mark_text)}</b></span><b>Tagline</b><span>${esc(rp.tagline||'—')}</span><b>Logo</b><span>${logoPanel(rp.logo,{notes:true})}</span><b>Applicant</b><span>${esc(req.applicant||'—')}</span><b>Nature of business</b><span>${esc(req.nature_of_business||'—')}</span>`;
 const regs=rp.registers.map(c=>`<tr><td>${esc(c.country)}</td><td>${esc(c.register.replace(/\s*\((Temmy|Signa)\)/,''))}</td><td>${esc((c.provider||(c.register.match(/\((.*?)\)/)||[])[1]||'').replace(/^\w/,m=>m.toUpperCase()))}</td></tr>`).join('');
 document.getElementById('s-where').innerHTML=`<table class="tick"><tr><th>Country</th><th>Register</th><th>Platform used</th></tr>${regs}</table>
  <div class="kv" style="margin-top:10px"><b>Company register</b><span>${rp.companies?'UK Companies House':'not requested'}</span><b>Domain names</b><span>${rp.domains?rp.tlds.map(t=>'<span class=plat>'+esc(t)+'</span>').join(''):'not requested'}</span>
  <b>Search engines</b><span>${rp.search_engines.map(p=>'<span class=plat>'+esc(p)+'</span>').join('')||'not requested'}</span><b>Social media</b><span>${rp.socials.map(p=>'<span class=plat>'+esc(p)+'</span>').join('')||'not requested'}</span><b>Online shopping</b><span>${rp.marketplaces.map(p=>'<span class=plat>'+esc(p)+'</span>').join('')||'not requested'}</span></div>`;
 document.getElementById('s-classes').innerHTML='<tr><th>Class</th><th>Official heading</th><th>Terms on this application</th></tr>'+D.classes.map(c=>`<tr><td><b>${c.n}</b><span class="tmno">${esc(c.short)}</span></td><td class="why">${esc(c.heading)}</td><td class="why">${esc(c.terms||'—')}</td></tr>`).join('');
 document.getElementById('s-criteria').innerHTML=(run.criteria||[]).map(c=>`<span class="plat q">${esc(c.match_type)}: ${esc(c.phrase)}${c.class_filtered?' [classes]':''}</span>`).join(' ');
 document.getElementById('s-warn').innerHTML=(run.warnings||[]).length?`<div class="banner todo" style="margin-top:18px"><b>Warnings</b><br>${run.warnings.map(esc).join('<br>')}</div>`:'';
 for(const [c] of Object.entries(CH))renderChannel(c);
 document.getElementById('n-excl').textContent=`(${D.portfolio.length+D.exclusions.length})`;
 document.getElementById('portfolio').innerHTML=D.portfolio.length?'<table class="tick"><tr><th>Kind</th><th>Value</th><th>Source</th><th></th></tr>'+D.portfolio.map(p=>`<tr><td>${esc(p.kind)}</td><td><b>${esc(p.value)}</b>${p.label&&p.label!==p.value?'<span class="tmno">'+esc(p.label)+'</span>':''}</td><td>${esc(p.source)}</td><td><a class="abtn p" onclick="delP('${p.id}')">Remove</a></td></tr>`).join('')+'</table>':'<p class="empty">Nothing yet — add the client\'s website and socials so they are excluded automatically.</p>';
 document.getElementById('exclusions').innerHTML=D.exclusions.length?'<table class="tick"><tr><th>Where</th><th>Value</th><th>Reason</th><th>By</th><th></th></tr>'+D.exclusions.map(e=>`<tr><td>${esc(CH[e.channel]||'any')}${e.platform?' / '+esc(e.platform):''}</td><td><b>${esc(e.value)}</b></td><td>${esc(REASON_LABEL[e.reason]||e.reason)}${e.note?'<span class="meta">'+esc(e.note)+'</span>':''}</td><td>${esc(e.created_by||'')}</td><td><a class="abtn p" onclick="delE('${e.id}')">Remove</a></td></tr>`).join('')+'</table>':'<p class="empty">None learned yet.</p>';
 document.getElementById('plan').innerHTML=D.terms.map(g=>`<h4 style="margin:14px 0 6px">${esc(CH[g.channel]||g.channel)} · ${esc(g.platform)}</h4><table class="tick"><tr><th style="width:70px">Kind</th><th>Query</th><th>Why</th><th style="width:150px">Status</th></tr>${g.queries.map(q=>`<tr><td>${q.kind}</td><td class="q">${esc(q.query)}</td><td class="why">${esc(q.rationale)}</td><td>${esc(q.status)}</td></tr>`).join('')}</table>`).join('');
 renderFindings();renderExcluded();renderForensicLog();renderS4();renderSettings();renderSensitivity();
 document.getElementById('reports').innerHTML=D.reports.length?D.reports.map(p=>{
 const state=p.revoked_at?`<b style="color:#8f1d1d">revoked ${(p.revoked_at||'').slice(0,10)}</b>`:(p.shared_at?`<b class="ok">sent to the client ${(p.shared_at||'').slice(0,10)}${p.shared_by?' by '+esc(p.shared_by):''}</b>`:'<b style="color:#8A5C11">not sent yet</b>');
 const acts=p.revoked_at?'':`<a target="_blank" href="/r/${p.token}">view as the client sees it</a> · <a href="/api/reports/${p.id}/report.docx">download Word</a> · <a href="/api/reports/${p.id}/report.pdf">download PDF</a> · <a href="#" onclick="copyLink('${p.token}');return false">copy link</a> · <a href="#" onclick="markSent('${p.id}',${p.shared_at?'false':'true'});return false">${p.shared_at?'undo sent':'mark as sent'}</a> · <a href="#" onclick="revoke('${p.id}');return false">revoke</a>`;
 return `<div style="padding:8px 0;border-bottom:1px solid #EEF1F4"><b>${esc(p.title||'')}</b> · ${p.n} finding${p.n===1?'':'s'}${p.section4_text?' · Section 4 included':''} · created ${(p.issued_at||'').slice(0,10)} by ${esc(p.issued_by||'')} · ${state}<br><span class="small">${acts}</span></div>`}).join(''):'No report created yet.';}
async function markSent(id,shared){await fetch('/api/reports/'+id+'/shared',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({shared})});load()}
function copyLink(tok){const u=location.origin+'/r/'+tok;navigator.clipboard?navigator.clipboard.writeText(u).then(()=>alert('Link copied:\n'+u)):prompt('Client link:',u)}
__SHARED__
function flat(x){const o={};const put=(k,v)=>{if(v===null||v===undefined||v===''||(Array.isArray(v)&&!v.length))return;o[k]=Array.isArray(v)?v.join('; '):v};
 for(const k of ['external_ref','title','url','status','owner','classes','goods','image_url','image_src','source','found_by','score','band','explanation','change','kind'])put(k,x[k]);
 for(const [k,v] of Object.entries(x.dates||{}))put('date: '+k,v);for(const [k,v] of Object.entries(x.detail||{}))put(k,v);return o}
function renderChannel(c){const rows=D.results.filter(x=>x.channel===c);const st=state[c]||(state[c]={status:'new',groups:new Set(['high','medium','low','cleared']),q:'',wide:false});
 const nNew=rows.filter(x=>x.review_status==='new').length;const b=document.getElementById('b-'+c);b.textContent=nNew;b.className='badge'+(nNew?' show':'');document.getElementById('n-'+c).textContent=`(${rows.length})`;
 document.getElementById('banner-'+c).innerHTML=nNew?`<div class="banner todo"><b>${nNew} result${nNew===1?'':'s'} awaiting review.</b> Exclude with a reason, mark for the report, or hold for a second look.</div>`:`<div class="banner done"><b>Every result in this group has been reviewed.</b></div>`;
 const view=rows.filter(x=>(!st.status||x.review_status===st.status)&&st.groups.has(GROUP(x.band))&&(!st.q||JSON.stringify([x.title,x.url,x.owner,x.platform,x.explanation,x.external_ref]).toLowerCase().includes(st.q)))
  .sort((a,b)=>(BO[a.band]??9)-(BO[b.band]??9)||(b.score??0)-(a.score??0)||String(a.title).localeCompare(String(b.title)));
 const box=document.getElementById('box-'+c);if(!view.length){box.innerHTML='<p class="empty">No results match the current filters.</p>';return}
 if(st.wide){const cols=[];for(const x of view)for(const k of Object.keys(flat(x)))if(!cols.includes(k))cols.push(k);
  box.className='wide';box.innerHTML='<table id="t"><tr><th></th><th>Assessment</th><th>Review</th><th>Action</th>'+cols.map(k=>`<th>${esc(k)}</th>`).join('')+'</tr>'+view.map(x=>{const f=flat(x);return `<tr class="${x.review_status==='excluded'?'row-ex':''}"><td><input type="checkbox" data-id="${x.id}" ${(sel[c]||new Set()).has(x.id)?'checked':''}></td><td><span class="band ${bc(x.band)}">${esc(x.band||'—')}</span>${lensMark(x)}</td><td>${revHtml(x)}</td><td style="min-width:110px">${actHtml(x)}</td>`+cols.map(k=>`<td>${/^https?:\/\//i.test(String(f[k]||''))?(k==='image_url'?`<a target="_blank" href="${esc(f[k])}"><img class="thumb" src="${esc(f[k])}"></a>`:link(f[k])):clamp(String(f[k]??''))}</td>`).join('')+'</tr>'}).join('')+'</table>';
  box.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=()=>{(sel[c]||(sel[c]=new Set()));cb.checked?sel[c].add(cb.dataset.id):sel[c].delete(cb.dataset.id);bulkbar(c)});bulkbar(c);return}
 box.className='';
 const head=c==='trademark'?'<tr><th></th><th>Mark found</th><th>Their C&amp;Ts</th><th>Proprietor</th><th>Assessment</th><th>Why we flagged it</th><th>Found by</th><th>Review</th><th>Action</th></tr>'
  :c==='company'?'<tr><th></th><th>Company</th><th>Status &amp; activity</th><th>Incorporated</th><th>Assessment</th><th>Why we flagged it</th><th>Found by</th><th>Review</th><th>Action</th></tr>'
  :c==='domain'?'<tr><th></th><th>Domain</th><th>Registered</th><th>Registrar</th><th>Liveness</th><th>Created</th><th>Expires</th><th>Final URL</th><th>Assessment</th><th>Why we flagged it</th><th>Found by</th><th>Review</th><th>Action</th></tr>'
  :'<tr><th></th><th>Platform</th><th>Type</th><th>Title</th><th>URL</th><th>Snippet</th><th>Source domain</th><th>Position</th><th>Image</th><th>Assessment</th><th>Why we flagged it</th><th>Found by</th><th>Review</th><th>Action</th></tr>';
 box.innerHTML='<table id="t">'+head+view.map(x=>rowHtml(c,x)).join('')+'</table>';
 box.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=()=>{(sel[c]||(sel[c]=new Set()));cb.checked?sel[c].add(cb.dataset.id):sel[c].delete(cb.dataset.id);bulkbar(c)});bulkbar(c)}
function revHtml(x){return x.review_status==='new'?'<span class="st s-new">new</span>':`<span class="st s-${x.review_status}">${x.review_status}</span><div class="rev">${x.review_reason?esc(REASON_LABEL[x.review_reason]||x.review_reason):''}${x.review_note?' — '+esc(x.review_note):''}${x.reviewed_by?'<br><span class="meta">'+esc(x.reviewed_by)+(x.auto_excluded?' (automatic)':'')+'</span>':''}</div>`}
function actHtml(x){return x.review_status==='excluded'?`<a class="abtn p" onclick="one('${x.id}','new')">Send back to Results</a>`:x.review_status==='reported'?`<a class="abtn p" onclick="one('${x.id}','new')">Send back to Results</a>`:`<a class="abtn r" onclick="picker(this,'${x.id}')">Exclude…</a><a class="abtn g" onclick="one('${x.id}','reported')">Report</a><a class="abtn h" onclick="one('${x.id}','held')">Hold</a>`}
function markType(x){const t=String((x.detail||{}).mark_type||'').toLowerCase();if(/combin|composite|word.*(fig|dev|image)|(fig|dev|image).*word/.test(t))return 'Word + Image';if(/fig|device|image|logo|3d|shape|colour|color|sound|pattern/.test(t))return 'Image';if(/word|text|standard/.test(t))return 'Word';return x.image_src?'Image':(t?t.charAt(0).toUpperCase()+t.slice(1):'Word')}
function markPill(x){const m=markType(x);const col=m==='Word'?'#17704a':(m==='Image'?'#8a2b8f':'#8a5a00');return `<span class="pill" style="margin-left:6px;border-color:${col};color:${col}">${m} mark</span>`}
function rowHtml(c,x){const d=x.detail||{},dt=x.dates||{};let a,b2,c3;
 const isNew=x.change==='new'&&D.run.kind==='monitoring';const newTag=isNew?'<span class="meta" style="color:#8f1d1d;font-weight:700">NEW since last run</span>':'';
 const tail=`<td><span class="band ${bc(x.band)}">${esc(x.band||'—')}</span>${lensMark(x)}<span class="meta">score ${x.score??''}</span></td><td class="why">${clamp(x.explanation||'')}</td><td class="why" style="min-width:160px;font-size:11.5px">${(x.found_by||[]).map(esc).join('<br>')}</td><td>${revHtml(x)}</td><td style="min-width:110px">${actHtml(x)}</td></tr>`;
 const cb=`<tr class="${x.review_status==='excluded'?'row-ex':''}"><td><input type="checkbox" data-id="${x.id}" ${(sel[c]||new Set()).has(x.id)?'checked':''}></td>`;
 if(c==='domain')return cb+`<td><b>${link(x.url||('http://'+x.external_ref+'/'),x.external_ref)}</b>${x.title&&x.title!==x.external_ref?`<span class="meta">${esc(x.title)}</span>`:''}${newTag}</td><td>${d.registered?'Yes':'No'}</td><td>${esc(x.owner||'')}</td><td>${esc(x.status||'')}${d.redirects_to?'<span class="meta">→ '+esc(d.redirects_to)+'</span>':''}</td><td>${esc(dt.created||'')}</td><td>${esc(dt.expires||'')}</td><td>${link(x.url)}</td>`+tail;
 if(c==='web'||c==='social'||c==='marketplace')return cb+`<td>${esc(x.platform)}${newTag}</td><td>${esc(x.kind)}</td><td class="why">${clamp(x.title||'')}</td><td>${link(x.url)}</td><td class="why">${clamp(d.snippet||'')}${d.price?'<span class="meta"><b>'+esc(d.price)+'</b> '+esc(d.seller||'')+'</span>':''}</td><td>${esc(d.source_domain||'')}</td><td>${d.position??''}</td><td>${x.image_url?`<a target="_blank" href="${esc(x.image_url)}"><img class="thumb" src="${esc(x.image_url)}"></a>`:''}</td>`+tail;
 if(c==='trademark'){a=`<b>${esc(x.title)}</b><span class="tmno">${esc(x.external_ref)}</span><span class="meta">${esc(x.status||'')}${dt.filing?' · filed '+esc(dt.filing):''}${dt.expiry?' · expires '+esc(dt.expiry):''}</span>${markPill(x)}${x.image_src?`<div style="margin-top:4px"><a target="_blank" rel="noopener" href="${esc(x.image_src)}"><img class="thumb" src="${esc(x.image_src)}" alt="mark image"></a></div>`:''}`;
  b2=`${esc(x.classes||'')}${x.goods?`<span class="goods">${clamp(x.goods)}</span>`:''}`;c3=esc(x.owner||'');}
 else if(c==='company'){a=`<b>${x.url?`<a target="_blank" href="${esc(x.url)}">${esc(x.title)}</a>`:esc(x.title)}</b><span class="tmno">${esc(x.external_ref)}</span>${d.previous_names?`<span class="meta">formerly ${esc(d.previous_names)}</span>`:''}`;
  b2=`${esc(x.status||'')}<span class="goods">${esc(d.sic_sectors||d.sic_codes||'')}</span>`;c3=esc(dt.incorporated||'');}
 return cb+`<td>${a}${newTag}</td><td>${b2}</td><td>${c3}</td>`+tail}
const VL={conflict:'Conflict',no_conflict:'No conflict',unsure:'Unsure'};
function latestForensic(){const m={};for(const f of (D.forensic||[]))if(!m[f.result_id])m[f.result_id]=f;return m}
function findingCell(x){const d=x.detail||{},dt=x.dates||{};
 if(x.channel==='trademark')return `<b>${esc(x.title)}</b><span class="tmno">${esc(x.external_ref)}</span><span class="meta">${esc(x.status||'')} · cl. ${esc(x.classes||'')} · ${esc(x.owner||'')}</span>`;
 if(x.channel==='company')return `<b>${link(x.url,x.title)}</b><span class="tmno">${esc(x.external_ref)}</span><span class="meta">${esc(x.status||'')} · ${esc(d.sic_sectors||'')}</span>`;
 if(x.channel==='domain')return `<b>${link(x.url||('http://'+x.external_ref+'/'),x.external_ref)}</b><span class="meta">${esc(x.status||'')}${x.owner?' · '+esc(x.owner):''}</span>`;
 return `<b>${link(x.url,x.title||x.url)}</b><span class="tmno">${esc(x.platform)} · ${esc(d.source_domain||'')}</span>`}
function renderFindings(){const rows=D.results.filter(x=>x.review_status==='reported').sort((a,b)=>(BO[a.band]??9)-(BO[b.band]??9)||(b.score??0)-(a.score??0));
 const b=document.getElementById('b-findings');b.textContent=rows.length;b.className='badge'+(rows.length?' show':'');document.getElementById('n-findings').textContent=`(${rows.length})`;
 const lf=latestForensic();const nrev=rows.filter(x=>!lf[x.id]).length;
 document.getElementById('f-banner').innerHTML=rows.length?`<div class="banner ${nrev?'todo':'done'}"><b>${rows.length} finding${rows.length===1?'':'s'} selected for the report.</b> ${nrev?nrev+' without a forensic review yet.':'All have a forensic review.'}</div>`:'<div class="banner todo"><b>No findings yet.</b> Mark rows as <i>Report</i> on the results tabs and they appear here.</div>';
 const box=document.getElementById('findings-box');if(!rows.length){box.innerHTML='';return}
 box.innerHTML='<table><tr><th>Finding</th><th>Where</th><th>Assessment</th><th>Why we flagged it</th><th style="min-width:220px">Additional Comments</th><th style="min-width:260px">Forensic review</th><th>Actions</th></tr>'+rows.map(x=>{const f=lf[x.id];
  return `<tr><td>${findingCell(x)}</td><td>${esc(CH[x.channel])}<span class="meta">${esc(x.platform)}</span></td><td><span class="band ${bc(x.band)}">${esc(x.band||'')}</span></td><td class="why">${clamp(x.explanation||'')}</td>
  <td><textarea class="notebox" id="note-${x.id}">${esc(x.review_note||'')}</textarea><a class="abtn p" onclick="saveNote('${x.id}')">Save</a></td>
  <td class="fx">${f?`<b class="v-${f.verdict}">${VL[f.verdict]||f.verdict}</b>${f.band_suggested?' · suggests <span class="band '+bc(f.band_suggested)+'">'+esc(f.band_suggested)+'</span>':''}<div>${esc(f.rationale||'')}</div><div class="meta"><i>${esc(f.recommendation||'')}</i></div><div class="meta">${esc(f.model)} · ${(f.created_at||'').slice(0,10)}</div>`:'<span class="meta">not reviewed</span>'}</td>
  <td style="min-width:150px"><a class="abtn p" onclick="one('${x.id}','new')">Send back to Results</a><a class="abtn g" onclick="openForensic('${x.id}')">Forensic Review</a></td></tr>`}).join('')+'</table>'}
function renderExcluded(){const rows=D.results.filter(x=>x.review_status==='excluded');document.getElementById('n-excluded').textContent=`(${rows.length})`;
 const box=document.getElementById('excluded-box');if(!rows.length){box.innerHTML='<p class="empty">Nothing excluded on this run.</p>';return}
 box.innerHTML='<table><tr><th>Result</th><th>Where</th><th>Assessment</th><th>Reason</th><th>By</th><th>Action</th></tr>'+rows.map(x=>`<tr class="row-ex"><td>${findingCell(x)}</td><td>${esc(CH[x.channel])}<span class="meta">${esc(x.platform)}</span></td><td><span class="band ${bc(x.band)}">${esc(x.band||'')}</span></td><td>${esc(REASON_LABEL[x.review_reason]||x.review_reason||'')}${x.review_note?'<span class="meta">'+esc(x.review_note)+'</span>':''}</td><td class="small">${esc(x.reviewed_by||'')}${x.auto_excluded?' <span class="meta">(automatic)</span>':''}</td><td><a class="abtn p" onclick="one('${x.id}','new')">Send back to Results</a></td></tr>`).join('')+'</table>'}
function renderForensicLog(){const fv=D.forensic||[];document.getElementById('n-forensic').textContent=`(${fv.length})`;
 document.getElementById('forensic-box').innerHTML=fv.length?`<table><tr><th>Result</th><th>Engine</th><th>Verdict</th><th>Suggested</th><th>Rationale</th><th>Recommendation</th><th>Requested by</th></tr>${fv.map(f=>`<tr><td><b>${esc(f.title)}</b><span class="tmno">${esc(CH[f.channel]||'')} · ${esc(f.platform||'')}</span></td><td><span class="band ${bc(f.engine_band)}">${esc(f.engine_band||'')}</span></td><td><b class="fx v-${f.verdict}">${VL[f.verdict]||f.verdict}</b></td><td>${f.band_suggested?`<span class="band ${bc(f.band_suggested)}">${esc(f.band_suggested)}</span>`:''}</td><td class="why">${esc(f.rationale||'')}</td><td class="why">${esc(f.recommendation||'')}</td><td class="small">${esc(f.requested_by||'')}<span class="meta">${esc(f.model)} · ${(f.created_at||'').slice(0,16).replace('T',' ')}</span></td></tr>`).join('')}</table>`:'<p class="empty">No forensic reviews on this run yet. Run them from the Findings tab.</p>'}
function renderS4(){const r=D.run;document.getElementById('s4-inc').checked=!!r.section4_included;document.getElementById('s4-app').checked=!!r.section4_approved;document.getElementById('s4-app').disabled=!r.section4_included||!(r.section4_text||'').trim();
 const ta=document.getElementById('s4-text');if(document.activeElement!==ta)ta.value=r.section4_text||'';
 document.getElementById('s4-status').innerHTML=r.section4_included?(r.section4_approved?`<span class="ok">Approved by ${esc(r.section4_approved_by||'')} ${(r.section4_approved_at||'').slice(0,16).replace('T',' ')}</span>`:'<span style="color:#8A5C11;font-weight:700">Included — awaiting approval</span>'):'Not included in the report'}
async function s4Set(patch){const r=await fetch('/api/runs/'+RUN+'/section4',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(patch)});if(!r.ok)alert(await r.text());load()}
async function saveNote(id){const v=document.getElementById('note-'+id).value;await fetch('/api/results/review',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({ids:[id],status:'note',note:v})});load()}
function modal(html){const o=document.createElement('div');o.className='overlay';o.innerHTML=`<div class="modal">${html}</div>`;o.onclick=e=>{if(e.target===o)o.remove()};document.body.appendChild(o);return o}
function openForensic(preId){const rows=D.results.filter(x=>x.review_status==='reported');if(!rows.length){alert('No findings to review. Mark rows as Report first.');return}
 const lf=latestForensic();const o=modal(`<h3>Run Forensic Reviews</h3><p class="note">Tick the findings to review. Each takes a few seconds and returns a verdict, a plain-English rationale and a recommendation; Section 4 is redrafted from the results.</p>
 <div class="list">${rows.map(x=>`<label><input type="checkbox" value="${x.id}" ${preId?(x.id===preId?'checked':''):(lf[x.id]?'':'checked')}> <span><b>${esc(x.title)}</b> <span class="meta">${esc(CH[x.channel])} · ${esc(x.platform)}${lf[x.id]?' · already reviewed ('+esc(VL[lf[x.id].verdict])+')':''}</span></span></label>`).join('')}</div>
 <div class="btns"><a class="btn sec" onclick="this.closest('.overlay').remove()">Cancel</a><a class="btn" id="fx-go">Confirm and run</a></div>`);
 o.querySelector('#fx-go').onclick=async()=>{const ids=[...o.querySelectorAll('input:checked')].map(i=>i.value);if(!ids.length){alert('Nothing ticked.');return}
  o.querySelector('.modal').innerHTML=`<h3>Running ${ids.length} forensic review${ids.length===1?'':'s'}…</h3><p class="note">Please leave this open. About ${Math.ceil(ids.length*6/60)||1} minute${ids.length*6>90?'s':''}.</p>`;
  const r=await fetch('/api/runs/'+RUN+'/forensic',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({ids})});const d=await r.json();
  if(!r.ok){o.querySelector('.modal').innerHTML=`<h3>Failed</h3><p>${esc(d.detail||'error')}</p><div class="btns"><a class="btn sec" onclick="this.closest('.overlay').remove()">Close</a></div>`;return}
  o.querySelector('.modal').innerHTML=`<h3>Forensic comments</h3><div class="list">${d.reviewed.map(x=>`<div style="padding:10px 12px;border-bottom:1px solid #EEF1F4" class="fx"><b>${esc(x.title)}</b> — <b class="v-${x.verdict}">${VL[x.verdict]}</b>${x.band_suggested?' · suggests '+esc(x.band_suggested):''}<div>${esc(x.rationale)}</div><div class="meta"><i>${esc(x.recommendation)}</i></div></div>`).join('')}${d.failed.map(x=>`<div style="padding:10px 12px;color:#8f1d1d">${esc(x.title)} — failed: ${esc(x.error)}</div>`).join('')}</div>
  <p class="note">Section 4 has been ${d.section4.section4_approved?'left as approved':'redrafted — review and approve it on the Findings tab'}.</p><div class="btns"><a class="btn" onclick="this.closest('.overlay').remove();load()">Done</a></div>`}}
async function clearFindings(){if(!confirm('Send every Finding back to Results?'))return;await fetch('/api/runs/'+RUN+'/findings/clear',{method:'POST'});load()}
function bulkbar(c){const n=(sel[c]||new Set()).size;const el=document.getElementById('bulk-'+c);el.style.display=n?'flex':'none';el.querySelector('.nsel').textContent=n}
document.querySelectorAll('.fstatus').forEach(s=>s.onchange=()=>{state[s.dataset.ch].status=s.value;renderChannel(s.dataset.ch)});
document.querySelectorAll('.fq').forEach(i=>i.oninput=()=>{state[i.dataset.ch].q=i.value.toLowerCase();renderChannel(i.dataset.ch)});
document.querySelectorAll('.fwide').forEach(i=>i.onchange=()=>{state[i.dataset.ch].wide=i.checked;renderChannel(i.dataset.ch)});
async function importXlsx(inp){const f=inp.files[0];if(!f)return;const fd=new FormData();fd.append('file',f);
 const r=await fetch('/api/runs/'+RUN+'/import',{method:'POST',body:fd});const d=await r.json();inp.value='';
 if(!r.ok){alert(d.detail||'import failed');return}alert(`Read ${JSON.stringify(d.read)}. Added ${d.inserted}, skipped ${d.skipped} already present, ${d.auto_excluded} auto-excluded.`);load()}
document.querySelectorAll('.chip[data-g]').forEach(ch=>ch.onclick=()=>{ch.classList.toggle('on');const g=state[ch.dataset.ch].groups;ch.classList.contains('on')?g.add(ch.dataset.g):g.delete(ch.dataset.g);renderChannel(ch.dataset.ch)});
async function review(ids,status,reason,note){const r=await fetch('/api/results/review',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({ids,status,reason,note})});if(!r.ok)alert(await r.text());for(const k in sel)sel[k].clear();load()}
function one(id,status){review([id],status)}
function picker(el,id){const td=el.parentElement;if(td.querySelector('.picker'))return;const opts=Object.entries(REASON_LABEL).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
 td.insertAdjacentHTML('beforeend',`<div class="picker"><select>${opts}</select><input placeholder="note (optional)"><div><a class="abtn r" onclick="confirmEx(this,'${id}')">Confirm exclusion</a><a class="abtn p" onclick="this.closest('.picker').remove()">Cancel</a></div></div>`)}
function confirmEx(el,id){const p=el.closest('.picker');review([id],'excluded',p.querySelector('select').value,p.querySelector('input').value)}
function bulk(c,status){const el=document.getElementById('bulk-'+c);review([...(sel[c]||[])],status,status==='excluded'?el.querySelector('.breason').value:null,el.querySelector('.bnote').value)}
function issue(){const n=D.results.filter(x=>x.review_status==='reported').length;const r=D.run;
 if(r.section4_included&&!r.section4_approved){alert('Section 4 is included but not yet approved. Tick "Approve Section 4" on the Findings tab, or untick "Include".');return}
 if(!n){const o=modal(`<h3>This report has no Findings</h3><p>Nothing is marked Reported. The client's link will show the summary and all results, but the Findings tab will say nothing was selected. Is that okay?</p><div class="btns"><a class="btn sec" onclick="this.closest('.overlay').remove()">No, go back</a><a class="btn" id="go">Yes, issue with no Findings</a></div>`);o.querySelector('#go').onclick=()=>{o.remove();doIssue(true)};return}
 const o=modal(`<h3>Issue report</h3><p>${n} finding${n===1?'':'s'} currently selected${r.section4_included?', Section 4 included and approved':''}. Do you want to clear the Findings, or run with the current Findings?</p><div class="btns"><a class="btn sec" onclick="this.closest('.overlay').remove()">Cancel</a><a class="btn sec" id="clr">Clear Findings</a><a class="btn" id="go">Run with current Findings</a></div>`);
 o.querySelector('#go').onclick=()=>{o.remove();doIssue(false)};o.querySelector('#clr').onclick=async()=>{o.remove();await fetch('/api/runs/'+RUN+'/findings/clear',{method:'POST'});load()}}
async function doIssue(allowEmpty){const t=prompt('Report title',(D.run.kind==='monitoring'?'Monitoring report — ':'Trademark audit — ')+D.run.mark_text);if(t===null)return;
 const r=await fetch('/api/runs/'+RUN+'/report',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({title:t,allow_empty:allowEmpty})});const d=await r.json();if(!r.ok){alert(d.detail||'failed');return}
 modal(`<h3>Report created</h3><p>${d.results} finding${d.results===1?'':'s'}${D.run.section4_included?', Section 4 included':''}. <b>Nothing has been sent.</b></p>
 <p class="note">Read it as the client will see it. Happy with it? Download the PDF and send it yourself; download the Word version if you want to amend it first. Nothing is sent automatically. When you have sent it, mark it as sent so the record is right.</p>
 <div class="btns"><a class="btn sec" target="_blank" href="${esc(d.url)}">View as the client sees it</a><a class="btn sec" href="/api/reports/${d.report_id}/report.docx">Download Word</a><a class="btn sec" href="/api/reports/${d.report_id}/report.pdf">Download PDF</a><a class="btn" onclick="this.closest('.overlay').remove();load()">Done</a></div>
 <p class="note" style="margin-top:10px">Client link: <span class="q">${esc(d.url)}</span></p>`)}
async function revoke(id){if(!confirm('Revoke this link? Anyone opening it will see "not valid".'))return;await fetch('/api/reports/'+id,{method:'DELETE'});load()}
async function rerun(){if(!confirm('Re-run the searches for this Deal as a monitoring run? Learned exclusions apply automatically.'))return;const r=await fetch('/api/runs/'+RUN+'/rerun',{method:'POST'});alert(r.ok?'Queued — see the runs list in a few minutes.':await r.text())}
async function addUrls(){const v=document.getElementById('purls').value;if(!v.trim())return;await fetch('/api/portfolio',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({client_id:D.run.client_id,urls:v})});document.getElementById('purls').value='';load()}
async function delP(id){await fetch('/api/portfolio/'+id,{method:'DELETE'});load()}
async function delE(id){await fetch('/api/exclusions/'+id,{method:'DELETE'});load()}
load();"""


# ---------------------------------------------------------------------------
# client: the magic link
# ---------------------------------------------------------------------------

def report_reference(run: dict) -> str:
    """The reference a client quotes back at us (Jonathan, 16 Sep 2026):

        TMH-A-19447090-260916
        └─┬─┘ │ └──┬───┘ └─┬──┘
          │   │    │       date of the run, YYMMDD (sorts chronologically)
          │   │    last 8 of the Zoho Deal id — finds the Deal instantly
          │   A audit · M monitoring
          house prefix

    Monitoring reports on one Deal therefore share everything but the date,
    so a client's run of reports reads as a series. Falls back to the run's
    own id when a run has no Deal behind it (a CLI or imported run).
    """
    kind = "M" if (run.get("kind") or "") == "monitoring" else "A"
    deal = str(run.get("zoho_deal_id") or run.get("deal_id") or "")
    tail = deal[-8:] if deal else str(run.get("id") or "")[:8].upper()
    when = run.get("started_at") or run.get("created_at")
    try:
        stamp = when.strftime("%y%m%d")
    except AttributeError:
        stamp = str(when or "")[2:10].replace("-", "") or date.today().strftime("%y%m%d")
    return f"TMH-{kind}-{tail}-{stamp}"


def mark_type_label(raw: str | None, has_image: bool) -> str:
    """Word / Image / Word + Image from the register's mark-type field, whatever
    vocabulary the source used (Temmy, Signa and WIPO differ); falls back to
    'Image' when the row carries an image and the source said nothing."""
    t = str(raw or "").lower()
    if re.search(r"combin|composite|word.*(fig|dev|image)|(fig|dev|image).*word", t):
        return "Word + Image"
    if re.search(r"fig|device|image|logo|3d|shape|colou?r|sound|pattern", t):
        return "Image"
    if re.search(r"word|text|standard", t):
        return "Word"
    return "Image" if has_image else (t[:1].upper() + t[1:] if t else "Word")


_CLIENT_FIELDS = ("id", "channel", "platform", "kind", "title", "url", "external_ref", "status", "owner",
                  "classes", "goods", "band", "change", "note", "image_url", "image_path", "image_src")
_CLIENT_DETAIL = ("mark_type", "snippet", "source_domain", "position", "price", "seller", "registered", "redirects_to",
                  "sic_sectors", "previous_names")
_CLIENT_DATES = ("filing", "registration", "expiry", "created", "expires", "incorporated", "dissolved")


def client_payload(view: dict, run: dict, plan: list[dict], full_rows: list[dict] | None = None) -> dict:
    """Everything the client page gets — and nothing else."""
    req = run.get("request") or {}
    by_id = {str(r["id"]): r for r in (full_rows or [])}
    rp = requested_platforms(run, plan)
    mark, classes = run.get("mark_text") or "", list(run.get("classes") or [])

    reg_country = {}
    for c in run.get("coverage") or []:
        reg = re.sub(r"\s*\(.*?\)", "", c.get("register") or "").strip()
        if reg:
            reg_country[reg] = c.get("country")
    from .signa import OFFICE_NAMES
    office_country = {name: country(code) for code, name in OFFICE_NAMES.items()}

    def safe(row: dict) -> dict:
        out = {k: row.get(k) for k in _CLIENT_FIELDS}
        from .images import image_src
        out["image_src"] = image_src(row)          # our stored copy first, source URL as fallback
        d, dt = row.get("detail") or {}, row.get("dates") or {}
        out["detail"] = {k: d.get(k) for k in _CLIENT_DETAIL if d.get(k) not in (None, "")}
        if row.get("channel") == "trademark":       # the Type column: Word / Image / Word + Image
            out["detail"]["mark_type"] = mark_type_label(d.get("mark_type"), bool(out["image_src"]))
        out["dates"] = {k: dt.get(k) for k in _CLIENT_DATES if dt.get(k)}
        out["why"] = client_reason(row, mark, classes)
        plat = re.sub(r"\s*\((Temmy|Signa)\)", "", out.get("platform") or "")
        if row.get("channel") == "trademark":
            plat = reg_country.get(plat) or office_country.get(plat) or plat
        elif row.get("channel") == "company":
            plat = "Companies House"
        elif row.get("channel") == "domain":
            plat = "Domain registry"
        out["platform"] = plat
        return out

    def merged(r: dict) -> dict:
        full = by_id.get(str(r.get("result_id") or r.get("id")))
        return {**r, **({"detail": full.get("detail"), "dates": full.get("dates")} if full else {})}

    groups = {k: [safe(merged(r)) for r in v] for k, v in (view.get("results_by_group") or {}).items()}

    # --- the sections the Braudit template carries, derived from this run ---
    reported_rows = [safe(merged(r)) for r in view.get("reported") or []]
    req = (run.get("request") or {})
    overview = _overview_rows(groups, reported_rows, view)
    crit_w, crit_d = _criteria_rows(plan)
    return {
        "report": view["report"],
        "mark": mark, "tagline": rp["tagline"], "logo_url": rp["logo_url"], "logo_searched": rp["logo_searched"],
        "logo": rp["logo_client"],          # no Vienna notes, no staff warning
        "searched_on": (run.get("finished_at") or datetime.now()).strftime("%d %b %Y") if run.get("finished_at") else "",
        "kind": run.get("kind"),
        # terms travel with the class (17 Sep 2026). They were dropped here, so
        # the report could only show the whole goods_text as one clamped blob.
        "classes": [{"n": c["n"], "short": c["short"], "heading": c["heading"],
                     "terms": c.get("terms") or ""} for c in classes_detail(run)],
        "countries": rp["countries"],
        "countries_text": _join_countries(rp["countries"]),
        "companies_register": "UK Companies House" if rp["companies"] else "",
        "domains": rp["domains"],
        "search_engines": sorted({re.sub(r"\s+(Web|Images)$", "", p) for p in rp["search_engines"]}),
        "socials": rp["socials"], "marketplaces": rp["marketplaces"],
        "reported": reported_rows,
        "results_by_group": groups,
        # --- Braudit-template sections (ruling B: report = findings, link = record) ---
        "client": req.get("client_name") or "",
        "goods_text": req.get("goods_text") or "",
        "overview": overview,
        "criteria_word": crit_w,
        "criteria_domain": crit_d,
        "criteria_summary": [(c["type"], c["phrase"]) for c in crit_w][:4],
        "exclusion_rules": _exclusion_prose(run, plan, view),
        "own_assets": [{"kind": p["kind"], "value": p["value"], "label": p.get("label") or ""}
                       for p in view.get("portfolio") or []],
        "cover": {
            "prepared_for": req.get("client_name") or "",
            "brand_reference": mark,
            "report_reference": report_reference(run),
            "client_contact": req.get("contact_name") or "",
            "client_email": req.get("contact_email") or "",
            "account_manager": req.get("deal_owner") or "",
            "prepared_by": (view.get("report") or {}).get("created_by") or "",
            "type_of_search": "Word and Image" if rp["logo_searched"] else "Word",
            "sic_code": req.get("sic_code") or "",
            "nature_of_business": req.get("nature_of_business") or "",
            "filtering_rules": _filtering_rules(run, req),
            "report_frequency": req.get("report_frequency") or "",
            "monitoring_since": req.get("monitoring_since") or "",
            "previous_report": req.get("previous_report") or "",
            "period": req.get("period") or "",
        },
        "portfolio": [{"kind": p["kind"], "value": p["value"], "label": p.get("label")} for p in view.get("portfolio") or []],
        "exclusions_by_group": {k: [{"value": e["value"], "reason": e["reason"], "note": e.get("note")} for e in v]
                                for k, v in (view.get("exclusions_by_group") or {}).items()},
        "forensic": [{"result_id": str(f.get("result_id")), "title": f.get("title"), "verdict": f.get("verdict"),
                      "band_suggested": f.get("band_suggested"), "rationale": f.get("rationale"),
                      "recommendation": f.get("recommendation"), "channel": f.get("channel")}
                     for f in view.get("forensic") or []],
    }


_OVERVIEW_ORDER = ["Trademarks", "Companies", "Domain names", "Web", "Social media", "Online shopping"]


def _overview_rows(groups: dict, reported: list, view: dict) -> list:
    """Results Overview — and it must reconcile.

    total = findings + everything else. The client document now carries only
    the findings (ruling B, 15 Sep), so this table is the only place a client
    can see how much is on the link and has not been printed. If these three
    numbers ever stop adding up, the report is lying about its own coverage.
    """
    label = {"trademark": "Trademarks", "company": "Companies", "domain": "Domain names",
             "web": "Web", "social": "Social media", "marketplace": "Online shopping"}
    found: dict = {}
    for rows in groups.values():
        for r in rows:
            found[label.get(r.get("channel"), "Other")] = found.get(label.get(r.get("channel"), "Other"), 0) + 1
    flagged: dict = {}
    for r in reported:
        k = label.get(r.get("channel"), "Other")
        flagged[k] = flagged.get(k, 0) + 1
    excl: dict = {}
    for k, rows in (view.get("exclusions_by_group") or {}).items():
        excl[k] = len(rows)

    out = []
    for name in _OVERVIEW_ORDER + sorted(set(found) - set(_OVERVIEW_ORDER)):
        total = found.get(name, 0) + excl.get(name, 0)
        if not total:
            continue
        fnd = flagged.get(name, 0)
        out.append({"platform": name, "total": total, "findings": fnd, "other": total - fnd})
    return out


def _criteria_rows(plan: list[dict]) -> tuple[list, list]:
    """(word criteria, domain criteria) as Search Type · Phrase · Remarks.

    Remarks is the engine's own rationale for generating that criterion, so
    the report explains each search rather than merely asserting it. Taken
    from the plan, which is the criteria of record — searched and declared
    are the same list by construction.
    """
    words: dict = {}
    domains: dict = {}
    for p in plan or []:
        if p.get("channel") == "domain":
            q = (p.get("query") or "").strip()
            if q and q not in domains:
                domains[q] = {"type": "Domain", "phrase": q,
                              "remarks": p.get("rationale") or ""}
            continue
        if p.get("channel") != "trademark" or p.get("kind") != "word":
            continue
        declared = (p.get("declared_as") or "").strip()
        if not declared:
            continue
        # declared_as reads "Exact Match: Coastal Nutrients"
        kind, _, phrase = declared.partition(":")
        key = (kind.strip(), phrase.strip())
        if key not in words:
            words[key] = {"type": kind.strip(), "phrase": phrase.strip(),
                          "remarks": p.get("rationale") or ""}
    return list(words.values()), list(domains.values())[:12]


def _filtering_rules(run: dict, req: dict) -> str:
    """The one-line scope summary the existing report carries."""
    bits = []
    mark = run.get("mark_text") or ""
    if mark:
        bits.append(f"mark scope: {mark} and close variants")
    classes = run.get("classes") or []
    if classes:
        bits.append("class touch any of " + ", ".join(str(c) for c in classes))
    if req.get("sic_code"):
        bits.append(f"SIC = {req['sic_code']} (companies)")
    bits.append("dead and expired trademarks retained, tagged Not live")
    return "; ".join(bits)


def _exclusion_prose(run: dict, plan: list[dict], view: dict) -> list:
    """One line per channel, generated from what the engine actually did.

    Stronger than the typed paragraph in the old report because it is
    produced from the run rather than written from memory.
    """
    classes = ", ".join(str(c) for c in (run.get("classes") or []))
    excl = view.get("exclusions_by_group") or {}
    n = lambda k: len(excl.get(k) or [])
    tlds = sorted({"." + q.split(".", 1)[1] for p in (plan or []) if p.get("channel") == "domain"
                   for q in [(p.get("query") or "")] if "." in q})
    out = [
        f"Trademarks: results matching none of the declared criteria were dropped. Broad stem "
        f"criteria were restricted to class{'es' if ',' in classes else ''} {classes or '—'}; close "
        f"matches were not class-filtered, because a near-identical mark in another class is still "
        f"worth seeing. Dead and expired records were kept and tagged Not live."
        + (f" {n('Trademarks')} set aside on review." if n("Trademarks") else ""),
        "Companies House: searched by name. A company carries no Nice classes, so the class filter "
        "does not apply to this channel."
        + (f" {n('Companies')} set aside on review." if n("Companies") else ""),
    ]
    if tlds:
        out.append(f"Domains: candidates checked across {', '.join(tlds[:8])}. Names that are not "
                   "registered are reported as Available; a lookup we could not complete is reported "
                   "as Not Assessed, never as Available.")
    out.append("Google, social media and online shopping: your own website, social accounts and "
               "storefronts were excluded automatically so they are not reported back to you as "
               "conflicts."
               + (f" {n('Web') + n('Social media') + n('Online shopping')} further results set aside "
                  "on review." if (n("Web") + n("Social media") + n("Online shopping")) else ""))
    if run.get("kind") == "monitoring":
        out.append("Exclusions carried forward from your previous reports were applied automatically "
                   "before our team reviewed the results.")
    return out


_THE = {"United Kingdom", "European Union", "United States", "United Arab Emirates", "Netherlands", "Philippines"}


def _join_countries(names: list[str]) -> str:
    out = [("the " + n) if n in _THE else n for n in names]
    return ", ".join(out[:-1]) + (" and " if len(out) > 1 else "") + out[-1] if out else ""


def report_page(payload: dict) -> str:
    hero = """<div class="rpt-head"><div class="in"><div><p class="eyebrow" id="h-eyebrow">Trademark audit</p><h1 id="h-title">…</h1><p class="sub" id="h-sub"></p>
<div class="head-actions"><a class="btn ghost" id="dl-word">Download (Word)</a><a class="btn ghost" id="dl-pdf">Download (PDF)</a><a class="btn ghost" onclick="window.print()">Print</a></div></div>
<div class="head-meta" id="h-meta"></div></div></div>"""
    tabs = ('<div class="tab on" data-pane="summary" role="tab">Summary</div>'
            '<div class="tab hot" data-pane="findings" role="tab">Findings <span class="badge" id="b-find"></span></div>'
            '<div class="tab" data-pane="all" role="tab">All results <span class="n" id="n-all"></span></div>'
            '<div class="tab" data-pane="portfolio" role="tab">Your portfolio <span class="n" id="n-port"></span></div>'
            '<div class="tab" data-pane="excl" role="tab">Exclusions <span class="n" id="n-excl"></span></div>'
            '<div class="tab" data-pane="forensic" role="tab" id="tab-forensic" style="display:none">Forensic review <span class="n" id="n-for"></span></div>')
    panes = """
<div class="pane on" id="pane-summary">
 <div class="cards"><div class="card"><p class="eyebrow">What we searched for</p><div class="kv" id="s-request"></div></div>
 <div class="card"><p class="eyebrow">This report</p><div class="tot"><span class="big" id="s-total">0</span><span class="lbl">results found across<br>everywhere we searched</span></div><div class="bar" id="s-bar"></div><table class="counts" id="s-counts"></table></div></div>
 <h3 style="margin-top:22px">Where we searched</h3><div class="kv" id="s-where"></div>
 <h3 style="margin-top:22px">Your classes</h3><p class="hd-note">The official class headings your application falls under, for reference.</p><table class="tick" id="s-classes"></table></div>
<div class="pane" id="pane-findings"><div id="f-banner"></div><div id="findings"></div><div id="s4c"></div></div>
<div class="pane" id="pane-all"><p class="note" style="margin-top:0">Everything the searches returned that has not been excluded, grouped by where it was found.</p><div class="subtabs" id="all-tabs"></div><div id="all-box"></div></div>
<div class="pane" id="pane-portfolio"><p class="note" style="margin-top:0">Assets we hold as yours. These are never reported to you as conflicts.</p><div id="portfolio"></div></div>
<div class="pane" id="pane-excl"><p class="note" style="margin-top:0">Results we have set aside, with the reason.</p><div id="excl"></div></div>
<div class="pane" id="pane-forensic"><p class="note" style="margin-top:0">Results that were given a closer, individual review.</p><div id="forensic"></div></div>
<div class="contact dark"><b>Anything look wrong — or need to speak to us?</b><br><span class="note">Call 0161 833 5400 or reply to the email this link came with.</span></div>
<p class="note">Sources: the official registers, company registries and platforms listed against each result, searched on the date shown. This page is a record of search results and is not legal advice.</p>"""
    script = ("const V=" + json.dumps(payload, default=str) + ";\n"
              + _CLIENT_JS.replace("__SHARED__", _SHARED_JS.replace("__COLS__", columns_json())))
    return shell(payload["report"].get("title") or "Trademark audit", hero, tabs, panes, script)


_SHARED_JS = r"""const COLS=__COLS__;
const dig=(o,p)=>p.split('.').reduce((a,k)=>(a&&typeof a==='object')?a[k]:undefined,o);
const shortUrl=u=>{const s=String(u||'').replace(/^https?:\/\/(www\.)?/,'');return s.length>52?s.slice(0,52)+'…':s};
const link=(u,t)=>u&&/^https?:\/\//i.test(u)?`<a class="lnk" target="_blank" rel="noopener" href="${esc(u)}" title="${esc(u)}">${esc(t||shortUrl(u))}</a>`:esc(t||u||'');
const clamp=t=>t?`<div class="clamp" onclick="this.classList.toggle('open')">${esc(t)}</div>`:'';
function colCell(x,spec){const kind=spec[2];let v=dig(x,spec[1]);
 if(kind==='image')return v?`<a target="_blank" rel="noopener" href="${esc(v)}"><img class="thumb" src="${esc(v)}"></a>`:'';
 if(kind==='yesno')return v===true?'Yes':(v===false?'No':'');
 if(v===null||v===undefined||v==='')return '';
 if(kind==='link')return link(v);
 if(kind==='strong_link_to:url')return `<b>${link(x.url||('http://'+String(v)+'/'),String(v))}</b>`;
 if(kind==='strong')return `<b>${esc(v)}</b>`;
 if(kind==='mono')return `<span class="tmno" style="display:inline">${esc(v)}</span>`;
 if(kind==='title')return esc(String(v).charAt(0).toUpperCase()+String(v).slice(1));
 if(kind==='clamp')return clamp(String(v));
 return esc(v)}
/* The logo panel. Rendered on every surface whether or not a logo exists —
   an empty frame that says "No logo attached" is information; a missing
   panel reads as "we searched it". */
function logoPanel(L,opts){opts=opts||{};if(!L)L={state:'none',state_label:'No logo attached',vienna:[],line:''};
 const frame=L.image_url
  ?`<a class="logo-frame" target="_blank" rel="noopener" href="${esc(L.image_url)}"><img src="${esc(L.image_url)}" alt="Logo"></a>`
  :`<div class="logo-frame empty">${esc(L.state==='awaited'?'Logo awaited':'No logo attached')}</div>`;
 const notes=opts.notes&&L.vienna_notes||{};
 const vienna=L.vienna&&L.vienna.length
  ?`<ul class="vienna-list">${L.vienna.map(v=>`<li><span class="vienna-code">${esc(v.code)}</span><span>${esc(v.description||'—')}${
      (notes[v.code]&&notes[v.code].length)?`<div class="vienna-note">${esc(notes[v.code].join(' '))}</div>`:''}</span></li>`).join('')}</ul>`
  :'';
 return `<div class="logo-panel">${frame}<div class="logo-body">`
  +`<div class="logo-state ${esc(L.state)}">${esc(L.state_label)}</div>`
  +`<div class="logo-line">${esc(L.line||'')}</div>${vienna}`
  +(opts.notes&&L.warning?`<div class="logo-warn">${esc(L.warning)}</div>`:'')
  +`</div></div>`}"""


_CLIENT_JS = r"""
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const BC={'High':'b-high','Medium/High':'b-mhigh','Medium':'b-med','Low/Medium':'b-lmed','Low':'b-low'};const bc=b=>BC[b]||'b-cleared';
const BO={'High':0,'Medium/High':1,'Medium':2,'Low/Medium':3,'Low':4,'Result (not live)':5,'Not Assessed':6,'Available':7};
const SC={'High':'s-high','Medium/High':'s-mhigh','Medium':'s-med','Low/Medium':'s-lmed','Low':'s-low'};
const CH={trademark:'Trademarks',company:'Companies',domain:'Domain names',web:'Web',social:'Social media',marketplace:'Online shopping'};
__SHARED__
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));document.querySelectorAll('.pane').forEach(x=>x.classList.remove('on'));t.classList.add('on');document.getElementById('pane-'+t.dataset.pane).classList.add('on')});
document.getElementById('h-eyebrow').textContent=V.kind==='monitoring'?'Trademark monitoring':'Trademark audit';
document.getElementById('h-title').textContent=V.report.title||('Trademark audit — '+V.mark);
document.getElementById('h-sub').textContent=`We searched for ${V.mark}${V.tagline?' and the tagline “'+V.tagline+'”':''}${V.logo_searched?' and your logo':''} across ${V.countries_text}${V.socials.length||V.marketplaces.length?', the web, social media and online shopping platforms':', the web'}. Findings our team selected for you are under Findings; everything else is under All results.`;
document.getElementById('h-meta').innerHTML=`<span class="pill">Searched ${esc(V.searched_on)}</span>`+V.countries.map(c=>`<span class="pill">${esc(c)}</span>`).join('');
if(V.token){document.getElementById('dl-word').href='/r/'+V.token+'/report.docx';document.getElementById('dl-pdf').href='/r/'+V.token+'/report.pdf';}
document.getElementById('s-request').innerHTML=`<b>Word mark</b><span><b>${esc(V.mark)}</b></span><b>Tagline</b><span>${esc(V.tagline||'—')}</span><b>Logo</b><span>${logoPanel(V.logo)}</span>`;
const all=[].concat(...Object.values(V.results_by_group));const bands={};for(const x of all)bands[x.band||'—']=(bands[x.band||'—']||0)+1;
document.getElementById('s-total').textContent=all.length;const order=Object.keys(bands).sort((a,b)=>(BO[a]??9)-(BO[b]??9));
document.getElementById('s-bar').innerHTML=order.map(b=>`<span class="${SC[b]||'s-cleared'}" style="width:${(100*bands[b]/(all.length||1)).toFixed(1)}%"></span>`).join('');
document.getElementById('s-counts').innerHTML=order.map(b=>`<tr><td><span class="dot ${SC[b]||'s-cleared'}"></span>${esc(b==='Result (not live)'?'Not live on the register':b)}</td><td>${bands[b]}</td></tr>`).join('')+`<tr class="tot-row"><td>Selected for your report</td><td>${V.reported.length}</td></tr>`;
const plats=a=>a.length?a.map(p=>'<span class="plat">'+esc(p)+'</span>').join(''):'—';
document.getElementById('s-where').innerHTML=`<b>Trademark registers</b><span>${plats(V.countries)}</span><b>Company register</b><span>${esc(V.companies_register||'—')}</span><b>Domain names</b><span>${V.domains?'Yes':'—'}</span><b>Search engines</b><span>${plats(V.search_engines)}</span><b>Social media</b><span>${plats(V.socials)}</span><b>Online shopping</b><span>${plats(V.marketplaces)}</span>`;
document.getElementById('s-classes').innerHTML='<tr><th>Class</th><th>Official heading</th></tr>'+V.classes.map(c=>`<tr><td><b>${c.n}</b><span class="tmno">${esc(c.short)}</span></td><td class="why">${esc(c.heading)}</td></tr>`).join('');
function table(rows,withNote){if(!rows.length)return '';const kinds=[...new Set(rows.map(x=>x.channel))];
 const hasFx=rows.some(x=>FX[x.id]);
 return ['trademark','company','domain','web','social','marketplace'].filter(k=>kinds.includes(k)).map(k=>{
  const rs=rows.filter(x=>x.channel===k);const spec=COLS[k]||COLS.web;
  const heads=spec.map(c=>`<th>${esc(c[0])}</th>`).join('')+'<th>Assessment</th><th>Why we flagged it</th>'+(hasFx?'<th>Our review</th>':'')+(withNote?'<th>Additional Comments</th>':'');
  const body=rs.map(x=>{const f=FX[x.id];
   return '<tr>'+spec.map(c=>`<td>${colCell(x,c)}</td>`).join('')
    +`<td><span class="band ${bc(x.band)}">${esc(x.band==='Result (not live)'?'Not live':x.band||'')}</span></td>`
    +`<td class="why">${esc(x.why||'')}</td>`
    +(hasFx?`<td class="why">${f?esc(f.rationale||''):''}</td>`:'')
    +(withNote?`<td class="why">${esc(x.note||'')}</td>`:'')+'</tr>'}).join('');
  return `<h4 style="margin:14px 0 6px">${CH[k]||k}</h4><div class="scrollx"><table class="wide-cols">${'<tr>'+heads+'</tr>'}${body}</table></div>`}).join('')}
const nb=document.getElementById('b-find');nb.textContent=V.reported.length;nb.className='badge'+(V.reported.length?' show':'');
document.getElementById('f-banner').innerHTML=V.reported.length?`<div class="banner todo"><b>${V.reported.length} finding${V.reported.length===1?'':'s'} selected by our team.</b> These are the results we think you should know about. Call us to talk any of them through.</div>`:'<div class="banner done"><b>Nothing of concern was selected for this report.</b></div>';
const FX={};for(const f of V.forensic)if(!FX[f.result_id])FX[f.result_id]=f;
document.getElementById('findings').innerHTML=V.reported.length?table(V.reported,true):'';
document.getElementById('s4c').innerHTML=V.report.section4?`<h3 style="margin-top:22px">Section 4. Personal Recommendations (without liability)</h3><div class="s4-client">${esc(V.report.section4)}</div>`:'';
const groups=Object.keys(V.results_by_group).sort((a,b)=>Object.keys(CH).indexOf(a)-Object.keys(CH).indexOf(b));document.getElementById('n-all').textContent=`(${all.length})`;
let cur=groups[0];function renderAll(){document.getElementById('all-tabs').innerHTML=groups.map(g=>`<span class="chip ${g===cur?'on c-cleared':''}" onclick="cur='${g}';renderAll()">${esc(CH[g]||g)} (${V.results_by_group[g].length})</span>`).join('');
 const rows=(V.results_by_group[cur]||[]).slice().sort((a,b)=>(BO[a.band]??9)-(BO[b.band]??9));document.getElementById('all-box').innerHTML=rows.length?table(rows,false):'<p class="empty">Nothing found here.</p>'}
if(groups.length)renderAll();else document.getElementById('all-box').innerHTML='<p class="empty">Nothing.</p>';
document.getElementById('n-port').textContent=`(${V.portfolio.length})`;const PK={trademark:'Trademark',company:'Company',domain:'Domain name',handle:'Social / storefront handle',url:'Web address'};
document.getElementById('portfolio').innerHTML=V.portfolio.length?'<table><tr><th>Type</th><th>Asset</th></tr>'+V.portfolio.map(p=>`<tr><td>${esc(PK[p.kind]||p.kind)}</td><td><b>${esc(p.value)}</b>${p.label&&p.label!==p.value?'<span class="tmno">'+esc(p.label)+'</span>':''}</td></tr>`).join('')+'</table>':'<p class="empty">None recorded yet.</p>';
const ex=Object.entries(V.exclusions_by_group);document.getElementById('n-excl').textContent=`(${ex.reduce((n,[k,v])=>n+v.length,0)})`;const RL={own_asset:'Your own asset',unrelated_goods:'Different goods or services',dead_or_parked:'No longer active',duplicate:'Duplicate',staff_judgement:'Reviewed by our team',client_instruction:'At your request'};
document.getElementById('excl').innerHTML=ex.length?ex.map(([k,v])=>`<h4 style="margin:14px 0 6px">${esc(CH[k]||k)}</h4><table><tr><th>Excluded</th><th>Reason</th></tr>${v.map(e=>`<tr><td><b>${esc(e.value)}</b></td><td>${esc(RL[e.reason]||e.reason)}${e.note?'<span class="meta">'+esc(e.note)+'</span>':''}</td></tr>`).join('')}</table>`).join(''):'<p class="empty">Nothing has been excluded.</p>';
document.getElementById('n-for').textContent=`(${V.forensic.length})`;if(V.forensic.length)document.getElementById('tab-forensic').style.display='';
document.getElementById('forensic').innerHTML=V.forensic.length?V.forensic.map(f=>`<div class="qbox"><b>${esc(f.title)}</b> — ${esc(f.verdict)}${f.band_suggested?' ('+esc(f.band_suggested)+')':''}<br><span class="note">${esc(f.rationale||'')}</span></div>`).join(''):'<p class="empty">No forensic reviews yet.</p>';
"""
