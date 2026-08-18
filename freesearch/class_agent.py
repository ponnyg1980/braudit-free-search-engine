"""Class & term suggestion agent — describe a business, get verified classes.

Spec: AI_CLASS_AGENT_SPEC.md. Built 10 Aug 2026.

THE ONE RULE
------------
The model NEVER writes a goods & services term. It only ever selects from a
list of real terms we hand it, and it answers with INDICES. A term it did not
receive cannot come out, because it never emits text at all.

Everything reaching a client is checked back against `data/class_terms.csv` —
13,007 terms harvested from marks actually registered in the last five years
(see build_class_terms.py). So "coffee" is offered because 6,182 real
registrations in class 30 use that exact word, not because a model thought it
sounded plausible.

Jonathan, 10 Aug: "this is a term creator which is 90% right but humans will
check before we apply". So this aims to be fast and useful rather than
exhaustive — but the no-invention rule is absolute regardless, because a
made-up term is not a 10% error, it's a term that cannot be filed.

DELIBERATELY TRANSFERABLE
-------------------------
Jonathan: "all of these class selection tools we want to build in a way that
is transferrable to other applications."

So this module is pure: no HTTP, no UI, no Free Search assumptions. One
function, `suggest()`, taking text and returning structured data. The HTTP
route in controller.py is a thin wrapper; Free Search, Brand Audit and the
staff tool are three callers of the same thing. Anything Free-Search-shaped
belongs in the caller, not here.
"""
from __future__ import annotations

from concurrent import futures

# How many stage-2 class calls may be in flight at once.
MAX_PARALLEL = 6

import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
VOCAB_CSV = DATA / 'class_terms.csv'

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = os.environ.get('CLASS_AGENT_MODEL', 'claude-sonnet-5')

# How many candidate terms per class the model is shown. Caps tokens, and
# frequency is evidence: a term 700 marks use is a safer suggestion than one
# used twice. 40 is plenty for a human to review.
CANDIDATES_PER_CLASS = 40
MAX_CLASSES = 8               # more than this and it isn't a suggestion
MAX_TEXT = 2000               # chars of description accepted

_vocab: dict[int, list[dict]] | None = None


# --------------------------------------------------------------- vocabulary --

def load_vocab() -> dict[int, list[dict]]:
    """{class: [{term, n_marks, share, band}, ...]} ordered by usage."""
    global _vocab
    if _vocab is not None:
        return _vocab
    v: dict[int, list[dict]] = {}
    if VOCAB_CSV.exists():
        with VOCAB_CSV.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    c = int(r['nice_class'])
                except (TypeError, ValueError):
                    continue
                v.setdefault(c, []).append({
                    'term': r['term'],
                    'n_marks': int(r.get('n_marks') or 0),
                    'share': float(r.get('share') or 0),
                    'band': r.get('band') or '',
                })
    for c in v:
        v[c].sort(key=lambda x: -x['n_marks'])
    _vocab = v
    return v


def class_label(n: int) -> str:
    try:
        from .nice_labels import short
    except ImportError:
        from nice_labels import short
    try:
        return short(int(n)) or ''
    except (TypeError, ValueError):
        return ''


# ------------------------------------------------------------------- the API --

class AgentError(RuntimeError):
    pass


def _call(system: str, user: str, *, cfg: dict, max_tokens: int = 1024,
          retries: int = 2) -> str:
    # .strip() is load-bearing. Pasting a key into a dashboard field very
    # easily carries a trailing newline, and a newline in a header value is
    # rejected outright by http.client with "Invalid header value" — the key
    # looks perfect in the UI and every request fails. This exact failure is
    # already recorded in this project's notes for the Temmy keys, and
    # api.py's _make_client() strips for the same reason.
    key = (cfg.get('ANTHROPIC_API_KEY')
           or os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not key:
        raise AgentError('ANTHROPIC_API_KEY not configured')
    # No `temperature`: deprecated on current models, and rejected with a 400.
    # Determinism here comes from the task shape instead — the model picks
    # from a fixed numbered list, and everything is re-validated on the way
    # out, so a stray choice is caught rather than merely discouraged.
    body = json.dumps({
        'model': cfg.get('model') or DEFAULT_MODEL,
        'max_tokens': max_tokens,
        'system': system,
        'messages': [{'role': 'user', 'content': user}],
    }).encode()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(API_URL, data=body, method='POST', headers={
            'content-type': 'application/json',
            'x-api-key': key,
            'anthropic-version': API_VERSION,
        })
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=45).read())
            parts = [b.get('text', '') for b in (r.get('content') or [])
                     if b.get('type') == 'text']
            return ''.join(parts)
        except urllib.error.HTTPError as e:
            last = f'HTTP {e.code}'
            if e.code in (400, 401, 403):     # our fault — retrying won't help
                break
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:                 # noqa: BLE001 - network flake
            # NEVER put the raw exception in `last`. A malformed key raises
            # "Invalid header value b'sk-ant-...'" — i.e. the exception text
            # CONTAINS THE KEY, and this message is returned to the caller.
            # That leaked key material to anyone hitting the endpoint until
            # it was caught on 10 Aug. Keep this generic.
            last = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
    raise AgentError(f'model call failed ({last})')


