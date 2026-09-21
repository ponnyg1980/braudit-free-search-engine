#!/usr/bin/env python3
"""Create the classes page on thetrademarkhelpline.com as a DRAFT.

WHY A SCRIPT AND NOT THE ADMIN UI. Every revision of this page is a re-run
rather than a re-paste: edit `freesearch/web/classes-page.html`, run
`build_wp_page.py`, run this. The page on WordPress is therefore always a
build of the mockup, and the two cannot drift.

IT ONLY EVER WRITES DRAFTS. `status` is hard-coded and there is no flag to
change it. Publishing is a decision, not a deployment step, so it stays a
human click in WP admin. Re-running on an already-published page updates the
content and leaves the published status alone — it will not silently unpublish
live copy, and it will not silently push a draft live.

CREDENTIALS. A WordPress application password, in `temmy-access/secrets.env`:

    WP_USER=jonathan            # the WP username, not the email
    WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx

Make one at Users -> Profile -> Application Passwords in WP admin. It is
scoped to the REST API, it can be revoked on its own, and it is not the
account password. Nothing here prints it.

    python3 publish_wp_page.py --check           # auth + plan, writes nothing
    python3 publish_wp_page.py                   # create/update the page draft
    python3 publish_wp_page.py --tools           # ...and the four tool pages
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRETS = HERE.parent / 'temmy-access' / 'secrets.env'
BODY = HERE / 'classes-page-wp.html'
API = 'https://www.thetrademarkhelpline.com/wp-json/wp/v2'
WIDGET_HOST = 'https://braudit-free-search.onrender.com'

PAGE = {
    'slug': 'trademark-goods-and-services-classifications',
    'title': 'Goods and Services Classes',
}

# The four class tools, each its own page. Content is one embed tag, which is
# exactly what /class-builder/ and /free-search/ already carry (their whole
# post_content is 109 characters). Slugs sit under class-builder so the URL
# reads the way the card that points at it does.
TOOLS = [
    ('class-builder-existing-trademark', 'Class Builder &mdash; Use an existing trademark',
     'class-builder/existing-trademark'),
    ('class-builder-my-company', 'Class Builder &mdash; Your company',
     'class-builder/my-company'),
    ('class-builder-describe-business', 'Class Builder &mdash; Answer a few questions',
     'class-builder/describe-business'),
    ('class-builder-my-website', 'Class Builder &mdash; Let us read your website',
     'class-builder/my-website'),
]


# Where a WP credential may already live. The estate has two publishers
# already and they disagree about this, so look in both rather than making a
# third convention: BAILII/fcl_ingest reads the environment (GO_LIVE.md step 2),
# and IPO Database Injection reads its own .env. Order is most-specific first.
CRED_FILES = (
    SECRETS,                                            # Braudit convention
    HERE.parent.parent / 'IPO Database Injection' / '.env',
    HERE.parent.parent / 'BAILII' / 'fcl_ingest' / '.env',
)


# The same credential has been written under three names and two separators
# across this estate, so read all of them rather than making someone match a
# convention they cannot see. `WORDPRESS_USER_NAME:jon_TMH` (colon, 18 Sep)
# was invisible to every loader here, which all split on '='.
_ALIAS = {
    'WP_USER': 'WP_USER', 'WORDPRESS_USER_NAME': 'WP_USER',
    'WORDPRESS_USER': 'WP_USER', 'WP_USERNAME': 'WP_USER',
    'WP_APP_PASSWORD': 'WP_APP_PASSWORD',
    'WORDPRESS_APP_PASSWORD': 'WP_APP_PASSWORD',
    'WORDPRESS_PASSWORD': 'WP_APP_PASSWORD', 'WP_PASSWORD': 'WP_APP_PASSWORD',
    'WP_URL': 'WP_URL', 'WORDPRESS_URL': 'WP_URL',
}
_LINE = re.compile(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)')


def _from_file(p: Path) -> dict:
    if not p.exists():
        return {}
    vals = {}
    for line in p.read_text(encoding='utf-8').splitlines():
        if line.lstrip().startswith('#'):
            continue
        m = _LINE.match(line)
        if m and m.group(1) in _ALIAS:
            vals[_ALIAS[m.group(1)]] = m.group(2).strip().strip('"').strip("'")
    return vals


def looks_like_app_password(pw: str) -> bool:
    """A WP application password is 24 characters in six space-separated
    groups of four. The REST API's Basic auth accepts ONLY that; the account
    password is refused by design and returns a bare 401 rest_not_logged_in,
    which reads like a typo rather than the wrong KIND of credential."""
    return len(pw.replace(' ', '')) == 24 and len(pw.split()) in (1, 6)


def creds() -> tuple[str, str, str]:
    """(user, app password, where it came from). Never prints the password."""
    import os
    if os.environ.get('WP_USER') and os.environ.get('WP_APP_PASSWORD'):
        return os.environ['WP_USER'], os.environ['WP_APP_PASSWORD'], 'environment'
    for p in CRED_FILES:
        v = _from_file(p)
        if v.get('WP_USER') and v.get('WP_APP_PASSWORD'):
            # A credential is per SITE. The one in IPO Database Injection is for
            # claimmyloss.co.uk and returns 401 here, so say which site a file
            # names rather than letting the caller puzzle over the 401.
            site = v.get('WP_URL', '')
            if site and 'thetrademarkhelpline' not in site:
                print(f'  note: {p.name} carries a credential for {site} — '
                      'application passwords are per site, so it will not '
                      'authenticate here. Looking further.')
                continue
            return v['WP_USER'], v['WP_APP_PASSWORD'], str(p)
    raise SystemExit(
        'No WordPress credential for thetrademarkhelpline.com found.\n'
        '  Looked in: $WP_USER/$WP_APP_PASSWORD, then '
        + ', '.join(str(p) for p in CRED_FILES) + '\n'
        '  The credential in "IPO Database Injection/.env" is for '
        'claimmyloss.co.uk and cannot work here.\n'
        '  Make one at Users -> Profile -> Application Passwords in TMH WP admin '
        'and add WP_USER / WP_APP_PASSWORD to temmy-access/secrets.env. '
        'Do not paste it into chat.')


def call(method: str, path: str, auth: str, payload: dict | None = None):
    req = urllib.request.Request(
        API + path, method=method,
        data=(json.dumps(payload).encode() if payload is not None else None),
        headers={'Authorization': 'Basic ' + auth,
                 'Content-Type': 'application/json',
                 'Accept': 'application/json',
                 'User-Agent': 'TMH-page-publisher'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')[:400]
        raise SystemExit(f'WordPress {e.code} on {method} {path}: {detail}')


def find_by_slug(slug: str, auth: str) -> dict | None:
    for status in ('draft,pending,private,publish', 'any'):
        rows = call('GET', f'/pages?slug={slug}&status={status}&per_page=5', auth)
        if isinstance(rows, list) and rows:
            return rows[0]
    return None


def as_html_block(content: str) -> str:
    """Wrap the markup in a Gutenberg raw-HTML block.

    WITHOUT THIS THE PAGE IS MANGLED (Jonathan, 21 Sep 2026). Content stored
    with no block delimiters is treated as classic content, so WordPress runs
    `wpautop` over it on output: it inserts <p> and <br> through the markup and
    breaks the inline <script> blocks. On this page those scripts build the
    hero grid, the four route cards, the band legend, the FAQ rows and both
    search-widget mounts -- i.e. everything that looked "missing" while the
    same file rendered perfectly on the Render host, which applies no filters.

    A wp:html block is output verbatim, which is the whole point of it.
    """
    c = content.lstrip()
    if c.startswith('<!-- wp:'):
        return content                       # already blocked, leave alone
    return '<!-- wp:html -->\n' + content.rstrip() + '\n<!-- /wp:html -->\n'


def upsert(slug: str, title: str, content: str, auth: str, dry: bool) -> dict:
    existing = find_by_slug(slug, auth)
    content = as_html_block(content)
    body = {'title': title, 'content': content, 'slug': slug}
    if existing:
        # Keep whatever status it already has. Never publish, never unpublish.
        verb, path = 'update', f"/pages/{existing['id']}"
        note = f"id={existing['id']} status={existing['status']}"
    else:
        body['status'] = 'draft'          # the only status this script ever sets
        verb, path = 'create', '/pages'
        note = 'new draft'
    print(f'  {verb:6} /{slug}  ({note}, {len(content):,} bytes)')
    if dry:
        return {'id': existing['id'] if existing else None, 'link': '(dry run)',
                'status': existing['status'] if existing else 'draft'}
    return call('POST', path, auth, body)


def preview(page: dict) -> str:
    if page.get('status') == 'publish':
        return page.get('link', '')
    return (f"https://www.thetrademarkhelpline.com/?page_id={page['id']}&preview=true"
            if page.get('id') else '(dry run)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='auth and plan only, write nothing')
    ap.add_argument('--tools', action='store_true', help='also create the four tool pages')
    a = ap.parse_args()

    user, app, source = creds()
    print(f'credential from {source} (user {user})')
    if not looks_like_app_password(app):
        raise SystemExit(
            f'That is {len(app)} characters with no spaces, so it looks like the ACCOUNT\n'
            '  password rather than an application password. The WordPress REST API\n'
            '  refuses the account password by design — it only accepts an application\n'
            '  password, which is 24 characters in six groups of four, like\n'
            '      abcd EFGH 1234 ijkl MNOP 5678\n'
            '  Make one at WP admin -> Users -> Profile -> Application Passwords, name it\n'
            '  "Claude page publisher", and put THAT in secrets.env. It only works over\n'
            '  the REST API and you can revoke it on its own without changing your login.')
    auth = base64.b64encode(f'{user}:{app}'.encode()).decode()

    me = call('GET', '/users/me?context=edit', auth)
    caps = me.get('capabilities') or {}
    print(f"signed in as {me.get('name')} (id {me.get('id')})"
          f" | edit_pages={bool(caps.get('edit_pages'))}"
          f" publish_pages={bool(caps.get('publish_pages'))}")
    if not caps.get('edit_pages'):
        raise SystemExit('this user cannot edit pages — use an admin or editor account')

    if not BODY.exists():
        raise SystemExit(f'{BODY.name} not found — run build_wp_page.py first')
    content = BODY.read_text(encoding='utf-8')

    print('\npages:')
    out = [upsert(PAGE['slug'], PAGE['title'], content, auth, a.check)]
    if a.tools:
        for slug, title, widget in TOOLS:
            one = (f'<script src="{WIDGET_HOST}/embed.js" '
                   f'data-widget="{widget}" data-tenant="tmh" async></script>')
            out.append(upsert(slug, title, one, auth, a.check))

    print('\npreview links:')
    for p in out:
        print('  ', preview(p))
    if a.check:
        print('\n--check: nothing was written.')
    else:
        print('\nAll drafts. Publish from WP admin when you are happy with them.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
