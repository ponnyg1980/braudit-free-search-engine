"""The audit runner — one frozen order in, a scored result set out.

This is the trademark layer of a TMH audit with no Braudit involvement:
UKIPO through Temmy, every other jurisdiction through Signa, WIPO for the
gaps, our own scoring module, and the existing xlsx template as the output.

Two behaviours are non-negotiable and are enforced here rather than left to
the caller.

FAIL CLOSED. A source that errors raises; it never contributes an empty list
that a report would then present as "nothing found". A run whose canary
fails is HELD, not published. "No conflicts" and "the search broke" must
never look the same.

EXCLUDE, DO NOT DROP. Results matching a client's own assets are kept and
marked. They count toward "checked and cleared" — which is a better proof of
coverage than silently shrinking the result set.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent

from . import _paths  # noqa: F401  — sets up tmh_scoring / tm_monitor imports
from . import criteria as criteria_mod          # noqa: E402
from . import sources as sources_mod            # noqa: E402
from .signa import SignaClient, SignaError, SignaRecord   # noqa: E402


@dataclass
class AuditRequest:
    """Everything needed to run one audit. Assembled from a frozen Deal."""
    client_name: str
    mark_text: str
    classes: list = field(default_factory=list)
    jurisdictions: list = field(default_factory=list)   # ISO codes from Trademark_Jurisdictions
    tagline: str | None = None
    applicant: str | None = None
    goods_text: str = ""
    exclusions: list = field(default_factory=list)      # own marks/numbers/domains
    # The order form's own word criteria, word 1 INCLUDED, as
    # (match_type, phrase). Named `extra_criteria` since before it carried
    # word 1; kept for every existing caller and stored request.
    extra_criteria: list = field(default_factory=list)
    # Set when this run IS the staff-pressed Contains fallback, naming the run
    # it came from, so the stored run says why it is broader than the order
    # form asked for.
    fallback_from_run: str = ""
    # True when a person chose those operators on the Deal (Search_Operator_n
    # or a search record), false when they were reconstructed by the legacy
    # reader. Only a choice governs the search - decision I, 22 Sep 2026.
    criteria_declared: bool = False
    deal_id: str = ""
    nature_of_business: str = ""
    # Cover-block identity, carried from the Deal so the report can name the
    # people involved (Jonathan, 16 Sep 2026: these were blank on every report
    # because the request never carried them).
    deal_name: str = ""
    contact_name: str = ""
    contact_email: str = ""
    deal_owner: str = ""
    sic_code: str = ""
    search_date: date = field(default_factory=date.today)
    include_companies: bool = True   # UK Companies House name-conflict layer
    include_domains: bool = True
    include_serp: bool = True        # Google web/images, socials, marketplaces via Serper
    logo_url: str | None = None
    vienna_codes: list = field(default_factory=list)
    logo_awaited: bool = False            # client said the logo will follow (Deal.Logo_Awaited)
    logo_ref: dict | None = None          # Zoho file held on Image_Mark_Information (no public URL)
    zoho_portfolio: list = field(default_factory=list)   # (kind, value, label) from Client_Search_Exclusions
    own_domains: list = field(default_factory=list)
    domain_criteria: list = field(default_factory=list)  # [(match_type, phrase)] from the Deal
    own_handles: list = field(default_factory=list)
    register_layers: list = field(default_factory=list)  # registers named on the Deal
    socials: list | None = None        # None = all platforms
    marketplaces: list | None = None   # None = all platforms


@dataclass
class AuditRow:
    """One cited mark, with where it came from and how it was found."""
    office: str
    app_number: str
    status: str
    mark_type: str
    mark_text: str
    filing_date: str
    classes: str
    owner: str
    owner_country: str
    goods: str
    source: str                 # the Source column — WHERE we got it
    found_by: list = field(default_factory=list)
    image_url: str = ""
    registration_date: str = ""
    expiry_date: str = ""
    filing_route: str = ""
    score: int = 0
    band: str = ""
    explanation: str = ""
    exclusion_status: str = "not_excluded"
    exclusion_reason: str = ""
    goods_similarity: dict | None = None   # Layer 2 evidence: band, shared terms, why
    assessment: dict | None = None         # two_layer.Assessment.as_row() — conflict, rights, tiers
    components: dict | None = None         # WHY it scored what it did, per compartment (18 Sep 2026)
    international_ref: str = ""            # the WIPO IR number, where the mark is also a Madrid designation

    @property
    def dedupe_key(self) -> tuple:
        return (self.office or "", (self.app_number or "").upper())


@dataclass
class AuditResult:
    request: AuditRequest
    rows: list = field(default_factory=list)
    criteria: criteria_mod.CriteriaSet | None = None
    sources: list = field(default_factory=list)
    searches_run: list = field(default_factory=list)   # (source, criterion, count)
    warnings: list = field(default_factory=list)
    company_rows: list = field(default_factory=list)
    plan: object = None                 # the SearchPlan — every query, declared
    image_candidates: list = field(default_factory=list)   # device-only Vienna hits
    domain_rows: list = field(default_factory=list)
    serp_rows: list = field(default_factory=list)          # web, socials, marketplaces
    held: bool = False
    hold_reason: str = ""

    def band_counts(self) -> dict:
        counts: dict = {}
        for r in self.rows:
            counts[r.band] = counts.get(r.band, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# exclusions
# ---------------------------------------------------------------------------

def _apply_exclusions(rows: list, exclusions: list) -> int:
    """Mark the client's own marks. Never removes a row."""
    if not exclusions:
        return 0
    numbers, texts = set(), set()
    for e in exclusions:
        if isinstance(e, dict):
            val = str(e.get("value") or "").strip()
            ref = str(e.get("source_record_id") or "").strip()
        else:
            val, ref = str(e).strip(), ""
        if ref:
            numbers.add(ref.upper().replace(" ", ""))
        if val:
            texts.add(val.upper())
            numbers.add(val.upper().replace(" ", ""))

    hit = 0
    for r in rows:
        num = (r.app_number or "").upper().replace(" ", "")
        txt = (r.mark_text or "").upper()
        if (num and num in numbers) or (txt and txt in texts):
            r.exclusion_status = "excluded_initial_asset"
            r.exclusion_reason = "client_owned"
            hit += 1
    return hit


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

