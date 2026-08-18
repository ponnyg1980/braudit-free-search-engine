-- Where they sell, and where they are seen.
--
-- The wizard has been sending these on every snapshot since 18 Aug 2026, but
-- journey/index.ts writes snapshots through an explicit allow-list and this
-- key was not on it, so the answers were silently discarded. They survived
-- only inside class_source.answers, and only for the describe-your-business
-- route — three of the four class routes lost them entirely.
--
-- Kept as jsonb rather than columns because the two lists are lists, the
-- option sets will change as we learn what people actually tick, and none of
-- it is queried in a hot path. Shape:
--
--   {"sells_via":["premises","marketplace"],
--    "sells_via_other":"trade shows",
--    "resale":"others",
--    "seen_via":["social","search"],
--    "seen_via_other":""}
--
-- sells_via feeds the class suggestion (it is what surfaces retail services
-- in 35 for a reseller). seen_via never does — it is for Brand Audit scope,
-- risk conversation and evidence of use.
alter table public.journey_sessions
    add column if not exists channels jsonb;

comment on column public.journey_sessions.channels is
    'Selling and promotion channels. sells_via/resale feed class selection; seen_via is captured for audit scope and evidence of use only.';
