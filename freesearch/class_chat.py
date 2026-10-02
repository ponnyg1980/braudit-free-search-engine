"""The ONE AI class assistant — a short conversation that ends in classes.

Jonathan, 2 Oct 2026: "The AI services are too complicated and do not work
well from a UI perspective. We have decided to switch to Opus so let's
simplify with one AI option. The AI just interacts with the person till it
has what it needs to do classes." It replaces the Describe-your-business and
Your-website AI routes and the AI Class Assistant. The non-AI routes stay.

The conversation, in Jonathan's order:
  1. ask for a website;
  2. no website -> goods, services or both?
  3. ask about the goods and/or services;
  4. find the classes;
  5. build the list of APPROVED terms for each class (terms registered at the
     UKIPO, from the vocabulary — the model never writes a term);
  6. rule out irrelevant terms;
  7. ask questions to rule out more until what is left is right.

Clients are shown CLASSES only; the terms travel with the order as
`Terms_Status = Draft` for a TMH team member to check and approve (staff see
and edit them). See BUILD_PLAN_LOG 2 Oct 2026.

STATELESS. The browser holds `state` (returned by every turn) and sends it
back with the next message, so the server keeps nothing and any instance can
answer any turn. State is validated on the way in: term ids are only ever
looked up in the vocabulary again, never trusted as text.
"""
from __future__ import annotations

import re

try:
    from . import class_agent as A
except ImportError:                       # bare-script context
    import class_agent as A               # type: ignore

MAX_QUESTIONS = 4          # refinement questions before we stop asking
MAX_TERMS_PER_CLASS = 15   # same backstop as class_agent stage 2
URL_RE = re.compile(r'((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+(?:/[^\s]*)?)', re.I)
NO_RE = re.compile(r"^\s*(no|nope|none|not yet|n/?a|we don'?t|i don'?t|haven'?t)\b", re.I)

GOODS_SERVICES = [{'label': 'Goods', 'value': 'goods'},
                  {'label': 'Services', 'value': 'services'},
                  {'label': 'Both', 'value': 'both'}]
YES_NO = [{'label': 'Yes', 'value': 'yes'}, {'label': 'No', 'value': 'no'},
          {'label': 'Not sure', 'value': 'unsure'}]


def _say(state: dict, text: str, *, quick=None, done=False) -> dict:
    return {'ok': True, 'reply': text, 'quick': quick or [], 'state': state,
            'done': done, 'classes': _client_classes(state) if done else []}


def _client_classes(state: dict) -> list:
    """What a CLIENT is shown: class number and label, never terms."""
    return [{'n': c['n'], 'label': c['label']} for c in state.get('classes', [])]


def staff_view(state: dict) -> list:
    """What STAFF are shown and the order carries: classes with their terms."""
    return [{'n': c['n'], 'label': c['label'],
             'terms': [t for t in c.get('terms', [])]} for c in state.get('classes', [])]


def _description(state: dict) -> str:
    parts = [state.get('site_summary', ''), state.get('about', '')]
    p = state.get('provides')
    if p:
        parts.append(f'They provide {"goods and services" if p == "both" else p}.')
    return '\n'.join(x for x in parts if x).strip()


# ------------------------------------------------------------ refinement --

_Q = """You help narrow a UK trademark specification. You are given what a \
business does and the goods and services terms currently selected for it, \
numbered. Some terms may not actually apply.

Write at most %d short yes/no questions, in plain British English addressed to \
the business owner ("Do you…?"), whose answer would rule terms OUT. Only ask \
where there is real doubt; if every term clearly applies, ask nothing. Never \
ask about something the description already answers. Ask about whole lines of \
goods or services that may not apply, never minor variants (roast, colour, \
size, flavour), and prefer one question that rules out several terms.

Reply with JSON only:
{"questions": [{"q": "Do you sell coffee machines as well as coffee?", \
"drop_if_no": [3, 7]}]}

drop_if_no lists the numbers of the terms to remove when the answer is No.""" % MAX_QUESTIONS


def _plan_questions(state: dict, cfg: dict) -> list:
    flat = [(ci, ti, t['term']) for ci, c in enumerate(state['classes'])
            for ti, t in enumerate(c['terms'])]
    if len(flat) < 2:
        return []
    listing = '\n'.join(f'{i+1}. [class {state["classes"][ci]["n"]}] {term}'
                        for i, (ci, ti, term) in enumerate(flat))
    raw = A._call(_Q, f'What the business does:\n"""{_description(state)}"""\n\n'
                      f'Selected terms:\n{listing}', cfg=cfg, max_tokens=1500)
    data = A._json_from(raw)
    out = []
    for q in (data.get('questions') or [])[:MAX_QUESTIONS]:
        text = str(q.get('q') or '').strip()[:200]
        drops = []
        for n in q.get('drop_if_no') or []:
            try:
                i = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(flat):
                ci, ti, term = flat[i]
                drops.append([state['classes'][ci]['n'], term])
        if text and drops:
            out.append({'q': text, 'drop': drops})
    return out


def _apply_drop(state: dict, drops: list) -> None:
    kill = {(int(n), str(t)) for n, t in drops}
    for c in state['classes']:
        c['terms'] = [t for t in c['terms'] if (c['n'], t['term']) not in kill]
    # A class left with no terms stays: the classes were found from what the
    # business does, and staff will add terms when they check.


# ------------------------------------------------------------ classifying --

