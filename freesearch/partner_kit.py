"""Partner kit: one live page per introducer, generated from the ref code.

Jonathan, 3 Oct 2026: "when we set up a partner they get ... a page full of
client facing widgets they can use in their own site or within emails ... I
also want to create QR Codes to the Free Search Tool for every introducer so
they can hand it out to people saying 'Get a free Search Report'."
Rulings the same day: a LIVE page per partner (not files), the QR goes
STRAIGHT INTO THE FREE SEARCH, and comes with a printable card.

Routes (api.py):
  /partner-kit/<code>            the kit page
  /partner-kit/<code>/qr.svg     QR to the free search, vector
  /partner-kit/<code>/qr.png     QR to the free search, 1200 px
  /partner-kit/<code>/card.pdf   A6 "Get a free Search Report" card
  /partner-kit/<code>/card-a4.pdf  four A6 cards on A4, for home printing
  ?name=<Partner name> on the card adds "Recommended by <name>".

The code is the ONLY input and is normalised exactly as everywhere else
(trim, lowercase, a-z 0-9 '-', 20 chars). There is deliberately NO partner
lookup: the kit never shows or depends on the partner list (handoff §3.4).
Any code renders a kit; Zoho decides at lead time whether it is a live partner.
Every page is noindex.
"""
from __future__ import annotations

import html
import io
import os
import re

SITE = 'https://www.thetrademarkhelpline.com'
ENGINE = 'https://tools.thetrademarkhelpline.com'   # TMH address, 6 Oct 2026 (onrender still answers older pasted code)
BOOKINGS = 'https://bookings.thetrademarkhelpline.com/portal-embed#/general-enquiry'
_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

PINK = '#E51652'
NAVY = '#2D455A'


def norm(code: str) -> str:
    return re.sub(r'[^a-z0-9-]', '', str(code or '').strip().lower())[:20]


def search_url(code: str) -> str:
    """Where the QR and the card send people: straight into the free search."""
    return f'{SITE}/free-search/?ref={code}'


def links(code: str) -> list[tuple[str, str, str]]:
    """(label, url, what it is) — every link works in an email or a post."""
    return [
        ('Free trademark search', search_url(code),
         'Your clients search their brand name against the UK register and get a free Search Report.'),
        ('Quick UK search', f'{SITE}/uk-trademark-quick-search/?ref={code}',
         'The fastest version: name, classes, result.'),
        ('Clearance Audit', f'{SITE}/clearance-audit/?ref={code}',
         'Our paid audit and consultation, for clients ready to protect a brand.'),
        ('Find the right classes', f'{SITE}/classifications/?ref={code}',
         'Type what they sell and see which of the 45 classes it falls into.'),
        ('Make an enquiry', f'{SITE}/make-an-enquiry/?ref={code}',
         'A simple form; one of our team gets back to them.'),
        ('Book a free call', f'{BOOKINGS}?Partner%20Code={code}',
         'Fifteen minutes with a trademark specialist.'),
        ('Your page on our website', f'{SITE}/p/{code}',
         'A welcome page for your clients with all of the above.'),
    ]


# (title, what it does, data-widget, extra data-* attributes)
WIDGETS = [
    ('Search box', 'A compact box: your visitor types a name and continues on our free search, with their results emailed to them.',
     'search-box', {'variant': 'free'}),
    ('Search bar', 'The same search as a single slim bar, for a sidebar or footer.',
     'search-box', {'variant': 'free', 'style': 'bar'}),
    ('Full free search, on your page', 'The whole free search runs inside your page: name, classes, results and the emailed Search Report.',
     'free-search', {}),
    ('Quick UK search, on your page', 'A shorter search: name, classes, result.',
     'uk-trademark-quick-search', {}),
    ('Clearance Audit box', 'For clients ready to protect a brand: starts our paid Clearance Audit.',
     'search-box', {'variant': 'audit'}),
    ('Class finder', 'Type what you sell and see the classes and the wording businesses register.',
     'class-finder', {}),
]


def _tag(widget: str, extra: dict, code: str, demo: bool = False) -> str:
    attrs = [f'src="{ENGINE}/embed.js"', f'data-widget="{widget}"']
    attrs += [f'data-{k}="{v}"' for k, v in extra.items()]
    if demo:
        attrs += ['data-tenant="demo"', 'data-demo="1"']
    else:
        attrs += ['data-tenant="tmh"', f'data-ref="{code}"']
    return '<script ' + ' '.join(attrs) + ' async></script>'


def email_button(code: str) -> str:
    """Bulletproof email button: table + inline styles, no script, no image."""
    u = search_url(code)
    return ('<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td style="border-radius:8px;background:{PINK}">'
            f'<a href="{u}" target="_blank" style="display:inline-block;padding:12px 22px;'
            'font-family:Arial,sans-serif;font-size:16px;font-weight:bold;color:#ffffff;'
            'text-decoration:none;border-radius:8px">Get a free Search Report</a>'
            '</td></tr></table>')


# ------------------------------------------------------------------ QR ----

def _qr(code: str):
    import segno
    return segno.make(search_url(code), error='m')


