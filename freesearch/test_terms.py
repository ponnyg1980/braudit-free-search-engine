"""python3 -m freesearch.test_terms  (handover T1-T4 rendering rules)"""
import re, datetime as dt
from freesearch import terms as T

def _t(r): return re.sub('<[^>]+>', '', r['html'])

def run():
    t = _t(T.render(worldwide=False))
    assert 'within 5 working days' in t
    assert 'register and the national registers of the countries you select.' in t
    assert 'within 2 working days' in _t(T.render(worldwide=False, turnaround_days='2'))
    assert 'within 1 working day of' in _t(T.render(worldwide=False, turnaround_days=1))
    for bad in ('7', '6', '0.5', 'abc', '-1'):
        try:
            T.render(worldwide=False, turnaround_days=bad)
            raise AssertionError('turnaround %r accepted' % bad)
        except T.TermsError:
            pass
    r = T.render(worldwide=True, current=['GB', 'US'], planning=['US', 'AU', 'EM'])
    assert 'register and the national registers of: Australia, United States.' in _t(r)
    assert r['jurisdictions'] == ['GB', 'US', 'AU', 'EM']
    try:
        T.render(worldwide=True)
        raise AssertionError('worldwide with no countries accepted')
    except T.TermsError:
        pass
    assert 'WIPO Madrid) register.' in _t(T.render(worldwide=True, current=['GB'], planning=['EM', 'WO']))
    assert T.add_working_days(dt.date(2026, 12, 23), 5) == dt.date(2027, 1, 4)
    assert T.add_working_days(dt.date(2026, 10, 2), 5) == dt.date(2026, 10, 9)
    h = T.render(worldwide=False)['html']
    assert h.count('href="https://www.thetrademarkhelpline.com/terms-and-conditions/" target="_blank"') == 2
    a = T.render(worldwide=False); b = T.render(worldwide=False)
    assert a['sha256'] == b['sha256']
    print('terms tests OK', T.holidays_digest())

if __name__ == '__main__':
    run()
