-- Journey logging: Free Search -> Brand Audit continuity + full event log
-- =============================================================================
-- Agreed 01 Aug 2026 (Jonathan). Two problems this fixes:
--
--   1. Nothing recorded a visitor's inputs unless they became a lead. A Free
--      Search that was abandoned half-way left no trace at all. This is the
--      record of what was typed, picked and decided, from the first real
--      input onward — independent of whether it ever converts.
--
--   2. Brand Audit re-asked everything from scratch, even from someone who'd
--      just run a Free Search seconds earlier, and its own live Elementor
--      form saved nothing until final submit — abandon at step 3 of 4 and
--      the visitor's information is just gone. journey_sessions is the
--      bridge: Free Search writes to it as the visitor goes; Brand Audit
--      reads from it to pre-fill (everything stays editable) and keeps
--      writing to its own request/brand rows the same way — continuously,
--      not just on final submit.
--
-- Deliberately NOT built on the Free Temmy Account / OTP gate in migration
-- 0001 — that's a separate, not-yet-live initiative. This is session-token
-- based (an opaque id minted client-side, no login required) so it ships
-- independently and can plug into accounts later if that lands.

-- --- journey_sessions --------------------------------------------------------
-- One row per visitor journey. Created on the first real decision (typing
-- and submitting a name), not on page load — a blank visit isn't data.
-- Upserted continuously as the visitor moves through Free Search and, if
-- they continue, Brand Audit. This is the current-state snapshot; the full
-- history of how it got there lives in journey_events below.

