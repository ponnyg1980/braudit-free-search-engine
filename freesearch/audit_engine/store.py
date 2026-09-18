"""Persistence — an AuditResult into Supabase, and the review actions on it.

The workbook was the product; now it is an export. What the engine returns
is written once into the `audit` schema (001_audit_schema.sql) and
everything downstream — triage, the magic link, monitoring deltas — reads
that.

Order of operations for a run, and why:

    1. insert the run (status running) and the plan       — evidence first
    2. insert every result, scored, review_status 'new'    — nothing dropped
    3. audit.apply_learned(run)                            — portfolio + learned exclusions
    4. audit.link_previous(run)                            — monitoring lineage
    5. run → complete | held                               — the canary decides

Step 3 is what makes "staff are terrible at supplying exclusions" stop
mattering: whatever was excluded last time, with whatever reason, is
excluded again before anyone looks.

Connection: AUDIT_DB_URL (or SUPABASE_DB_URL) in secrets.env / environment.
The pooler URL from the Supabase dashboard works; the DSN is never logged.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_HERE = Path(__file__).resolve().parent

_CHANNEL = {"Search Engine": "web", "Social": "social", "Marketplace": "marketplace",
            "Domains": "domain", "Companies": "company", "Registers": "trademark",
            "Trademarks": "trademark"}


def _dsn() -> str:
    for k in ("AUDIT_DB_URL", "SUPABASE_DB_URL"):
        if os.environ.get(k):
            return os.environ[k]
    for p in (_HERE.parent / "secrets.env", _HERE / "secrets.env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in ("AUDIT_DB_URL", "SUPABASE_DB_URL"):
                        return v.strip().strip('"').strip("'")
    raise RuntimeError("AUDIT_DB_URL not set (environment or secrets.env)")


def _jsonable(o):
    if is_dataclass(o):
        return {k: _jsonable(v) for k, v in asdict(o).items()}
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    return o


def _office_platform(code: str) -> str:
    try:
        from .signa import OFFICE_NAMES
        return OFFICE_NAMES.get(code, code)
    except Exception:
        return code


# ---------------------------------------------------------------------------
# result rows → one shape
# ---------------------------------------------------------------------------

def _tm_row(r) -> dict:
    return dict(
        channel="trademark", platform=_office_platform(r.office), kind="word",
        external_ref=r.app_number, dedupe_key=f"tm:{r.office}:{(r.app_number or '').upper()}",
        title=r.mark_text, url=None, status=r.status, owner=r.owner, classes=r.classes,
        goods=r.goods, image_url=r.image_url or None, source=r.source, found_by=list(r.found_by),
        dates={"filing": r.filing_date, "registration": r.registration_date, "expiry": r.expiry_date},
        detail={"mark_type": r.mark_type, "owner_country": r.owner_country,
                "filing_route": r.filing_route, "office": r.office,
                "international_ref": getattr(r, "international_ref", "") or None,
                # The two-layer assessment, kept whole. D3: conflict and rights
                # are two numbers and never merge. D8: the evidence level is
                # NAMED on every row, so "classes only — cited specification
                # missing" is distinguishable from a real specification match
                # (the two have different remedies). D12 seniority rides here
                # too rather than being flattened into the band.
                "goods_band": (r.goods_similarity or {}).get("band"),
                "shared_goods_terms": (r.goods_similarity or {}).get("shared_terms"),
                "goods_why": (r.goods_similarity or {}).get("explanation"),
                "evidence_level": (r.goods_similarity or {}).get("evidence"),
                **{k: v for k, v in (r.assessment or {}).items()
                   if k in ("conflict", "rights", "mark_tier", "trade_tier",
                            "mark_reason", "trade_reason", "rights_reason",
                            "client_is_senior", "live")}},
        score=r.score, band=r.band, explanation=r.explanation,
        excluded=(r.exclusion_status == "excluded_initial_asset"),
    )


def _image_candidate_row(r) -> dict:
    d = _tm_row(r)
    d.update(kind="image", band=d["band"] or "Not Assessed", score=d["score"] or 0,
             explanation=d["explanation"] or "device-only Vienna match — visual comparison pending")
    d["hold"] = True
    return d


def _company_row(r) -> dict:
    return dict(
        channel="company", platform="Companies House (UK)", kind="word",
        external_ref=r.number, dedupe_key=f"co:gb:{(r.number or '').upper()}",
        title=r.name, url=r.url, status=r.status, owner=None, classes=None, goods=None,
        image_url=None, source=r.source, found_by=list(r.found_by),
        dates={"incorporated": r.incorporated, "dissolved": r.dissolved},
        detail={"company_type": r.company_type, "sic_codes": r.sic_codes, "sic_sectors": r.sic_sectors,
                "address": r.address, "jurisdiction": r.jurisdiction, "officers": r.officers,
                "previous_names": r.previous_names, "enriched": r.enriched},
        score=r.score, band=r.band, explanation=r.explanation,
        excluded=(r.exclusion_status == "excluded_initial_asset"),
    )


def _domain_row(r) -> dict:
    return dict(
        channel="domain", platform="Registrars (RDAP)", kind="word",
        external_ref=r.domain, dedupe_key=f"dom:{r.domain.lower()}",
        title=r.page_title or r.domain, url=r.final_url or (f"http://{r.domain}/" if r.resolves else None),
        status=r.liveness, owner=r.registrar or None, classes=None, goods=None, image_url=None,
        source=r.source, found_by=list(r.found_by),
        dates={"created": r.created, "expires": r.expires, "updated": r.updated},
        detail={"registered": r.registered, "rdap_status": r.status, "nameservers": r.nameservers,
                "resolves": r.resolves, "ip": r.ip, "mx": r.mx_present, "http_status": r.http_status,
                "redirects_to": r.redirects_to},
        score=r.score, band=r.band, explanation=r.explanation,
        excluded=(r.exclusion_status == "excluded_initial_asset"),
    )


def _serp_row(r) -> dict:
    ch = _CHANNEL.get(r.channel, "web")
    return dict(
        channel=ch, platform=r.platform, kind=r.kind,
        external_ref=r.url, dedupe_key=f"{ch}:{r.platform}:{r.dedupe_key}",
        title=r.title, url=r.url, status=None, owner=r.seller or None, classes=None, goods=None,
        image_url=r.image_url or None, source=f"Serper /{r.endpoint}", found_by=list(r.found_by),
        dates={}, detail={"snippet": r.snippet, "position": r.position, "source_domain": r.source_domain,
                          "price": r.price, "seller": r.seller, "rating": r.rating,
                          "query": r.query, "endpoint": r.endpoint},
        score=r.score, band=r.band, explanation=r.explanation,
        excluded=(r.exclusion_status == "excluded_initial_asset"),
    )


def rows_for(result) -> list[dict]:
    out = [_tm_row(r) for r in result.rows]
    out += [_image_candidate_row(r) for r in result.image_candidates]
    out += [_company_row(r) for r in result.company_rows]
    out += [_domain_row(r) for r in result.domain_rows]
    out += [_serp_row(r) for r in result.serp_rows]
    # dedupe inside the run on the key the table enforces
    seen, uniq = set(), []
    for d in out:
        if d["dedupe_key"] in seen:
            continue
        seen.add(d["dedupe_key"])
        uniq.append(d)
    return uniq


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

def _scoring_version() -> str | None:
    """The scoring package version in force, stamped onto every run.

    Never hard-coded: the two fields that were (uk_monitor's SCORING_VERSION
    and THRESHOLD_VERSION) had not moved since before the 17 Sep consolidation,
    so a stored band could not be traced to the code that produced it. Returns
    None rather than raising - a run must not fail because provenance is
    unavailable, and null already means "unknown" in this column.
    """
    try:
        import tmh_scoring
        return str(tmh_scoring.__version__)
    except Exception:
        return None


class Store:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or _dsn()

    def _conn(self):
        # prepare_threshold=None: Supabase's pooler runs in transaction mode,
        # where server-side prepared statements break after the first few calls.
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=20, prepare_threshold=None)

    # -- clients ------------------------------------------------------------

    def ensure_client(self, name: str, zoho_account_id: str | None = None) -> str:
        with self._conn() as c:
            if zoho_account_id:
                row = c.execute("""insert into audit.clients (zoho_account_id, name) values (%s, %s)
                                   on conflict (zoho_account_id) do update set name = excluded.name
                                   returning id""", (zoho_account_id, name)).fetchone()
            else:
                row = c.execute("select id from audit.clients where name = %s and zoho_account_id is null",
                                (name,)).fetchone() or \
                      c.execute("insert into audit.clients (name) values (%s) returning id", (name,)).fetchone()
            return str(row["id"])

    def seed_portfolio(self, client_id: str, items: list[tuple], source: str = "zoho", by: str | None = None) -> int:
        """items: [(kind, value, label), ...] kinds: trademark|company|domain|handle|url"""
        n = 0
        with self._conn() as c:
            for kind, value, label in items:
                value = _norm_portfolio_value(kind, value)
                if not value:
                    continue
                cur = c.execute("""insert into audit.client_portfolio (client_id, kind, value, label, source, created_by)
                                   values (%s, %s, %s, %s, %s, %s) on conflict do nothing""",
                                (client_id, kind, value, label, source, by))
                n += cur.rowcount
        return n

    # -- runs ----------------------------------------------------------------

    def save_run(self, result, client_id: str | None, deal_id: str = "", kind: str = "audit",
                 previous_run_id: str | None = None, created_by: str | None = None) -> dict:
        """Persist one AuditResult. Returns {run_id, results, auto_excluded, linked, status}."""
        req = result.request
        rows = rows_for(result)
        crit = [{"phrase": c.phrase, "match_type": c.match_type, "class_filtered": c.class_filtered,
                 "origin": c.origin, "rationale": c.rationale}
                for c in (result.criteria.criteria if result.criteria else [])]
        from .nice import country as _country
        coverage = []
        for src in result.sources:
            for j in (src.requested_for or (src.jurisdiction,)):
                coverage.append({"jurisdiction": j, "country": _country(j), "register": src.label,
                                 "provider": src.provider, "queried_as": src.jurisdiction})
        credits = None
        for line in result.searches_run:
            m = re.search(r"Serper: (\d+) credits", line)
            if m:
                credits = int(m.group(1))

        with self._conn() as c:
            run = c.execute("""
                insert into audit.runs (client_id, zoho_deal_id, kind, status, mark_text, classes, jurisdictions,
                                        request, criteria, sources, coverage, searches_run, warnings, canary, hold_reason,
                                        serper_credits, previous_run_id, started_at, created_by, scoring_version)
                values (%s, %s, %s, 'running', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
                returning id""",
                (client_id, deal_id or None, kind, req.mark_text, list(req.classes), list(req.jurisdictions),
                 Jsonb(_jsonable(req)), Jsonb(crit), [s.label for s in result.sources], Jsonb(coverage),
                 list(result.searches_run), list(result.warnings),
                 Jsonb({"held": result.held, "reason": result.hold_reason}),
                 result.hold_reason or None, credits, previous_run_id, created_by,
                 _scoring_version())).fetchone()
            run_id = str(run["id"])

            # What this run refused to search on (18 Sep 2026). Recorded, not
            # enforced: the filtering already happened in criteria.build().
            ignored = list(getattr(result.criteria, "ignored_words", None) or [])
            if ignored:
                c.cursor().executemany(
                    """insert into audit.ignored_words
                           (run_id, word, kind, source, where_applied)
                       values (%s, %s, %s, %s, %s)""",
                    [(run_id, e.get("word"), e.get("kind"), e.get("source"),
                      e.get("where")) for e in ignored])

            if result.plan:
                c.cursor().executemany("""
                    insert into audit.search_plan (run_id, seq, channel, platform, kind, query, provider,
                                                   endpoint, params, rationale, declared_as, status)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(run_id, i, _CHANNEL.get(ps.channel, "web"), ps.platform, ps.kind, ps.query,
                      ps.provider, ps.endpoint, Jsonb(_jsonable(ps.params or {})), ps.rationale,
                      ps.declared_as, ps.status)
                     for i, ps in enumerate(result.plan.searches, start=1)])

            c.cursor().executemany("""
                insert into audit.results (run_id, client_id, channel, platform, kind, external_ref, dedupe_key,
                                           title, url, status, owner, classes, goods, dates, detail, image_url,
                                           source, found_by, score, band, explanation,
                                           review_status, review_reason, review_note, reviewed_by, reviewed_at,
                                           auto_excluded)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, case when %s then now() end, %s)""",
                [(run_id, client_id, d["channel"], d["platform"], d["kind"], d["external_ref"], d["dedupe_key"],
                  d["title"], d["url"], d["status"], d["owner"], d["classes"], d["goods"],
                  Jsonb(_jsonable(d["dates"])), Jsonb(_jsonable(d["detail"])), d["image_url"],
                  d["source"], d["found_by"], d["score"], d["band"], d["explanation"],
                  "excluded" if d["excluded"] else ("held" if d.get("hold") else "new"),
                  "own_asset" if d["excluded"] else None,
                  "declared exclusion on the request" if d["excluded"] else
                  ("visual comparison pending" if d.get("hold") else None),
                  "engine" if (d["excluded"] or d.get("hold")) else None,
                  bool(d["excluded"] or d.get("hold")),
                  bool(d["excluded"]))
                 for d in rows])

            auto = c.execute("select audit.apply_learned(%s) as n", (run_id,)).fetchone()["n"]
            linked = c.execute("select audit.link_previous(%s) as n", (run_id,)).fetchone()["n"]
            status = "held" if result.held else "complete"
            c.execute("update audit.runs set status = %s, finished_at = now() where id = %s", (status, run_id))
        return {"run_id": run_id, "results": len(rows), "auto_excluded": auto, "linked": linked,
                "status": status}

    def add_results(self, run_id: str, client_id: str | None, rows: list[dict], by: str | None = None) -> dict:
        """Append rows to an existing run (manual import). Duplicates of rows
        already in the run are skipped; learned exclusions are applied to
        whatever is new."""
        inserted = 0
        with self._conn() as c:
            for d in rows:
                cur = c.execute("""
                    insert into audit.results (run_id, client_id, channel, platform, kind, external_ref, dedupe_key,
                                               title, url, status, owner, classes, goods, dates, detail, image_url,
                                               source, found_by, score, band, explanation, review_status, review_note)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s)
                    on conflict (run_id, dedupe_key) do nothing""",
                    (run_id, client_id, d["channel"], d["platform"], d["kind"], d["external_ref"], d["dedupe_key"],
                     d["title"], d["url"], d["status"], d["owner"], d["classes"], d["goods"],
                     Jsonb(_jsonable(d["dates"])), Jsonb(_jsonable(d["detail"])), d["image_url"], d["source"],
                     d["found_by"], d["score"], d["band"], d["explanation"], f"imported by {by}" if by else "imported"))
                inserted += cur.rowcount
            auto = c.execute("select audit.apply_learned(%s) as n", (run_id,)).fetchone()["n"]
            c.execute("update audit.results set change = 'new', first_seen_run_id = run_id where run_id = %s and change is null", (run_id,))
        return {"inserted": inserted, "skipped": len(rows) - inserted, "auto_excluded": auto}

    def add_forensic(self, result_id: str, review: dict, by: str | None) -> str:
        with self._conn() as c:
            row = c.execute("""insert into audit.forensic_reviews (result_id, model, verdict, band_suggested, rationale,
                                                                  recommendation, evidence, requested_by)
                               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                            (result_id, review["model"], review["verdict"], review["band_suggested"],
                             review["rationale"], review["recommendation"], Jsonb(_jsonable(review.get("evidence") or {})), by)).fetchone()
            return str(row["id"])

    def section4(self, run_id: str) -> dict:
        with self._conn() as c:
            return c.execute("""select section4_included, section4_text, section4_approved, section4_approved_by,
                                       section4_approved_at from audit.runs where id = %s""", (run_id,)).fetchone()

    def set_section4(self, run_id: str, included: bool | None = None, text: str | None = None,
                     approved: bool | None = None, by: str | None = None) -> dict:
        """Any edit to the text or the include flag withdraws approval — approval
        is of a specific wording."""
        with self._conn() as c:
            if text is not None:
                c.execute("update audit.runs set section4_text = %s, section4_approved = false, section4_approved_by = null, section4_approved_at = null where id = %s", (text, run_id))
            if included is not None:
                c.execute("update audit.runs set section4_included = %s, section4_approved = case when %s then section4_approved else false end where id = %s", (included, included, run_id))
            if approved is not None:
                c.execute("""update audit.runs set section4_approved = %s, section4_approved_by = case when %s then %s end,
                             section4_approved_at = case when %s then now() end where id = %s""", (approved, approved, by, approved, run_id))
        return self.section4(run_id)

    def mark_shared(self, report_id: str, by: str | None, shared: bool = True) -> int:
        """Record that this report was actually sent to the client (or undo it)."""
        with self._conn() as c:
            return c.execute("""update audit.reports set shared_at = case when %s then now() end,
                                       shared_by = case when %s then %s end where id = %s""",
                             (shared, shared, by, report_id)).rowcount

    def report(self, report_id: str) -> dict | None:
        with self._conn() as c:
            return c.execute("select * from audit.reports where id = %s", (report_id,)).fetchone()

    def revoke_report(self, report_id: str, by: str | None) -> int:
        with self._conn() as c:
            return c.execute("update audit.reports set revoked_at = now(), revoked_by = %s where id = %s and revoked_at is null",
                             (by, report_id)).rowcount

    def clear_findings(self, run_id: str, by: str | None) -> int:
        with self._conn() as c:
            return c.execute("""update audit.results set review_status = 'new', reviewed_by = %s, reviewed_at = now()
                                where run_id = %s and review_status = 'reported'""", (by, run_id)).rowcount

    def forensic(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("""select f.*, r.title, r.channel, r.platform, r.band as engine_band
                                  from audit.forensic_reviews f join audit.results r on r.id = f.result_id
                                 where r.run_id = %s order by f.created_at desc""", (run_id,)).fetchall()

    # -- review --------------------------------------------------------------

    def review(self, result_ids: list[str], status: str, reason: str | None = None,
               note: str | None = None, by: str | None = None) -> int:
        """new | excluded | reported | held | forensic. Excluding teaches (trigger)."""
        if status == "excluded" and not reason:
            raise ValueError("an exclusion needs a reason")
        with self._conn() as c:
            cur = c.execute("""update audit.results
                                  set review_status = %s, review_reason = %s, review_note = %s,
                                      reviewed_by = %s, reviewed_at = now(), auto_excluded = false
                                where id = any(%s::uuid[])""",
                            (status, reason, note, by, result_ids))
            return cur.rowcount

    def set_note(self, result_ids: list[str], note: str, by: str | None = None) -> int:
        with self._conn() as c:
            return c.execute("update audit.results set review_note = %s, reviewed_by = %s, reviewed_at = now() where id = any(%s::uuid[])",
                             (note, by, result_ids)).rowcount

    def latest_run(self, client_id: str, kind: str | None = None) -> str | None:
        with self._conn() as c:
            row = c.execute("""select id from audit.runs where client_id = %s and status in ('complete','held')
                               and (%s::text is null or kind = %s) order by created_at desc limit 1""",
                            (client_id, kind, kind)).fetchone()
            return str(row["id"]) if row else None

    def triage(self, run_id: str, channel: str | None = None, status: str | None = "new") -> list[dict]:
        with self._conn() as c:
            return c.execute("""
                select id, channel, platform, kind, title, url, external_ref, status, owner, classes,
                       score, band, explanation, review_status, review_reason, review_note, change, found_by
                  from audit.results
                 where run_id = %s and (%s::text is null or channel = %s::audit.channel)
                   and (%s::text is null or review_status = %s::audit.review_status)
                 order by channel, score desc nulls last, title""",
                (run_id, channel, channel, status, status)).fetchall()

    def summary(self, run_id: str) -> dict:
        with self._conn() as c:
            rows = c.execute("""select channel, review_status, band, count(*) n from audit.results
                                where run_id = %s group by 1, 2, 3 order by 1, 2, 3""", (run_id,)).fetchall()
        out: dict = {}
        for r in rows:
            out.setdefault(r["channel"], {}).setdefault(r["review_status"], {})[r["band"]] = r["n"]
        return out

    # -- reads for the triage app ------------------------------------------

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return c.execute("""
                select r.id, r.kind, r.status, r.mark_text, r.classes, r.jurisdictions, r.zoho_deal_id,
                       r.created_at, r.finished_at, r.hold_reason, r.client_id, cl.name as client_name,
                       (select count(*) from audit.results x where x.run_id = r.id) as total,
                       (select count(*) from audit.results x where x.run_id = r.id and x.review_status = 'new') as new_count,
                       (select count(*) from audit.results x where x.run_id = r.id and x.review_status = 'excluded') as excluded_count,
                       (select count(*) from audit.results x where x.run_id = r.id and x.review_status = 'reported') as reported_count,
                       (select count(*) from audit.reports p where p.run_id = r.id) as reports
                  from audit.runs r left join audit.clients cl on cl.id = r.client_id
                 order by r.created_at desc limit %s""", (limit,)).fetchall()

    def run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            return c.execute("""select r.*, cl.name as client_name, cl.zoho_account_id
                                  from audit.runs r left join audit.clients cl on cl.id = r.client_id
                                 where r.id = %s""", (run_id,)).fetchone()

    def results(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("""
                select id, channel, platform, kind, external_ref, title, url, status, owner, classes, goods,
                       dates, detail, image_url, """ + ("image_path, " if self.has_image_path() else "") + """source, found_by, score, band, explanation,
                       review_status, review_reason, review_note, reviewed_by, reviewed_at, auto_excluded, change
                  from audit.results where run_id = %s
                 order by channel, score desc nulls last, title""", (run_id,)).fetchall()

    def report_meta(self, token: str) -> dict:
        """Who made the report and when — audit.report_view() does not return
        created_by or the token, and both belong on the cover."""
        with self._conn() as c:
            return c.execute("select id, token, title, issued_by, issued_at, created_at "
                             "from audit.reports where token = %s", (token,)).fetchone() or {}

    # -- stored images (images.py) -------------------------------------------
    _img_col: bool | None = None

    def has_image_path(self) -> bool:
        """True once db/006_stored_images.sql has been run (Supabase SQL editor —
        the app role cannot alter tables). Until then everything image-related
        degrades to the source URL and nothing errors."""
        if Store._img_col is None:
            with self._conn() as c:
                Store._img_col = bool(c.execute(
                    "select 1 from information_schema.columns where table_schema='audit' "
                    "and table_name='results' and column_name='image_path'").fetchone())
        return Store._img_col

    def results_needing_images(self, run_id: str) -> list[dict]:
        if not self.has_image_path():
            return []
        with self._conn() as c:
            return c.execute("""select id, image_url, external_ref from audit.results
                                 where run_id = %s and image_url is not null and image_url <> ''
                                   and image_path is null""", (run_id,)).fetchall()

    def set_image_path(self, result_id: str, rel_path: str) -> None:
        if not self.has_image_path():
            return
        with self._conn() as c:
            c.execute("update audit.results set image_path = %s where id = %s", (rel_path, result_id))

    def plan(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("select * from audit.search_plan where run_id = %s order by seq", (run_id,)).fetchall()

    def portfolio(self, client_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("select * from audit.client_portfolio where client_id = %s order by kind, value",
                             (client_id,)).fetchall()

    def exclusions(self, client_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("""select * from audit.exclusions where client_id = %s and active
                                order by channel, platform, value""", (client_id,)).fetchall()

    def reports_for_run(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            return c.execute("""select p.*, (select count(*) from audit.report_results rr where rr.report_id = p.id) as n
                                  from audit.reports p where p.run_id = %s order by p.created_at desc""",
                             (run_id,)).fetchall()

    def add_portfolio(self, client_id: str, kind: str, value: str, label: str | None, by: str | None) -> int:
        return self.seed_portfolio(client_id, [(kind, value, label)], source="staff", by=by)

    def remove_portfolio(self, item_id: str) -> int:
        with self._conn() as c:
            return c.execute("delete from audit.client_portfolio where id = %s", (item_id,)).rowcount

    def learned_for_results(self, result_ids: list[str]) -> list[dict]:
        """audit.exclusions rows the results_learn trigger created for these
        results, with the client's Zoho Account and the run — what the Zoho
        writer needs."""
        with self._conn() as c:
            return c.execute("""select e.*, cl.zoho_account_id, r.run_id
                                  from audit.exclusions e
                                  join audit.results r on r.id = e.source_result_id
                                  join audit.clients cl on cl.id = e.client_id
                                 where e.source_result_id = any(%s::uuid[])""", (result_ids,)).fetchall()

    def client_account(self, client_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("select zoho_account_id from audit.clients where id = %s", (client_id,)).fetchone()
            return (row or {}).get("zoho_account_id")

    def portfolio_items(self, client_id: str, values: list[str]) -> list[dict]:
        with self._conn() as c:
            return c.execute("""select id, kind, value, label from audit.client_portfolio
                                 where client_id = %s and value = any(%s::text[])""", (client_id, values)).fetchall()

    def set_exclusion_zoho_id(self, excl_id: str, zoho_id: str) -> int:
        with self._conn() as c:
            return c.execute("update audit.exclusions set zoho_id = %s where id = %s", (zoho_id, excl_id)).rowcount

    def deactivate_exclusion(self, excl_id: str) -> int:
        with self._conn() as c:
            return c.execute("update audit.exclusions set active = false where id = %s", (excl_id,)).rowcount

    # -- reports -------------------------------------------------------------

    def create_report(self, run_id: str, result_ids: list[str], title: str, by: str | None = None,
                      notes: dict | None = None, expires_days: int | None = None) -> dict:
        notes = notes or {}
        with self._conn() as c:
            run = c.execute("""select client_id, section4_included, section4_text, section4_approved
                                 from audit.runs where id = %s""", (run_id,)).fetchone()
            if run["section4_included"] and not run["section4_approved"]:
                raise ValueError("Section 4 is included but has not been approved")
            s4 = (run["section4_text"] or "").strip() if run["section4_included"] else None
            rep = c.execute("""insert into audit.reports (run_id, client_id, title, issued_by, issued_at, expires_at, section4_text)
                               values (%s, %s, %s, %s, now(),
                                       case when %s::int is null then null else now() + (%s::int || ' days')::interval end, %s)
                               returning id, token""",
                            (run_id, run["client_id"], title, by, expires_days, expires_days, s4 or None)).fetchone()
            # The staff note typed when a row was marked Reported becomes the
            # client's "Additional Comments" unless an explicit note is given.
            own = {str(r["id"]): r["review_note"] for r in c.execute(
                "select id, review_note from audit.results where id = any(%s::uuid[])", (result_ids,)).fetchall()}
            c.cursor().executemany("insert into audit.report_results (report_id, result_id, position, note) values (%s, %s, %s, %s)",
                          [(rep["id"], rid, i, notes.get(rid) or own.get(rid)) for i, rid in enumerate(result_ids, start=1)])
            c.execute("""update audit.results set review_status = 'reported', reviewed_by = coalesce(%s, reviewed_by),
                         reviewed_at = now() where id = any(%s::uuid[]) and review_status <> 'excluded'""",
                      (by, result_ids))
        return {"report_id": str(rep["id"]), "token": rep["token"]}

    def report_by_token(self, token: str) -> dict | None:
        with self._conn() as c:
            return c.execute("""select * from audit.reports where token = %s and revoked_at is null
                                and (expires_at is null or expires_at > now())""", (token,)).fetchone()

    def report_view(self, token: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("select audit.report_view(%s) as v", (token,)).fetchone()
            return row["v"]


def _norm_portfolio_value(kind: str, value: str) -> str:
    v = (value or "").strip()
    if kind == "domain":
        v = re.sub(r"^https?://", "", v.lower()).split("/")[0]
        v = re.sub(r"^(www\.|m\.)", "", v)
    elif kind == "handle":
        v = re.sub(r"^https?://[^/]+/", "", v.lower()).strip("/").split("/")[0].lstrip("@")
    elif kind == "url":
        v = v.rstrip("/")
    return v


def portfolio_from_urls(urls: list[str]) -> list[tuple]:
    """'coastalnutrients.com', 'facebook.com/coastalnutrients/' → portfolio items.

    A bare marketplace root (amazon.com/) identifies nothing and is dropped —
    a storefront URL would be a handle; the root would exclude the whole of
    Amazon.
    """
    items = []
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        host = re.sub(r"^https?://", "", u.lower()).split("/")[0]
        host = re.sub(r"^(www\.|m\.)", "", host)
        path = re.sub(r"^https?://[^/]+", "", re.sub(r"^(?!https?://)", "http://", u.lower())).strip("/")
        if host in _PLATFORM_HOSTS:
            if path:
                items.append(("handle", path.split("/")[0].lstrip("@"), f"{host}/{path}"))
            continue  # bare platform root: nothing to exclude
        items.append(("domain", host, u))
    return items


_PLATFORM_HOSTS = {
    "facebook.com", "instagram.com", "instragram.com", "linkedin.com", "tiktok.com", "youtube.com",
    "x.com", "twitter.com", "amazon.com", "amazon.co.uk", "ebay.com", "ebay.co.uk", "etsy.com",
    "temu.com", "alibaba.com", "pinterest.com", "threads.net",
}