def _classify(state: dict, cfg: dict) -> str | None:
    """Steps 4-6: classes, the approved-term list per class, irrelevant out."""
    text = _description(state)
    res = A.suggest(text, provides=state.get('provides'), cfg=cfg)
    if not res.get('ok'):
        return res.get('message') or 'error'
    classes = []
    for c in res.get('classes') or []:
        classes.append({'n': c['n'], 'label': c['label'], 'why': c.get('why', ''),
                        'terms': [{'term': t['term'], 'n_marks': t.get('n_marks'),
                                   'band': t.get('band')} for t in c.get('terms') or []]
                                 [:MAX_TERMS_PER_CLASS]})
    state['classes'] = classes
    state['failed_classes'] = res.get('failed_classes') or []
    return None if classes else 'none'


# ------------------------------------------------------------ the turn --

def _clean_state(s) -> dict:
    s = s if isinstance(s, dict) else {}
    out = {k: s.get(k) for k in ('step', 'website', 'site_summary', 'provides',
                                 'about', 'questions', 'asked') if s.get(k) is not None}
    vocab = A.load_vocab() or {}
    classes = []
    for c in (s.get('classes') or [])[:A.MAX_CLASSES]:
        try:
            n = int(c.get('n'))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= n <= 45:
            continue
        known = {t['term'] for t in vocab.get(n, [])}
        terms = [t for t in (c.get('terms') or [])
                 if isinstance(t, dict) and t.get('term') in known][:MAX_TERMS_PER_CLASS]
        classes.append({'n': n, 'label': A.class_label(n), 'why': str(c.get('why') or '')[:160],
                        'terms': terms})
    out['classes'] = classes
    out['step'] = str(out.get('step') or 'start')
    out['asked'] = int(out.get('asked') or 0)
    return out


def turn(payload: dict, cfg: dict | None = None) -> dict:
    """One conversational turn. payload = {state, message}."""
    cfg = dict(cfg or {})
    st = _clean_state(payload.get('state'))
    msg = str(payload.get('message') or '').strip()[:A.MAX_TEXT]
    step = st['step']

    if step == 'start':
        st['step'] = 'website'
        return _say(st, "Let's find the right classes for your trademark. "
                        "Does your business have a website? Paste the address, "
                        "or tell me you don't have one yet.",
                    quick=[{'label': "We don't have a website", 'value': 'no'}])

    if step == 'website':
        m = URL_RE.search(msg)
        if m and not NO_RE.match(msg):
            try:
                from . import controller
            except ImportError:
                import controller   # type: ignore
            r = controller.handle_read_website({'url': m.group(1)})
            a = (r or {}).get('answers') or {}
            if r.get('ok') and (a.get('goods') or a.get('services') or a.get('pitch')):
                st['website'] = m.group(1)
                st['site_summary'] = '; '.join(x for x in (a.get('pitch'), a.get('goods'),
                                                         a.get('services'), a.get('unique')) if x)
                if a.get('provides') in ('goods', 'services', 'both'):
                    st['provides'] = a['provides']
                st['step'] = 'confirm_site'
                return _say(st, "Thanks. From your website, I understand: "
                                f"{a.get('pitch') or st['site_summary']}\n\n"
                                "Is that right? Tell me anything to add or correct, "
                                "for example products you're planning to sell.",
                            quick=[{'label': "That's right", 'value': "That's right"}])
            st['step'] = 'provides'
            return _say(st, (r or {}).get('message') or "I couldn't read enough from that site. "
                            "No problem: do you sell goods, services, or both?",
                        quick=GOODS_SERVICES)
        st['step'] = 'provides'
        return _say(st, 'No problem. Do you sell goods, services, or both?', quick=GOODS_SERVICES)

    if step == 'provides':
        v = msg.lower()
        p = 'both' if 'both' in v else 'goods' if 'good' in v or 'product' in v else \
            'services' if 'servic' in v else ''
        if not p:
            return _say(st, 'Just so I search the right part of the register: goods, '
                            'services, or both?', quick=GOODS_SERVICES)
        st['provides'] = p
        st['step'] = 'about'
        ask = {'goods': 'What goods do you sell? List the main products, in your own words.',
               'services': 'What services do you provide? Describe them in your own words.',
               'both': 'Tell me about both: the goods you sell and the services you provide.'}[p]
        return _say(st, ask)

    if step in ('about', 'confirm_site'):
        if step == 'confirm_site' and msg and not re.match(r"^\s*(that'?s right|yes|correct|right)\b", msg, re.I):
            st['about'] = msg
        elif step == 'about':
            if len(msg) < 10:
                return _say(st, 'Could you tell me a bit more? A sentence or two is plenty.')
            st['about'] = msg
        err = _classify(st, cfg)
        if err:
            st['step'] = 'about'
            return _say(st, "I couldn't work out the classes from that. Could you describe "
                            'what you sell or do in a little more detail?')
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
            if msg.lower().startswith('no'):
                _apply_drop(st, q.get('drop') or [])
            st['questions'] = qs
            st['asked'] += 1
        return _next_question(st)

    st['step'] = 'done'
    return _say(st, 'Your classes are ready.', done=True)


def _next_question(st: dict, intro: bool = False) -> dict:
    names = ', '.join(f"Class {c['n']} ({c['label']})" for c in st['classes'])
    qs = st.get('questions') or []
    if qs and st.get('asked', 0) < MAX_QUESTIONS:
        lead = (f"I've found these classes for you: {names}.\n\n"
                'A few quick questions so we only include what applies. '
                if intro else '')
        return _say(st, lead + qs[0]['q'], quick=YES_NO)
    st['step'] = 'done'
    st['questions'] = []
    return _say(st, f"Thanks, that's everything I need. Your classes: {names}.\n\n"
                    'Please check them below and approve them. A member of our team will '
                    'check them again before anything is filed.', done=True)
