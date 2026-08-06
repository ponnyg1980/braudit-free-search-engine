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


def build_search_sql(stems: list[str], *, limit: int = MAX_ROWS) -> str:
    """One SELECT that unions all stems, returning class-complete candidate
    rows. Prefix match (`ILIKE 'stem%'`) — fast on the verbal-element index and
    enough to catch the variants that matter (MOMENT -> MOMENTUM…)."""
    clean = [sanitise_stem(s) for s in stems if sanitise_stem(s)]
    if not clean:
        return ''
    conds = ' OR '.join(f"m.verbal_element_text ILIKE '{s}%'" for s in clean)
    return f"""
SELECT t.application_number, t.status, m.verbal_element_text, m.feature AS mark_type,
       array_agg(DISTINCT nc.number) FILTER (WHERE nc.number IS NOT NULL) AS classes,
       (array_agg(DISTINCT a.name))[1] AS applicant_name,
       (array_agg(DISTINCT a.company_number))[1] AS company_number
FROM marks m
JOIN trademarks t ON t.id = m.trademark_id
LEFT JOIN nice_class_trademarks nct ON nct.trademark_id = t.id
LEFT JOIN nice_classes nc ON nc.id = nct.nice_class_id
LEFT JOIN applicant_trademarks apt ON apt.trademark_id = t.id
LEFT JOIN applicants a ON a.id = apt.applicant_id
WHERE ({conds})
GROUP BY t.application_number, t.status, m.verbal_element_text, m.feature
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

    def search(self, stems: list[str], *, limit: int = MAX_ROWS
               ) -> tuple[list[MarkRecord], bool]:
        """Return (records, truncated)."""
        sql = build_search_sql(stems, limit=limit)
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
        return records, total > len(records)
