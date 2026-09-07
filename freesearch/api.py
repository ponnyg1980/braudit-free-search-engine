"""Zero-dependency dev server + adapter notes.

This wraps the framework-agnostic controller in Python's stdlib
`http.server` so the endpoint is runnable *today* with no pip install — handy
for local front-end integration and for proving the contract. It is NOT the
production server; for production, drop `handle_free_search` /
`handle_jurisdictions` into whatever the tenant already runs:

    FastAPI:
        @app.post('/free-search')
        async def free_search(req: Request):
            return handle_free_search(await req.json(), get_temmy_client())

    Flask:
        @app.post('/free-search')
        def free_search():
            return jsonify(handle_free_search(request.get_json(), client))

    Supabase Edge (Deno) / Lambda:
        call an HTTP shim in front of the same controller.

CORS is wide-open here for embedding; production must restrict
Access-Control-Allow-Origin to the tenant allow-list (TMH, Temmy portal,
approved introducer domains) and put IP rate-limiting in front.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from urllib.parse import parse_qs, urlparse

from .controller import (handle_enrich, handle_free_search, handle_jurisdictions,
                          handle_lookup, handle_read_website, handle_suggest_classes)


def _make_client():
    """Build a TemmyClient from env. Imported lazily so importing this module
    never requires Temmy credentials (tests inject a fake client instead)."""
    import sys
    from pathlib import Path
    deploy = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
    sys.path.insert(0, str(deploy))
    from freesearch.temmy_pooled import PooledTemmyClient
    # .strip() defends against the trailing-newline / stray-space corruption
    # seen in the Temmy credential files (a '\n' makes an invalid HTTP header
    # value — exactly the failure the MCP Query Runs call surfaced).
    key = os.environ.get('TEMMY_API_KEY', '').strip()
    base = (os.environ.get('TEMMY_API_BASE_URL') or '').strip() or None
    # rate_limit_sec=0: TemmyDB is our own, free and unlimited — the client's
    # default 0.25s inter-call throttle only adds latency to the free search,
    # which the service parallelises across a thread pool. PooledTemmyClient
    # keeps one keep-alive Session so those parallel fetches reuse connections.
    kwargs = {'api_key': key, 'rate_limit_sec': 0}
    if base:
        kwargs['base_url'] = base
    return PooledTemmyClient(**kwargs)


# The same host serves the widget (decision 20 Jul: one deploy, one domain —
# partners embed a script tag; every widget update is one push, no per-partner
# copies to drift). Files come from freesearch/web/.
_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
_PAGES = {
    '': 'free-search.html',                    # GET /  -> the wizard
    '/free-search': 'free-search.html',
    # The short wizard (Name -> Classes -> Results). Same file — the page
    # switches itself into quick mode from the pathname.
    '/uk-trademark-quick-search': 'free-search.html',
    # Standalone Class Builder (Jonathan, 21 Aug): Name -> Classes ->
    # Review & Send. Same file again — builder mode from the pathname. No
    # register search: the class tools feed a review screen where classes,
    # descriptions and UKIPO terms are amended, then SENT (to a Deal's
    # Classes & Terms when opened from Zoho, or emailed as a CSV to a new
    # Class Tools lead from the public site).
    '/class-builder': 'free-search.html',
    # Full search report at a unique URL (Jonathan, 21 Aug): the emailed
    # Free/Quick Search reports link here; the page renders the COMPLETE
    # stored result (all flagged marks) in the Sector-Report design and
    # offers a print-to-PDF download. /report/<session> also works — see
    # the startswith route in do_GET.
    '/search-report': 'report.html',
    '/class-assistant': 'class-assistant.html',
    # Same file, AI panel switched on by the pathname (Jonathan, 28 Aug) —
    # one page, two routes, so the plain and AI tools cannot drift apart.
    '/class-assistant-ai': 'class-assistant.html',
    '/search-bar': 'search-bar.html',
    '/search-box': 'search-box.html',          # compact drop-anywhere entry point
    # free-search.html's CONFIG.BRAND_AUDIT_URL points at '/brand-audit/', so
    # without this the "Request a Brand Audit" button on the results screen
    # 404s. The file existed and was built; it was simply never routed.
    '/brand-audit': 'brand-audit.html',
    # Client-facing audit checkout (Jonathan, 27 Aug): the Version 1 flow
    # that FOLLOWS Free/Quick Search — 9 pages from contact capture to
    # thank-you + fact find, pre-populated from the journey session (?s=).
    # The multi-brand tool above stays parked and untouched.
    '/audit': 'audit-checkout.html',
    # Standalone staff tool over POST /suggest-classes (Jonathan, 10 Aug).
    # Same endpoint Free Search and Brand Audit call — one agent, three UIs.
    '/class-agent': 'class-agent.html',
    # One page showing every embeddable widget as a partner site would see it
    # (Jonathan, 28 Aug) — each one dropped in via embed.js, with the exact
    # script tag to copy underneath it.
    '/widgets': 'widgets.html',
    # Staff-facing sales enquiry form (Jonathan, 7 Sep). NOT a client surface:
    # guided call workspace, ad-hoc completion, multiple marks on one order,
    # and a payment gate. Token-gated below — see _STAFF_PATHS.
    '/staff-enquiry': 'staff-enquiry.html',
}

# Pages that require the staff token. Everything else on this host is public
# by design (the wizards are embedded on partner sites), so the check is an
# explicit allow-list rather than a default-deny that someone could forget to
# extend. A wrong or missing token gets the sign-in page, never the form.
_STAFF_PATHS = {'/staff-enquiry'}

def _staff_ok(token: str) -> bool:
    """Constant-time check of the shared staff token.

    STAFF_TOKEN unset means the staff pages are unreachable rather than open
    to everyone — an absent secret must never be the same as no lock.
    """
    want = os.environ.get('STAFF_TOKEN', '')
    if not want:
        return False
    import hmac
    return hmac.compare_digest(str(token or ''), want)


# Shown instead of the form when the token is missing or wrong. Stores what
# is typed and reloads with it on the query string, so staff paste the token
# once per browser and every later link just works.
_STAFF_GATE_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>TMH Staff</title>
<link rel="stylesheet" href="/braudit.css">
<style>body{display:grid;place-items:center;min-height:100vh;background:#eef2f5}
.b{background:#fff;border:1.5px solid var(--line);border-radius:14px;padding:30px 32px;
width:min(420px,calc(100vw - 32px))}h1{margin:0 0 6px;font-size:19px;color:var(--navy)}
p{margin:0 0 18px;color:var(--quiet);font-size:14px}
input{width:100%;padding:12px 14px;border:1.5px solid var(--line);border-radius:10px;
font-size:15px;font-family:inherit;margin-bottom:14px}
input:focus{outline:none;border-color:var(--pink);box-shadow:0 0 0 3px rgba(229,22,82,.12)}
</style></head><body><div class="b">
<h1>Staff access</h1><p>This page is for TMH staff. Enter the access token to continue.</p>
<input type="password" id="k" placeholder="Access token" autofocus
 onkeydown="if(event.key==='Enter')go()">
<button class="btn primary" style="width:100%" onclick="go()">Continue</button>
</div><script>
try{var s=localStorage.getItem('tmh_staff_token');
    if(s&&!new URLSearchParams(location.search).get('k')){
      var u=new URL(location.href);u.searchParams.set('k',s);location.replace(u.toString());}
}catch(e){}
function go(){var v=document.getElementById('k').value.trim();if(!v)return;
  try{localStorage.setItem('tmh_staff_token',v);}catch(e){}
  var u=new URL(location.href);u.searchParams.set('k',v);location.href=u.toString();}
</script></body></html>"""

