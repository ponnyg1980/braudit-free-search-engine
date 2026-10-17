"""The ONE AI class assistant — a short conversation that ends in classes.

Jonathan, 2 Oct 2026: "The AI services are too complicated and do not work
well from a UI perspective. We have decided to switch to Opus so let's
simplify with one AI option. The AI just interacts with the person till it
has what it needs to do classes."

17 Oct 2026 — CLASSIFY THE MARK'S USE, NOT JUST THE BUSINESS
(_Hub/CLASS_AGENT_MARK_USE_DESIGN_2026-10-17.md, agreed with Jonathan). The
old flow only asked "what do you do", so the Temmy owl came out as legal
services. The conversation is now:
  1. the MARK comes from what the page already holds (word, tagline, logo,
     business name, website, description); only what is missing is asked;
     a logo is looked at once and described;
  2. what JOB the mark does - confirming what we have ("Is this the name of
     [Business] itself?"); mascot only with a logo, slogan only with a
     tagline or word;
  3. what it is used for (confirming the description / website we hold);
  4. WHERE it will mainly be seen (tick boxes);
  5. genuine plans for the next few years;
  6. Opus reasons over ONE summary of the mark (the brief) - classes and
     terms graded core / likely / possible; weak-mark notes for staff;
  7. at most a few questions, only where the answer changes the classes,
     mainly to find what was MISSED. "If in doubt, include the term": a
     "No" only removes a class graded possible.

Clients are shown CLASSES only; terms (graded), the brief and the flags travel
with the order for staff (Terms_Status = Draft).

STATELESS. The browser holds `state` (returned by every turn) and sends it
back. State is validated on the way in: terms are only ever looked up in the
vocabulary again, never trusted as text.
"""
from __future__ import annotations

import re

try:
    from . import class_agent as A
except ImportError:                       # bare-script context
    import class_agent as A               # type: ignore

MAX_QUESTIONS = 4
URL_RE = re.compile(r'((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s]*)?)', re.I)
NO_RE = re.compile(r"^\s*(no|nope|none|nothing|not yet|n/?a|we don'?t|i don'?t|haven'?t)\b", re.I)
YES_RE = re.compile(r"^\s*(y|yes|yeah|yep|correct|that'?s right|right)\b", re.I)
YES_NO = [{'label': 'Yes', 'value': 'yes'}, {'label': 'No', 'value': 'no'},
          {'label': 'Not sure', 'value': 'unsure'}]

WHAT = [{'label': 'A word or name', 'value': 'word'}, {'label': 'A logo or image', 'value': 'logo'},
        {'label': 'A slogan or tagline', 'value': 'tagline'}]

# Jonathan, 17 Oct 2026 (3): "Where will this brand primarily be seen - present a list of boxes".
# Evidence for the model's reasoning, NOT a fixed class lookup.
SEEN = [
    ('website', 'On our website or online shop'),
    ('social', 'On social media'),
    ('software', 'On an app or software (screens, app icon, log-in page)'),
    ('products', 'On the products themselves'),
    ('packaging', 'On packaging, labels or tags'),
    ('marketplaces', 'On online marketplaces (Amazon, Etsy, eBay …)'),
    ('premises', 'On our premises: shop front, signage, menus, inside a shop, café, salon or office'),
    ('vehicles', 'On vehicles, uniforms or workwear'),
    ('print', 'On printed material: brochures, business cards, leaflets'),
    ('client_docs', 'On reports, documents or emails we send to clients'),
    ('advertising', 'In advertising: print, radio, TV, online ads'),
    ('events', 'At events, exhibitions or sponsorships'),
    ('merch_sold', 'On clothing, gifts or merchandise we sell'),
    ('giveaways', 'On free promotional giveaways'),
]
SEEN_LABEL = dict(SEEN)
ROLE_LABEL = {
    'house': 'the name of the whole business',
    'product': 'the name of one particular product or service',
    'range': 'the name of a range or product line',
    'mascot': 'a mascot or character',
    'slogan': 'a slogan used in advertising',
    'event': 'the name of an event or campaign',
}


