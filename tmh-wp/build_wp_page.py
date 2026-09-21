#!/usr/bin/env python3
"""Turn the served mockup into a WordPress-ready content block.

ONE SOURCE. `freesearch/web/classes-page.html` is the page; this script is the
build step that makes it safe to paste into WordPress. Editing the mockup and
re-running this is the whole workflow — the two can never drift, because one is
generated from the other.

Three things have to change on the way in, and all three are the kind of thing
that is quietly wrong for weeks if done by hand:

1. CSS SCOPE. The mockup owns its whole document, so it styles `h2`, `p`,
   `.card`, `.btn`. Dropped into a theme those selectors hit the header, the
   footer and every other page element that shares a class name. Every selector
   is therefore prefixed with `#tmh-gs-page`, and `:root` becomes that id so the
   custom properties still resolve.

2. ASSET URLS. Root-relative paths (`/brand/...`) resolve against
   thetrademarkhelpline.com once the markup is on WordPress, where those files
   do not exist. They are rewritten absolute to the widget host.

3. THE FINDER. The mockup hand-frames it in an iframe because it is same-origin
   there. On WordPress it becomes the ordinary embed tag, which is exactly how
   /class-builder/ and /free-search/ already carry their widgets (their page
   content is one script tag and nothing else). embed.js handles sizing, so the
   hand-rolled height listener goes too.

    python3 build_wp_page.py            # writes classes-page-wp.html
    python3 build_wp_page.py --check    # verify only, write nothing
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / 'freesearch' / 'web' / 'classes-page.html'
OUT = HERE / 'classes-page-wp.html'

SCOPE = '#tmh-gs-page'
WIDGET_HOST = 'https://braudit-free-search.onrender.com'
SITE = 'https://www.thetrademarkhelpline.com'

# At-rules whose bodies hold selectors that still need scoping, vs ones whose
# contents are not selectors at all and must be left alone.
_NESTED_AT = ('@media', '@supports')
_OPAQUE_AT = ('@keyframes', '@font-face', '@import', '@charset')


def scope_css(css: str, scope: str = SCOPE) -> str:
    """Prefix every selector so nothing leaks into the theme."""
    out, i = [], 0
    while i < len(css):
        brace = css.find('{', i)
        if brace == -1:
            out.append(css[i:]); break
        # A comment BEFORE the next rule passes through untouched, along with
        # the whitespace in front of it. Checking only at the cursor missed any
        # comment preceded by a newline, and the comment body then went through
        # the selector splitter — which turned the file header into
        # "#tmh-gs-page /* MOCKUP of ..." split on its commas.
        com = css.find('/*', i)
        if com != -1 and com < brace:
            end = css.find('*/', com + 2)
            end = len(css) if end == -1 else end + 2
            out.append(css[i:end]); i = end; continue
        head = css[i:brace]
        stripped = head.strip()

        if stripped.startswith(_OPAQUE_AT):
            depth, j = 0, brace
            while j < len(css):
                if css[j] == '{': depth += 1
                elif css[j] == '}':
                    depth -= 1
                    if depth == 0: j += 1; break
                j += 1
            out.append(css[i:j]); i = j; continue

        if stripped.startswith(_NESTED_AT):
            depth, j = 0, brace
            while j < len(css):
                if css[j] == '{': depth += 1
                elif css[j] == '}':
                    depth -= 1
                    if depth == 0: break
                j += 1
            inner = css[brace + 1:j]
            out.append(head + '{' + scope_css(inner, scope) + '}')
            i = j + 1; continue

        close = css.find('}', brace)
        close = len(css) if close == -1 else close
        body = css[brace + 1:close]
        out.append(_scope_selector_list(head, scope) + '{' + body + '}')
        i = close + 1
    return ''.join(out)


def _scope_selector_list(head: str, scope: str) -> str:
    lead = head[:len(head) - len(head.lstrip())]
    parts = [p.strip() for p in head.strip().split(',') if p.strip()]
    scoped = []
    for p in parts:
        if p == ':root':
            scoped.append(scope)                      # variables live on the wrapper
        elif p in ('body', 'html'):
            scoped.append(scope)                      # page-level rules land on it too
        elif p.startswith(scope):
            scoped.append(p)                          # already scoped, leave alone
        else:
            scoped.append(f'{scope} {p}')
    return lead + ', '.join(scoped)


FINDER_EMBED = (
    '  <!-- The live Keyword Class Finder, embedded the same way every other TMH\n'
    '       widget is: one script tag. embed.js frames it and sizes it.\n'
    '       The wrapper carries the 26px gap under the header buttons that the\n'
    '       served page puts on the iframe itself (Jonathan, 21 Sep). Without\n'
    '       it the WP page would silently lose that spacing, because this\n'
    '       substitution throws the iframe and its inline style away. A\n'
    '       transform that REPLACES an element must carry its layout over. -->\n'
    '  <div style="margin-top:26px">\n'
    f'    <script src="{WIDGET_HOST}/embed.js" data-widget="class-finder" '
    'data-tenant="tmh" async></script>\n'
    '  </div>'
)


def build() -> str:
    html = SRC.read_text(encoding='utf-8')

    # EVERY style block, not just the first. This used to be re.search, so with
    # the v2 page's two blocks only the @font-face one was processed and the
    # second — body{...} a{...} a:hover{...} — went out UNSCOPED and would have
    # restyled the whole theme. findall, always.
    blocks = re.findall(r'<style>(.*?)</style>', html, re.S)
    body = re.search(r'<body>(.*?)</body>', html, re.S)
    if not blocks or not body:
        raise SystemExit('classes-page.html no longer has a <style> and a <body>')

    css = '\n'.join(scope_css(b) for b in blocks)
    doc = body.group(1)
    # any style block that lived in the body is now in css; do not ship it twice
    doc = re.sub(r'<style>.*?</style>', '', doc, flags=re.S)

    # Fonts come from Google, which is a cross-origin stylesheet link and so
    # cannot travel inside post content. WordPress needs the @import form.
    if 'fonts.googleapis.com' in html and 'fonts.googleapis.com' not in css:
        css = ("@import url('https://fonts.googleapis.com/css2?"
               "family=Public+Sans:wght@400;600;700;800&display=swap');\n") + css

    # THE ONE DELIBERATE EXCEPTION TO "EVERYTHING IS SCOPED" (Jonathan, 21 Sep).
    #
    # The theme is Hello Elementor, and its DEFAULT page template prints the
    # WordPress page title as `.page-header > h1.entry-title` ABOVE the
    # content. The design carries its own eyebrow and h1, so the page rendered
    # with two titles stacked -- "Goods and Services Classes" sitting over
    # "The part of your trademark most people get wrong". The page is not
    # built in Elementor, so it has no per-page "hide title" toggle.
    #
    # This rule has to reach OUTSIDE #tmh-gs-page, which nothing else here is
    # allowed to do. It is made safe by `:has()`: it hides the theme header
    # ONLY on a page that contains this block, so it cannot affect any other
    # page on the site, and it needs no page id (an id would break the day the
    # page is recreated). Two H1s also means the wrong one ranks; after this
    # the design's h1 is the page's only h1.
    css += (
        "\n/* Hide the Hello Elementor page title on pages carrying this block.\n"
        "   :has() keeps it to THIS page -- see build_wp_page.py for why. */\n"
        ".site-main:has(#tmh-gs-page) > .page-header,\n"
        "body:has(#tmh-gs-page) .site-main > .page-header{display:none}\n"
        # The Elementor kit sets .site-main{max-width:1140px}. The design is
        # built to 1236 -- 1180 of content plus its own 28px of padding each
        # side (content-box, so the padding sits OUTSIDE the max-width). On a
        # 1140 container the hero lost 96px, which squeezed the left text
        # column from 570 to 522 and read as the title sitting too far left.
        # Nothing was off-centre: both halves measured symmetric throughout.
        # Widening to 1236 restores the designed proportions exactly. The
        # block carries its own max-width and margin:0 auto, so it can never
        # exceed the design width however wide the container gets.
        "body:has(#tmh-gs-page) .site-main{max-width:1236px;"
        "margin-left:auto;margin-right:auto}\n"
    )

    # 3. the finder: iframe -> embed tag, and drop the listener that sized it
    # Match on the src, not on a class/id the mockup builder is free to rename.
    # It WAS `<iframe class="finder"`, the v2 build emits `id="finder"`, and the
    # substitution silently did nothing — shipping a root-relative
    # /class-finder iframe that would 404 on the WP domain.
    doc, n = re.subn(r'<iframe[^>]*src="/class-finder[^"]*"[^>]*>\s*</iframe>',
                     FINDER_EMBED, doc, flags=re.S)
    if n != 1:
        raise SystemExit(f'expected 1 finder iframe to convert, converted {n}')
    doc = re.sub(r'/\* The finder is the real widget.*?\n\}\);\n', '', doc, flags=re.S)

    # 2. assets absolute to the widget host; tool links to the WP pages
    doc = doc.replace('src="/brand/', f'src="{WIDGET_HOST}/brand/')
    doc = doc.replace("'/brand/", f"'{WIDGET_HOST}/brand/")
    doc = doc.replace('href="/class-builder"', f'href="{SITE}/class-builder/"')
    doc = doc.replace("const CB='/class-builder/'", f"const CB='{SITE}/class-builder/'")

    for leftover in ('src="/brand/', 'href="/class-', 'src="/class-'):
        if leftover in doc:
            raise SystemExit(f'root-relative URL left in the output: {leftover}')

    return (
        '<!-- Goods and services classes. GENERATED by tmh-wp/build_wp_page.py\n'
        '     from freesearch/web/classes-page.html — edit that, re-run the build,\n'
        '     re-publish. Everything is scoped to #tmh-gs-page so none of it\n'
        '     touches the theme. -->\n'
        f'<div id="tmh-gs-page">\n<style>\n{css}\n</style>\n{doc}\n</div>\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='verify only, write nothing')
    a = ap.parse_args()
    out = build()

    # Scan the CSS ONLY. Scanning the whole output swept the page's JavaScript
    # too, where `return {` and `if(x) {` look exactly like a bare element rule
    # — which produced a WARNING about a selector called "return".
    css_only = '\n'.join(re.findall(r'<style>(.*?)</style>', out, re.S))
    bare = re.findall(r'(?m)^\s*([a-z][a-z0-9]*(?:\s*,\s*[a-z][a-z0-9]*)*)\s*\{',
                      css_only)
    unscoped = [b for b in bare if b not in ('from', 'to')]
    print(f'source  {SRC.relative_to(HERE.parent)}  {len(SRC.read_text()):,} bytes')
    print(f'output  {len(out):,} bytes, {out.count(SCOPE):,} scoped selectors')
    print(f'checks  unscoped element rules: {len(unscoped)}'
          f' | embed tags: {out.count("embed.js")}'
          f' | absolute assets: {out.count(WIDGET_HOST + "/brand/")}')
    if unscoped:
        print('  WARNING, these would leak into the theme:', unscoped[:6])
        return 1
    if not a.check:
        OUT.write_text(out, encoding='utf-8')
        print(f'wrote   {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
