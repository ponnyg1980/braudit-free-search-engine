// Supabase Edge Function — journey logging + Free Search -> Brand Audit
// continuity. Sibling to the `free-search` function; same conventions
// (CORS, tenant allow-list, json() helper), separate function because this
// one has nothing to do with scoring and shouldn't share a deploy unit with
// the parity-locked search path.
//
// WHY THIS EXISTS (Jonathan, 01 Aug 2026)
//   1. Free Search recorded nothing unless a visitor became a lead — an
//      abandoned session left no trace. journey_sessions + journey_events
//      record every real step/decision from the first input onward.
//   2. The live Brand Audit form re-asks everything from scratch and saves
//      nothing until final submit. brand_audit_requests/_brands are created
//      as DRAFTS the moment someone starts, upserted continuously, and can
//      be pre-filled from a journey_sessions row handed off from Free
//      Search — everything pre-filled stays editable, nothing is locked.
//
// Deliberately session-token based, not tied to the Free Temmy Account/OTP
// gate in the `free-search` function — that's a separate, not-yet-live
// initiative. No login required here.
//
// Routes (all under /journey):
//   POST /session/start        -> create a journey_sessions row
//   POST /session/event        -> log one event + optional snapshot upsert;
//                                  also the automatic enrichment trigger and
//                                  the Zoho Lead push (see REVISED note)
//   GET  /session?id=          -> fetch a session (Brand Audit pre-fill)
//   POST /audit/start          -> create a brand_audit_requests draft
//   POST /audit/event          -> log one event + optional request upsert
//   POST /audit/brand          -> upsert one brand_audit_brands row
//   POST /audit/brand-remove   -> delete one brand row
//   POST /audit/submit         -> finalise a request (status -> submitted)
//   GET  /audit?id=            -> fetch a request + its brands
//   POST /enrich                -> manual/explicit re-run of the resolver for
//                                  one session (also called automatically —
//                                  see REVISED note)
//   POST /zoho-linked           -> Zoho Flow posts back {session_id or
//                                  request_id, zoho_lead_id, zoho_lead_url}
//                                  once it creates the record, so a later
//                                  push for the SAME visitor updates it
//                                  instead of creating a duplicate
//
// REVISED 01 Aug 2026 (Jonathan): "Cerebrum is being used to target people
// for the industry report, so it makes sense those reports start there,
// track through there and then go to Zoho Leads... if we create a basic
// supabase to hold the enquiry data as people go through the process, we
// only need to deliver via ZohoFlow [at the meaningful moments], and then
// Zoho can send it to Cerebrum for outreach, as it will need to do with all
// of our deal and account activity."
//
// Unlike Industry Report (which Cerebrum-initiated targeting genuinely
// drives, so Cerebrum tracks it first), Free Search and Brand Audit are
// organic website traffic. So THIS function no longer talks to Cerebrum at
// all — every previous `CEREBRUM_WEBHOOK_URL` push has been removed. The
// flow is now: this function captures continuously in Supabase (unchanged —
// "every time they move through pages it capture the inputs and/or
// changes"), pushes to Zoho at the meaningful moments (contact captured,
// brand audit submitted, or a resolver-found contact), and Zoho's own
// existing downstream automation (external to this codebase, same as for
// deal/account activity) is what relays to Cerebrum from there. If a
// Cerebrum-side workflow for Free Search does get built later, it should
// trigger off Zoho, not off a second direct feed from here.
//
// Env: ALLOWED_ORIGINS, TENANT_ALLOWLIST, SUPABASE_URL,
//      SUPABASE_SERVICE_ROLE_KEY (same values as the free-search function),
//      plus:
//      ZOHO_FLOW_URL          a Zoho Flow webhook — same pattern as Industry
//                             Report's Zoho push. Flow does the dedupe; we
//                             just hand it a flat payload with a `record_type`
//                             discriminator (lead | brand_audit) so one Flow
//                             can branch, rather than needing several URLs.
//                             Includes `zoho_lead_id` when we already have
//                             one for this visitor, so Flow updates instead
//                             of creating a duplicate.
//      ENGINE_URL             the private Python engine (Cloud Run) — same
//                             value as the free-search function's env. Used
//                             ONLY to proxy POST /enrich to the engine's own
//                             /enrich route (freesearch/enrichment.py). No
//                             scoring logic here, same discipline as
//                             free-search/index.ts.

