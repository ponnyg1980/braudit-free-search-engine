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

-- How we came by the email on this session.
--
-- 'ai_gate'      volunteered to unlock an AI class route (NOT marketing consent)
-- 'report_form'  volunteered to receive the report — the strongest signal
-- 'resolver'     found by enrichment, never given (see contact_resolver.py)
--
-- The AI-gate address previously lived only inside class_source.email, so
-- journey_sessions.email stayed null and the Zoho push saw "no contact" —
-- falling through to the resolver and manufacturing a "Search Result Only"
-- lead for somebody who had just typed their address in. Zoho dedupes on
-- Email, so it has to be in the standard field; the source travels beside it
-- because consent is not the same in all three cases.
alter table public.journey_sessions
    add column if not exists email_source text;

comment on column public.journey_sessions.email_source is
    'ai_gate | report_form | resolver — how the email was obtained. Consent differs; do not treat an ai_gate address as marketing opt-in.';
