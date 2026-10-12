-- Definitions of two tables that exist in the live project (jwanlhdmhgmbybcdhvkx)
-- but were never committed as migrations. Read back from the live database on
-- 12 Oct 2026 (information_schema + pg_constraint + pg_indexes). Idempotent:
-- running it against the live project changes nothing.
-- No data and no secrets are in this file.

-- Terms of Service accepted at order (handover 28 Sep 2026).
-- Written by journey /terms/record, read by /terms/accepted.
create table if not exists public.terms_acceptances (
  id                     uuid primary key default gen_random_uuid(),
  session_id             text not null,
  document               text not null,
  version                text not null,
  sha256                 text not null,
  rendered_html          text not null,
  turnaround_days        integer not null check (turnaround_days >= 1 and turnaround_days <= 5),
  worldwide              boolean not null,
  jurisdictions          text[] not null default '{}'::text[],
  current_jurisdictions  text[] not null default '{}'::text[],
  planning_jurisdictions text[] not null default '{}'::text[],
  buying_as_consumer     boolean,
  consumer_consent_at    timestamptz,
  accepted_at            timestamptz not null default now(),
  accepted_by            text not null,
  source                 text not null,
  zoho_deal_id           text,
  created_at             timestamptz not null default now(),
  unique (session_id, sha256)
);
alter table public.terms_acceptances enable row level security;
comment on table public.terms_acceptances is
  'Terms of Service accepted at order (handover 28 Sep 2026). Service role only; read via journey /terms/copy.';

-- Xero OAuth token store for the journey (the ONLY Xero writer, /xero/process).
-- One row (id = 1). Refresh tokens rotate on every use, so nothing outside the
-- journey may read and use this row.
create table if not exists public.xero_tokens (
  id            integer primary key,
  refresh_token text,
  access_token  text,
  expires_at    timestamptz,
  updated_at    timestamptz default now()
);
alter table public.xero_tokens enable row level security;
-- RLS on, no policies: service role only.