import { serve } from "https://deno.land/std@0.208.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ALLOWED_ORIGINS = (Deno.env.get("ALLOWED_ORIGINS") ?? "")
  .split(",").map((s) => s.trim()).filter(Boolean);
const TENANT_ALLOWLIST = new Set(
  (Deno.env.get("TENANT_ALLOWLIST") ?? "tmh").split(",").map((s) => s.trim()),
);
const ZOHO_FLOW_URL = Deno.env.get("ZOHO_FLOW_URL") ?? "";
const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "http://localhost:8080";

const admin = createClient(
  Deno.env.get("SUPABASE_URL") ?? "",
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
  { auth: { persistSession: false } },
);

// --- helpers -----------------------------------------------------------------

function corsHeaders(origin: string | null): HeadersInit {
  const allow = origin && (ALLOWED_ORIGINS.length === 0 ||
    ALLOWED_ORIGINS.includes(origin)) ? origin : "null";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

function json(body: unknown, status = 200, origin: string | null = null) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
  });
}

async function readJson(req: Request): Promise<Record<string, unknown> | null> {
  try { return await req.json(); } catch { return null; }
}

// Only these columns may be written to a snapshot via `snapshot: {...}` in a
// session/event or audit/event call — an explicit allow-list so a client
// bug can never silently write to id/status/timestamps/foreign keys.
const SESSION_SNAPSHOT_FIELDS = new Set([
  "name", "trading_name", "first_name", "last_name", "email", "phone",
  "business_name", "business_website", "consent_marketing", "classes",
  "tagline", "has_logo", "trading_now", "planning_to_trade", "class_source",
  "last_result", "current_screen", "status",
  // email_source: how we came by the address — 'ai_gate' | 'report_form' |
  // 'resolver'. Consent differs by source and Cerebrum must be able to tell
  // them apart; an address given to get class suggestions is not permission
  // to market.
  "email_source",
  // Added 18 Aug 2026 (migration 0005). The wizard had been sending this for
  // two days and it was being dropped here — the allow-list is the whole
  // point of this constant, so a new field is not captured until it is added.
  "channels",
]);
const AUDIT_SNAPSHOT_FIELDS = new Set([
  "first_name", "last_name", "email", "phone", "business_name",
  "business_website", "consent_marketing", "trading_now",
  "planning_to_trade", "current_screen",
]);
const BRAND_FIELDS = new Set([
  "brand_name", "classes", "terms", "class_source", "logo_flag",
  "logo_later", "logo_url", "tagline", "website_url",
  "business_description", "competitor_name", "competitor_website",
  "position",
]);

function pick(src: Record<string, unknown>, allow: Set<string>) {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(src)) if (allow.has(k)) out[k] = src[k];
  return out;
}

// Fire-and-forget POST — never awaited by the caller's response, never
// throws. Same contract as tmh_report_lead()'s wp_remote_post calls: the
// browser's own request must never hang or fail because a downstream system
// (Cerebrum, Zoho) is slow or down.
function fireAndForget(url: string, body: Record<string, unknown>) {
  if (!url) return; // not configured yet — silently a no-op, same as the
                     // free-search function's "not configured" branch
  fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {});
}

// Placeholder identity for a visitor who hasn't given an email yet — kept
// only for internal bookkeeping now (Cerebrum no longer reads it directly
// from this function). session_id is the real key everywhere below.
function contactEmailFor(sessionId: string, email: unknown): string {
  const e = typeof email === "string" ? email.trim() : "";
  return e || `${sessionId}@leads.thetrademarkhelpline.com`;
}

async function currentSession(sessionId: string) {
  const { data } = await admin.from("journey_sessions")
    .select("*").eq("session_id", sessionId).maybeSingle();
  return data;
}

