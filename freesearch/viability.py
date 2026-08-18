"""Trademark Viability Score for Free Search — the on-page opinion.

Jonathan, 11 Aug: the results page "does not give any kind of opinion on
whether the trademark is viable... can we re-purpose the likelihood of success
from the Industry Report for this?" — and, when asked how strong the opinion
should be: "Use the rules we developed within the industry report search,
where we weighted decisions to avoid scaring people off based just on a word
search... I am referring to the Tailored Report section of the Industry
Report."

That is the Trademark Viability Score in goal3-industry-report/viability.py.
This is a PORT, not a re-derivation: the weights, the per-band caps, the
descriptive-word list and the undisclosed floor are copied across unchanged so
a visitor who does the free search and then the Industry Report sees the same
number for the same facts. If you change a constant here, change it there too.

WHAT THE WEIGHTING IS ACTUALLY FOR
----------------------------------
The caps in `conflicts()` are the whole point of Jonathan's note. A free
search is a WORD search: it flags anything that looks like the name, and a
common-ish word can flag two hundred marks that are all Low Risk. Summed
linearly that zeroes the dial and tells someone whose every risk is "low" that
their brand is doomed — the opposite of what the data says. So severity
dominates volume: all the Low marks in the world cost at most 10 points,
Medium at most 25, while genuine High risks properly hurt.

WHAT THIS IS NOT
----------------
It is not a risk band, and it must never be presented as one. The per-mark
bands (High/Medium/Low Risk) are the register evidence and stay on the page
exactly as scoring.py computed them. This is a separate, softer, forward-
looking number — "how viable does this look" — and the two are shown side by
side, never merged. Per the scoring rules: conflict and rights are two
numbers; nothing here collapses them into one verdict.

Fail-closed rule: however good the strengths look, a single High Risk conflict
caps the spoken verdict at "worth a closer look". The maths alone could let a
strongly distinctive name outrun one genuine High, and it must not.
"""
from __future__ import annotations

import re

# ── ported verbatim from goal3-industry-report/viability.py ─────────────────
STRENGTH_WEIGHTS = {'uniqueness': 0.35, 'distinctiveness': 0.40,
                    'proof_of_use': 0.25}
CONFLICT_DRAG = 0.40
# Never printed, never disclosed in client-facing copy — a published floor
# reads as rigged. Internal only, same as the Industry Report.
MASTER_FLOOR = 41

GENERIC_WORDS = {
    'uk', 'gb', 'ltd', 'limited', 'plc', 'llp', 'group', 'holdings', 'co',
    'company', 'the', 'and', 'of', 'services', 'service', 'solutions',
    'consulting', 'consultancy', 'management', 'partners', 'associates',
    'international', 'global', 'national', 'direct', 'online', 'digital',
    'pro', 'plus', 'premier', 'premium', 'quality', 'first', 'best',
    'smart', 'easy', 'simple', 'express', 'sustainable', 'green', 'eco',
    'new', 'modern', 'classic', 'london', 'british', 'england', 'scotland',
    'wales',
}


def _clamp(x):
    return max(0, min(100, round(x)))


def uniqueness(n_similar: int) -> int:
    """95 when the register shows nothing like it, sliding down as the crowd
    grows; softens after the first dozen — 20 vs 200 is a difference of
    degree, not of kind."""
    return _clamp(95 - min(n_similar * 0.9, 75))


def conflicts(high: int, medium: int, low: int) -> int:
    """Conflict pressure as headroom (100 = clear, 0 = blocked).

    The per-band caps are the anti-scaremongering rule: severity dominates
    volume. Low can never cost more than 10 points in total, Medium 25, while
    genuine High risks properly hurt (20 each, up to 60)."""
    penalty = min(high * 20, 60) + min(medium * 2.5, 25) + min(low * 0.08, 10)
    return _clamp(100 - penalty)


def distinctiveness(name: str, sector_terms=()) -> int:
    """Share of the name that is NOT ordinary or sector trade language.

    Scaled 20..95 deliberately: a name made entirely of descriptive words is
    not hopeless (stylisation, logos and acquired distinctiveness are all
    routes we use every week), and an invented word is not a guarantee."""
    words = [w for w in re.split(r'[^a-z0-9]+', (name or '').lower()) if w]
    if not words:
        return 50
    sector_vocab = set()
    for t in sector_terms or []:
        sector_vocab.update(re.split(r'[^a-z0-9]+', str(t).lower()))
    sector_vocab.discard('')
    descriptive = sum(1 for w in words
                      if w in GENERIC_WORDS or w in sector_vocab)
    share_distinctive = 1 - descriptive / len(words)
    return _clamp(20 + share_distinctive * 75)


