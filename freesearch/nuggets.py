"""'Did you know' cards for the Free Search waiting moments.

COPIED, deliberately, from goal3-industry-report/nuggets.py rather than
imported. The engine deploys as a self-contained service and must not reach
into a sibling project at runtime. Jonathan's copy rules travel with it:

  * nothing states or implies a guarantee; the 98% and the 4,000 are
    historical fact, phrased as history
  * every card is a real, checkable point about trademarks -- the waiting
    screen is the one moment we have someone's full attention, so it teaches
    rather than sells

WHAT IS DIFFERENT HERE
----------------------
The Industry Report runs a pure-CSS carousel on fixed nth-child delays,
because Python is blocked mid-build in Streamlit and no JS could have driven
it. Free Search has no such constraint, so rotation runs in the browser and
can therefore run for exactly as long as the work takes, LOOP if it takes
longer, and stop the moment the answer lands.

Split by wait rather than by phase (Jonathan, 18 Aug): the AI class search is
the longer wait and takes the first six; the register search takes the last
two, so nobody sees the same card twice in one session.
"""
from __future__ import annotations

NUGGETS = [
    ('Your brand is an asset in its own right',
     'Trademarks are saleable assets. A brand can hold value entirely separately from the company that uses it — and as the registered owner, that asset is yours.'),

    ("You don't have to own it through your company",
     'Some people register trademarks in their own personal name, or in trust, and licence it back to their company. It protects the brand if the company ever becomes insolvent, and it gives you far stronger individual negotiating power if the company is sold.'),

    ("It's how the big platforms verify you",
     'Google, Amazon and eBay use trademark registrations to confirm you have the right to use a name, logo or tagline. Without one, proving it is a great deal harder.'),

    ("It isn't only names",
     'A trademark can be a logo, a tagline, a sound — even a smell. If it identifies you to your customers, it may be protectable.'),

    ('It can save you a fortune in legal fees',
     'Owning a trademark can save hundreds of thousands of pounds in enforcement action. It can be used to demand surrender of similar domain names, social media accounts and counterfeit listings — often without going near a court.'),

    ('Protection stops at the border',
     'Trademarks are territorial. Applying abroad means working with local attorneys in each country — we have a long-established network of affordable ones who can handle your case.'),

    ('The first five years are the strongest',
     "For the first five years you can enforce your rights without having to prove you've used the mark. It's why large organisations often re-apply for key trademarks every five years, rather than simply renewing at ten."),

    ("We've done this a few times",
     "We've registered over 4,000 trademarks since 2008. Last year our application success rate was 98%.")
]

# The AI class search -- the longest wait in the wizard at ~5s, and the moment
# the visitor has just typed a lot and is owed something to read.
AI_SEARCH = NUGGETS[:6]

# The register search before the results screen.
FREE_SEARCH = NUGGETS[6:]

# Seconds a card is held. Shorter than the Industry Report's 5s: these waits
# are ~5s not ~20s, so a 5s dwell would show one card and waste the rest.
DWELL = 3.4


def payload() -> dict:
    """Everything the browser needs, in one blob."""
    return {
        'dwell': DWELL,
        'ai_search': [{'title': t, 'text': x} for t, x in AI_SEARCH],
        'free_search': [{'title': t, 'text': x} for t, x in FREE_SEARCH],
    }