def qr_svg(code: str) -> bytes:
    buf = io.BytesIO()
    _qr(code).save(buf, kind='svg', scale=10, border=4, dark=NAVY, xmldecl=True)
    return buf.getvalue()


def qr_png(code: str) -> bytes:
    buf = io.BytesIO()
    _qr(code).save(buf, kind='png', scale=30, border=4, dark=NAVY)
    return buf.getvalue()


# ---------------------------------------------------------------- card ----

def _draw_card(c, x, y, w, h, code: str, name: str):
    """One A6 card with its bottom-left corner at (x, y). Units: points."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.units import mm

    navy, pink = HexColor(NAVY), HexColor(PINK)
    slate = HexColor('#3f4c58')
    c.setFillColor(white)
    c.rect(x, y, w, h, stroke=0, fill=1)

    # Logo
    logo = os.path.join(_WEB, 'brand', 'logo.png')
    if os.path.exists(logo):
        lw = 48 * mm
        c.drawImage(logo, x + (w - lw) / 2, y + h - 8 * mm - lw * 448 / 1200,
                    width=lw, height=lw * 448 / 1200, mask='auto')
    top = y + h - 8 * mm - 48 * mm * 448 / 1200

    c.setFillColor(navy)
    c.setFont('Helvetica-Bold', 19)
    c.drawCentredString(x + w / 2, top - 11 * mm, 'Get a free Search Report')
    c.setFillColor(slate)
    c.setFont('Helvetica', 9.5)
    c.drawCentredString(x + w / 2, top - 17 * mm, 'Is your brand name clear to use?')
    c.drawCentredString(x + w / 2, top - 21.5 * mm, 'Check it against the UK trademark register.')

    # QR, drawn from the matrix so it stays vector in the PDF
    qr = _qr(code)
    mat = list(qr.matrix)
    n = len(mat)
    q = 50 * mm
    qx, qy = x + (w - q) / 2, top - 26 * mm - q
    cell = q / (n + 4)
    c.setFillColor(white)
    c.rect(qx, qy, q, q, stroke=0, fill=1)
    c.setFillColor(navy)
    for r, row in enumerate(mat):
        for col, v in enumerate(row):
            if v:
                c.rect(qx + (col + 2) * cell, qy + q - (r + 3) * cell, cell, cell, stroke=0, fill=1)

    c.setFillColor(pink)
    c.setFont('Helvetica-Bold', 11)
    c.drawCentredString(x + w / 2, qy - 6 * mm, 'Scan to start your free search')
    c.setFillColor(slate)
    c.setFont('Helvetica', 7.5)
    c.drawCentredString(x + w / 2, qy - 10.5 * mm, 'thetrademarkhelpline.com/free-search')
    if name:
        c.setFillColor(navy)
        c.setFont('Helvetica-Oblique', 9)
        c.drawCentredString(x + w / 2, qy - 16 * mm, f'Recommended by {name}')

    # Footer band
    c.setFillColor(navy)
    c.rect(x, y, w, 11 * mm, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 8)
    c.drawCentredString(x + w / 2, y + 6.4 * mm, 'The Trademark Helpline®   ·   0161 833 5400')
    c.setFont('Helvetica', 6.5)
    c.drawCentredString(x + w / 2, y + 2.8 * mm,
                        'Established Representative at the UK Intellectual Property Office since 2008')


def card_pdf(code: str, name: str = '', a4: bool = False) -> bytes:
    from reportlab.lib.pagesizes import A4, A6
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    name = re.sub(r'[\x00-\x1f<>]', '', str(name or ''))[:60].strip()
    buf = io.BytesIO()
    if a4:
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A6
        for ix in (0, 1):
            for iy in (0, 1):
                _draw_card(c, ix * w, iy * h, w, h, code, name)
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.setDash(2, 3)
        c.line(w, 0, w, 2 * h)
        c.line(0, h, 2 * w, h)
    else:
        c = canvas.Canvas(buf, pagesize=A6)
        _draw_card(c, 0, 0, *A6, code, name)
    c.setTitle('Get a free Search Report - The Trademark Helpline')
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- page ----

def page(code: str) -> str:
    e = html.escape
    base = f'/partner-kit/{code}'
    link_rows = ''.join(
        f'<tr><td><b>{e(lbl)}</b><div class="m">{e(why)}</div></td>'
        f'<td><input readonly value="{e(u)}" onclick="this.select()"></td>'
        f'<td><button type="button" class="cp" data-copy="{e(u)}">Copy</button></td></tr>'
        for lbl, u, why in links(code))
    wcards = ''
    for i, (title, what, widget, extra) in enumerate(WIDGETS, 1):
        live = _tag(widget, extra, code)
        wcards += (f'<section class="w"><h3>{i}. {e(title)}</h3><p>{e(what)}</p>'
                   f'<div class="code"><pre>{e(live)}</pre>'
                   f'<button type="button" class="cp" data-copy="{e(live)}">Copy code</button></div>'
                   f'<details><summary>Preview</summary><div class="pv">'
                   f'{_tag(widget, extra, code, demo=True)}</div>'
                   '<p class="m">Previews run in our sandbox, so nothing you type here reaches us.</p>'
                   '</details></section>')
    btn = email_button(code)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Your partner kit — The Trademark Helpline</title>
<style>
body{{margin:0;background:#F7F8FA;color:#1D1D1B;font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:22px}}
header img{{height:52px}}
h1{{font-size:32px;line-height:1.15;color:{NAVY};margin:0;letter-spacing:-.6px}}
h2{{font-size:22px;color:{NAVY};margin:38px 0 8px}}
h3{{font-size:18px;color:{NAVY};margin:0 0 4px}}
.eyebrow{{font-size:12.5px;font-weight:800;letter-spacing:1.4px;text-transform:uppercase;color:{PINK}}}
.card{{background:#fff;border:1px solid #E6E9ED;border-radius:14px;padding:20px;box-shadow:0 2px 8px rgba(45,69,90,.06)}}
.m{{color:#617383;font-size:14px}}
table.lk{{width:100%;border-collapse:collapse}} table.lk td{{padding:10px 8px;border-top:1px solid #E6E9ED;vertical-align:middle}}
table.lk tr:first-child td{{border-top:0}}
input{{width:100%;min-width:220px;font:13px ui-monospace,Menlo,monospace;padding:8px;border:1px solid #E6E9ED;border-radius:8px;background:#F7F8FA}}
.cp{{background:{PINK};color:#fff;border:0;border-radius:8px;padding:8px 14px;font-weight:700;cursor:pointer;white-space:nowrap}}
.cp:hover{{background:#c9134a}}
.qr{{display:grid;grid-template-columns:200px 1fr;gap:22px;align-items:center}}
.qr img{{width:200px;height:200px;border:1px solid #E6E9ED;border-radius:10px;background:#fff}}
.dl a{{display:inline-block;margin:4px 8px 4px 0;padding:9px 14px;border:1.5px solid {NAVY};border-radius:8px;color:{NAVY};font-weight:700;text-decoration:none}}
.dl a:hover{{background:{NAVY};color:#fff}}
.w{{background:#fff;border:1px solid #E6E9ED;border-radius:14px;padding:18px 20px;margin:14px 0}}
.code{{display:flex;gap:10px;align-items:flex-start}} pre{{flex:1;margin:0;white-space:pre-wrap;word-break:break-all;font:12.5px/1.5 ui-monospace,Menlo,monospace;background:#F7F8FA;border:1px solid #E6E9ED;border-radius:8px;padding:10px}}
details{{margin-top:10px}} summary{{cursor:pointer;color:{NAVY};font-weight:700}} .pv{{margin-top:10px}}
.note{{background:#FDE7EE;border-radius:12px;padding:14px 18px;margin-top:14px}}
@media(max-width:640px){{.qr{{grid-template-columns:1fr}} table.lk td{{display:block}} }}
</style></head><body><div class="wrap">
<header><img src="/brand/logo.png" alt="The Trademark Helpline"><div>
<div class="eyebrow">Partner kit</div><h1>Everything you need to refer clients</h1>
<div class="m">Your referral code: <b>{e(code)}</b>. It is already in every link, code and QR below.</div></div></header>

<div class="note">Each link and widget carries your code, so when a client uses it we know they came from you.
Use them on your website, in emails, on social posts and in print.</div>

<h2>1. Your QR code and printed card</h2>
<div class="card qr"><img src="{base}/qr.svg" alt="QR code to the free trademark search">
<div><p><b>Get a free Search Report.</b> The QR takes people straight into our free trademark search.</p>
<div class="dl"><a href="{base}/card.pdf" download>Card (A6 PDF)</a><a href="{base}/card-a4.pdf" download>Four cards on A4 (PDF)</a>
<a href="{base}/qr.png" download>QR (PNG)</a><a href="{base}/qr.svg" download>QR (SVG, for designers)</a></div>
<p class="m">Want your name on the card? Add <code>?name=Your%20Business</code> to the card link and it will read "Recommended by Your Business".</p></div></div>

<h2>2. Links for emails, posts and messages</h2>
<div class="card"><table class="lk">{link_rows}</table></div>

<h2>3. An email button</h2>
<div class="card"><p class="m">Paste this into an email that accepts HTML. It works without images or scripts.</p>
<div style="margin:10px 0 14px">{btn}</div>
<div class="code"><pre>{e(btn)}</pre><button type="button" class="cp" data-copy="{e(btn)}">Copy code</button></div></div>

<h2>4. Widgets for your website</h2>
<p class="m">Paste the code where you want the widget to appear. It sizes itself to fit.</p>
{wcards}

<p class="m" style="margin-top:34px">Questions? Call us on <a href="tel:01618335400">0161 833 5400</a>.</p>
</div>
<script>
document.addEventListener('click',function(ev){{var b=ev.target.closest('.cp');if(!b)return;
var t=b.getAttribute('data-copy');(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){{
var o=b.textContent;b.textContent='Copied';setTimeout(function(){{b.textContent=o}},1500)}},function(){{window.prompt('Copy this:',t)}});}});
</script></body></html>'''