def _from_signa(rec: SignaRecord) -> AuditRow:
    return AuditRow(
        office=rec.office_code, app_number=rec.application_number, status=rec.status,
        mark_type=rec.mark_type, mark_text=rec.mark_text, filing_date=rec.filing_date,
        classes=rec.nice_classes, owner=rec.owner, owner_country=rec.owner_country,
        goods=rec.goods, source=rec.source_label, image_url=rec.image_url,
        registration_date=rec.registration_date, expiry_date=rec.expiry_date,
        filing_route=rec.filing_route,
    )


def _from_temmy(rec) -> AuditRow:
    return AuditRow(
        office="GB", app_number=rec.tm_number, status=rec.status,
        mark_type=(rec.mark_type or "Word"), mark_text=rec.mark_text,
        filing_date=(rec.filing_date or "")[:10] if rec.filing_date else "",
        classes=rec.classes or "", owner=rec.owner or "", owner_country="",
        goods=rec.goods or "", source="UKIPO (Temmy)",
        registration_date=(rec.registration_date or "")[:10] if rec.registration_date else "",
        # TemmyDB holds the representation itself (images.file, base64). The
        # URI is carried as a marker so the store knows an image exists; the
        # bytes are pulled per result by images.py, never in the search query.
        image_url=(f"temmy:{rec.image_uri}" if getattr(rec, "image_uri", "") else ""),
    )


def _class_overlap(classes_str: str, wanted: list) -> bool:
    if not wanted:
        return True
    import re
    nums = {int(n) for n in re.split(r"[^0-9]+", classes_str or "") if n.isdigit()}
    return bool(nums & set(wanted))