# ------------------------------------------------------------ helpers --

def _say(state: dict, text: str, *, quick=None, multi=False, done=False) -> dict:
    out = {'ok': True, 'reply': text, 'quick': quick or [], 'multi': bool(multi), 'state': state,
           'done': done, 'classes': _client_classes(state) if done else []}
    if done:
        out['brief'] = state.get('brief', '')
    return out


def _client_classes(state: dict) -> list:
    """What a CLIENT is shown: class number and label, never terms."""
    return [{'n': c['n'], 'label': c['label']} for c in state.get('classes', [])]


def staff_view(state: dict) -> list:
    """What STAFF are shown and the order carries: classes, their certainty and graded terms."""
    return [{'n': c['n'], 'label': c['label'], 'certainty': c.get('certainty', ''), 'why': c.get('why', ''),
             'terms': list(c.get('terms', []))} for c in state.get('classes', [])]


def _s(v, cap=300) -> str:
    return str(v).strip()[:cap] if isinstance(v, (str, int, float)) else ''


def mark_name(m: dict) -> str:
    """How the chat refers to the mark: WORD, "your tagline", "your logo"."""
    if m.get('word'):
        return m['word']
    if m.get('tagline'):
        return f'"{m["tagline"]}"'
    if m.get('has_logo'):
        return 'your logo'
    return 'your trademark'


def role_options(m: dict) -> list:
    """Jonathan, 17 Oct 2026 (2): confirm what we have; mascot only with a logo, slogan only with a
    tagline or word."""
    name = mark_name(m)
    biz = m.get('business')
    opts = [{'label': f'The name of {biz} itself' if biz else 'The name of our whole business', 'value': 'house'}]
    opts.append({'label': f'{name} is the name of one particular product or service'
                 if (m.get('word') or m.get('tagline')) else 'The name of one product or service', 'value': 'product'})
    opts.append({'label': 'A range or product line', 'value': 'range'})
    if m.get('has_logo'):
        opts.append({'label': 'A mascot or character', 'value': 'mascot'})
    if m.get('tagline') or m.get('word'):
        opts.append({'label': 'A slogan used in advertising', 'value': 'slogan'})
    opts.append({'label': 'An event or campaign', 'value': 'event'})
    return opts


def _picks(msg: str, allowed: set) -> tuple[list, str]:
    """A multi-select answer: values joined by '|', plus any free text typed."""
    vals, other = [], []
    for part in re.split(r'\s*\|\s*', msg or ''):
        if not part:
            continue
        (vals if part in allowed else other).append(part)
    return list(dict.fromkeys(vals)), '; '.join(other)[:300]


def brief(st: dict) -> str:
    """The ONE summary the model reasons over (and staff read). Plain text."""
    m = st.get('mark') or {}
    kinds = [k for k, on in (('word', m.get('word')), ('logo', m.get('has_logo')), ('tagline', m.get('tagline'))) if on]
    lines = []
    what = []
    if m.get('word'):
        what.append(f'word mark "{m["word"]}"')
    if m.get('tagline'):
        what.append(f'tagline "{m["tagline"]}"')
    if m.get('has_logo'):
        what.append('logo' + (f' ({m["logo_desc"]})' if m.get('logo_desc') else ''))
    lines.append('Mark: ' + (', '.join(what) or 'not given') + (f' [{"/".join(kinds)}]' if kinds else ''))
    if m.get('logo_words'):
        lines.append(f'Words in the logo: {m["logo_words"]}')
    roles = [ROLE_LABEL[r] for r in st.get('roles') or [] if r in ROLE_LABEL]
    if roles or st.get('role_other'):
        lines.append('Job the mark does: ' + '; '.join(roles + ([st['role_other']] if st.get('role_other') else [])))
    if st.get('about'):
        lines.append(f'Used for (in their words): {st["about"]}')
    seen = [SEEN_LABEL[k] for k in st.get('seen') or [] if k in SEEN_LABEL]
    if seen or st.get('seen_other'):
        lines.append('Where it will be seen: ' + '; '.join(seen + ([st['seen_other']] if st.get('seen_other') else [])))
    if st.get('plans'):
        lines.append(f'Genuine plans (next few years): {st["plans"]}')
    bg = [x for x in (m.get('business') and f'Owner: {m["business"]}', m.get('website') and f'website {m["website"]}',
                      st.get('site_summary'), m.get('description')) if x]
    if bg:
        lines.append('Background, the owner\'s business: ' + '; '.join(bg))
    if m.get('depicts_goods'):
        lines.append(f'Note: the logo shows {m["depicts_goods"]}')
    return '\n'.join(lines)