def _json_from(txt: str) -> dict:
    """Models sometimes wrap JSON in prose or a fence. Take the outermost {}."""
    m = re.search(r'\{.*\}', txt or '', re.S)
    if not m:
        raise AgentError('no JSON in response')
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AgentError(f'malformed JSON: {e}') from e


# ------------------------------------------------------------------ stage one --

_S1 = """You classify UK trademark applications into Nice classes.

You are given a description of a business. Return ONLY the Nice classes the \
business genuinely trades in. Do not pad the list — a focused business may \
need only one or two classes. Never return a class just because it is \
adjacent or might apply one day.

Nice classes 1-34 are GOODS (physical things made or sold). Classes 35-45 \
are SERVICES (things done for people).

Reply with JSON only:
{"classes": [30, 43], "why": {"30": "sells roasted coffee", "43": "runs a cafe"}}

At most %d classes. If the description is too vague to classify, return \
{"classes": [], "why": {}} rather than guessing.""" % MAX_CLASSES


def _stage1(text: str, provides: str | None, cfg: dict) -> tuple[list[int], dict]:
    hint = ''
    if provides == 'goods':
        hint = '\n\nThe business has told us it provides GOODS only. Return classes 1-34 only.'
    elif provides == 'services':
        hint = '\n\nThe business has told us it provides SERVICES only. Return classes 35-45 only.'
    elif provides == 'both':
        hint = '\n\nThe business has told us it provides BOTH goods and services.'

    raw = _call(_S1, f'Business description:\n"""{text}"""{hint}', cfg=cfg, max_tokens=700)
    data = _json_from(raw)

    out, why = [], {}
    src_why = data.get('why') or {}
    for c in (data.get('classes') or []):
        try:
            n = int(c)
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= 45:
            continue
        # Enforce the goods/services split ourselves — the prompt asks, this
        # guarantees. Nice splits at exactly this line, so an answer on the
        # wrong side of it is always wrong, whatever the model believed.
        if provides == 'goods' and n > 34:
            continue
        if provides == 'services' and n < 35:
            continue
        if n not in out:
            out.append(n)
            if str(c) in src_why:
                why[n] = str(src_why[str(c)])[:160]
    return out[:MAX_CLASSES], why


# ------------------------------------------------------------------ stage two --

_S2 = """You select goods & services terms for a UK trademark application.

You are given a business description and a NUMBERED list of real Nice \
specification terms, taken from trademarks actually registered in the UK.

Return the numbers of the terms that accurately describe what this business \
does. Prefer terms that are clearly true of the business over terms that are \
merely possible. It is better to return five right terms than twenty loose ones.

RETURN NUMBERS ONLY. Do not write, edit, reword, translate, combine or invent \
terms. You are choosing from the list, not composing.

Reply with JSON only: {"pick": [1, 4, 9]}

If none of the terms fit, return {"pick": []}."""


def _stage2(text: str, cls: int, candidates: list[dict], cfg: dict) -> list[dict]:
    if not candidates:
        return []
    listing = '\n'.join(f'{i+1}. {c["term"]}' for i, c in enumerate(candidates))
    user = (f'Business description:\n"""{text}"""\n\n'
            f'Nice class {cls} ({class_label(cls)}). Candidate terms:\n{listing}')
    raw = _call(_S2, user, cfg=cfg, max_tokens=500)
    data = _json_from(raw)

    picked, seen = [], set()
    for i in (data.get('pick') or []):
        try:
            idx = int(i) - 1
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(candidates) or idx in seen:
            continue
        seen.add(idx)
        picked.append(candidates[idx])
    return picked


# ---------------------------------------------------------------- public API --

_SA = """You read a company's website and fill in a short form about them.

You will be given the visible text of a business's own website. Answer only \
from what the text actually says. Where the site does not tell you something, \
leave that field empty — do not infer, embellish, or write marketing copy.

Reply with JSON only:
{
  "pitch": "one plain sentence: what this business does",
  "provides": "goods" | "services" | "both" | "",
  "goods": "the physical products they make or sell, comma separated",
  "services": "the services they perform for people, comma separated",
  "unique": "anything distinctive the site states about them",
  "confidence": "high" | "medium" | "low"
}

"provides" is about what they OFFER: goods are physical things they make or \
sell, services are things they do for customers. A shop selling other \
people's products is still selling goods.

Set confidence to "low" if the page is mostly navigation, a holding page, or \
too vague to tell what they actually sell."""