def _run_temmy(crit_set, warnings: list) -> list:
    """UK register via TemmyDB.

    Temmy blocks generously in SQL and lets scoring judge, so it takes the
    phrase rather than a match mode — one call per phrase covers every match
    type against the UK register.

    The class filter has to be applied here rather than in the query, because
    Temmy's blocking is phrase-based. Without it a broad stem returns the
    whole UK field unfiltered while Signa returns a class-filtered set for
    the same criterion, and the UK half of the audit is far noisier than the
    EU half for no defensible reason. A phrase is filtered only when EVERY
    criterion using it is class-filtered — if the phrase is also a close
    match, its results must not be narrowed.
    """
    import temmy_source  # imported lazily so a Signa-only audit needs no Temmy creds

    filtered_only: dict = {}
    for c in crit_set.criteria:
        filtered_only.setdefault(c.phrase, []).append(c.class_filtered)

    out = []
    for phrase in sorted(filtered_only):
        apply_classes = all(filtered_only[phrase]) and bool(crit_set.classes)
        # Temmy's query-runs API returns the odd transient 500; three tries
        # with backoff before we fail closed, so a blip is not a held audit.
        import time as _time
        recs, last = None, None
        for attempt in range(3):
            try:
                recs = temmy_source.candidates_for_term(phrase, max_rows=TEMMY_MAX_ROWS)
                break
            except Exception as exc:
                last = exc
                _time.sleep(3 * (attempt + 1))
        if recs is None:
            raise RuntimeError(f"Temmy search failed for {phrase!r} after 3 attempts: {last}") from last
        if len(recs) >= TEMMY_MAX_ROWS:
            # Arbitrary truncation is how a client's own mark once fell out of
            # its own audit. Say so rather than reporting a clipped set silently.
            warnings.append(
                f"WARNING Temmy '{phrase}' hit the {TEMMY_MAX_ROWS}-row candidate cap "
                "— result set truncated; narrow the criterion or raise the cap")
        kept = 0
        for rec in recs:
            row = _from_temmy(rec)
            if apply_classes and not _class_overlap(row.classes, crit_set.classes):
                continue
            row.found_by.append(f"Temmy: {phrase}")
            out.append(row)
            kept += 1
        note = f"Temmy '{phrase}': {len(recs)} candidates"
        if apply_classes:
            note += f" -> {kept} in client classes"
        warnings.append(note)
    return out


def _run_signa(client, source, crit_set, warnings: list) -> list:
    out = []
    for c in crit_set.criteria:
        classes = crit_set.class_csv if c.class_filtered else None
        recs = client.search(c.phrase, c.signa_match, source.jurisdiction, classes)
        for rec in recs:
            row = _from_signa(rec)
            row.found_by.append(f"{c.match_type}: {c.phrase}")
            out.append(row)
        warnings.append(f"{source.label} {c.match_type} '{c.phrase}': {len(recs)}")
    return out


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

# NOTE (16 Sep 2026): the trade-similarity calibration is NOT hand-rolled here.
# `tmh_scoring.two_layer` (tmh-scoring/, was the scoring harness until 17 Sep) implements the settled
# two-layer model — specification text primary, classes as weak supporting
# evidence, "specifications unrelated: shared classes do not rescue it", and
# the D8 requirement that the evidence level is NAMED on every row
# ("classes only - cited specification missing" and so on). The fortnightly
# runner uses it through uk_monitor/_compat.py and writes Evidence_Level to
# Zoho. The audit engine is still on the older tmh_scoring.score_word_result
# path and needs migrating to the same scorer rather than growing a parallel
# set of rules. An earlier attempt to re-derive the calibration here produced
# false High bands on live client data and was removed.

def _class_list(classes) -> list:
    """The register gives classes as text ("35, 45", "Class 9"); the goods
    comparison needs integers."""
    import re as _re
    if isinstance(classes, (list, tuple, set)):
        return [int(c) for c in classes if str(c).isdigit()]
    return [int(n) for n in _re.split(r"[^0-9]+", str(classes or "")) if n.isdigit()]


def _prefer_local_office(rows: list) -> tuple:
    """Drop WIPO duplicates where the local office already returned the mark.

    WIPO is run as a backup on every international audit (offices.py), and it
    bills per search rather than per country, so it costs nothing to include.
    The price is duplication: a Madrid mark designating the UK comes back once
    from UKIPO and again from WIPO.

    Jonathan, 17 Sep 2026: "Where there is duplication, display the result from
    the local office, not the WIPO." The local record is the better one — it
    carries the national number, the national status and the national dates,
    which is what a client acts on.

    Matched on normalised mark text AND normalised proprietor. Two different
    marks reading the same and owned by the same proprietor are the same right;
    requiring both keeps unrelated marks that merely share a name apart. A WIPO
    row with no local counterpart is kept — that is exactly what the backup is
    for.
    """
    def norm(v: str) -> str:
        return " ".join(str(v or "").upper().split())

    local: dict = {}
    for r in rows:
        if (r.office or "").upper() != "WO" and r.mark_text:
            local.setdefault((norm(r.mark_text), norm(r.owner)), []).append(r)

    kept, merged = [], 0
    for r in rows:
        if (r.office or "").upper() == "WO":
            twins = local.get((norm(r.mark_text), norm(r.owner)))
            if twins:
                # Keep the international number ON the local record rather than
                # throwing it away (Jonathan, 17 Sep 2026: "use the WIPO where
                # there is conflict, that way we get the local and international
                # ID"). The client acts on the national right; the IR number is
                # what identifies the same mark across every other designation,
                # so losing it would cost us the link between them.
                for t in twins:
                    if r.app_number and r.app_number not in (t.international_ref or ""):
                        t.international_ref = " / ".join(
                            x for x in ((t.international_ref or ""), r.app_number) if x)
                merged += 1
                continue
        kept.append(r)
    return kept, merged


