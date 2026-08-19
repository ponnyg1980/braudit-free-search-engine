// Supabase Edge Function — public front door for Braudit Free Search.
//
// The ONLY internet-facing surface. It does what Supabase is good at — CORS,
// tenant allow-listing, the account gate, OTP, and the lead/CRM writes — and
// proxies the actual search to the Python engine, the parity-locked scoring
// source of truth. NO scoring logic here, ever: duplicating it would re-open
// the free-vs-paid risk-band divergence the Python parity tests prevent.
//
// THE GATE (agreed 09 Jul 2026)
//   • First FREE_ANON_SEARCHES from an IP (per tenant): anonymous, allowed.
//   • Beyond that: 401 account_required — front-end runs the email-OTP Free
//     Temmy Account flow, then retries with a session.
//   • Signed-in account holder: unlimited searches, gated:true (verified
//     email + business info already captured — they're a known lead).
//
// Routes:
//   GET  /free-search/jurisdictions   -> picker data (proxied, cached)
//   POST /free-search                 -> gated search
//   POST /free-search/account         -> finalise Free Temmy Account (STUB→Zoho)
//
// Env:
//   ENGINE_URL              Python engine base URL (private, Cloud Run)
//   ALLOWED_ORIGINS         comma-list of embeddable origins
//   TENANT_ALLOWLIST        comma-list of accepted tenant_id values
//   IP_HASH_SALT            secret salt for IP hashing (GDPR — no raw IP stored)
//   SUPABASE_URL            (auto-injected)
//   SUPABASE_SERVICE_ROLE_KEY (auto-injected)

import { serve } from "https://deno.land/std@0.208.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const ENGINE_URL = Deno.env.get("ENGINE_URL") ?? "http://localhost:8080";
const ALLOWED_ORIGINS = (Deno.env.get("ALLOWED_ORIGINS") ?? "")
  .split(",").map((s) => s.trim()).filter(Boolean);
// How many anonymous searches an IP gets before it must create a free
// account. Two, not one (Jonathan, 18 Aug): one converts harder but people
// bounce before they have seen the tool is any good; two lets them find the
// value and still asks early enough to matter. Changing this number is the
// whole knob — nothing else needs touching.
const FREE_ANON_SEARCHES = 2;

const TENANT_ALLOWLIST = new Set(
  (Deno.env.get("TENANT_ALLOWLIST") ?? "tmh").split(",").map((s) => s.trim()),
);
const IP_HASH_SALT = Deno.env.get("IP_HASH_SALT") ?? "dev-salt-change-me";

const admin = createClient(
  Deno.env.get("SUPABASE_URL") ?? "",
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
  { auth: { persistSession: false } },
);

// --- helpers ---------------------------------------------------------------

function corsHeaders(origin: string | null): HeadersInit {
  const allow = origin && (ALLOWED_ORIGINS.length === 0 ||
    ALLOWED_ORIGINS.includes(origin)) ? origin : "null";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Vary": "Origin",
  };
}

function json(body: unknown, status = 200, origin: string | null = null,
              extra: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin),
               ...extra },
  });
}

