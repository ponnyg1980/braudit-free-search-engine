"""Query Runs SQL retriever — the fast, class-complete candidate source.

Query Runs (`/api/v2/query-runs`) returns, in ONE ~1.5s call, every mark whose
verbal element matches the search, together with its classes, status, mark
type and applicant. That replaces the REST search+hydrate dance (which was ~8s
and lost class matches because classes lived only on the per-record detail).

Only `_candidate_records` changes when this is used; scoring, adapters, the
serializer, the gate and the wizard are all untouched.

SQL SAFETY
----------
Query Runs is read-only and single-statement (write/DDL keywords are rejected
server-side), but the search stem still comes from user input, so we sanitise
it hard: strip everything except letters, digits, spaces and hyphens, then
escape. The stem can only ever appear inside an `ILIKE '<stem>%'` literal.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from .models import MarkRecord
from .adapters import canon_status

_DEPLOY = Path(__file__).resolve().parents[1] / 'deploy-v2-hotfix'
if str(_DEPLOY) not in sys.path:
    sys.path.insert(0, str(_DEPLOY))
from temmy import TemmyQueryRunsClient, TemmyError  # type: ignore  # noqa: E402

log = logging.getLogger(__name__)

MAX_ROWS = 1000          # candidate ceiling per search (text match narrows hard)
UKIPO_CASE_URL = 'https://trademarks.ipo.gov.uk/ipo-tmcase/page/Results/1/{}'


def sanitise_stem(stem: str) -> str:
    """Keep only safe characters, then SQL-escape single quotes. The result is
    only ever interpolated inside an ILIKE string literal."""
    s = re.sub(r"[^A-Za-z0-9 \-]", '', stem or '').strip()
    return s.replace("'", "''")


# Trigram floor (D17, tmh-scoring): 0.25, not pg_trgm's 0.30 default - HAILAFLO/HEALAFLOW measures 0.267.
TRGM_FLOOR = 0.25
TRGM_MIN_LEN = 5


def build_search_sql(stems: list[str], *, limit: int = MAX_ROWS, heads: list[str] | None = None) -> str:
    """One SELECT returning class-complete candidate rows.

    17 Oct 2026 (Jonathan: a free search for TEMMY returned nothing, while TEMMT, TOMMY, TAMMY and TIMMY are
    live in class 42). It used to be PREFIX ONLY (`ILIKE 'stem%'`): a mark that differs anywhere in the first
    letters never reached the scorer. Blocking must never be narrower than the scorer (D17), so three nets now,
    like the audit's temmy_source:
      * prefix of the stem (as before: MOMENT -> MOMENTUM),
      * the whole head word anywhere in the mark (substring),
      * trigram similarity to the whole head word >= 0.25 for words of 5+ letters, through the trigram index
        (`%` operator with set_limit(0.25) - measured ~7 s vs ~10 s for a similarity() scan).
    Ordered substring-first then by similarity, so the LIMIT keeps the closest, never an arbitrary slice.
    Assessment is unchanged: scoring still decides what is shown."""
    clean = [sanitise_stem(s) for s in stems if sanitise_stem(s)]
    if not clean:
        return ''
    hs = [sanitise_stem(h).upper() for h in (heads or []) if sanitise_stem(h)]
    conds = [f"m.verbal_element_text ILIKE '{s}%'" for s in clean]
    conds += [f"upper(m.verbal_element_text) LIKE '%{h}%'" for h in hs]
    trg = [h for h in hs if len(h.replace(' ', '')) >= TRGM_MIN_LEN]
    conds += [f"upper(m.verbal_element_text) % '{h}'" for h in trg]
    sim = (f"GREATEST({', '.join(f'similarity(upper(m.verbal_element_text), {chr(39)}{h}{chr(39)})' for h in hs)})"
           if len(hs) > 1 else (f"similarity(upper(m.verbal_element_text), '{hs[0]}')" if hs else "0"))
    sub = (' OR '.join(f"upper(m.verbal_element_text) LIKE '%{h}%'" for h in hs)) or 'false'
    lim = f"WITH lim AS MATERIALIZED (SELECT set_limit({TRGM_FLOOR}) AS l)\n" if trg else ''
    frm = "lim, marks m" if trg else "marks m"
    gate = "lim.l > 0 AND " if trg else ''
    return f"""
{lim}SELECT t.application_number, t.status, m.verbal_element_text, m.feature AS mark_type,
       array_agg(DISTINCT nc.number) FILTER (WHERE nc.number IS NOT NULL) AS classes,
       (array_agg(DISTINCT a.name))[1] AS applicant_name,
       (array_agg(DISTINCT a.company_number))[1] AS company_number,
       max(CASE WHEN ({sub}) THEN 1 ELSE 0 END) AS sub_hit,
       max({sim}) AS sim