def proof_of_use(years: float | None) -> int:
    """0 years = 25 — starting out isn't a fault, it's just less evidence."""
    if not years or years <= 0:
        return 25
    return _clamp(25 + min(years, 10) / 10 * 70)


def strengths_composite(scores: dict) -> int:
    return round(sum(scores[k] * w for k, w in STRENGTH_WEIGHTS.items()))


def master(scores: dict) -> int:
    """Strengths set the ceiling; conflict pressure drags it down; floored."""
    positive = strengths_composite(scores)
    drag = (100 - scores['conflicts']) * CONFLICT_DRAG
    return max(MASTER_FLOOR, round(positive - drag))


# ── Free Search inputs ─────────────────────────────────────────────────────
# The guided questions ask "new or existing brand?" and, if existing, how long
# it has been in use. Those are bands, not dates, so each maps to the middle of
# its range — deliberately conservative at the top end (5+ years reads as 7,
# not 10) because proof of use is an evidence claim we would have to stand up.
DURATION_YEARS = {'under1': 0.5, '1to3': 2.0, '3to5': 4.0, '5plus': 7.0}


def years_from_answers(brand_age, in_use_for):
    """Turn the guided-question answers into years, or None if unanswered.

    Takes whatever the public payload contained — these arrive from an
    anonymous browser, so a list or a number where a string was expected is a
    thing that happens, and it must return None rather than raise.
    """
    age = (brand_age if isinstance(brand_age, str) else '').strip().lower()
    if age == 'new':
        return 0.0
    if age == 'existing':
        dur = (in_use_for if isinstance(in_use_for, str) else '').strip()
        return DURATION_YEARS.get(dur, 1.0)
    return None


def compute(*, name: str, n_similar: int, high: int, medium: int, low: int,
            years: float | None = None, sector_terms=()) -> dict:
    s = {
        'uniqueness': uniqueness(n_similar),
        'conflicts': conflicts(high, medium, low),
        'distinctiveness': distinctiveness(name, sector_terms),
        'proof_of_use': proof_of_use(years),
    }
    return {'scores': s, 'master': master(s)}


# ── the spoken opinion ─────────────────────────────────────────────────────
# Four tiers. The wording is the point: it has to be honest enough to be worth
# reading and calm enough that a crowded word search doesn't send someone away
# thinking their brand is finished. Every tier ends by pointing at a human,
# per the scoring rules — "if in any doubt, book an appointment".

# WHAT THE NUMBER MEASURES — and why the label had to change.
#
# It scores the NAME: how alone it is on the register, how protectable the
# wording is, how long it has been used, dragged down by conflict pressure.
# It has never been a probability of getting a registration.
#
# Labelled "chance" (from the design comp) or "viable" (from the Industry
# Report), a strong brand that belongs to somebody else read as a promise —
# CARLSBERG scoring 79% "chance" against a registered CARLSBERG is the tool
# contradicting itself in public. Labelled BRAND STRENGTH the same 79 is
# simply true: it is a strong name, and it is taken. The number stays, the
# claim it makes shrinks to one it can support.
SCORE_LABEL = 'brand strength'
SCORE_CAPTION = ('How strong the name looks in its own right \u2014 not '
                 'whether this exact word mark is available.')

_HEADLINES = {
    'strong':  'This looks like a strong candidate',
    'good':    'This looks promising',
    'mixed':   'This is worth a closer look before you file',
    'crowded': 'This one needs advice before you file',
    'review':  'That word mark is already taken \u2014 but the brand may not be',
}


def _norm(t: str) -> str:
    """Casefold and strip everything that isn't a letter or digit.

    'CARLSBERG', 'Carlsberg' and 'Carlsberg.' are the same mark for this
    purpose. 'CARLSBERG BRITVIC' is not.
    """
    return re.sub(r'[^a-z0-9]+', '', (t or '').lower())


def identical_live(name: str, marks) -> dict | None:
    """The first LIVE mark whose text is identical to what was searched.

    Scoring decisions D3 and D12. A live identical mark is conflict 9 /
    rights 9 — the strongest finding the model has — and because the client
    has not filed anything, any live identical registration is necessarily
    senior to them. D12 is explicit that this is "not a weaker opportunity,
    it is a different situation": the output is "Review — client may be at
    risk", NOT a risk band and NOT a score.

    That is why this returns before any arithmetic happens. A composite that
    scored CARLSBERG at 79% against a registered CARLSBERG was not a tuning
    problem — averaging cannot express "this specific door is shut".
    """
    want = _norm(name)
    if not want:
        return None
    for m in marks or []:
        if not isinstance(m, dict):
            continue
        if (m.get('status') or '').strip().lower() not in ('registered', 'pending'):
            continue
        if _norm(m.get('mark') or m.get('mark_text')) == want:
            return m
    return None