def _score(rows: list, crit_set, client_goods: str = "", client_filing_date=None) -> tuple:
    """Score every row through the two-layer model, and separate genuine
    matches from blocking noise.

    SCORING DECISIONS - authoritative.md is the spec; `two_layer.assess` is
    the implementation, and it is the SAME function the fortnightly Watch
    runner scores with (uk_monitor/_compat.py). One pair of marks gets one
    answer whoever asked — which is the whole point of D2: the operator that
    found a result governs recall, never assessment.

    What that buys over the older `tmh_scoring.score_word_result` path this
    replaced (migrated 16 Sep 2026):
      D1  Layer 1 sees only the two mark texts; Layer 2 only the trades.
      D8  Trade similarity from SPECIFICATION TEXT, classes as weak support,
          with the evidence level NAMED on every row.
      D4  Half-bands mean ambiguity: where the layers disagree sharply the
          band is pulled back so a human looks.
      D6  Expiry is a gradient — a mark lapsed last month is still a right.
      D12 If the cited mark is older, the client may be the one at risk.

    Temmy deliberately over-returns: it blocks in SQL on a substring or a
    shared leading token and lets scoring decide. A mark that shares four
    letters and nothing else comes back with mark tier 0 — "no meaningful
    resemblance" — and is a blocking artefact, not a finding. Those are
    dropped with a count kept for the run log.
    """
    from tmh_scoring.goods_similarity import build_idf

    # One implementation, shared with rescore.py. The logic used to live
    # inline in this loop; a second copy for re-scoring is exactly how
    # finding G happened, so it moved to scoring_paths and both callers use
    # it (21 Sep 2026).
    from .scoring_paths import score_register_row

    # The idf is built from this run's own candidate specifications, so a term
    # every mark in the sector uses self-downweights (D8) without a hand-kept
    # word list going stale.
    idf = build_idf([r.goods for r in rows if r.goods]) if client_goods else {}
    client_mark = crit_set.criteria[0].phrase if crit_set.criteria else ""

    matched, noise = [], 0
    for r in rows:
        out = score_register_row(
            client_mark=client_mark, cited_mark=r.mark_text,
            client_classes=crit_set.classes, cited_classes=r.classes,
            status=r.status,
            client_goods=client_goods, cited_goods=r.goods or "",
            registration_date=r.registration_date or None,
            expiry_date=r.expiry_date or None,
            filing_date=r.filing_date or None,
            client_filing_date=client_filing_date,
            idf=idf,
        )
        if out is None:
            noise += 1
            continue
        r.score, r.band = out["score"], out["band"]
        r.explanation = out["explanation"]
        r.assessment = out["assessment"]
        # The same numbers, kept apart instead of flattened into one sentence,
        # so Triage can show which compartment did the work (18 Sep 2026), and
        # since tmh-scoring 2.5.0 the raw axis scores too, so a row can be
        # re-banded at a different sensitivity from its own breakdown.
        r.components = out["components"]
        if out["goods_similarity"]:
            r.goods_similarity = out["goods_similarity"]
        matched.append(r)
    return matched, noise


TEMMY_MAX_ROWS = 12000
IMAGE_CANDIDATE_CAP = 500      # per register; the pHash/CLIP pass works on these

_BAND_ORDER = {"High": 0, "Medium/High": 1, "Medium": 2, "Low/Medium": 3,
               "Low": 4, "Result (not live)": 5}


def _sort(rows: list) -> list:
    return sorted(rows, key=lambda r: (_BAND_ORDER.get(r.band, 9), -r.score,
                                       r.mark_text.upper()))