# ------------------------------------------------------------ questions --

_QM = """You are a UK trademark attorney checking a class list for ONE trademark before a clearance \
audit. You are given the summary of the mark and the classes found, each graded core / likely / \
possible.

Write at most %d short yes/no questions, in plain British English to the owner ("Will ...?", \
"Do you ...?"), ONLY where the answer would change the classes. Above all look for lines of trade \
that may have been MISSED for this mark. Use the mark's name in the question (e.g. "Will the owl \
appear on clothing or gifts you sell?"). Never ask anything the summary already answers, never \
about minor variants, and ask nothing if the list is already complete.

Reply with JSON only:
{"questions": [{"q": "...", "add_if_yes": [25], "drop_if_no": [16]}]}

add_if_yes: classes to add when the answer is Yes. drop_if_no: only classes graded "possible" \
that a No would rule out. Either may be empty.""" % MAX_QUESTIONS


def _plan_questions(st: dict, cfg: dict) -> list:
    listing = '\n'.join(f'Class {c["n"]} ({c["label"]}) - {c.get("certainty", "possible")}: {c.get("why", "")}'
                        for c in st['classes'])
    data = A._json_from(A._call(_QM, f'The mark:\n"""{st["brief"]}"""\n\nClasses found:\n{listing}',
                                cfg=cfg, max_tokens=2000, effort='medium'))
    have = {c['n'] for c in st['classes']}
    possible = {c['n'] for c in st['classes'] if c.get('certainty') == 'possible'}
    out = []
    for q in (data.get('questions') or [])[:MAX_QUESTIONS]:
        text = _s(q.get('q'), 200)

        def nums(key, ok):
            got = []
            for n in q.get(key) or []:
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= 45 and ok(n) and n not in got:
                    got.append(n)
            return got
        add = nums('add_if_yes', lambda n: n not in have)
        drop = nums('drop_if_no', lambda n: n in possible)
        if text and (add or drop):
            out.append({'q': text, 'add': add, 'drop': drop})
    return out


def _add_classes(st: dict, nums: list, cfg: dict) -> None:
    nums = [n for n in nums if n not in {c['n'] for c in st['classes']}][:A.MARK_MAX_CLASSES]
    if not nums:
        return
    terms, failed = A.mark_terms(st['brief'], nums, cfg)
    for n in nums:
        st['classes'].append({'n': n, 'label': A.class_label(n), 'certainty': 'likely',
                              'why': 'added from your answer', 'terms': [
                                  {**t, 'certainty': 'likely' if t.get('certainty') == 'core' else t.get('certainty')}
                                  for t in terms.get(n, [])]})
    st['failed_classes'] = sorted(set(st.get('failed_classes') or []) | set(failed))


def _drop_classes(st: dict, nums: list) -> None:
    """Only classes graded possible are ever removed ("if in doubt, include")."""
    kill = set(nums)
    st['classes'] = [c for c in st['classes'] if not (c['n'] in kill and c.get('certainty') == 'possible')]


# ------------------------------------------------------------ classifying --

