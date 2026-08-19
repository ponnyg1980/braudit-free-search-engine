-- Enrichment tracking for Free Search sessions with no contact info.
-- =============================================================================
-- Added 01 Aug 2026, alongside the `journey` function's POST /enrich route
-- and (originally) freesearch/enrichment.py's Apollo org+people search.
--
-- REVISED 01 Aug 2026 (FREESEARCH_ENRICHMENT_BRIEF.md / ENRICHMENT_SPEC.md,
-- temmy-lead-engine/): the automatic resolver behind /enrich is now
-- temmy-lead-engine/contact_resolver.py (Serper + Companies House), not
-- Apollo — Apollo became a manual staff button elsewhere. Column shapes and
-- the reasoning below are unchanged; only what populates enrichment_result
-- changed. No migration needed for the swap itself — enrichment_result is
-- jsonb and the resolver's response shape (company_name/website/domain/
-- phone/address/company_number/sic_codes/officer_names/step/corroboration/
-- suppression/credits_used/reason) fits the same column.
--
-- Deliberately NEW columns, not a write into journey_sessions.email/phone.
-- email/phone mean "the visitor gave us this" — that's what the
-- lead_captured -> Zoho push logic keys off, and what nurture-automation
-- eligibility depends on. A resolved contact is neither: it's a best-effort
-- guess at a way to reach the business, not something the visitor handed
-- over, and (per the Cerebrum build doc's compliance note) goes to a human
-- for individually-judged outreach, never straight into automated
-- marketing. Mixing the two into one column would silently misuse a
-- resolution result as if it were consent-backed contact info.

alter table public.journey_sessions
    add column if not exists enrichment_status text,
        -- 'pending' | 'found' | 'not_found' — null until /enrich has run once
    add column if not exists enrichment_result jsonb,
        -- the full contact_resolver.py::resolve() response: step, website,
        -- domain, phone, address, company_number, sic_codes, officer_names,
        -- corroboration {checked,matched,reason}, suppression
        -- {checked,suppressed,aid}, credits_used, reason
    add column if not exists enriched_at timestamptz;

comment on column public.journey_sessions.enrichment_status is
    'Result of the automatic Serper+Companies-House resolution attempt for '
    'a no-contact-info search (contact_resolver.py). Never read as, or '
    'written into, email/phone — see migration header.';
comment on column public.journey_sessions.enrichment_result is
    'Full JSON response from temmy-lead-engine/contact_resolver.py::resolve '
    '— step/website/domain/phone/address/company_number/sic_codes/'
    'officer_names/corroboration/suppression/credits_used when found, or a '
    'reason code when not.';