# ---------------------------------------------------------------------------
# canary
# ---------------------------------------------------------------------------

def _canary(result: AuditResult) -> None:
    """Hold the run if it cannot be shown to have worked.

    Two cheap checks that between them catch the failure modes we have
    actually seen: a search that silently returned nothing, and an export
    whose mark-text column came through empty (the Hopper defect — 304 rows,
    every mark blank, which would have gone out as a clean report).
    """
    # Nothing resolved to a real register, so the only thing searched was the
    # backup. WIPO standing in for a country we cannot reach directly is the
    # design; WIPO standing in for a value we failed to READ is a wrong
    # search wearing the right label, and it must not reach a client.
    bad = sources_mod.unrecognised(result.request.jurisdictions)
    if bad and not any(s.jurisdiction != "WO" for s in result.sources):
        result.held, result.hold_reason = True, (
            "jurisdiction not recognised: " + ", ".join(bad)
            + " — nothing resolved to a register, so only WIPO was searched. "
            "Fix Trademark_Jurisdictions on the Deal (ISO codes: GB, EM, US) "
            "and re-run")
        return
    if not result.rows:
        # Decision I, 22 Sep 2026. This hold was written when every run
        # derived its own broad criteria, and against those an empty result
        # really is more likely broken than clean. A DECLARED search is the
        # opposite case: Exact Match is chosen precisely when the mark's words
        # are common, and finding nothing is the answer the client is paying
        # for. Holding it would turn the correct result into a blocked run and
        # teach staff to broaden every order until something came back.
        #
        # The narrow case still holds: nothing declared, nothing found, so we
        # cannot tell a clean register from a broken search.
        if getattr(result.request, "criteria_declared", False):
            return
        result.held, result.hold_reason = True, (
            "no records returned from any source — a clearance audit that finds "
            "nothing is more likely broken than clean")
        return
    blank = sum(1 for r in result.rows if not (r.mark_text or "").strip())
    if blank and blank / len(result.rows) > 0.10:
        result.held, result.hold_reason = True, (
            f"{blank} of {len(result.rows)} rows have empty mark text — "
            "column alignment fault, not a result")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_audit(req: AuditRequest, signa_client=None) -> AuditResult:
    crit_set = criteria_mod.build(
        req.mark_text, classes=req.classes, applicant=req.applicant,
        tagline=req.tagline,
        # Decision I: declared operators replace the derived set; a legacy
        # guess is only ever an addition.
        declared=(req.extra_criteria if req.criteria_declared else None),
        extra=(None if req.criteria_declared else req.extra_criteria),
    )
    srcs = sources_mod.resolve(req.jurisdictions, layers=req.register_layers)
    result = AuditResult(request=req, criteria=crit_set, sources=srcs)
    result.warnings.extend(crit_set.notes)
    # A jurisdiction we could not read is not a jurisdiction we cannot reach.
    # Say so on the run either way; _canary holds it when nothing resolved.
    for value in sources_mod.unrecognised(req.jurisdictions):
        result.warnings.append(
            f"WARNING jurisdiction {value!r} is not an ISO code (GB, EM, US ...) - "
            "it could not be resolved to a register and has been left to WIPO. "
            "Check Trademark_Jurisdictions on the Deal.")

    from .plan import build_plan
    result.plan = build_plan(
        req.mark_text, classes=req.classes, jurisdictions=req.jurisdictions,
        tagline=req.tagline, applicant=req.applicant, logo_url=req.logo_url,
        vienna_codes=req.vienna_codes, own_domains=req.own_domains,
        own_handles=req.own_handles, socials=req.socials,
        marketplaces=req.marketplaces, companies=req.include_companies,
        extra_criteria=req.extra_criteria, domain_criteria=req.domain_criteria)

    client = signa_client
    collected: list = []
    for src in srcs:
        if src.provider == sources_mod.TEMMY:
            collected.extend(_run_temmy(crit_set, result.searches_run))
        else:
            if client is None:
                client = SignaClient()
            try:
                collected.extend(_run_signa(client, src, crit_set, result.searches_run))
            except SignaError as exc:
                # Fail closed: one dead register must not silently narrow the audit.
                raise RuntimeError(f"{src.label} unavailable: {exc}") from exc

    # Trademark IMAGE rows from the plan.
    #
    # GB goes to Temmy and only to Temmy: TemmyDB holds UKIPO's own Vienna
    # coding and the query-runs API is free, while Signa is metered. Signa
    # keeps every non-GB register. The plan already decided which is which,
    # so this dispatches on the planned provider rather than assuming one.
    if req.vienna_codes:
        for ps in result.plan.searches:
            if ps.channel != "Trademarks" or ps.kind != "image":
                continue
            is_candidates = "candidates" in ps.query

            if ps.provider == "temmy":
                import temmy_source  # lazy: a Signa-only audit needs no Temmy creds
                cap = IMAGE_CANDIDATE_CAP if is_candidates else 2000
                try:
                    cands = temmy_source.vienna_candidates(
                        req.vienna_codes,
                        term=ps.params.get("q") or None,
                        classes=ps.params.get("nice_classes") or None,
                        min_shared=int(ps.params.get("min_shared") or 1),
                        max_rows=cap)
                except Exception as exc:
                    # Fail loud, never silent: an image search that could not
                    # run must not read as an image search that found nothing.
                    result.warnings.append(
                        f"WARNING image search {ps.platform} failed: {exc}")
                    ps.status = "Failed"
                    continue
                for c in cands:
                    row = _from_temmy(c.record)
                    row.found_by.append(ps.declared_as)
                    row.found_by.append(
                        f"Vienna divisions {', '.join(c.divisions)} "
                        f"({c.shared} shared, figurative similarity {c.score:.2f})")
                    if is_candidates:
                        row.band = "Candidate (visual comparison pending)"
                        result.image_candidates.append(row)
                    else:
                        collected.append(row)   # combination marks: scored on the word
                note = f"{ps.platform} {ps.query}: {len(cands)}"
                if len(cands) >= cap:
                    note += f" (capped at {cap})"
                    result.warnings.append(
                        f"WARNING Temmy image search '{ps.query}' hit the {cap}-row cap "
                        "— results are the highest-scoring, but the set is truncated")
                result.searches_run.append(note)
                ps.status = "Executed"
                continue

            if client is None:
                client = SignaClient()
            try:
                recs = client.search_by_vienna(
                    req.vienna_codes, ps.params.get("jurisdiction", "GB"),
                    ps.params.get("nice_classes") or None,
                    cap=IMAGE_CANDIDATE_CAP if is_candidates else 2000,
                    q=ps.params.get("q"), match=ps.params.get("match", "contains"))
            except SignaError as exc:
                result.warnings.append(f"WARNING image search {ps.platform} failed: {exc}")
                ps.status = "Failed"
                continue
            for rec in recs:
                row = _from_signa(rec)
                row.found_by.append(ps.declared_as)
                if is_candidates:
                    row.band = "Candidate (visual comparison pending)"
                    result.image_candidates.append(row)
                else:
                    collected.append(row)        # combination marks: scored on the word
            result.searches_run.append(f"{ps.platform} {ps.query}: {len(recs)}"
                                       + (f" (capped at {IMAGE_CANDIDATE_CAP})" if is_candidates
                                          and len(recs) >= IMAGE_CANDIDATE_CAP else ""))
            ps.status = "Executed"

    # mark the word rows and company rows executed, everything else planned
    for ps in result.plan.searches:
        if ps.status == "Suggested":
            if ps.channel == "Trademarks" and ps.kind == "word":
                ps.status = "Executed"
            elif ps.channel == "Companies" and req.include_companies:
                ps.status = "Executed"
            else:
                ps.status = "Planned (channel not yet built)"

    # Dedupe, preserving every criterion that found a record — that provenance
    # is what lets a reviewer see why a row is present.
    merged: dict = {}
    for row in collected:
        key = row.dedupe_key
        if key in merged:
            for fb in row.found_by:
                if fb not in merged[key].found_by:
                    merged[key].found_by.append(fb)
        else:
            merged[key] = row
    rows = [r for r in merged.values() if r.app_number or r.mark_text]

    excluded = _apply_exclusions(rows, req.exclusions)
    if excluded:
        result.warnings.append(f"{excluded} row(s) flagged as the client's own assets")

    rows, wipo_dupes = _prefer_local_office(rows)
    if wipo_dupes:
        result.warnings.append(
            f"{wipo_dupes} WIPO record(s) merged into the local office's record for "
            "the same mark — the local number, status and dates are reported, with "
            "the international registration number kept alongside them")

    rows, noise = _score(rows, crit_set, client_goods=req.goods_text or "")
    if noise:
        result.warnings.append(
            f"{noise} candidate(s) discarded as blocking noise — returned by the "
            "register's coarse pre-filter but matching no declared criterion")
    result.rows = _sort(rows)

    # Companies House runs after the trademark layer and never blocks it: a
    # company-register outage must not cost us the trademark audit, which is
    # the part a client is actually paying for.
    if req.include_companies:
        try:
            from .companies import search_companies
            # The applicant's own entity is an exclusion by definition.
            cexcl = list(req.exclusions) + ([req.applicant] if req.applicant else [])
            crows, cnotes = search_companies(crit_set, exclusions=cexcl)
            result.company_rows = crows
            result.searches_run.extend(cnotes)
        except Exception as exc:
            result.warnings.append(
                f"WARNING Companies House layer failed and was skipped: {exc}. "
                "The trademark layer is unaffected; re-run to include companies.")

    # Domains: RDAP + DNS + HTTP on the plan's candidates.
    if req.include_domains:
        try:
            from .domains import execute as run_domains
            drows, dnotes = run_domains(result.plan, own_domains=req.own_domains)
            result.domain_rows = drows
            result.searches_run.extend(dnotes)
        except Exception as exc:
            result.warnings.append(f"WARNING domain layer failed and was skipped: {exc}")

    # Serper: Google web + images, every selected social and marketplace.
    if req.include_serp:
        try:
            from .serper import execute as run_serper
            srows, snotes = run_serper(result.plan)
            _serp_exclusions(srows, req)
            _serp_score(srows, req)
            result.serp_rows = srows
            result.searches_run.extend(snotes)
        except Exception as exc:
            result.warnings.append(f"WARNING Serper layer failed and was skipped: {exc}")

    # Decision I, second half. A DECLARED search that finds nothing on the
    # register is not a failure — on a mark chosen for Exact Match because its
    # words are common, it is very often the correct answer. Say so plainly,
    # and name the one broadening that is allowed, so a staff member reading
    # an empty register section knows why it is empty and what the alternative
    # would cost. The engine does not take that alternative: Jonathan,
    # 22 Sep 2026, "the fall back is too broad" — it is a person's decision.
    if req.criteria_declared and not result.rows and not req.fallback_from_run:
        declared = "; ".join(f"{c.match_type}: {c.phrase}" for c in crit_set.criteria)
        result.warnings.append(
            f"NOTHING FOUND ON THE REGISTER for the criteria the order form "
            f"declared ({declared}). That is a result, not an error — nothing "
            f"on the searched registers matches. The only broadening available "
            f"is Contains on the whole phrase "
            f"('Contains: {req.mark_text}'), which is deliberately not applied "
            f"automatically because it is broad. Run it from Triage if the "
            f"client needs the wider field.")

    _canary(result)
    return result


