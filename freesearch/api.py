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
                          handle_lookup)


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
    '/class-assistant': 'class-assistant.html',
    '/search-bar': 'search-bar.html',
    '/search-box': 'search-box.html',          # compact drop-anywhere entry point
    # free-search.html's CONFIG.BRAND_AUDIT_URL points at '/brand-audit/', so
    # without this the "Request a Brand Audit" button on the results screen
    # 404s. The file existed and was built; it was simply never routed.
    '/brand-audit': 'brand-audit.html',
}

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
    if(s.dataset.target)  q+='&target='+encodeURIComponent(s.dataset.target);
    if(s.dataset.journey) q+='&journey='+encodeURIComponent(s.dataset.journey);
  }
  var f=document.createElement('iframe');
  f.src=s.src.replace(/embed\\.js.*$/, page+q);
  f.style.cssText='width:100%;min-height:'+(page==='search-box'?'200px':'640px')
    +';border:0;display:block';
  f.setAttribute('title','Free Trademark Search');
  f.setAttribute('loading','lazy');
  s.parentNode.insertBefore(f, s);
  window.addEventListener('message', function(e){
    if(!e.data) return;
    if(e.data.brauditHeight) f.style.height = e.data.brauditHeight+'px';
    // The compact box hands the visitor over to the full wizard. It cannot
    // navigate the host page itself from inside a cross-origin iframe, so it
    // asks us to. Only ever a navigation, never arbitrary script.
    if(e.data.brauditNavigate) window.location.href = e.data.brauditNavigate;
  });
})();"""


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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

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

    def do_OPTIONS(self):
        self._send({}, 204)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path in _PAGES:
            page = os.path.join(_WEB, _PAGES[path])
            try:
                with open(page, 'rb') as f:
                    self._send_raw(f.read(), 'text/html; charset=utf-8')
            except OSError:
                self._send({'ok': False, 'error': 'widget not found'}, 404)
            return
        if path == '/embed.js':
            self._send_raw(_EMBED_JS.encode(), 'application/javascript')
            return
        if path == '/healthz':
            self._send({'ok': True})
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
        if path not in ('/free-search', '/enrich'):
            self._send({'ok': False, 'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, json.JSONDecodeError):
            self._send({'ok': False, 'error': 'invalid JSON'}, 400)
            return
        if path == '/enrich':
            out = handle_enrich(payload)
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
