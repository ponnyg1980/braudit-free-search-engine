#!/usr/bin/env python3
"""Turn the designer's .dc.html into the served mockup.

THE DESIGN IS THE SOURCE. `Goods and Services Classes v2.dc.html` is what the
designer edits; this script converts it into `freesearch/web/classes-page.html`,
which the engine serves and which `build_wp_page.py` then turns into the
WordPress block. Three files, one direction of travel, so a design revision is:

    python3 build_mockup.py && python3 build_wp_page.py && python3 publish_wp_page.py

Doing this by hand once was fine. Doing it every revision is how the served page
and the design drift apart, which is exactly what happened between v1 and v2 —
the widgets, the contact block and three Temmy characters never made it across.

WHAT IT HAS TO DO. A .dc.html is static HTML with `{{ slot }}` holes that a
DCLogic class fills at runtime. The browser cannot run that, so each slot gets
handled here:

  heroGrid, routeCards, bandLegend, faqRows   -> an empty container, filled by
      the runtime block appended at the end FROM THE DESIGN'S OWN DATA. The
      copy therefore still comes from the .dc.html and cannot drift.
  classRows, detail, and the sample search UI -> deleted and replaced with the
      LIVE /class-finder widget. That block is sample data in the design and
      the whole point of serving this page is that it is real.
  searchWidget, closingSearch                 -> the two Braudit search boxes.
  heroFit                                     -> a ref for a ResizeObserver we
      do not need; the container is kept, the attribute dropped.

    python3 build_mockup.py            # writes classes-page.html
    python3 build_mockup.py --check    # verify only, write nothing
"""
from __future__ import annotations

import argparse
import html as _html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESIGN = (HERE.parent.parent / 'Downloads' / 'Website design advice (13)'
          / 'Goods and Services Classes v2.dc.html')
OUT = HERE.parent / 'freesearch' / 'web' / 'classes-page.html'

WIDGET_HOST = 'https://braudit-free-search.onrender.com'
BOOKING = 'https://bookings.thetrademarkhelpline.com/#/webinitialcall'
STALE_BOOKING = 'https://link.cerebrumai.io/widget/booking/ETUyDA6n6q2ppSYZGVQD'


def design_path() -> Path:
    """The design folder is outside the repo and moves; look in the obvious
    places rather than hard-failing on one."""
    # The design folder is a sibling of the repo, not inside it, and the
    # connected-folder root differs between machines. Search rather than
    # hard-code: the design has moved twice already.
    roots = [DESIGN,
             HERE.parent.parent / 'Website design advice (13)' / DESIGN.name,
             HERE.parent / DESIGN.name]
    for p in roots:
        if p.exists():
            return p
    for base in (HERE.parent.parent, HERE.parent.parent.parent):
        try:
            hit = sorted(base.glob(f'*/{DESIGN.name}')) or sorted(base.glob(DESIGN.name))
        except OSError:
            continue
        if hit:
            return hit[0]
    raise SystemExit(f'design not found; looked for {DESIGN.name} near {HERE.parent}')


def extract(src: str, name: str) -> str:
    """One `const NAME = ...;` block from the design's script, verbatim."""
    m = re.search(rf'(const {name}\s*=\s*.*?;)\n(?=const |\n|class )', src, re.S)
    if not m:
        raise SystemExit(f'could not find `const {name}` in the design')
    return m.group(1)


# The live finder, in place of the design's sample browser. Rendered as a
# same-origin iframe rather than embed.js because this page IS the widget host;
# ?embed=1 tells the finder a host page owns the heading.
FINDER = f'''
      <!-- LIVE class finder, replacing the design's sample browser. -->
      <iframe id="finder" src="/class-finder?embed=1" title="Find your trademark class"
              style="width:100%;border:0;display:block;height:1180px"></iframe>
      <div style="border-top:1px solid #E6E9ED;background:#F7F8FA;padding:14px 34px;
           font-size:13px;line-height:1.6;color:#617383">
        Counts here are of every mark registered in that class, from 683,327 UK
        registrations in the last five years. The figures in the panel below count
        something different and narrower &mdash; marks filed by businesses in your
        sector &mdash; which is why the two do not match. Both are counts.
      </div>'''


