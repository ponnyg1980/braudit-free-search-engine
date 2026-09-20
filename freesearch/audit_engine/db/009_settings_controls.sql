-- 009 — the interactive half of Search & Score Settings (19 Sep 2026).
-- Global settings change only through R&D, by Jonathan. Nothing staff do in
-- Triage writes a global list: a word they want everywhere becomes a PROPOSAL
-- carrying the evidence, and a sensitivity change is scoped to one run and
-- recorded with both outcomes so R&D can tell a good call from a workaround.

-- A word staff want on the global list. Never applied automatically.
create table if not exists audit.global_proposals (
    id              uuid primary key default gen_random_uuid(),
    word            text not null,
    kind            text not null default 'industry',
    proposed_from   uuid references audit.runs(id) on delete set null,
    client_id       uuid,
    client_name     text,
    proposed_by     text not null,
    reason          text,
    -- what it did on the run it was proposed from: the evidence R&D rules on
    rows_removed    integer,
    bands_moved     integer,
    status          text not null default 'proposed',   -- proposed|accepted|declined
    ruled_by        text,
    ruled_at        timestamptz,
    ruling_note     text,
    created_at      timestamptz not null default now()
);
create index if not exists global_proposals_status on audit.global_proposals (status, created_at desc);
create index if not exists global_proposals_word   on audit.global_proposals (lower(word));

-- A sensitivity change, scoped to one run. BOTH outcomes are kept: without
-- the standard one the log says what somebody did but not whether it helped,
-- which is the difference between R&D evidence and an audit trail.
create table if not exists audit.score_overrides (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references audit.runs(id) on delete cascade,
    compartment       text not null,          -- mark_similarity | trade | banding | image
    step              text not null,          -- stricter | standard | looser
    standard_outcome  jsonb,                  -- band counts as the defaults give them
    override_outcome  jsonb,                  -- band counts under the override
    rows_moved        integer,
    reason            text,
    created_by        text not null,
    scoring_version   text,
    created_at        timestamptz not null default now()
);
create index if not exists score_overrides_run on audit.score_overrides (run_id, created_at desc);

comment on table audit.global_proposals is
  'Words staff asked to be made global. A queue for R&D, never applied on its own.';
comment on table audit.score_overrides is
  'Per-run sensitivity changes, dual-scored. Never global.';