# Partners drop this one line into their page:
#   <script src="https://<host>/embed.js" data-tenant="acme" async></script>
# It injects the wizard in an iframe pinned to this host, so the widget is
# always the deployed version and the tenant id rides along.
#
# data-widget picks which one: 'free-search' (default, the full wizard),
# 'search-box' (the compact entry point), 'class-assistant', 'search-bar'.
# 'search-box' additionally accepts data-target (where the wizard lives) and
# data-journey (the journey function's base URL, since the engine and the
# Edge Function are different hosts in production).
#
# min-height is per-widget: the wizard needs room, the compact box would look
# absurd in a 640px frame. Both then self-report their real height.
_EMBED_JS = """(function(){
  var s=document.currentScript, t=(s&&s.dataset.tenant)||'tmh';
  var page=(s&&s.dataset.widget)||'free-search';
  var q='?tenant='+encodeURIComponent(t)+'&embed=1';
  if(page==='search-box'){
    if(s.dataset.variant) q+='&variant='+encodeURIComponent(s.dataset.variant);
    if(s.dataset.style)   q+='&style='+encodeURIComponent(s.dataset.style);
    if(s.dataset.target)  q+='&target='+encodeURIComponent(s.dataset.target);
    if(s.dataset.journey) q+='&journey='+encodeURIComponent(s.dataset.journey);
  }
  // data-chrome="full" keeps the tool's own logo and Book a Free Call visible
  // inside an embed. Default is off: a host page normally has both already,
  // and two of each reads as a mistake (developer feedback, 2 Sep).
  if(s.dataset.chrome) q+='&chrome='+encodeURIComponent(s.dataset.chrome);
  // Domain-hosted wizard pages (20 Aug): the search box hands off to a
  // WordPress page carrying ?s=<session>&screen=&q=&journey= — forward those
  // into the iframe so the embedded wizard adopts the session instead of
  // starting over. Only the known resume keys pass through.
  try{
    var hp=new URLSearchParams(window.location.search);
    // 'fs' is the WordPress-safe alias for the session param: WP reserves
    // ?s= for site search and 404s page URLs carrying it (21 Aug). The
    // host page uses fs; the iframe wizard still receives s.
    ['s','fs','screen','q','journey','searchbase','deal'].forEach(function(k){
      var v=hp.get(k); if(v) q+='&'+(k==='fs'?'s':k)+'='+encodeURIComponent(v);
    });
  }catch(e){}
  var minH = page==='search-box' ? (s.dataset.style==='bar'?'70px':'200px') : '760px';
  // Change spec 22 Aug (section 3): reserve space immediately so the WP page
  // never looks empty and the footer never jumps; show a branded loading
  // panel until the app's first height report proves it has rendered; fall
  // back to a static retry message on timeout.
  var wrap=document.createElement('div');
  wrap.style.cssText='position:relative;width:100%;min-height:'+minH;
  var f=document.createElement('iframe');
  f.src=s.src.replace(/embed\\.js.*$/, page+q);
  f.style.cssText='width:100%;min-height:'+minH+';border:0;display:block';
  f.setAttribute('title','Free Trademark Search');
  f.setAttribute('loading','lazy');
  wrap.appendChild(f);
  var panel=null, rot=null, tm=null, ready=false;
  if(page!=='search-box'){
    panel=document.createElement('div');
    panel.style.cssText='position:absolute;top:0;left:0;right:0;bottom:0;display:flex;'+
      'flex-direction:column;align-items:center;justify-content:center;background:#f7f8fa;'+
      'border:1px solid #e3e8ee;border-radius:12px;text-align:center;padding:28px;z-index:2;'+
      'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif';
    panel.innerHTML='<div style="width:46px;height:46px;background:#E51652;color:#fff;'+
      'font-weight:800;font-size:18px;display:flex;align-items:center;justify-content:center;'+
      'transform:skewX(-12deg);margin:0 0 16px"><span style="transform:skewX(12deg)">TH</span></div>'+
      '<div style="font-weight:700;color:#1c2d3b;font-size:17px;margin-bottom:8px">'+
      'Loading your UK trademark search\\u2026</div>'+
      '<div data-bmsg style="color:#5b6b7a;font-size:14px;max-width:480px;line-height:1.55"></div>';
    wrap.appendChild(panel);
    var msgs=[
      'A register search helps identify earlier marks that may affect a new application.',
      'Challenges can arise during examination, opposition or after registration, so early research matters.',
      'A word search is an important first step \\u2014 logos, prior use and activity outside the register can also affect risk.'
    ];
    var mi=0, msgEl=panel.querySelector('[data-bmsg]');
    msgEl.textContent=msgs[0];
    rot=setInterval(function(){ mi=(mi+1)%msgs.length; msgEl.textContent=msgs[mi]; },5000);
    var arm=function(){
      tm=setTimeout(function(){
        if(ready) return;
        clearInterval(rot);
        msgEl.innerHTML='The search tool is taking longer than usual to load. '+
          '<a href="#" data-bretry style="color:#E51652;font-weight:700">Try again</a> '+
          'or call us on 0161 833 5400.';
        var a=panel.querySelector('[data-bretry]');
        if(a) a.addEventListener('click',function(ev){
          ev.preventDefault();
          msgEl.textContent=msgs[0]; mi=0;
          rot=setInterval(function(){ mi=(mi+1)%msgs.length; msgEl.textContent=msgs[mi]; },5000);
          var src=f.src; f.src='about:blank';
          setTimeout(function(){ f.src=src; },60);
          arm();
        });
      },25000);
    };
    arm();
  }
  function appReady(){
    if(ready) return; ready=true;
    if(rot) clearInterval(rot);
    if(tm) clearTimeout(tm);
    if(panel&&panel.parentNode) panel.parentNode.removeChild(panel);
  }
  s.parentNode.insertBefore(wrap, s);
  window.addEventListener('message', function(e){
    if(!e.data) return;
    // ONLY listen to our own iframe. Each embed registers its own listener on
    // the shared window, so without this check a page carrying two widgets
    // has every listener acting on every message — the tall wizard's height
    // report stretched the compact search boxes to match it (found on the
    // /widgets test page, 28 Aug). Same for navigation and scroll requests.
    if(e.source !== f.contentWindow) return;
    // The first height report is the app saying "I have rendered" — replace
    // the loading panel at once, never prolong it (spec: never delay merely
    // to display all messages).
    if(e.data.brauditHeight){ f.style.height = e.data.brauditHeight+'px'; appReady(); }
    if(e.data.brauditReady) appReady();
    // Screen changes and the register-search overlay live INSIDE a very tall
    // iframe; window.scrollTo in there is a no-op and position:fixed pins to
    // the iframe box, not the visitor's viewport. Without this the search
    // wait looked like a crash on WordPress (Jonathan, 26 Aug): the hint
    // carousel was rendering 1000px+ below the fold. The app asks, we scroll.
    if(e.data.brauditScrollTop){
      try{ wrap.scrollIntoView({behavior:'smooth', block:'start'}); }catch(_e){}
    }
    // The compact box hands the visitor over to the full wizard. It cannot
    // navigate the host page itself from inside a cross-origin iframe, so it
    // asks us to. Only ever a navigation, never arbitrary script.
    if(e.data.brauditNavigate) window.location.href = e.data.brauditNavigate;
  });
})();"""