def _classify(st: dict, cfg: dict) -> str | None:
    st['brief'] = brief(st)
    res = A.suggest_mark(st['brief'], cfg=cfg)
    if not res.get('ok'):
        return res.get('message') or 'error'
    st['classes'] = [{'n': c['n'], 'label': c['label'], 'why': c.get('why', ''), 'certainty': c['certainty'],
                      'terms': [{'term': t['term'], 'n_marks': t.get('n_marks'), 'band': t.get('band'),
                                 'certainty': t.get('certainty')} for t in c.get('terms') or []]}
                     for c in res.get('classes') or []]
    st['flags'] = res.get('flags') or []
    st['failed_classes'] = res.get('failed_classes') or []
    return None if st['classes'] else 'none'


# ------------------------------------------------------------ state --

def _clean_mark(m) -> dict:
    m = m if isinstance(m, dict) else {}
    out = {k: _s(m.get(k), cap) for k, cap in (('word', 120), ('tagline', 200), ('business', 160), ('website', 200),
                                                ('description', 1500), ('logo_desc', 240), ('logo_words', 120),
                                                ('depicts_goods', 160))}
    out['has_logo'] = bool(m.get('has_logo') or out['logo_desc'])
    out['logo_character'] = bool(m.get('logo_character'))
    return {k: v for k, v in out.items() if v}


def _clean_state(s) -> dict:
    s = s if isinstance(s, dict) else {}
    out = {k: _s(s.get(k), 1500) for k in ('step', 'website', 'site_summary', 'about', 'plans', 'role_other',
                                           'seen_other', 'brief', 'what') if s.get(k)}
    out['brief'] = _s(s.get('brief'), 4000)
    out['mark'] = _clean_mark(s.get('mark'))
    out['roles'] = [r for r in (s.get('roles') or []) if r in ROLE_LABEL]
    out['seen'] = [k for k in (s.get('seen') or []) if k in SEEN_LABEL]
    out['flags'] = [_s(f, 240) for f in (s.get('flags') or [])][:4]
    qs = []
    for q in (s.get('questions') or [])[:MAX_QUESTIONS]:
        if isinstance(q, dict) and q.get('q'):
            qs.append({'q': _s(q['q'], 200),
                       'add': [int(n) for n in q.get('add') or [] if str(n).isdigit() and 1 <= int(n) <= 45],
                       'drop': [int(n) for n in q.get('drop') or [] if str(n).isdigit() and 1 <= int(n) <= 45]})
    out['questions'] = qs
    vocab = A.load_vocab() or {}
    classes = []
    for c in (s.get('classes') or [])[:A.MARK_MAX_CLASSES]:
        try:
            n = int(c.get('n'))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= n <= 45:
            continue
        known = {t['term'] for t in vocab.get(n, [])}
        terms = [t for t in (c.get('terms') or [])
                 if isinstance(t, dict) and t.get('term') in known][:A.MARK_TERMS_PER_CLASS]
        cert = c.get('certainty') if c.get('certainty') in A.CERTAINTY else 'possible'
        classes.append({'n': n, 'label': A.class_label(n), 'why': _s(c.get('why'), 200), 'certainty': cert,
                        'terms': terms})
    out['classes'] = classes
    out['step'] = str(out.get('step') or 'start')
    out['asked'] = int(s.get('asked') or 0) if str(s.get('asked') or '0').isdigit() else 0
    return out


def _from_context(ctx, cfg: dict) -> dict:
    """Jonathan, 17 Oct 2026 (1): the mark comes from the form / Deal if completed already."""
    ctx = ctx if isinstance(ctx, dict) else {}
    m = _clean_mark(ctx)
    logo = ctx.get('logo')
    if logo:
        m['has_logo'] = True
        d = A.describe_logo(str(logo), cfg=cfg)
        if d.get('description'):
            m['logo_desc'] = d['description']
            m['logo_character'] = d.get('character', False)
            if d.get('words'):
                m['logo_words'] = d['words']
            if d.get('depicts_goods'):
                m['depicts_goods'] = d['depicts_goods']
    return _clean_mark(m)