def build() -> str:
    src = design_path().read_text(encoding='utf-8')

    body = re.search(r'<x-dc>(.*)</x-dc>', src, re.S)
    if not body:
        raise SystemExit('no <x-dc> body in the design')
    doc = body.group(1)

    helmet = re.search(r'<helmet>(.*?)</helmet>', doc, re.S)
    head_extra = helmet.group(1).strip() if helmet else ''
    doc = re.sub(r'<helmet>.*?</helmet>', '', doc, flags=re.S)

    # The design's @font-face rules point at BUNDLER UUIDs
    # (src:url("b6eb5237-...")), which are ids inside the Design Component
    # export, not files. They 404 on the widget host AND on WordPress, so the
    # page silently rendered Public Sans in a fallback face. Swap the whole
    # block for the real Google Fonts stylesheet, which is where the brand
    # spec says Public Sans comes from.
    if re.search(r'url\("[0-9a-f]{8}-[0-9a-f]{4}-', head_extra):
        head_extra = re.sub(r'(?:/\*[^*]*\*/\s*)?@font-face\s*\{[^}]*\}\s*', '',
                            head_extra)
        head_extra = re.sub(r'<style>\s*</style>', '', head_extra).strip()
        head_extra = FONT_LINK + ('\n' + head_extra if head_extra else '')
    if re.search(r'url\("[0-9a-f]{8}-', head_extra):
        raise SystemExit('a bundler-UUID font url survived the swap')

    # 1. The sample browser: from the search input through the footer bar.
    #    Anchored on the two pane slots so a copy change above cannot shift it.
    start = doc.find('<input type="text" placeholder="{{ searchPlaceholder }}"')
    end = doc.find('{{ detail }}')
    if start == -1 or end == -1:
        raise SystemExit('could not locate the sample browser to replace')
    end = doc.find('</div>', doc.find('</div>', end) + 6) + 6      # close both panes
    doc = doc[:start] + FINDER + doc[end:]
    doc = re.sub(r'<p[^>]*>Sample data for design review\..*?</p>', '', doc, flags=re.S)

    # 2. Slots that the runtime block fills from the design's own data.
    for slot, el in (('heroGrid', '<div id="heroGrid" style="display:contents"></div>'),
                     ('routeCards', '<div id="routes" style="display:contents"></div>'),
                     ('bandLegend', '<div id="bands" style="display:contents"></div>'),
                     ('faqRows', '<div id="faq" style="display:contents"></div>')):
        doc = doc.replace('{{ %s }}' % slot, el)

    # 3. The two Braudit search boxes, and the ref we do not need.
    doc = re.sub(r'<div ref="\{\{ searchWidget \}\}"([^>]*)></div>',
                 r'<div id="w-quick"\1></div>', doc)
    doc = re.sub(r'<div ref="\{\{ closingSearch \}\}"([^>]*)></div>',
                 r'<div id="w-free"\1></div>', doc)
    doc = doc.replace(' ref="{{ heroFit }}"', '')

    # 4. Design-tool attributes the browser has no use for.
    doc = re.sub(r'\s(?:style-hover|style-focus|data-comment-anchor|sc-camel-on-\w+|hint-placeholder-count)="[^"]*"', '', doc)
    doc = re.sub(r'</?sc-for[^>]*>', '', doc)

    # 5. Images. The design carries bundler UUIDs (`src="37efd46c-…"`), which
    #    resolve only inside the bundle. Map them to the real asset paths on the
    #    widget host. The map was built by decoding the bundle's manifest and
    #    matching each image against the supplied asset files by content and
    #    dimensions, not by guessing from the order they appear.
    amap = design_path().parent / 'classes-page-asset-map.json'
    if amap.exists():
        import json
        for uid, rel in json.loads(amap.read_text()).items():
            doc = doc.replace(f'src="{uid}"', f'src="{WIDGET_HOST}/{rel}"')
    stray = re.findall(r'src="([0-9a-f]{8}-[0-9a-f-]{27,})"', doc)
    if stray:
        raise SystemExit(
            'unmapped bundler asset ids: ' + ', '.join(sorted(set(stray))[:4]) +
            f'\n  add them to {amap.name} — decode the bundle manifest and match '
            'by sha256 or by pixel dimensions, never by position.')

    # 6. The stale booking link the handover README flagged.
    doc = doc.replace(STALE_BOOKING, BOOKING)

    left = re.findall(r'\{\{[^}]+\}\}', doc)
    if left:
        raise SystemExit(f'unhandled template slots remain: {sorted(set(left))[:6]}')

    data = '\n'.join(extract(src, n) for n in ('CLASSES', 'BANDS', 'ROUTES', 'FAQS'))
    return PAGE.format(head_extra=head_extra, body=doc.strip(), data=data,
                       host=WIDGET_HOST)


FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Public+Sans:wght@400;600;700;800&display=swap">'
)

PAGE = '''<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goods and services classes &mdash; The Trademark Helpline</title>
<meta name="description" content="Your trademark protects your name only for the goods and services you claimed. Four free ways to work out your classes and terms, using wording from real UK registrations.">
<!-- GENERATED by tmh-wp/build_mockup.py from "Goods and Services Classes v2.dc.html".
     Edit the DESIGN and re-run the build; do not edit this file. -->
{head_extra}
</head>
<body>
{body}

<script>
/* Copy and data lifted VERBATIM from the design, so the two cannot drift. */
{data}

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

/* Hero: the 45-class grid, a handful lit. */
const LIT = [5, 9, 25, 30, 35, 41, 42];
if($('heroGrid')) $('heroGrid').outerHTML = Array.from({{length:45}}, (_,i)=>i+1).map(n=>
  `<div style="height:34px;border-radius:8px;display:flex;align-items:center;
    justify-content:center;font-family:'Public Sans',sans-serif;font-size:12.5px;
    font-weight:700;${{LIT.includes(n)
      ? 'background:#E51652;border:1px solid #E51652;color:#fff'
      : 'background:#fff;border:1px solid #E6E9ED;color:#8b99a5'}}">${{n}}</div>`).join('');

/* The four tools. */
if($('routes')) $('routes').outerHTML = ROUTES.map(r=>`
  <div style="border:1px solid #E6E9ED;border-radius:16px;padding:26px 26px 28px;display:flex;
       flex-direction:column;transition:transform .18s ease,box-shadow .18s ease"
       onmouseover="this.style.transform='scale(1.035)';this.style.boxShadow='0 14px 34px rgba(45,69,90,.13)'"
       onmouseout="this.style.transform='';this.style.boxShadow=''">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px">
      <span style="font-family:'Public Sans',sans-serif;font-size:12px;font-weight:800;
        letter-spacing:1.2px;color:#E51652">${{r.n}}</span>
      <span style="font-family:'Public Sans',sans-serif;font-size:10.5px;font-weight:800;
        letter-spacing:1px;text-transform:uppercase;color:#fff;border-radius:99px;padding:5px 11px;
        background:${{r.n==='1' ? '#E51652' : '#2D455A'}}">${{esc(r.badge)}}</span>
      ${{r.ai ? '<img src="{host}/brand/temmy-ai-bot.svg" alt="" style="width:30px;height:30px;display:block">' : ''}}
    </div>
    <h3 style="font-size:20px;font-weight:800;letter-spacing:-.4px;margin:0 0 8px;line-height:1.28">${{esc(r.t)}}</h3>
    <p style="font-size:15px;line-height:1.5;color:#2D455A;font-weight:700;margin:0 0 12px">${{esc(r.w)}}</p>
    <p style="font-size:15.5px;line-height:1.6;color:#617383;margin:0">${{esc(r.d)}}</p>
  </div>`).join('');

/* Band legend, on navy. */
if($('bands')) $('bands').outerHTML = Object.values(BANDS).map(b=>`
  <div style="display:flex;align-items:baseline;gap:12px">
    <span style="font-family:'Public Sans',sans-serif;font-size:11px;font-weight:800;
      letter-spacing:.9px;text-transform:uppercase;color:#fff;opacity:${{b.op}};
      min-width:64px">${{esc(b.label)}}</span>
    <span style="font-size:14.5px;color:#c7d2dc;line-height:1.45">${{esc(b.hint)}}</span>
  </div>`).join('');

/* FAQs, one open at a time. */
if($('faq')){{
  $('faq').outerHTML = FAQS.map((f,i)=>`
    <button type="button" data-faq="${{i}}" style="width:100%;text-align:left;background:none;
      border:0;border-top:${{i ? '1px solid #E6E9ED' : '0'}};padding:20px 24px;font-family:inherit;
      font-size:17px;font-weight:700;color:#1D1D1B;cursor:pointer;display:flex;gap:16px;
      align-items:center"><span style="flex:1">${{esc(f.q)}}</span>
      <span id="pm-${{i}}" style="color:#E51652;font-size:22px;font-weight:800;line-height:1">+</span></button>
    <div id="a-${{i}}" style="display:none;padding:0 24px 20px">${{
      f.a.map(p=>`<p style="font-size:16.5px;line-height:1.68;color:#3f4c58;margin:0 0 12px">${{esc(p)}}</p>`).join('')}}</div>`).join('');
  document.addEventListener('click', e=>{{
    const b = e.target.closest('[data-faq]'); if(!b) return;
    const i = b.dataset.faq, a = $('a-'+i), open = a.style.display !== 'none';
    FAQS.forEach((_,j)=>{{ $('a-'+j).style.display='none'; $('pm-'+j).textContent='+'; }});
    if(!open){{ a.style.display=''; $('pm-'+i).textContent='\\u2212'; }}
  }});
}}

/* The two Braudit search boxes, mounted rather than written as static tags so
   they load once and survive a re-render (the design's own mountWidget rule). */
function mountWidget(el, attrs){{
  if(!el || el.dataset.loaded) return;
  el.dataset.loaded = '1';
  const s = document.createElement('script');
  s.src = '{host}/embed.js';
  s.async = true;
  Object.keys(attrs).forEach(k => s.setAttribute(k, attrs[k]));
  el.appendChild(s);
}}
mountWidget($('w-quick'), {{'data-widget':'search-box','data-variant':'quick','data-tenant':'tmh'}});
mountWidget($('w-free'),  {{'data-widget':'search-box','data-variant':'free','data-style':'bar','data-tenant':'tmh'}});

/* The finder reports its own height. */
window.addEventListener('message', e=>{{
  const h = e.data && e.data.brauditHeight;
  const f = $('finder');
  if(f && h && Number(h) > 300 && e.source === f.contentWindow) f.style.height = Number(h) + 'px';
}});
</script>
</body>
</html>
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--local', action='store_true',
                    help='also write a double-clickable copy next to the design')
    a = ap.parse_args()
    out = build()
    print(f'design  {design_path().name}')
    print(f'output  {len(out):,} bytes')
    print(f'checks  slots left: 0 | widgets: {out.count("mountWidget($(")}'
          f' | finder: {"/class-finder?embed=1" in out}'
          f' | stale booking: {out.count(STALE_BOOKING)}')
    if not a.check:
        OUT.write_text(out, encoding='utf-8')
        print(f'wrote   {OUT}')

    if a.local:
        # A copy that opens straight from Finder. The .dc.html design CANNOT
        # be opened this way -- its slots are unfilled ({{ heroGrid }} prints
        # as text) and its images are bundler ids, so it shows no Temmy and no
        # grid. This file is the same page with everything resolved.
        #
        # Exactly ONE url in the build is root-relative -- the finder iframe --
        # and on file:// that resolves to the disk root and silently shows an
        # empty pane. Everything else is already absolute to the widget host.
        local, n = re.subn(r'src="/class-finder\?embed=1"',
                           f'src="{WIDGET_HOST}/class-finder?embed=1"', out)
        if n != 1:
            raise SystemExit(f'expected 1 finder iframe to absolutise, got {n}')
        if re.search(r'(?:src|href)="/(?!/)', local):
            stray = re.findall(r'(?:src|href)="/(?!/)[^"]*', local)[:3]
            raise SystemExit(f'root-relative url would break on file://: {stray}')
        local = local.replace(
            '<body>',
            '<body>\n<!-- LOCAL PREVIEW. Generated by tmh-wp/build_mockup.py --local.\n'
            '     Open this file, not the .dc.html: the design carries unfilled\n'
            '     {{ slots }} and bundler-id images, so it cannot render alone.\n'
            '     Needs internet -- the images, the finder and the two search\n'
            '     widgets are served live from the widget host. -->', 1)
        dest = design_path().parent / 'Goods and Services Classes v2 - PREVIEW.html'
        dest.write_text(local, encoding='utf-8')
        print(f'local   {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