# ── static assets ────────────────────────────────────────────────────────
# Added 17 Aug 2026. Until now the engine served only the pages in _PAGES and
# embed.js — there was no route for a stylesheet or an image, which is why the
# six widgets each carried a private copy of the CSS and why brand/*.png could
# not load anywhere.
#
# Only these three directories, and only these extensions. This is a public,
# unauthenticated host, so the path is treated as hostile:
#
#   * the resolved real path must sit INSIDE the served directory, checked
#     with os.path.commonpath after realpath — that defeats ../ traversal,
#     encoded traversal, and a symlink pointing out of the tree, which a
#     simple '..' not in path check does not
#   * unknown extensions are refused rather than served as octet-stream, so a
#     stray .py or .env in web/ can never be downloaded
_ASSET_DIRS = {'/braudit.css': ('', 'braudit.css'),
               '/wizard.css': ('', 'wizard.css'),
               '/brand/': ('brand', None),
               '/fonts/': ('fonts', None)}
_ASSET_TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2', '.woff': 'font/woff',
}


def _static(path: str):
    """Return (bytes|None, content_type) for a static asset path, else None.

    None  -> not a static route at all, let the caller carry on routing.
    (None, ct) -> it IS a static route but the file isn't there: a real 404.
    """
    if path in ('/braudit.css', '/wizard.css'):
        rel = path.lstrip('/')
    elif path.startswith('/brand/') or path.startswith('/fonts/'):
        rel = path.lstrip('/')
    else:
        return None

    ext = os.path.splitext(rel)[1].lower()
    ctype = _ASSET_TYPES.get(ext)
    if not ctype:
        return (None, 'text/plain')          # unknown type -> 404, never served

    full = os.path.realpath(os.path.join(_WEB, rel))
    root = os.path.realpath(_WEB)
    # commonpath raises ValueError on different drives/roots; treat as outside.
    try:
        inside = os.path.commonpath([full, root]) == root
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(full):
        return (None, ctype)
    try:
        with open(full, 'rb') as f:
            return (f.read(), ctype)
    except OSError:
        return (None, ctype)