def _serp_exclusions(rows, req) -> None:
    """Flag the client's own site and handles. Never removes a row."""
    import re
    own_d = {d.lower() for d in (req.own_domains or [])}
    own_h = {h.lower().lstrip("@") for h in (req.own_handles or [])}
    for r in rows:
        dom = (r.source_domain or "").lower()
        path = re.sub(r"^https?://[^/]+/", "", r.url or "").lower()
        first = path.split("/")[0].lstrip("@") if path else ""
        if dom in own_d or any(dom.endswith("." + d) for d in own_d):
            r.exclusion_status = "excluded_initial_asset"
        elif first and first in own_h:
            r.exclusion_status = "excluded_initial_asset"


def _serp_score(rows, req) -> None:
    """Title/snippet similarity to the mark, through the same scorer.

    A web hit has no status or classes, so only similarity contributes;
    that keeps SERP rows deliberately below register rows of equal name
    match. Position is recorded, not scored — prominence is context for a
    reviewer, not evidence of rights.
    """
    from .scoring_paths import score_web_row, web_searches

    ws = web_searches(req.mark_text)
    for r in rows:
        text = f"{r.title} {r.seller}".strip() or r.url
        out = score_web_row(text=text, url=r.url or "",
                            mark_text=req.mark_text, searches=ws)
        r.score, r.band = out["score"], out["band"]
        r.components = out["components"]
        r.explanation = out["explanation"]
