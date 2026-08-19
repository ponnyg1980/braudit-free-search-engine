-- zoho_lead_id round-trip for journey_sessions and brand_audit_requests.
-- =============================================================================
-- Added 01 Aug 2026, alongside the `journey` function's architecture pivot:
-- Cerebrum no longer receives direct pushes from Free Search / Brand Audit
-- (see journey/index.ts header comment). Zoho becomes the sole recipient,
-- and Cerebrum will hear about these contacts via Zoho's own downstream
-- automation instead — same as any other deal/account activity.
--
-- Jonathan, 01 Aug: "Currently our website deposits all queries in leads via
-- Zoho Flow... then zoho can send it to Cerebrum for communication/outreach
-- as it will need to do with all of our deal and account activity."
--
-- A single visitor's journey can trigger MULTIPLE Zoho pushes over time
-- (an automatic-enrichment find, then later a self-submitted lead_captured,
-- then later an audit_submitted). Without a round-trip ID, each push would
-- create a SEPARATE Zoho Lead for the same person. zoho_lead_id lets
-- zohoPayload() distinguish "create" (first push, Lead_Status: New) from
-- "update" (later pushes, existing record, Lead_Status left alone — a human
-- or existing Zoho automation owns it after creation).
--
-- Populated by the NEW POST /zoho-linked callback route, which Zoho Flow is
-- expected to call once it creates (or matches) a Lead from a zohoPayload
-- push — mirrors Industry Report's existing `zoho_linked` event convention.

alter table public.journey_sessions
    add column if not exists zoho_lead_id text,
    add column if not exists zoho_lead_url text;

alter table public.brand_audit_requests
    add column if not exists zoho_lead_id text,
    add column if not exists zoho_lead_url text;

comment on column public.journey_sessions.zoho_lead_id is
    'Zoho CRM Lead ID once Zoho Flow creates/matches a record for this '
    'session (via POST /zoho-linked). Null means no Zoho record exists yet '
    '-- the next push for this session will create one (Lead_Status: New) '
    'rather than update.';
comment on column public.journey_sessions.zoho_lead_url is
    'Convenience direct link to the Zoho Lead, if Zoho Flow supplies one.';
comment on column public.brand_audit_requests.zoho_lead_id is
    'Zoho CRM Lead ID for this brand audit request. /audit/submit falls '
    'back to the linked journey_sessions.zoho_lead_id (via session_id) when '
    'this is still null, so a visitor who already has a Zoho Lead from an '
    'earlier session-side push gets updated, not duplicated.';
comment on column public.brand_audit_requests.zoho_lead_url is
    'Convenience direct link to the Zoho Lead, if Zoho Flow supplies one.';
