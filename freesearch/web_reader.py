"""Fetch a website and reduce it to plain text, safely.

Used by the "Your website URL" class route: we read the visitor's own site and
use it to ANSWER the describe-your-business questions, which they then check
and correct. Jonathan, 10 Aug: "What the website URL should do, is try and
answer the questions from describe your business."

SECURITY — READ THIS BEFORE CHANGING ANYTHING
---------------------------------------------
This is an UNAUTHENTICATED endpoint that fetches a URL supplied by a stranger.
Done naively that is a server-side request forgery hole: someone points it at
http://169.254.169.254/ or http://localhost:5432 and uses our server to reach
things they cannot reach themselves.

So every URL is checked before we connect:

  * http/https only — no file://, gopher://, ftp://
  * the hostname is RESOLVED and every resulting IP checked; a public-looking
    name can resolve to 127.0.0.1
  * private, loopback, link-local, multicast and reserved ranges rejected
  * redirects followed manually, re-checking the target each hop, because a
    public URL can 302 straight to an internal one
  * hard caps on time and size

The address checks use Python's own `ipaddress` module rather than a regex —
regexes miss IPv6 forms, decimal-encoded IPv4 and IPv4-mapped IPv6, all of
which are standard SSRF bypasses.
"""
from __future__ import annotations

import gzip
import io
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 1_500_000        # stop reading past this
MAX_REDIRECTS = 4
TIMEOUT = 10
UA = 'Mozilla/5.0 (compatible; TMHClassFinder/1.0; +https://www.thetrademarkhelpline.com)'


class FetchError(RuntimeError):
    """Raised with a message safe to show a visitor."""


def _ip_ok(host: str) -> bool:
    """True only if every address this host resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise FetchError("We couldn't find that website address.") from None
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


def _check(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ('http', 'https'):
        raise FetchError('Only http and https addresses can be read.')
    if not p.hostname:
        raise FetchError("That doesn't look like a website address.")
    if not _ip_ok(p.hostname):
        raise FetchError("We can't read that address.")
    return url


def fetch_text(url: str) -> tuple[str, str]:
    """Fetch a page and return (visible_text, final_url).

    Redirects are followed by hand so each hop is re-checked — urllib would
    happily follow a public URL to an internal one for us.
    """
    url = (url or '').strip()
    if not url:
        raise FetchError('Please give a website address.')
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url

    seen = 0
    while True:
        _check(url)
        req = urllib.request.Request(url, headers={
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Encoding': 'gzip',
            'Accept-Language': 'en-GB,en;q=0.9',
        })
        try:
            # No automatic redirects — we vet each hop ourselves.
            opener = urllib.request.build_opener(_NoRedirect)
            resp = opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get('Location')
                seen += 1
                if not loc or seen > MAX_REDIRECTS:
                    raise FetchError("We couldn't read that website.") from None
                url = urllib.parse.urljoin(url, loc)
                continue
            raise FetchError(f'That website returned an error ({e.code}).') from None
        except Exception:                                   # noqa: BLE001
            raise FetchError("We couldn't reach that website.") from None

        ctype = (resp.headers.get('Content-Type') or '').lower()
        if 'html' not in ctype and 'text' not in ctype:
            raise FetchError('That address is not a web page.')

        raw = resp.read(MAX_BYTES)
        if (resp.headers.get('Content-Encoding') or '').lower() == 'gzip':
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_BYTES)
            except OSError:
                pass
        charset = 'utf-8'
        m = re.search(r'charset=([\w-]+)', ctype)
        if m:
            charset = m.group(1)
        html = raw.decode(charset, errors='replace')
        return visible_text(html), resp.geturl() or url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):   # noqa: D102 - surfaces as HTTPError
        return None


_DROP = re.compile(r'<(script|style|noscript|svg|iframe|template)\b.*?</\1>',
                   re.S | re.I)
_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'[ \t\r\f\v]+')
_NL = re.compile(r'\n{3,}')


def visible_text(html: str, *, limit: int = 6000) -> str:
    """Strip HTML to the words a human would read.

    Deliberately crude — no parser dependency, and the agent only needs the
    gist. Title and meta description come first because on a thin page they
    are often the only real statement of what the business does.
    """
    head = []
    t = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
    if t:
        head.append(_TAG.sub(' ', t.group(1)).strip())
    d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
                  html, re.I)
    if d:
        head.append(d.group(1).strip())

    body = _DROP.sub(' ', html)
    body = re.sub(r'<(br|/p|/div|/li|/h\d|/tr)\s*/?>', '\n', body, flags=re.I)
    body = _TAG.sub(' ', body)
    body = (body.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&quot;', '"').replace('&#39;', "'")
                .replace('&lt;', '<').replace('&gt;', '>'))
    body = _WS.sub(' ', body)
    body = '\n'.join(ln.strip() for ln in body.split('\n') if ln.strip())
    body = _NL.sub('\n\n', body)

    out = '\n'.join([x for x in head if x] + [body]).strip()
    return out[:limit]


def looks_thin(text: str) -> bool:
    """True when there isn't enough here to classify from.

    A JavaScript-rendered site returns a shell to a plain fetch, and a landing
    page may be three words and a form. Either way, guessing from it is worse
    than admitting it and asking the visitor to describe the business — which
    is exactly what the route 2 copy now tells them.
    """
    words = re.findall(r'[a-zA-Z]{3,}', text or '')
    return len(words) < 60