_JOURNEY_URL = (os.environ.get('JOURNEY_URL')
                or 'https://jwanlhdmhgmbybcdhvkx.supabase.co/functions/v1/journey')
AUDIT_NET_PENCE = 9900          # £99 per mark. No multi-mark discount (7 Sep).


def _journey_session(session_id: str) -> dict | None:
    """Read a journey session server-side. Used to re-derive the price and
    re-check the staff gate without trusting anything the browser sent."""
    import urllib.error
    import urllib.parse
    import urllib.request
    if not session_id:
        return None
    url = (_JOURNEY_URL.rstrip('/') + '/session?id='
           + urllib.parse.quote(str(session_id)))
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            d = json.loads(r.read().decode())
        return d.get('session') if d.get('ok') else None
    except Exception:
        return None


def _journey_event(session_id: str, event_type: str, payload: dict) -> None:
    """Append a journey event. Best effort — never raises into a caller."""
    import urllib.request
    if not session_id:
        return
    body = json.dumps({'session_id': session_id, 'event_type': event_type,
                       'payload': payload or {}}).encode()
    req = urllib.request.Request(_JOURNEY_URL.rstrip('/') + '/session/event',
                                 data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=12).read()
    except Exception:
        pass


def _staff_gate(enq: dict) -> list:
    """Server-side mirror of the eleven gate rules in staff-enquiry.html.

    The browser copy drives the UI; THIS one decides whether money may be
    taken. Scope FR-022 and AC-06: a direct API call for an ineligible
    enquiry must be refused, so the rules cannot live only in the page.
    Returns the ids of the rules that FAIL — empty means eligible.
    """
    import datetime as _dt

    def s(*path):
        cur = enq
        for p in path:
            if not isinstance(cur, dict):
                return ''
            cur = cur.get(p)
        return cur if cur is not None else ''

    def full(v):
        return len(str(v or '').strip()) > 0

    def real(v):
        return len(str(v or '').strip()) >= 20

    def email_ok(v):
        v = str(v or '').strip()
        return '@' in v and '.' in v.split('@')[-1] and ' ' not in v

    def d(v):
        try:
            return _dt.date.fromisoformat(str(v)[:10])
        except Exception:
            return None

    def working_days_after(start, n):
        cur, left = start, n
        while left > 0:
            cur += _dt.timedelta(days=1)
            if cur.weekday() < 5:
                left -= 1
        return cur

    marks = [m for m in (enq.get('marks') or [])
             if isinstance(m, dict) and full(m.get('text'))]
    classes = [c for c in str(enq.get('classes') or '').replace(',', ' ').split()
               if c.isdigit() and 1 <= int(c) <= 45]
    audit_d, consult_d = d(s('recommend', 'auditDate')), d(s('consult', 'date'))
    pay_d = d(s('billing', 'payDate'))
    today = _dt.date.today()

    bad = []
    if not (full(s('contact', 'first')) and full(s('contact', 'last'))
            and email_ok(s('contact', 'email')) and full(s('contact', 'phone'))
            and full(s('contact', 'position'))):
        bad.append('G-00')
    if not (real(s('discovery', 'desc')) and real(s('discovery', 'reason'))):
        bad.append('G-01')
    if not marks:
        bad.append('G-02')
    if not classes:
        bad.append('G-03')
    if not all(bool(s('advice', k))
               for k in ('risks', 'fees', 'noguarantee', 'questions')):
        bad.append('G-04')
    if not (real(s('recommend', 'scope')) and real(s('recommend', 'rationale'))):
        bad.append('G-05')
    if s('recommend', 'handling') == 'expedited' and not full(s('recommend', 'expReason')):
        bad.append('G-06')
    if not (consult_d and full(s('consult', 'time')) and full(s('consult', 'adviser'))
            and bool(s('consult', 'agreed')) and consult_d >= today):
        bad.append('G-07')
    if not (audit_d and consult_d and audit_d < consult_d):
        bad.append('G-08')
    pay_when = s('billing', 'payWhen')
    pay_ok = (pay_when == 'now'
              or (pay_when == 'date' and pay_d and audit_d
                  and working_days_after(pay_d, 3) <= audit_d))
    if not (pay_ok and bool(s('billing', 'agreed'))):
        bad.append('G-09')
    if not (full(s('billing', 'entity')) and email_ok(s('billing', 'email'))
            and full(s('billing', 'addr'))):
        bad.append('G-10')
    if not bool(s('readback', 'confirmed')):
        bad.append('G-11')
    return bad