FROM {frm}
JOIN trademarks t ON t.id = m.trademark_id
LEFT JOIN nice_class_trademarks nct ON nct.trademark_id = t.id
LEFT JOIN nice_classes nc ON nc.id = nct.nice_class_id
LEFT JOIN applicant_trademarks apt ON apt.trademark_id = t.id
LEFT JOIN applicants a ON a.id = apt.applicant_id
WHERE {gate}({' OR '.join(conds)})
GROUP BY t.application_number, t.status, m.verbal_element_text, m.feature
ORDER BY sub_hit DESC, sim DESC
LIMIT {int(limit)}
""".strip()


def _int_list(v) -> list[int]:
    """A Postgres int[] arrives as a Python list or a '{1,2}' string."""
    if isinstance(v, list):
        out = []
        for x in v:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                pass
        return sorted(set(out))
    s = str(v or '').strip('{}')
    return sorted({int(p) for p in re.split(r'[,\s]+', s) if p.strip().isdigit()})


def record_from_row(row: dict) -> MarkRecord | None:
    """Build a MarkRecord from a Query Runs result row.

    Classes are present here (unlike the REST search item), so no per-record
    hydration is needed for scoring or class filtering.
    """
    mark_text = str(row.get('verbal_element_text') or '').strip()
    if not mark_text:
        return None
    app_no = str(row.get('application_number') or '')
    raw_status = str(row.get('status') or '')
    return MarkRecord(
        mark_text=mark_text,
        mark_type=_norm_type(row.get('mark_type')),
        status=canon_status(raw_status),
        status_display=raw_status,
        classes=_int_list(row.get('classes')),
        application_number=app_no,
        owner_name=str(row.get('applicant_name') or '').strip(),
        source_url=UKIPO_CASE_URL.format(app_no) if app_no else '',
        source='queryruns',
    )


def _norm_type(feature) -> str:
    f = str(feature or '').lower()
    if not f:
        return ''
    if 'combin' in f or ('fig' in f and 'text' in f):
        return 'Combined'
    if 'figur' in f or 'device' in f or 'image' in f:
        return 'Figurative'
    if 'stylis' in f or 'styliz' in f:
        return 'Stylized'
    if f == 'word' or 'verbal' in f:
        return 'Word'
    return str(feature)


class QueryRunsRetriever:
    """Thin wrapper: build SQL, run it, collect all rows as MarkRecords."""

    def __init__(self, query_runs_key: str, *, base_url: str | None = None):
        kwargs = {'query_runs_key': query_runs_key.strip()}
        if base_url:
            kwargs['base_url'] = base_url.strip()
        self._qr = TemmyQueryRunsClient(**kwargs)

    def search(self, stems: list[str], *, limit: int = MAX_ROWS, heads: list[str] | None = None
               ) -> tuple[list[MarkRecord], bool]:
        """Return (records, truncated)."""
        sql = build_search_sql(stems, limit=limit, heads=heads)
        if not sql:
            return [], False
        manifest = self._qr.submit(sql=sql, page_size=limit, preview_limit=100)
        query_id = manifest.get('query_id')
        total = int((manifest.get('pagination') or {}).get('total', 0) or 0)
        rows = list(manifest.get('preview') or [])
        # Pull the remaining rows (beyond the 100-row preview) via pages.
        if query_id and total > len(rows):
            try:
                page = 1
                while len(rows) < total and len(rows) < limit:
                    body = self._qr.get_page(query_id, page)
                    items = body.get('items') or []
                    if not items:
                        break
                    rows = items if page == 1 else rows + items
                    if len(items) < 1:
                        break
                    page += 1
            except TemmyError:
                log.warning('query-runs paging failed; using preview only',
                            exc_info=True)
            finally:
                try:
                    self._qr.delete(query_id)
                except Exception:
                    pass
        records = [r for r in (record_from_row(x) for x in rows) if r]
        # the cap reached means the closest `limit` were kept - say so (D17: never silently)
        return records, total > len(records) or len(records) >= limit