def _tier(master_score: int, high: int) -> tuple[str, bool]:
    """Returns (tier, capped_by_conflict).

    Fail closed: one genuine High Risk conflict caps the verdict, whatever the
    arithmetic says. A strongly distinctive name with ten years of use scores
    93 before conflicts and still scores 76 with two High Risk marks against
    it — the drag is real but it is not enough, because a named blocker is a
    specific obstacle and general strength does not cancel it.

    The second return value says whether that override actually bit. It has to
    be surfaced, not hidden: a 76% dial sitting next to "needs advice before
    you file" reads as a contradiction unless the page explains which of the
    two is doing the work.
    """
    unc = ('strong' if master_score >= 75 else
           'good' if master_score >= 60 else
           'mixed' if master_score >= 50 else 'crowded')
    if high >= 2:
        tier = 'crowded'
    elif high == 1:
        tier = 'mixed' if master_score >= 55 else 'crowded'
    else:
        return unc, False
    return tier, _RANK[tier] < _RANK[unc]


_RANK = {'crowded': 0, 'mixed': 1, 'good': 2, 'strong': 3}
# The master dial takes its colour from the TIER, not from its own number, so
# the override can never leave a green dial next to a red-flag headline.
# These MUST stay in step with --dial-good / --dial-mid / --dial-poor in
# freesearch/web/braudit.css. They are sent in the payload rather than chosen
# in the browser, so the emailed report and the screen cannot disagree.
#
# The amber moved from #E69500 to #9A7015 on 17 Aug: measured against the dial
# track (#E9EDF1) the old value was 2.06:1, i.e. the ring was barely
# distinguishable from the empty part of it. 3:1 is the WCAG minimum for a
# graphic that carries meaning, and this one carries the whole score.
DIAL_GOOD = '#1F8A5B'
DIAL_MID = '#9A7015'
DIAL_POOR = '#C0392B'


_TIER_COLOUR = {'strong': DIAL_GOOD, 'good': DIAL_GOOD,
                'mixed': DIAL_MID, 'crowded': DIAL_POOR,
                # Amber, not red. The name is strong; the route is blocked.
                # Red would say the brand is worthless, which is not what an
                # exact match means.
                'review': DIAL_MID}


def _body(tier: str, *, name: str, high: int, medium: int, low: int,
          total: int, scores: dict, capped: bool = False,
          identical: dict | None = None) -> list[str]:
    """Plain-English paragraphs. Each one names the actual evidence, so the
    verdict reads as a reading of THEIR result rather than a stock message."""
    out = []
    n = name or 'your name'

    # Exact match takes the whole commentary. Jonathan, 18 Aug: say the word
    # mark does not look likely, say the brand may still have somewhere to go,
    # and ask them to call before deciding anything.
    if identical:
        owner = (identical.get('company_name')
                 or identical.get('owner_name') or '').strip()
        who = f', held by {owner}' if owner else ''
        out.append(f'{n} is already on the register as a word mark{who}, '
                   f'spelled exactly as you have searched it. On that basis a '
                   f'word-mark application for the same name does not look '
                   f'likely to succeed.')
        out.append('That is not the end of it. A brand is more than its word '
                   'mark, and there may well be other elements worth '
                   'protecting \u2014 a logo, a tagline, a stylised version of '
                   'the name, or a specification that does not overlap with '
                   'theirs. Marks that are vulnerable for non-use are another '
                   'route we look at.')
        out.append('The score above measures the name on its own merits, and '
                   'on that it does well \u2014 it is a strong name. It is '
                   'simply one that somebody else got to first.')
        out.append('Please call us to discuss before you make any decisions. '
                   'This is a word search of the UK register, not a legal '
                   'opinion, and this is exactly the situation where five '
                   'minutes with an adviser saves a great deal.')
        return out

    if total == 0:
        out.append(f'We found nothing on the UK register that conflicts with '
                   f'{n}. That is the best starting position there is.')
    elif high:
        out.append(f'We found {high} mark{"s" if high != 1 else ""} on the '
                   f'register close enough to {n} to be a real obstacle — '
                   f'those are the ones that decide whether an application '
                   f'goes through.')
    elif medium:
        out.append(f'Nothing on the register looks like a straight blocker. '
                   f'{medium} mark{"s are" if medium != 1 else " is"} close '
                   f'enough to be worth reading properly before you file.')
    else:
        out.append(f'We flagged {total} similar-looking '
                   f'mark{"s" if total != 1 else ""}, but none of them scored '
                   f'above low risk. A word search casts a wide net, and a '
                   f'long list of low-risk hits is normal rather than '
                   f'alarming.')

    d = scores['distinctiveness']
    if d < 45:
        out.append('The bigger factor here is the wording itself. Names built '
                   'from everyday trade words are harder to register as words '
                   'alone — but a logo, styling, or evidence of use are all '
                   'routes we use every week.')
    elif d >= 75:
        out.append('The wording works in your favour: it is distinctive '
                   'rather than descriptive, which is exactly what the '
                   'examiner is looking for.')

    if scores['proof_of_use'] >= 60:
        out.append('You have also been using the name for a while, which '
                   'builds unregistered rights and helps answer the "who was '
                   'first?" question if it is ever asked.')

    if capped:
        # Says out loud why a healthy-looking score sits under a cautious
        # headline, rather than leaving the visitor to spot the gap.
        out.append('The score above measures the name on its own merits, and '
                   'on that it does well. We have still marked this one for '
                   'advice, because a close mark on the register is a '
                   'specific obstacle and a strong name does not cancel it.')

    out.append('This is a word search of the UK register, not a legal '
               'opinion — if you are in any doubt, book an appointment and '
               'one of our advisers will go through it with you.')
    return out