def _audit_pay(payload: dict) -> dict:
    """Create a Stripe Checkout session for an audit order.

    The AMOUNT IS COMPUTED HERE, never trusted from the browser: £99 net per
    mark, plus 20% VAT unless the applicant is outside the UK (vat_exempt).
    The consultation is free either way, so it changes the line-item wording
    and not the price.

    Two routes in:
      * client checkout (audit-checkout.html) — one mark, as before;
      * staff enquiry (staff-enquiry.html, staff=1) — the mark COUNT and the
        eligibility both come from the stored journey session, and the
        eleven gate rules are re-checked here. A direct call for an
        ineligible enquiry is refused (scope AC-06).

    Needs STRIPE_SECRET_KEY in the environment (Render env var — the value
    lives in temmy-access/secrets.env, never in this repo). Unset -> the
    page falls back to "we'll call you to take payment".
    """
    import hashlib
    import urllib.error
    import urllib.parse
    import urllib.request

    sk = (os.environ.get('STRIPE_SECRET_KEY') or '').strip()
    if not sk:
        return {'ok': False, 'error': 'payment not configured', 'status': 200}

    vat_exempt = bool(payload.get('vat_exempt'))
    consult = bool(payload.get('consult'))
    ref = str(payload.get('ref') or '')[:40]
    email = str(payload.get('email') or '')[:200]
    session_id = str(payload.get('session_id') or '')[:60]
    staff = bool(payload.get('staff'))
    qty, enq = 1, None

    if staff:
        sess = _journey_session(session_id)
        lr = (sess or {}).get('last_result') or {}
        enq = lr.get('staff_enquiry') if isinstance(lr, dict) else None
        if not isinstance(enq, dict):
            return {'ok': False, 'error': 'enquiry not found', 'status': 200}
        unmet = _staff_gate(enq)
        if unmet:
            _journey_event(session_id, 'audit_pay_refused', {'unmet': unmet})
            return {'ok': False, 'error': 'enquiry is not ready for payment',
                    'unmet': unmet, 'status': 200}
        qty = max(1, len([m for m in (enq.get('marks') or [])
                          if isinstance(m, dict) and str(m.get('text') or '').strip()]))
        email = email or str((enq.get('billing') or {}).get('email') or '')[:200]

    unit = (AUDIT_NET_PENCE if vat_exempt
            else int(round(AUDIT_NET_PENCE * 1.20)))
    name = ('Trademark Audit & Consultation' if consult else 'Trademark Audit')
    desc = ('Audit Promotion applied'
            + (' — VAT not applicable (outside UK)' if vat_exempt
               else ' — includes VAT @ 20%')
            + (f' — {qty} marks' if qty > 1 else ''))
    back = 'https://www.thetrademarkhelpline.com/?audit_paid='
    form = {
        'mode': 'payment',
        'client_reference_id': ref or session_id or 'AUD',
        'success_url': back + '1',
        'cancel_url': back + '0',
        'line_items[0][quantity]': str(qty),
        'line_items[0][price_data][currency]': 'gbp',
        'line_items[0][price_data][unit_amount]': str(unit),
        'line_items[0][price_data][product_data][name]': name,
        'line_items[0][price_data][product_data][description]': desc,
        'metadata[invoice_ref]': ref,
        'metadata[session_id]': session_id,
        'metadata[marks]': str(qty),
        'metadata[source]': 'staff_enquiry' if staff else 'client_checkout',
    }
    if email:
        form['customer_email'] = email
    # Idempotency: the same enquiry at the same price returns the SAME
    # Checkout Session instead of a second one, so a double click or a retry
    # cannot produce two payments (scope INT-004 / AC-07).
    idem = hashlib.sha256(
        f'{session_id}|{ref}|{qty}|{unit}'.encode()).hexdigest()
    req = urllib.request.Request(
        'https://api.stripe.com/v1/checkout/sessions',
        data=urllib.parse.urlencode(form).encode(),
        headers={'Authorization': 'Bearer ' + sk,
                 'Idempotency-Key': 'audit_' + idem,
                 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        _journey_event(session_id, 'audit_pay_started',
                       {'marks': qty, 'unit_pence': unit,
                        'total_pence': unit * qty,
                        'stripe_session': str(data.get('id') or '')})
        return {'ok': True, 'url': data.get('url'),
                'marks': qty, 'total_pence': unit * qty, 'status': 200}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get('error', {}).get('message', '')
        except Exception:
            err = str(e)
        return {'ok': False, 'error': f'stripe: {err[:200]}', 'status': 200}
    except Exception as e:
        return {'ok': False, 'error': f'stripe unreachable: {e}', 'status': 200}


def _stripe_webhook(raw: bytes, sig_header: str) -> dict:
    """Stripe's confirmation of payment — the SOURCE OF TRUTH.

    Until this existed, a paid audit was only recorded if the customer's
    browser came back to ?audit_paid=1. A closed tab lost the payment.
    Stripe retries this endpoint for days, so the record survives.

    Verifies the signature before trusting anything: an unsigned or
    mis-signed body is rejected outright.
    """
    import hashlib
    import hmac
    import time

    secret = (os.environ.get('STRIPE_WEBHOOK_SECRET') or '').strip()
    if not secret:
        return {'ok': False, 'error': 'webhook not configured', 'status': 503}
    parts = dict(p.split('=', 1) for p in str(sig_header or '').split(',')
                 if '=' in p)
    ts, got = parts.get('t', ''), parts.get('v1', '')
    if not ts or not got:
        return {'ok': False, 'error': 'unsigned', 'status': 400}
    try:
        if abs(time.time() - int(ts)) > 300:      # replay window
            return {'ok': False, 'error': 'stale', 'status': 400}
    except ValueError:
        return {'ok': False, 'error': 'bad timestamp', 'status': 400}
    want = hmac.new(secret.encode(), (ts + '.').encode() + raw,
                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return {'ok': False, 'error': 'bad signature', 'status': 400}

    try:
        ev = json.loads(raw.decode())
    except Exception:
        return {'ok': False, 'error': 'bad json', 'status': 400}
    if ev.get('type') != 'checkout.session.completed':
        return {'ok': True, 'ignored': ev.get('type'), 'status': 200}

    obj = (ev.get('data') or {}).get('object') or {}
    meta = obj.get('metadata') or {}
    _journey_event(str(meta.get('session_id') or ''), 'audit_paid', {
        'stripe_session': obj.get('id'),
        'payment_intent': obj.get('payment_intent'),
        'amount_total': obj.get('amount_total'),
        'currency': obj.get('currency'),
        'marks': meta.get('marks'),
        'invoice_ref': meta.get('invoice_ref'),
        'source': meta.get('source'),
        'customer_email': (obj.get('customer_details') or {}).get('email'),
        # Xero cannot be raised yet: there is no refresh token, only an
        # expired access token (checked 7 Sep). Flagged so the paid orders
        # awaiting an invoice can be listed rather than silently forgotten.
        'xero_invoice': 'pending_credentials',
    })
    return {'ok': True, 'status': 200}


def _allowed_origin(origin: str) -> str:
    """CORS allow-list from env. ALLOWED_ORIGINS unset -> '*' (dev only);
    set it to a comma-separated list in production (TMH, portal, partners)."""
    allowed = (os.environ.get('ALLOWED_ORIGINS') or '').strip()
    if not allowed:
        return '*'
    if origin and origin in [o.strip() for o in allowed.split(',')]:
        return origin
    return 'null'


class _Handler(BaseHTTPRequestHandler):
    server_version = 'BrauditFreeSearch/1.0'

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin',
                         _allowed_origin(self.headers.get('Origin', '')))
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Engine-Key')

    def _send(self, body: dict, status: int = 200):
        payload = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_raw(self, payload: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self._cors()
        # Allow partner pages to iframe the widget.
        self.send_header('Content-Security-Policy', 'frame-ancestors *')
        # Added 10 Aug 2026. These responses previously carried NO cache
        # headers, so browsers fell back to heuristic caching and could keep
        # serving an old copy of the widget for hours after a deploy — which
        # is how a fixed bug appears to still be broken, and how a partner
        # site can sit on stale markup indefinitely.
        #
        # no-cache does NOT mean "never store": it means "always revalidate
        # before use". The browser still keeps the file and we still get a
        # cheap 304 when nothing changed. These are small single files served
        # from one host, so the cost is a round trip, and the alternative is
        # visitors running whichever version they happened to load first.
        self.send_header('Cache-Control', 'no-cache, must-revalidate')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _engine_key_ok(self) -> bool:
        """True unless a key is configured and the caller did not present it."""
        want = (os.environ.get('ENGINE_SHARED_KEY') or '').strip()
        if not want:
            return True                       # not configured — open, as before
        got = (self.headers.get('X-Engine-Key') or '').strip()
        # Constant-time compare: a plain == leaks the key one character at a
        # time to anyone patient enough to measure the response.
        import hmac
        return hmac.compare_digest(got, want)

    def do_OPTIONS(self):
        self._send({}, 204)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path in _STAFF_PATHS and not _staff_ok(params.get('k', '')):
            # Interim staff access (Jonathan, 7 Sep): a shared token in ?k=,
            # the same pattern the onboarding site's admin pages use. It is
            # deliberately the cheapest thing that works, because it is
            # replaced by portal sign-in once the Portal can issue a token —
            # see Portal_Identity_in_Search_Spec.md §5.
            self._send_raw(_STAFF_GATE_HTML.encode(), 'text/html; charset=utf-8')
            return
        if path in _PAGES or path.startswith('/report/'):
            # /report/<session-uuid> is the pretty unique-URL form of the
            # search report; the page reads the id from the path itself.
            page = os.path.join(_WEB, _PAGES.get(path, 'report.html'))
            try:
                with open(page, 'rb') as f:
                    self._send_raw(f.read(), 'text/html; charset=utf-8')
            except OSError:
                self._send({'ok': False, 'error': 'widget not found'}, 404)
            return
        if path == '/embed.js':
            self._send_raw(_EMBED_JS.encode(), 'application/javascript')
            return
        asset = _static(parsed.path)
        if asset is not None:
            body, ctype = asset
            if body is None:
                self._send({'ok': False, 'error': 'not found'}, 404)
            else:
                self._send_raw(body, ctype)
            return
        if path == '/healthz':
            self._send({'ok': True})
            return
        if path == '/nuggets':
            from .nuggets import payload as _nug
            self._send({'ok': True, 'nuggets': _nug()})
            return
        if path == '/jurisdictions':
            out = handle_jurisdictions()
            self._send(out, out.get('status', 200))
        elif path.startswith('/lookup/'):
            action = path.split('/lookup/', 1)[1]
            out = handle_lookup(action, params, _make_client())
            self._send(out, out.get('status', 200))
        elif path in ('', '/health'):
            self._send({'ok': True, 'service': 'free-search'})
        else:
            self._send({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        path = self.path.rstrip('/')
        if path == '/stripe-webhook':
            # Handled BEFORE the engine-key check and before any JSON parse:
            # Stripe cannot send our shared header, and the signature is over
            # the exact raw bytes, so re-serialising the body would break it.
            try:
                length = int(self.headers.get('Content-Length', 0) or 0)
                raw = self.rfile.read(length) if length else b''
            except (ValueError, OSError):
                self._send({'ok': False, 'error': 'bad body'}, 400)
                return
            out = _stripe_webhook(raw, self.headers.get('Stripe-Signature', ''))
            self._send(out, out.get('status', 200))
            return
        if path not in ('/free-search', '/enrich', '/suggest-classes', '/read-website',
                        '/class-scope', '/audit-pay'):
            self._send({'ok': False, 'error': 'not found'}, 404)
            return
        if not self._engine_key_ok():
            # The usage gate lives in the Supabase Edge Function, in FRONT of
            # this engine. Without a shared secret that gate is decorative:
            # anyone who opens developer tools sees this URL and can call it
            # directly, for ever, for free — and the AI routes cost real money
            # per call.
            #
            # Opt-in by design. Unset ENGINE_SHARED_KEY and nothing changes,
            # so deploying this cannot break a live site; set it here and on
            # the Edge Function and the back door closes. Set it AFTER both
            # are deployed, never between.
            self._send({'ok': False, 'error': 'forbidden'}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, json.JSONDecodeError):
            self._send({'ok': False, 'error': 'invalid JSON'}, 400)
            return
        if path == '/audit-pay':
            out = _audit_pay(payload)
        elif path == '/enrich':
            out = handle_enrich(payload)
        elif path == '/suggest-classes':
            out = handle_suggest_classes(payload)
        elif path == '/read-website':
            out = handle_read_website(payload)
        elif path == '/class-scope':
            # Class Builder's review screen: the application-grade selection
            # (official Nice headings + registered-vocabulary terms) WITHOUT
            # running a register search. Same spec_terms source of truth as
            # the search journey's scope capture.
            try:
                try:
                    from . import spec_terms  # package context (Render)
                except ImportError:
                    import spec_terms         # bare-script context (local dev)
                rows = spec_terms.build_application_scope(
                    payload.get('classes'), payload.get('class_source'))
                out = {'ok': True, 'application_scope': rows}
            except Exception as e:  # degrade loudly but JSON-shaped
                out = {'ok': False, 'error': f'class-scope failed: {e}',
                       'status': 500}
        else:
            out = handle_free_search(payload, _make_client())
        self._send(out, out.get('status', 200))

    def log_message(self, *args):  # keep dev-server output quiet
        pass


def serve(host: str = '0.0.0.0', port: int = 8080):
    srv = ThreadingHTTPServer((host, port), _Handler)
    print(f'Free Search dev server on http://{host}:{port}')
    print('  GET  /jurisdictions   POST /free-search   POST /enrich')
    srv.serve_forever()


if __name__ == '__main__':
    serve(port=int(os.environ.get('PORT', 8080)))