def answers_from_website(page_text: str, *, cfg: dict | None = None) -> dict:
    """Turn website text into answers to the describe-your-business questions.

    Jonathan, 10 Aug: "What the website URL should do, is try and answer the
    questions from describe your business."

    Deliberately fills the SAME form the visitor would otherwise complete by
    hand, rather than jumping straight to classes. They see what we read off
    their site, correct anything wrong, and only then do we classify. A page
    we misread becomes a visible mistake they can fix, instead of a silently
    wrong set of classes.

    Never raises — a failed read means an empty form, not a broken page.
    """
    cfg = dict(cfg or {})
    text = (page_text or '').strip()[:6000]
    if len(text) < 40:
        return {'ok': False, 'error': 'thin_page'}
    try:
        raw = _call(_SA, f'Website text:\n"""{text}"""', cfg=cfg, max_tokens=700)
        data = _json_from(raw)
    except AgentError as exc:
        return {'ok': False, 'error': 'agent_failed', 'detail': str(exc)}

    def s(k, cap=600):
        v = data.get(k)
        return str(v).strip()[:cap] if isinstance(v, (str, int, float)) else ''

    provides = s('provides', 20).lower()
    if provides not in ('goods', 'services', 'both'):
        provides = ''
    conf = s('confidence', 10).lower()
    if conf not in ('high', 'medium', 'low'):
        conf = 'medium'

    return {'ok': True, 'answers': {
        'pitch': s('pitch', 300), 'provides': provides,
        'goods': s('goods'), 'services': s('services'),
        'unique': s('unique'),
    }, 'confidence': conf}


def suggest(text: str, *, provides: str | None = None, cfg: dict | None = None,
            candidates_per_class: int = CANDIDATES_PER_CLASS) -> dict:
    """Suggest Nice classes and verified terms for a business description.

    `provides` is 'goods' | 'services' | 'both' | None. It comes from the
    guided questions and is the single most valuable input: it halves the
    register, and it is enforced in code, not merely requested in the prompt.

    Returns:
      {ok, classes: [{n, label, why, terms: [{term, n_marks, share, band}]}],
       model, verified, dropped}

    `verified` is True when every returned term matched the vocabulary
    exactly. `dropped` counts anything the model returned that did not — it
    should always be 0; a non-zero value means the prompt is drifting and
    wants looking at.
    """
    cfg = dict(cfg or {})
    text = (text or '').strip()[:MAX_TEXT]
    if len(text) < 10:
        return {'ok': False, 'error': 'too_short',
                'message': 'Tell us a little more about the business.'}

    vocab = load_vocab()
    if not vocab:
        return {'ok': False, 'error': 'no_vocabulary',
                'message': 'Term vocabulary unavailable.'}

    classes, why = _stage1(text, provides, cfg)
    if not classes:
        return {'ok': True, 'classes': [], 'model': cfg.get('model') or DEFAULT_MODEL,
                'verified': True, 'dropped': 0,
                'message': "We couldn't work this out from that description."}

    # Stage 2 runs ONE model call per class, and those calls are independent —
    # each picks terms from its own class's pool and knows nothing about the
    # others. Run sequentially that is 1 + N round trips: measured at 10.8s
    # for a four-class business, which is far too long to sit in front of.
    # In parallel the wall time is stage 1 plus the slowest single class.
    #
    # Same pattern, and the same reasoning, as _fill_details in lookup.py.
    # Capped at MAX_PARALLEL so a nine-class description doesn't open nine
    # sockets at the model at once.
    pools = {c: vocab.get(c, [])[:candidates_per_class] for c in classes}

    def _one(c):
        try:
            return c, _stage2(text, c, pools[c], cfg)
        except AgentError:
            return c, []           # one class failing must not lose the rest

    picked_by_class = {}
    if len(classes) > 1:
        with futures.ThreadPoolExecutor(
                max_workers=min(len(classes), MAX_PARALLEL)) as ex:
            for c, picked in ex.map(_one, classes):
                picked_by_class[c] = picked
    else:
        for c in classes:
            picked_by_class[c] = _one(c)[1]

    out, dropped = [], 0
    for c in classes:                      # rebuild in stage-1's order
        picked = picked_by_class.get(c, [])
        # The guarantee. Even though _stage2 returns objects taken FROM the
        # pool, re-check identity against the vocabulary before it leaves —
        # cheap, and it means no future refactor can quietly open a hole.
        allowed = {p['term'] for p in pools[c]}
        clean = [p for p in picked if p['term'] in allowed]
        dropped += len(picked) - len(clean)
        out.append({'n': c, 'label': class_label(c), 'why': why.get(c, ''),
                    'terms': clean})

    return {'ok': True, 'classes': out,
            'model': cfg.get('model') or DEFAULT_MODEL,
            'verified': dropped == 0, 'dropped': dropped}
