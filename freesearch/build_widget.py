"""Regenerate the data embedded in the class-assistant widget.

WHY THIS EXISTS

The widget ships with TAXONOMY and PROFILES inlined so the "By industry" route
needs no round-trip. That's good for speed and it means the demo IS the product
— but only if the inlined data is generated from the engine.

Every bug we've hit on this widget (fashion returning nothing, accountant
returning class 5 skincare, the old 79-type list) was the same bug: hand-written
data in the page drifting from the real engine. So the data is never written by
hand. It is generated here, from `taxonomy.SECTORS` and `sic_engine`, and
injected into the two `const` lines in the HTML.

Run after any seed refresh or taxonomy change:

    python -m freesearch.build_widget          # rebuild + report
    python -m freesearch.build_widget --check  # verify only, non-zero on drift
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import taxonomy as tx
from .bands import TIER_LABELS
from .sic_engine import map_sic_codes
from .nice_labels import short as nice_short

WEB = Path(__file__).resolve().parent / 'web'
WIDGET = WEB / 'class-assistant.html'

# The widget is embedded on client sites, so payload is a feature. It carries
# only what it renders: the classes worth showing, and terms for the ones the
# client is actually likely to pick. The full term set stays server-side in
# data/sic_seed.json and is what the audit/application flow reads.
TERMS_PER_CLASS = 5
CLASSES_PER_TYPE = 8
TERMS_FOR_TIERS = ('a', 'b', 'c')     # not 'A few have this'


def _nice_lookup() -> dict:
    """{class: {l: short label, h: official heading}} for every Nice class."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'))
        from nice_classes import NICE_HEADINGS  # type: ignore
    except Exception:
        NICE_HEADINGS = {}
    return {str(n): {'l': nice_short(n), 'h': NICE_HEADINGS.get(n, '')}
            for n in range(1, 46)}


def build_taxonomy() -> dict:
    return {
        'sectors': {sec: list(types) for sec, types in tx.SECTORS.items()},
        'aliases': {k: list(v) for k, v in tx.ALIASES.items()},
    }


def build_profiles() -> dict:
    """Precompute every business type's banded classes + terms."""
    profiles: dict = {}
    for sector, types in tx.SECTORS.items():
        for name, codes in types.items():
            m = map_sic_codes([str(c) for c in codes])
            entries = []
            for c in m['classes'][:CLASSES_PER_TYPE]:
                entries.append({
                    'n': c['nice_class'],
                    't': c['tier'],
                    'b': c['band'],                     # display label
                    'f': c.get('frequency'),
                    'sh': c.get('share'),               # for "82% of them"
                    'tm': ([{'x': t['text'], 'b': t.get('band', 'd')}
                            for t in (c.get('terms') or [])[:TERMS_PER_CLASS]]
                           if c['tier'] in TERMS_FOR_TIERS else []),
                })
            profiles[name] = {'s': sector, 'm': m['method'], 'c': entries}
    return {
        'nice': _nice_lookup(),
        'profiles': profiles,
        'acts': {k: sorted(v['allows']) for k, v in tx.ACTIVITIES.items()},
    }


def _replace_const(html: str, name: str, value) -> str:
    marker = f'const {name} = '
    i = html.find(marker)
    if i < 0:
        raise RuntimeError(f'{name} not found in {WIDGET.name}')
    j = html.find('\n', i)
    return html[:i] + marker + json.dumps(value, separators=(',', ':')) + ';' + html[j:]


def main(check: bool = False) -> int:
    taxonomy = build_taxonomy()
    profiles = build_profiles()

    html = WIDGET.read_text()
    # The band words live in bands.py and nowhere else — the widget is given
    # them, it does not keep its own copy.
    new = _replace_const(html, 'BANDS', TIER_LABELS)
    new = _replace_const(new, 'ACTS_UI', {
        'list': [{'key': k, 'label': v['label'], 'note': v.get('note', '')}
                 for k, v in tx.ACTIVITIES.items()],
        'expansion': {'key': tx.EXPANSION_FLAG, 'label': tx.EXPANSION_LABEL},
    })
    new = _replace_const(new, 'RECS', tx.RECOMMENDATION)
    new = _replace_const(new, 'TAXONOMY', taxonomy)
    new = _replace_const(new, 'PROFILES', profiles)

    n_types = len(profiles['profiles'])
    emp = sum(1 for p in profiles['profiles'].values() if p['m'] == 'empirical')
    withterms = sum(1 for p in profiles['profiles'].values()
                    if any(c['tm'] for c in p['c']))

    if check:
        drift = new != html
        print(f'widget {"DRIFTED — run without --check" if drift else "in sync"}')
        return 1 if drift else 0

    WIDGET.write_text(new)
    print(f'{WIDGET.name}: {len(new):,} chars')
    print(f'  business types embedded : {n_types}')
    print(f'  on empirical filing data: {emp}/{n_types}')
    print(f'  with banded terms       : {withterms}/{n_types}')
    print(f'  sectors / aliases       : {len(taxonomy["sectors"])} / {len(taxonomy["aliases"])}')
    return 0


if __name__ == '__main__':
    sys.exit(main(check='--check' in sys.argv))