async function hashIp(ip: string): Promise<string> {
  // Salted SHA-256 — supports rate-limiting/fraud checks without storing a
  // raw IP (GDPR data-minimisation; consistent with the org's LIA).
  const data = new TextEncoder().encode(`${IP_HASH_SALT}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16)
    .padStart(2, "0")).join("");
}

async function accountFromAuth(req: Request): Promise<string | null> {
  // A signed-in Free Temmy Account presents a Supabase JWT.
  const auth = req.headers.get("Authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token) return null;
  const { data, error } = await admin.auth.getUser(token);
  if (error || !data?.user) return null;
  return data.user.id;
}

// Shared secret so the engine can refuse anything that did not come through
// this gate. Optional on purpose: leave ENGINE_SHARED_KEY unset on both sides
// and behaviour is exactly as before, which means these can be deployed in
// either order without an outage. Set it on the ENGINE last.
const ENGINE_SHARED_KEY = Deno.env.get("ENGINE_SHARED_KEY") ?? "";

async function proxyText(path: string, init: RequestInit) {
  const headers = new Headers(init.headers ?? {});
  if (ENGINE_SHARED_KEY) headers.set("X-Engine-Key", ENGINE_SHARED_KEY);
  const r = await fetch(`${ENGINE_URL}${path}`, { ...init, headers });
  return { status: r.status, text: await r.text() };
}

// --- handler ---------------------------------------------------------------

serve(async (req) => {
  const origin = req.headers.get("Origin");
  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/free-search/, "") || "/";

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }

  // Picker data — static, cache hard.
  if (req.method === "GET" && path === "/jurisdictions") {
    const r = await proxyText("/jurisdictions", { method: "GET" });
    return json(JSON.parse(r.text), r.status, origin,
      { "Cache-Control": "public, max-age=86400" });
  }

  // --- gated search --------------------------------------------------------
  if (req.method === "POST" && (path === "/" || path === "")) {
    let payload: Record<string, unknown>;
    try {
      payload = await req.json();
    } catch {
      return json({ ok: false, error: "invalid JSON" }, 400, origin);
    }

    const tenant = String(payload.tenant_id ?? "tmh");
    if (!TENANT_ALLOWLIST.has(tenant)) {
      return json({ ok: false, error: "unknown tenant" }, 403, origin);
    }

    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
      "unknown";
    const ipHash = await hashIp(ip);
    const accountId = await accountFromAuth(req);

    // Gate: anonymous callers get exactly one search per IP/tenant.
    if (!accountId) {
      const { data: count } = await admin.rpc("free_search_count_for_ip",
        { p_ip_hash: ipHash, p_tenant: tenant });
      if ((count ?? 0) >= FREE_ANON_SEARCHES) {
        return json({
          ok: false,
          status: 401,
          error: "account_required",
          message: "You've used your free searches. Create a free " +
            "Temmy account (takes a minute, email verification only) for " +
            "unlimited searches and your full report.",
          signup: {
            method: "email_otp",
            collects: ["first_name", "last_name", "email", "phone",
                       "business_name", "business_website"],
            benefits: ["Unlimited free UK searches",
                       "Full conflict list with ownership detail",
                       "Save your marks, classes and jurisdictions",
                       "Access the Temmy Portal"],
          },
        }, 401, origin);
      }
    }

    // Run the search. Account holders get the ungated report.
    const r = await proxyText("/free-search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // engine reads no auth; the Edge Function decides gating and asks the
      // engine for the gated shape when the caller is a known account.
      body: JSON.stringify({ ...payload, _gated: Boolean(accountId) }),
    });

    // Record usage (drives the gate + lead analytics). Best-effort.
    try {
      const parsed = JSON.parse(r.text);
      const s = parsed?.result?.summary ?? {};
      await admin.from("free_search_usage").insert({
        ip_hash: ipHash,
        tenant_id: tenant,
        account_id: accountId,
        primary_mark: String(payload.name ?? "").slice(0, 200),
        classes: Array.isArray(payload.classes) ? payload.classes : [],
        overall_risk: s.overall_risk ?? null,
        total_flagged: s.total_flagged ?? null,
      });
    } catch (_) { /* never fail the search on a ledger write */ }

    return json(JSON.parse(r.text), r.status, origin);
  }

  // --- finalise Free Temmy Account ----------------------------------------
  // Called after the client completes Supabase email-OTP verification. Writes
  // the business profile and (fast-follow) upserts the Zoho lead. Supabase
  // Auth handles the OTP itself client-side (signInWithOtp) — this handler
  // persists the business info tied to the now-verified user.
  if (req.method === "POST" && path === "/account") {
    const accountId = await accountFromAuth(req);
    if (!accountId) {
      return json({ ok: false, error: "verify your email first" }, 401, origin);
    }
    let body: Record<string, unknown>;
    try { body = await req.json(); } catch {
      return json({ ok: false, error: "invalid JSON" }, 400, origin);
    }

    const profile = {
      account_id: accountId,
      tenant_id: String(body.tenant_id ?? "tmh"),
      first_name: body.first_name ?? null,
      last_name: body.last_name ?? null,
      email: body.email ?? null,
      phone: body.phone ?? null,
      business_name: body.business_name ?? null,
      business_website: body.business_website ?? null,
      business_summary: body.business_summary ?? null,
      trading_now: body.trading_now ?? [],
      planning_to_trade: body.planning_to_trade ?? [],
      consent_marketing: Boolean(body.consent_marketing),
      updated_at: new Date().toISOString(),
    };
    const { error } = await admin.from("temmy_business_profile")
      .upsert(profile, { onConflict: "account_id" });
    if (error) {
      return json({ ok: false, error: "could not save profile" }, 500, origin);
    }

    // FAST-FOLLOW: push this profile to Zoho as a lead and store zoho_lead_id.
    // Expansion of grouped jurisdictions (EU->27) happens at that write via
    // the engine's expand_for_profiling equivalent.
    return json({
      ok: true,
      account_id: accountId,
      next: "search_unlocked",
      todo_zoho: "upsert lead + store zoho_lead_id (fast-follow)",
    }, 200, origin);
  }

  return json({ ok: false, error: "not found" }, 404, origin);
});