# ------------------------------------------------------------ the turn --

def turn(payload: dict, cfg: dict | None = None) -> dict:
    """One conversational turn. payload = {state, message, context (first turn only)}."""
    cfg = dict(cfg or {})
    st = _clean_state(payload.get('state'))
    msg = str(payload.get('message') or '').strip()[:A.MAX_TEXT]
    step = st['step']

    if step == 'start':
        st['mark'] = _from_context(payload.get('context'), cfg)
        m = st['mark']
        if not (m.get('word') or m.get('tagline') or m.get('has_logo')):
            st['step'] = 'what'
            return _say(st, "Let's find the right classes for your trademark. What are you protecting?",
                        quick=WHAT)
        return _ask_role(st, intro=True)

    if step == 'what':
        v = msg.lower()
        kind = 'logo' if 'logo' in v or 'image' in v else 'tagline' if 'slogan' in v or 'tagline' in v else 'word'
        st['what'] = kind
        st['step'] = 'what_detail'
        return _say(st, {'word': 'What is the word or name?',
                         'tagline': 'What is the slogan or tagline?',
                         'logo': 'Describe the logo in a few words, for example "a cartoon owl wearing glasses". '
                                 'If it contains words, tell me those too.'}[kind])

    if step == 'what_detail':
        if len(msg) < 1:
            return _say(st, 'Please type it in.')
        kind = st.get('what') or 'word'
        if kind == 'logo':
            st['mark'].update(has_logo=True, logo_desc=msg[:240])
        else:
            st['mark'][kind] = msg[:200 if kind == 'tagline' else 120]
        st['mark'] = _clean_mark(st['mark'])
        return _ask_role(st)

    if step == 'role':
        roles, other = _picks(msg, set(ROLE_LABEL))
        if not roles and not other:
            return _ask_role(st)
        st['roles'], st['role_other'] = roles, other
        return _ask_about(st, cfg)

    if step == 'website':
        m = URL_RE.search(msg)
        if m and not NO_RE.match(msg):
            st['mark']['website'] = m.group(1)[:200]
            return _read_site(st, m.group(1))
        st['step'] = 'about'
        return _say(st, f'No problem. In a sentence or two: what do you sell or provide under {mark_name(st["mark"])}?')

    if step in ('about', 'about_confirm'):
        if step == 'about_confirm' and YES_RE.match(msg) and len(msg) < 25:
            st['about'] = st['mark'].get('description') or st.get('site_summary') or ''
        elif len(msg) < 10 and not st.get('about'):
            return _say(st, 'Could you tell me a bit more? A sentence or two is plenty.')
        else:
            st['about'] = msg if step == 'about' else '; '.join(
                x for x in (st['mark'].get('description') or st.get('site_summary'), msg) if x)
        st['step'] = 'seen'
        return _say(st, f'Where will {mark_name(st["mark"])} mainly be seen? Tick everything that applies, '
                        'and type anything else.',
                    quick=[{'label': lab, 'value': k} for k, lab in SEEN], multi=True)

    if step == 'seen':
        seen, other = _picks(msg, set(SEEN_LABEL))
        if not seen and not other:
            return _say(st, 'Please tick at least one, or type where it will be used.',
                        quick=[{'label': lab, 'value': k} for k, lab in SEEN], multi=True)
        st['seen'], st['seen_other'] = seen, other
        st['step'] = 'plans'
        return _say(st, f'Is there anything you genuinely plan to use {mark_name(st["mark"])} on in the next '
                        'few years? For example new products, an app, or merchandise.',
                    quick=[{'label': 'Nothing planned', 'value': 'no'}])

    if step == 'plans':
        st['plans'] = '' if NO_RE.match(msg) else msg[:600]
        err = _classify(st, cfg)
        if err:
            st['step'] = 'about'
            return _say(st, "I couldn't work out the classes from that. Could you describe what you sell or "
                            f'provide under {mark_name(st["mark"])} in a little more detail?')
        try:
            st['questions'] = _plan_questions(st, cfg)
        except A.AgentError:
            st['questions'] = []
        st['step'] = 'refine'
        return _next_question(st, intro=True)

    if step == 'refine':
        qs = st.get('questions') or []
        if qs:
            q = qs.pop(0)
            if YES_RE.match(msg):
                _add_classes(st, q.get('add') or [], cfg)
            elif NO_RE.match(msg):
                _drop_classes(st, q.get('drop') or [])
            st['questions'] = qs
            st['asked'] += 1
        return _next_question(st)

    st['step'] = 'done'
    return _say(st, 'Your classes are ready.', done=True)