// Zoho gets the record at the meaningful moments — contact captured, brand
// audit submitted, or a resolver-found contact (see /session/event's
// search_run branch) — never on every step. Same discipline as Industry
// Report: no anonymous rows in the CRM. `record_type` lets one Zoho Flow
// branch between a plain Free Search lead and a Brand Audit submission
// rather than needing two Flow URLs.
//
// `zoho_lead_id`, when we already have one for this visitor (see
// /zoho-linked below), tells Flow to UPDATE that record rather than create
// a second one — this is what makes "we only need to deliver via ZohoFlow
// [at the meaningful moments]" hold even for a visitor who both completes a
// Free Search AND goes on to submit a Brand Audit: one Zoho Lead, kept
// current, not two.
//
// `lead_status` is set to "New" ONLY on first creation (omit it — undefined
// keys are dropped by JSON.stringify — on an update) because Lead_Status
// is a REP-MANAGED field afterward (its real picklist is things like
// "Assigned"/"Qualified for Conversion"/"Not Contacted" — a human or an
// existing Zoho workflow owns it from here, this function must not stomp on
// a status a rep has already changed).
function zohoPayload(recordType: "lead" | "brand_audit",
                      fields: Record<string, unknown>,
                      opts: { zohoLeadId?: string | null; isNewRecord?: boolean } = {}) {
  return {
    record_type: recordType, lead_source_hint: "Trademark Search Form",
    zoho_lead_id: opts.zohoLeadId || undefined,
    lead_status: opts.isNewRecord ? "New" : undefined,
    ...fields,
  };
}

// Shared by the automatic search_run trigger (/session/event) and the
// explicit /enrich route (kept for a manual re-run) — one place that talks
// to the engine's resolver, stores the result, and logs the event, so the
// two callers can't drift out of sync with each other.
//
// class_source is ONE object — whichever class-picking route the visitor
// last used (see free-search.html). Route 2 is the visitor's OWN website (a
// legitimate resolution input); routes 4/5 are a COMPETITOR's website/
// trademark (corroboration context ONLY — see contact_resolver.py's "NEVER
// RESOLVE FROM THE COMPETITOR FIELDS"); route 1 is a free-text business
// description (context); route 3 is the visitor's own SIC/company pick
// (context). Getting route 2 vs. 4 backwards here would feed a competitor's
// site into resolution — same mistake the spec explicitly warns against —
// so this mapping is deliberately explicit, not a generic "grab whatever's
// in class_source.value".
async function runEnrichment(session: Record<string, unknown>):
    Promise<{ found: boolean; engineResult: Record<string, unknown> }> {
  const session_id = String(session.session_id);
  const cs = (session.class_source ?? {}) as Record<string, unknown>;
  const csRoute = String(cs.route ?? "");
  const ownWebsiteFromWizard = csRoute === "2" ? String(cs.value ?? "") : "";
  const competitorWebsite = csRoute === "4" ? String(cs.value ?? "") : "";
  const competitorTrademark = csRoute === "5" ? String(cs.value ?? "") : "";
  const competitorName = csRoute === "5" ? String(cs.label ?? "") : "";
  const descriptionContext = csRoute === "1" ? String(cs.value ?? "") : "";
  const sicContext = csRoute === "3" ? String(cs.label ?? "") : "";

  let engineResult: Record<string, unknown>;
  try {
    const r = await fetch(`${ENGINE_URL}/enrich`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        search_term: session.name,
        business_website: session.business_website || ownWebsiteFromWizard || null,
        business_name: session.business_name ?? null,
        trading_name: session.trading_name ?? sicContext ?? null,
        tagline: session.tagline ?? descriptionContext ?? null,
        competitor_website: competitorWebsite || null,
        competitor_trademark: competitorTrademark || null,
        competitor_name: competitorName || null,
      }),
    });
    engineResult = await r.json();
  } catch {
    engineResult = { ok: false, found: false, reason: "engine_unavailable" };
  }

  const found = Boolean(engineResult.found);
  await admin.from("journey_sessions").update({
    enrichment_status: found ? "found" : "not_found",
    enrichment_result: engineResult,
    enriched_at: new Date().toISOString(),
  }).eq("session_id", session_id);

  await admin.from("journey_events").insert({
    session_id,
    event_type: found ? "enrichment_found" : "enrichment_not_found",
    payload: engineResult,
  });

  return { found, engineResult };
}