create table if not exists public.journey_sessions (
    session_id        text primary key,        -- opaque token, minted client-side
    tenant_id         text not null default 'tmh',
    source            text not null default 'free_search',  -- 'free_search' | 'brand_audit_direct'

    -- identity — why Free Search starts here: a name is enough to identify
    -- the company and attempt outreach even if nothing else is ever given.
    name              text,
    trading_name      text,

    -- contact — filled whenever first given, by either tool; never reset.
    first_name        text,
    last_name         text,
    email             text,
    phone             text,
    business_name     text,
    business_website  text,
    consent_marketing boolean not null default false,

    -- Free Search inputs snapshot (for Brand Audit pre-fill + for the record)
    classes           int[]  not null default '{}',
    tagline           text,
    has_logo          boolean not null default false,
    trading_now       text[] not null default '{}',
    planning_to_trade text[] not null default '{}',
    class_source      jsonb,

    last_result       jsonb,          -- last computed free-search result, if any
    current_screen    text,           -- last screen reached — where they are / left off

    status            text not null default 'in_progress',
        -- 'in_progress' | 'search_completed' | 'lead_captured' | 'abandoned'

    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index if not exists idx_js_tenant_time
    on public.journey_sessions (tenant_id, created_at desc);
create index if not exists idx_js_email
    on public.journey_sessions (email) where email is not null;

comment on table public.journey_sessions is
    'One row per visitor journey through Free Search (and into Brand Audit). '
    'Created on first real input, upserted continuously — never only at the '
    'end. session_id is an opaque client-minted token, not tied to login.';

-- --- brand_audit_requests ------------------------------------------------------
-- One row per audit request/submission. Created as a draft the moment
-- someone lands on Brand Audit (or adds their first brand) — not only on
-- final submit, so nothing is lost on abandon. Links back to the
-- journey_sessions row it was handed off from, if any.

create table if not exists public.brand_audit_requests (
    id                uuid primary key default gen_random_uuid(),
    session_id        text references public.journey_sessions (session_id) on delete set null,
    tenant_id         text not null default 'tmh',

    first_name        text,
    last_name         text,
    email             text,
    phone             text,
    business_name     text,
    business_website  text,
    consent_marketing boolean not null default false,

    trading_now       text[] not null default '{}',
    planning_to_trade text[] not null default '{}',

    status            text not null default 'draft',   -- 'draft' | 'submitted'
    current_screen    text,

    zoho_lead_id      text,

    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    submitted_at      timestamptz
);

create index if not exists idx_bar_session on public.brand_audit_requests (session_id);
create index if not exists idx_bar_status_time on public.brand_audit_requests (status, created_at desc);

comment on table public.brand_audit_requests is
    'One row per Brand Audit request. Created as a draft on landing, not on '
    'submit — status flips to submitted at the end. Optionally linked back '
    'to the journey_sessions row it was handed off from.';

-- --- brand_audit_brands ---------------------------------------------------------
-- Child of brand_audit_requests — the multi-brand support. One row per
-- brand being checked in this request: "they may have multiple brands to
-- check" (Jonathan, 01 Aug 2026), so classes/terms are scoped per brand,
-- not per request.

create table if not exists public.brand_audit_brands (
    id                    uuid primary key default gen_random_uuid(),
    request_id            uuid not null references public.brand_audit_requests (id) on delete cascade,
    position              int not null default 0,     -- display order within the request

    brand_name            text,
    classes               int[] not null default '{}',
    terms                 text[] not null default '{}',
    class_source          jsonb,

    logo_flag             boolean not null default false,
    logo_later            boolean not null default false,
    logo_url              text,
    tagline               text,

    website_url           text,
    business_description  text,
    competitor_name       text,
    competitor_website    text,

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

create index if not exists idx_bab_request on public.brand_audit_brands (request_id, position);

comment on table public.brand_audit_brands is
    'One row per brand within a brand_audit_requests row — lets one contact '
    'submit multiple brands in a single audit request.';

-- --- journey_events -----------------------------------------------------------
-- Append-only. One row per step or decision: a screen entered, a field
-- committed, a class or jurisdiction toggled, a search run, a lead
-- captured. This is the audit trail Jonathan asked for — "every step and
-- decision will be logged" — independent of the current-state snapshots
-- above, which get overwritten; these rows never are.
--
-- One unified log across BOTH tools, not two separate ones — a request that
-- started life as a Free Search session should read as one continuous
-- story, not two disconnected logs someone has to stitch together by hand.
-- At least one of session_id / request_id is set on any given row (a
-- Free-Search-side event has no request yet; a Brand-Audit-side event may
-- have both, once handed off).

create table if not exists public.journey_events (
    id           bigint generated always as identity primary key,
    session_id   text references public.journey_sessions (session_id) on delete cascade,
    request_id   uuid references public.brand_audit_requests (id) on delete cascade,
    event_type   text not null,   -- e.g. 'screen_enter','field_commit','class_added',
                                   -- 'class_removed','jurisdiction_set','search_run',
                                   -- 'lead_captured','brand_audit_handoff','brand_added',
                                   -- 'brand_removed','audit_submitted'
    screen       text,
    payload      jsonb not null default '{}',
    created_at   timestamptz not null default now(),
    constraint je_has_a_parent check (session_id is not null or request_id is not null)
);

create index if not exists idx_je_session_time
    on public.journey_events (session_id, created_at) where session_id is not null;
create index if not exists idx_je_request_time
    on public.journey_events (request_id, created_at) where request_id is not null;

comment on table public.journey_events is
    'Append-only log of every step/decision across the whole journey — Free '
    'Search and, if it continues, Brand Audit — as one continuous story. '
    'Never updated or deleted. This is the record even where the current-'
    'state snapshot on journey_sessions/brand_audit_requests later changes, '
    'or the visitor abandons before it would ever be captured any other way.';

-- --- RLS -----------------------------------------------------------------------
-- Edge Functions use the service-role key and bypass RLS for reads/writes.
-- These are enabled with no policies, so the anon key gets zero access by
-- default — deliberately stricter than migration 0001's tables, since
-- nothing here needs to be visible to a signed-in portal user (no accounts
-- are involved in this flow at all).

alter table public.journey_sessions      enable row level security;
alter table public.journey_events        enable row level security;
alter table public.brand_audit_requests  enable row level security;
alter table public.brand_audit_brands    enable row level security;
