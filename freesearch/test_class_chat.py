"""Offline tests for the mark-use class assistant (17 Oct 2026). The model is stubbed.

    python -m pytest freesearch/test_class_chat.py -q
"""
import json
import unittest

try:
    from . import class_agent as A, class_chat as C
except ImportError:                                   # pragma: no cover
    import class_agent as A, class_chat as C          # type: ignore


def first_terms(n, k=3):
    return [t['term'] for t in A.load_vocab()[n][:k]]


class Stub:
    """Replaces A._call: answers by which prompt it was given."""
    def __init__(self, classes=((9, 'core'), (42, 'core'), (16, 'possible')), questions=None):
        self.classes, self.questions, self.calls = classes, questions or [], []

    def __call__(self, system, user, **kw):
        self.calls.append(system[:40])
        if system is A._S1M:
            return json.dumps({'classes': [{'n': n, 'certainty': c, 'why': 'x'} for n, c in self.classes],
                               'flags': ['The owl alone is not descriptive.']})
        if system is A._S2M:
            return json.dumps({'core': [1], 'likely': [2], 'possible': [3]})
        if system is C._QM:
            return json.dumps({'questions': self.questions})
        if system is A._SL:
            return json.dumps({'description': 'a cartoon owl wearing glasses', 'character': True,
                               'words': '', 'depicts_goods': ''})
        raise AssertionError('unexpected prompt')


class Base(unittest.TestCase):
    def setUp(self):
        self._call = A._call
        self.stub = Stub()
        A._call = self.stub

    def tearDown(self):
        A._call = self._call

    def go(self, st, msg='', ctx=None):
        out = C.turn({'state': st, 'message': msg, **({'context': ctx} if ctx is not None else {})})
        self.assertTrue(out['ok'])
        return out


class Prefill(Base):
    def test_known_mark_is_not_asked_for_again(self):
        out = self.go({}, ctx={'word': 'TEMMY', 'has_logo': True, 'business': 'The Trademark Helpline'})
        self.assertEqual(out['state']['step'], 'role')
        self.assertIn('TEMMY', out['reply'])
        vals = [q['value'] for q in out['quick']]
        self.assertEqual(vals, ['house', 'product', 'range', 'mascot', 'slogan', 'event'])
        self.assertIn('The Trademark Helpline itself', out['quick'][0]['label'])
        self.assertTrue(out['multi'])

    def test_nothing_known_asks_what_is_protected(self):
        out = self.go({}, ctx={})
        self.assertEqual(out['state']['step'], 'what')

    def test_mascot_only_with_a_logo_slogan_only_with_words(self):
        vals = [q['value'] for q in C.role_options({'word': 'ALPHA'})]
        self.assertNotIn('mascot', vals)
        self.assertIn('slogan', vals)
        vals = [q['value'] for q in C.role_options({'has_logo': True})]
        self.assertIn('mascot', vals)
        self.assertNotIn('slogan', vals)

    def test_logo_is_described_once(self):
        out = self.go({}, ctx={'logo': 'data:image/png;base64,iVBORw0KGgo=', 'business': 'TMH'})
        self.assertEqual(out['state']['mark']['logo_desc'], 'a cartoon owl wearing glasses')
        self.assertNotIn('logo', {k for k in out['state']['mark'] if k == 'logo'})   # image never kept in state


class Flow(Base):
    def run_owl(self, answers):
        st = self.go({}, ctx={'word': 'TEMMY', 'has_logo': True, 'business': 'The Trademark Helpline',
                              'description': 'Trademark advice and a software portal for clients.'})['state']
        st = self.go(st, 'mascot|house')['state']
        self.assertEqual(st['step'], 'about_confirm')
        st = self.go(st, "That's right")['state']
        self.assertEqual(st['step'], 'seen')
        st = self.go(st, 'software|client_docs|social')['state']
        st = self.go(st, 'Merchandise for clients at some point')
        for a in answers:
            st = self.go(st['state'], a)
        return st

    def test_brief_carries_everything(self):
        out = self.run_owl([])
        b = out['state']['brief']
        for bit in ('TEMMY', 'mascot', 'app or software', 'Merchandise', 'The Trademark Helpline'):
            self.assertIn(bit, b)
        self.assertTrue(out['done'])
        self.assertEqual([c['n'] for c in out['classes']], [9, 42, 16])
        self.assertNotIn('terms', out['classes'][0])                 # clients never see terms
        self.assertEqual(out['state']['flags'], ['The owl alone is not descriptive.'])

    def test_terms_are_graded_and_from_the_vocabulary(self):
        out = self.run_owl([])
        c9 = out['state']['classes'][0]
        self.assertEqual([t['certainty'] for t in c9['terms']], ['core', 'likely', 'possible'])
        known = {t['term'] for t in A.load_vocab()[9]}
        self.assertTrue(all(t['term'] in known for t in c9['terms']))

    def test_yes_adds_a_missed_class_and_no_only_drops_possible(self):
        self.stub.questions = [{'q': 'Will the owl appear on clothing you sell?', 'add_if_yes': [25], 'drop_if_no': []},
                               {'q': 'Will you print brochures?', 'add_if_yes': [], 'drop_if_no': [16, 9]}]
        out = self.run_owl(['yes', 'no'])
        ns = [c['n'] for c in out['state']['classes']]
        self.assertIn(25, ns)            # missed class added
        self.assertNotIn(16, ns)         # possible class dropped on a clear No
        self.assertIn(9, ns)             # a core class is never dropped
        self.assertTrue(out['state']['classes'][-1]['terms'])

    def test_unsure_changes_nothing(self):
        self.stub.questions = [{'q': 'Brochures?', 'add_if_yes': [35], 'drop_if_no': [16]}]
        out = self.run_owl(['unsure'])
        self.assertEqual([c['n'] for c in out['state']['classes']], [9, 42, 16])


class StateIsValidated(Base):
    def test_invented_terms_and_values_are_dropped(self):
        st = C._clean_state({'step': 'refine', 'roles': ['house', 'hacker'], 'seen': ['software', 'x'],
                             'classes': [{'n': 9, 'certainty': 'core', 'terms': [{'term': 'made up thing'}]},
                                         {'n': 99, 'terms': []}]})
        self.assertEqual(st['roles'], ['house'])
        self.assertEqual(st['seen'], ['software'])
        self.assertEqual([c['n'] for c in st['classes']], [9])
        self.assertEqual(st['classes'][0]['terms'], [])


if __name__ == '__main__':
    unittest.main()