def verdict(summary: dict, *, name: str, years: float | None = None,
            sector_terms=(), marks=()) -> dict:
    """The whole opinion block, ready to render.

    `summary` is the free-search summary dict (total_flagged / high / medium /
    low). Returns the score, the four dials, the headline, the body copy, and
    which next step to lead with.
    """
    high = int(summary.get('high') or 0)
    medium = int(summary.get('medium') or 0)
    low = int(summary.get('low') or 0)
    total = int(summary.get('total_flagged') or 0)

    # D12 first, before anything is scored. An identical live mark is not a
    # point on a scale — it is a different question, and answering it with a
    # percentage is worse than saying nothing.
    same = identical_live(name, marks)

    v = compute(name=name, n_similar=total, high=high, medium=medium,
                low=low, years=years, sector_terms=sector_terms)
    tier, capped = _tier(v['master'], high)
    # An identical live mark takes over the verdict but NOT the number.
    # Jonathan, 18 Aug: the word mark may be gone while the brand still has
    # somewhere to go — a logo, a tagline, a different specification — so
    # withholding the score reads as the tool failing rather than answering.
    # What the number MEANS had to change for that to be honest: it scores the
    # name on its own merits, which is why a strong brand owned by somebody
    # else still scores well. See SCORE_CAPTION.
    if same:
        tier, capped = 'review', True


    # Which next step leads. Jonathan, 11 Aug: "most people proceed to
    # application rather than request an audit" — so on a clean result we get
    # out of their way and lead with the application. Where the register is
    # genuinely messy, the audit leads, because that is the honest answer and
    # it is also the one that saves them an application fee.
    lead = 'audit' if tier in ('mixed', 'crowded', 'review') else 'application'

    return {
        'score': v['master'],
        'show_score': True,
        'scores': v['scores'],
        'tier': tier,
        'colour': _TIER_COLOUR[tier],
        'capped_by_conflict': capped,
        'headline': _HEADLINES[tier],
        'body': _body(tier, name=name, high=high, medium=medium, low=low,
                      total=total, scores=v['scores'], capped=capped,
                      identical=same),
        'caption': SCORE_CAPTION,
        'review_mark': ({'mark': same.get('mark') or same.get('mark_text') or '',
                         'status': same.get('status') or '',
                         'owner': (same.get('company_name')
                                   or same.get('owner_name') or '').strip()}
                        if same else None),
        'lead': lead,
        # Each dial carries its OWN colour, derived from its own value.
        #
        # Only the master takes the tier colour, because only the master is
        # the verdict. A sub-dial has to be read on its own merits: a name can
        # score 95 for distinctiveness on a result we have banded crowded, and
        # painting that dial red would say something untrue about the wording.
        # Sending colour per dial removes the guess — a renderer reaching for
        # the top-level `colour` for all four would tint every strength the
        # tier colour and misreport three of them.
        'dials': [
            _dial('uniqueness', 'Uniqueness',
                  'How alone your name is on the register', v['scores']),
            _dial('distinctiveness', 'Distinctiveness',
                  'How protectable the wording is', v['scores']),
            _dial('proof_of_use', 'Proof of use',
                  'Time in genuine use', v['scores']),
            _dial('conflicts', 'Conflicts',
                  'Headroom — how little the similar marks bite', v['scores'],
                  kind='negative'),
        ],
    }


def _value_colour(v: int) -> str:
    """Green / amber / red read off the value itself — the sub-dial rule."""
    return DIAL_GOOD if v >= 70 else (DIAL_MID if v >= 45 else DIAL_POOR)


def _dial(key: str, label: str, sub: str, scores: dict,
          kind: str = 'strength') -> dict:
    # `conflicts` is scored as HEADROOM (100 = clear), so the same
    # green-is-good rule applies to it and no inversion is needed.
    val = scores[key]
    return {'key': key, 'label': label, 'sub': sub, 'value': val,
            'kind': kind, 'colour': _value_colour(val)}
