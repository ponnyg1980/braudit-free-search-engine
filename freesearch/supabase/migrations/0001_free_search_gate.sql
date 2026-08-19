-- Free Search gate + Free Temmy Account profile
-- =============================================================================
-- The gate rule (agreed 09 Jul 2026):
--   * 1st search from an IP (per tenant): anonymous, allowed.
--   * 2nd+ search from that IP: requires a Free Temmy Account (email OTP).
--   * Signed-in account holders: unlimited searches, and their report is
--     ungated because the account already gave us a verified email + business
--     info — a higher-quality lead than any form.
--
-- The account IS the lead gate, moved earlier and OTP-verified.
--
-- GDPR note (this org runs an LIA and cares): we never store the raw client
-- IP. We store a salted SHA-256 hash, so the row supports rate-limiting and
-- fraud checks but is not itself personal data traced back to an individual.

-- --- usage ledger ------------------------------------------------------------

create table if not exists public.free_search_usage (
    id            bigint generated always as identity primary key,
    ip_hash       text        not null,          -- salted SHA-256, never raw IP
    tenant_id     text        not null default 'tmh',
    account_id    uuid        references auth.users (id) on delete set null,
    -- lightweight query fingerprint for analytics, not the full payload
    primary_mark  text,
    classes       int[]       not null default '{}',
    overall_risk  text,
    total_flagged int,
    created_at    timestamptz not null default now()
);

create index if not exists idx_fsu_ip_tenant_time
    on public.free_search_usage (ip_hash, tenant_id, created_at desc);
create index if not exists idx_fsu_account
    on public.free_search_usage (account_id, created_at desc);

comment on table public.free_search_usage is
    'One row per free search. Drives the 1-anonymous-search-per-IP gate and '
    'feeds lead analytics. ip_hash is salted; raw IP is never stored.';

-- How many searches has this IP already run for this tenant, ever?
-- (A rolling window can be added later; "ever" is the strictest and simplest
--  first cut, and re-searching is exactly the behaviour we want to convert.)
create or replace function public.free_search_count_for_ip(
    p_ip_hash text, p_tenant text)
returns integer
language sql stable as $$
    select count(*)::int
    from public.free_search_usage
    where ip_hash = p_ip_hash and tenant_id = p_tenant
      and account_id is null;      -- only anonymous searches count toward the cap
$$;

-- --- business profile (the info captured at account creation) -----------------

create table if not exists public.temmy_business_profile (
    account_id        uuid primary key references auth.users (id) on delete cascade,
    tenant_id         text not null default 'tmh',
    first_name        text,
    last_name         text,
    email             text,                 -- mirror of auth email, OTP-verified
    phone             text,
    business_name     text,
    business_website  text,
    business_summary  text,                 -- feeds class suggestion later (2b)
    trading_now       text[] not null default '{}',
    planning_to_trade text[] not null default '{}',
    consent_marketing boolean not null default false,
    zoho_lead_id      text,                 -- set once pushed to Zoho
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

comment on table public.temmy_business_profile is
    'Free Temmy Account business info. Created after email OTP verification. '
    'The canonical lead record that syncs to Zoho.';

-- --- RLS ---------------------------------------------------------------------
-- The Edge Function uses the service-role key and bypasses RLS. These policies
-- exist so that if the portal front-end ever reads these tables with the anon
-- key under a user session, a user sees only their own rows.

alter table public.free_search_usage       enable row level security;
alter table public.temmy_business_profile   enable row level security;

drop policy if exists own_usage on public.free_search_usage;
create policy own_usage on public.free_search_usage
    for select using (auth.uid() = account_id);

drop policy if exists own_profile on public.temmy_business_profile;
create policy own_profile on public.temmy_business_profile
    for all using (auth.uid() = account_id) with check (auth.uid() = account_id);