// --- handler -------------------------------------------------------------------

serve(async (req) => {
  const origin = req.headers.get("Origin");
  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/journey/, "") || "/";

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }

  // --- session/start -----------------------------------------------------------
  if (req.method === "POST" && path === "/session/start") {
    const body = await readJson(req);
    if (!body) return json({ ok: false, error: "invalid JSON" }, 400, origin);
    const tenant = String(body.tenant_id ?? "tmh");
    if (!TENANT_ALLOWLIST.has(tenant)) {
      return json({ ok: false, error: "unknown tenant" }, 403, origin);
    }
    const session_id = crypto.randomUUID();
    const { error } = await admin.from("journey_sessions").insert({
      session_id,
      tenant_id: tenant,
      source: body.source === "brand_audit_direct" ? "brand_audit_direct" : "free_search",
      name: body.name ?? null,
      trading_name: body.trading_name ?? null,
      current_screen: body.screen ?? null,
    });
    if (error) return json({ ok: false, error: "could not start session" }, 500, origin);

    return json({ ok: true, session_id }, 200, origin);
  }

  // --- session/event -------------------------------------------------------------
  // One call does both: append the event row (the permanent record) AND
  // (optionally) merge a partial snapshot onto journey_sessions (the
  // current-state view Brand Audit reads for pre-fill). Never fails the
  // event write because the snapshot merge had nothing in it.
  if (req.method === "POST" && path === "/session/event") {
    const body = await readJson(req);
    if (!body || !body.session_id || !body.event_type) {
      return json({ ok: false, error: "session_id and event_type required" }, 400, origin);
    }
    const session_id = String(body.session_id);

    const { error: evErr } = await admin.from("journey_events").insert({
      session_id,
      event_type: String(body.event_type),
      screen: body.screen ?? null,
      payload: body.payload ?? {},
    });
    if (evErr) return json({ ok: false, error: "could not log event" }, 500, origin);

    if (body.snapshot && typeof body.snapshot === "object") {
      const patch = pick(body.snapshot as Record<string, unknown>, SESSION_SNAPSHOT_FIELDS);
      if (Object.keys(patch).length) {
        patch.updated_at = new Date().toISOString();
        await admin.from("journey_sessions").update(patch).eq("session_id", session_id);
        // Best-effort — the event above is already durable even if this fails.
      }
    }

    // Read the session back post-merge so any push below reflects the
    // current picture, not just whatever this one event happened to carry.
    const session = await currentSession(session_id);
    if (session) {
      // Zoho gets it the moment real contact info exists — "once a search
      // submits to their contact information it would then fire to Zoho."
      // zoho_lead_id carries forward if this visitor was already pushed
      // (e.g. found by the automatic resolver below on an earlier event) so
      // Flow updates that record instead of creating a second one.
      if (body.event_type === "lead_captured" && session.email) {
        fireAndForget(ZOHO_FLOW_URL, zohoPayload("lead", {
          session_id, tenant_id: session.tenant_id, search_term: session.name,
          classes: session.classes, class_source: session.class_source,
          first_name: session.first_name, last_name: session.last_name,
          email: session.email, phone: session.phone,
          business_name: session.business_name, business_website: session.business_website,
          trading_now: session.trading_now, planning_to_trade: session.planning_to_trade,
        }, { zohoLeadId: session.zoho_lead_id, isNewRecord: !session.zoho_lead_id }));
      }

      // Automatic enrichment trigger — REPLACES the old Cerebrum-initiated
      // "needs_enrichment" branch, which stopped being reachable once this
      // function stopped pushing to Cerebrum directly (see header comment).
      // Fires once, right when a search completes with no contact info at
      // all: `search_run` is the event free-search.html logs when a search
      // actually executes. Guarded so it never re-runs on a session that's
      // already been resolved (enrichment_status set) or that already has a
      // real channel (email/phone) or a Zoho record (zoho_lead_id).
      if (body.event_type === "search_run" && !session.email && !session.phone &&
          !session.zoho_lead_id && !session.enrichment_status && session.name) {
        const { found, engineResult } = await runEnrichment(session);
        if (found) {
          // A resolver-found contact is not consent to market — see
          // contact_resolver.py's compliance note. Lead_Source "Search
          // Result Only" is an EXISTING real Zoho picklist value that
          // already exists for exactly this shape of lead (found via a
          // search, not self-submitted) — downstream Zoho/Cerebrum
          // automation can treat it distinctly from a self-given contact.
          fireAndForget(ZOHO_FLOW_URL, zohoPayload("lead", {
            session_id, tenant_id: session.tenant_id, search_term: session.name,
            classes: session.classes, class_source: session.class_source,
            business_name: session.business_name,
            website: engineResult.website, phone: engineResult.phone,
            address: engineResult.address, company_number: engineResult.company_number,
            sic_codes: engineResult.sic_codes, officer_names: engineResult.officer_names,
            resolution_step: engineResult.step,
            is_enriched_contact: true,
            lead_source_hint: "Search Result Only",
          }, { zohoLeadId: null, isNewRecord: true }));
        }
      }
    }
    return json({ ok: true }, 200, origin);
  }

  // --- session (GET) ---------------------------------------------------------------
  if (req.method === "GET" && path === "/session") {
    const id = url.searchParams.get("id");
    if (!id) return json({ ok: false, error: "id required" }, 400, origin);
    const { data, error } = await admin.from("journey_sessions")
      .select("*").eq("session_id", id).maybeSingle();
    if (error) return json({ ok: false, error: "lookup failed" }, 500, origin);
    if (!data) return json({ ok: false, error: "not found" }, 404, origin);
    return json({ ok: true, session: data }, 200, origin);
  }

  // --- audit/start -----------------------------------------------------------------
  if (req.method === "POST" && path === "/audit/start") {
    const body = await readJson(req);
    if (!body) return json({ ok: false, error: "invalid JSON" }, 400, origin);
    const tenant = String(body.tenant_id ?? "tmh");
    if (!TENANT_ALLOWLIST.has(tenant)) {
      return json({ ok: false, error: "unknown tenant" }, 403, origin);
    }
    const { data, error } = await admin.from("brand_audit_requests").insert({
      tenant_id: tenant,
      session_id: body.session_id ?? null,
      current_screen: body.screen ?? null,
    }).select("id").single();
    if (error || !data) return json({ ok: false, error: "could not start audit request" }, 500, origin);

    await admin.from("journey_events").insert({
      request_id: data.id,
      session_id: body.session_id ?? null,
      event_type: "brand_audit_handoff",
      screen: body.screen ?? null,
      payload: { from_session: Boolean(body.session_id) },
    });

    return json({ ok: true, request_id: data.id }, 200, origin);
  }

  // --- audit/event -----------------------------------------------------------------
  if (req.method === "POST" && path === "/audit/event") {
    const body = await readJson(req);
    if (!body || !body.request_id || !body.event_type) {
      return json({ ok: false, error: "request_id and event_type required" }, 400, origin);
    }
    const request_id = String(body.request_id);

    const { error: evErr } = await admin.from("journey_events").insert({
      request_id,
      session_id: body.session_id ?? null,
      event_type: String(body.event_type),
      screen: body.screen ?? null,
      payload: body.payload ?? {},
    });
    if (evErr) return json({ ok: false, error: "could not log event" }, 500, origin);

    if (body.snapshot && typeof body.snapshot === "object") {
      const patch = pick(body.snapshot as Record<string, unknown>, AUDIT_SNAPSHOT_FIELDS);
      if (Object.keys(patch).length) {
        patch.updated_at = new Date().toISOString();
        await admin.from("brand_audit_requests").update(patch).eq("id", request_id);
      }
    }
    return json({ ok: true }, 200, origin);
  }

  // --- audit/brand (upsert one brand row) -------------------------------------------
  if (req.method === "POST" && path === "/audit/brand") {
    const body = await readJson(req);
    if (!body || !body.request_id) {
      return json({ ok: false, error: "request_id required" }, 400, origin);
    }
    const fields = pick(body, BRAND_FIELDS);
    fields.updated_at = new Date().toISOString();

    if (body.brand_id) {
      const { error } = await admin.from("brand_audit_brands")
        .update(fields).eq("id", String(body.brand_id));
      if (error) return json({ ok: false, error: "could not update brand" }, 500, origin);
      return json({ ok: true, brand_id: body.brand_id }, 200, origin);
    }
    const { data, error } = await admin.from("brand_audit_brands").insert({
      request_id: String(body.request_id), ...fields,
    }).select("id").single();
    if (error || !data) return json({ ok: false, error: "could not create brand" }, 500, origin);
    return json({ ok: true, brand_id: data.id }, 200, origin);
  }

  // --- audit/brand-remove ------------------------------------------------------------
  if (req.method === "POST" && path === "/audit/brand-remove") {
    const body = await readJson(req);
    if (!body || !body.brand_id) {
      return json({ ok: false, error: "brand_id required" }, 400, origin);
    }
    // Soft to the caller, hard delete in the DB — the removal itself is
    // already permanently recorded as a journey_event by the front end
    // before it calls this, so nothing about the decision is lost.
    await admin.from("brand_audit_brands").delete().eq("id", String(body.brand_id));
    return json({ ok: true }, 200, origin);
  }

  // --- audit/submit --------------------------------------------------------------------
  if (req.method === "POST" && path === "/audit/submit") {
    const body = await readJson(req);
    if (!body || !body.request_id) {
      return json({ ok: false, error: "request_id required" }, 400, origin);
    }
    const request_id = String(body.request_id);
    const patch = pick(body, AUDIT_SNAPSHOT_FIELDS);
    patch.status = "submitted";
    patch.submitted_at = new Date().toISOString();
    patch.updated_at = patch.submitted_at;

    const { error } = await admin.from("brand_audit_requests")
      .update(patch).eq("id", request_id);
    if (error) return json({ ok: false, error: "could not submit" }, 500, origin);

    await admin.from("journey_events").insert({
      request_id, event_type: "audit_submitted", payload: {},
    });

    // Full record now exists — pull it (+ brands, with their term_basket
    // detail from class_source) and push to Zoho Flow. Cerebrum no longer
    // receives this directly (see header note, 01 Aug redesign) — it will
    // hear about this contact from Zoho's own downstream automation, same
    // as any other deal/account activity.
    const { data: reqRow } = await admin.from("brand_audit_requests")
      .select("*").eq("id", request_id).maybeSingle();
    const { data: brandRows } = await admin.from("brand_audit_brands")
      .select("*").eq("request_id", request_id).order("position", { ascending: true });

    // A visitor who arrived here via brand_audit_handoff (audit/start with a
    // session_id) may already have a Zoho Lead from an earlier push on that
    // session (lead_captured, or an automatic enrichment find). Reuse it —
    // brand_audit_requests.zoho_lead_id only gets set by its OWN /zoho-linked
    // callback, so without this lookup a returning session would create a
    // second Zoho record for the same person instead of updating the first.
    let zohoLeadId: string | null = reqRow?.zoho_lead_id ?? null;
    if (!zohoLeadId && reqRow?.session_id) {
      const { data: sessRow } = await admin.from("journey_sessions")
        .select("zoho_lead_id").eq("session_id", reqRow.session_id).maybeSingle();
      zohoLeadId = sessRow?.zoho_lead_id ?? null;
    }

    if (reqRow) {
      fireAndForget(ZOHO_FLOW_URL, zohoPayload("brand_audit", {
        request_id, session_id: reqRow.session_id, tenant_id: reqRow.tenant_id,
        first_name: reqRow.first_name, last_name: reqRow.last_name,
        email: reqRow.email, phone: reqRow.phone,
        business_name: reqRow.business_name, business_website: reqRow.business_website,
        trading_now: reqRow.trading_now, planning_to_trade: reqRow.planning_to_trade,
        brands: (brandRows ?? []).map((b: Record<string, unknown>) => ({
          brand_name: b.brand_name, classes: b.classes, terms: b.terms,
          class_source: b.class_source, tagline: b.tagline,
          website_url: b.website_url, business_description: b.business_description,
          competitor_name: b.competitor_name, competitor_website: b.competitor_website,
        })),
      }, { zohoLeadId, isNewRecord: !zohoLeadId }));
    }

    return json({ ok: true, request_id }, 200, origin);
  }

  // --- audit (GET, request + brands) ---------------------------------------------------
  if (req.method === "GET" && path === "/audit") {
    const id = url.searchParams.get("id");
    if (!id) return json({ ok: false, error: "id required" }, 400, origin);
    const { data: reqRow, error: reqErr } = await admin.from("brand_audit_requests")
      .select("*").eq("id", id).maybeSingle();
    if (reqErr) return json({ ok: false, error: "lookup failed" }, 500, origin);
    if (!reqRow) return json({ ok: false, error: "not found" }, 404, origin);
    const { data: brands } = await admin.from("brand_audit_brands")
      .select("*").eq("request_id", id).order("position", { ascending: true });
    return json({ ok: true, request: reqRow, brands: brands ?? [] }, 200, origin);
  }

  // --- enrich (manual/explicit re-run) ------------------------------------------
  // The AUTOMATIC trigger lives in /session/event's search_run branch above
  // (runEnrichment()) — this route is now only for an explicit re-run (e.g.
  // ops tooling, or a future manual Zoho/Cerebrum button that wants a fresh
  // attempt). Same idempotency guard, same shared runEnrichment(), and — as
  // of the 01 Aug redesign — the SAME Zoho push a found result gets
  // automatically, not a Cerebrum push (this function no longer talks to
  // Cerebrum at all, see header comment).
  if (req.method === "POST" && path === "/enrich") {
    const body = await readJson(req);
    if (!body || !body.session_id) {
      return json({ ok: false, error: "session_id required" }, 400, origin);
    }
    const session_id = String(body.session_id);
    const session = await currentSession(session_id);
    if (!session) return json({ ok: false, error: "unknown session" }, 404, origin);

    // Idempotent / cheap: a session that already has a real contact channel
    // or a Zoho record doesn't need enrichment, and re-running it would just
    // spend Serper credits for nothing.
    if (session.email || session.phone || session.zoho_lead_id) {
      return json({ ok: true, found: false, reason: "already_has_contact" }, 200, origin);
    }

    const { found, engineResult } = await runEnrichment(session);
    if (found) {
      fireAndForget(ZOHO_FLOW_URL, zohoPayload("lead", {
        session_id, tenant_id: session.tenant_id, search_term: session.name,
        classes: session.classes, class_source: session.class_source,
        business_name: session.business_name,
        website: engineResult.website, phone: engineResult.phone,
        address: engineResult.address, company_number: engineResult.company_number,
        sic_codes: engineResult.sic_codes, officer_names: engineResult.officer_names,
        resolution_step: engineResult.step,
        is_enriched_contact: true,
        lead_source_hint: "Search Result Only",
      }, { zohoLeadId: null, isNewRecord: true }));
    }

    return json({ ok: true, ...engineResult }, 200, origin);
  }

  // --- zoho-linked ---------------------------------------------------------------
  // Zoho Flow posts back here once it creates a record from a zohoPayload
  // push, same pattern as Industry Report's `zoho_linked` event. Stores the
  // ID on whichever row it belongs to so the NEXT push for the same visitor
  // (lead_captured after an enrichment push, or audit_submitted after a
  // lead_captured push) updates that record instead of creating a duplicate
  // — see zohoPayload's docstring.
  if (req.method === "POST" && path === "/zoho-linked") {
    const body = await readJson(req);
    if (!body || !body.zoho_lead_id) {
      return json({ ok: false, error: "zoho_lead_id required" }, 400, origin);
    }
    const patch = {
      zoho_lead_id: String(body.zoho_lead_id),
      zoho_lead_url: body.zoho_lead_url ? String(body.zoho_lead_url) : null,
    };
    if (body.session_id) {
      await admin.from("journey_sessions").update(patch)
        .eq("session_id", String(body.session_id));
    }
    if (body.request_id) {
      await admin.from("brand_audit_requests").update(patch)
        .eq("id", String(body.request_id));
    }
    if (!body.session_id && !body.request_id) {
      return json({ ok: false, error: "session_id or request_id required" }, 400, origin);
    }
    return json({ ok: true }, 200, origin);
  }

  return json({ ok: false, error: "not found" }, 404, origin);
});