def _ask_role(st: dict, intro: bool = False) -> dict:
    st['step'] = 'role'
    m = st['mark']
    lead = "Let's find the right classes for your trademark. " if intro else ''
    seen = (f'I can see {mark_name(m)}' if m.get('word') or m.get('tagline') else 'I can see your logo') \
        + (f' ({m["logo_desc"]})' if m.get('logo_desc') else '') + '. '
    return _say(st, lead + seen + 'What job does it do? Tick all that apply.', quick=role_options(m), multi=True)


def _ask_about(st: dict, cfg: dict) -> dict:
    m = st['mark']
    name = mark_name(m)
    if m.get('description'):
        st['step'] = 'about_confirm'
        return _say(st, f"Here's what we have about what you do:\n{m['description']}\n\n"
                        f'Is that what {name} is used for? Tell me anything to add, especially anything '
                        f'specific to {name}.', quick=[{'label': "That's right", 'value': "That's right"}])
    if m.get('website') and not st.get('site_summary'):
        return _read_site(st, m['website'])
    st['step'] = 'website'
    return _say(st, 'Does your business have a website? Paste the address, or tell me you don\'t have one yet.',
                quick=[{'label': "We don't have a website", 'value': 'no'}])


def _read_site(st: dict, url: str) -> dict:
    name = mark_name(st['mark'])
    try:
        try:
            from . import controller
        except ImportError:
            import controller   # type: ignore
        r = controller.handle_read_website({'url': url}) or {}
    except Exception as exc:                       # noqa: BLE001 - background only
        print(f'[class-chat] website read failed: {type(exc).__name__}', flush=True)
        r = {}
    a = r.get('answers') or {}
    if r.get('ok') and (a.get('goods') or a.get('services') or a.get('pitch')):
        st['site_summary'] = '; '.join(x for x in (a.get('pitch'), a.get('goods'), a.get('services'),
                                                   a.get('unique')) if x)[:1500]
        st['step'] = 'about_confirm'
        return _say(st, f"From your website I understand: {a.get('pitch') or st['site_summary']}\n\n"
                        f'Is that what {name} is used for? Tell me anything to add, especially anything '
                        f'specific to {name}.', quick=[{'label': "That's right", 'value': "That's right"}])
    st['step'] = 'about'
    return _say(st, f'In a sentence or two: what do you sell or provide under {name}?')


def _next_question(st: dict, intro: bool = False) -> dict:
    names = ', '.join(f"Class {c['n']} ({c['label']})" for c in st['classes'])
    qs = st.get('questions') or []
    if qs and st.get('asked', 0) < MAX_QUESTIONS:
        lead = (f"I've found these classes for you: {names}.\n\nA few quick questions so nothing is missed. "
                if intro else '')
        return _say(st, lead + qs[0]['q'], quick=YES_NO)
    st['step'] = 'done'
    st['questions'] = []
    return _say(st, f"Thanks, that's everything I need. Your classes: {names}.\n\n"
                    'Please check them below. A member of our team will check them again before anything is filed.',
                done=True)
