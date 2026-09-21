-- 010 — sensitivity at Triage/Client Review level (21 Sep 2026).
--
-- Ruling (Jonathan, 20 Sep 2026; decision H in SCORING DECISIONS):
--   "We want the sensitivity rules to be applied at a Triage/Client Review
--    level - so they are saved within that report (similar to how exclusions
--    are) but can be ammended and/or cleared. (Clearing is dangerous so we
--    want to make sure that it is warned and logged)."
--
-- THE SHAPE, AND WHY
--
-- One row per EVENT, never an update in place. Applying, amending and
-- clearing all append; the row in force is the one with superseded_at null.
-- The history is the point: "fuzzy +1, then +2 by Sarah at 14:20, cleared by
-- Tom at 16:05 because the client disputed the list" is the evidence R&D rules
-- a global change on, and an UPDATE would destroy it while looking tidier.
--
-- Clearing is therefore not a delete. It appends an event carrying the prior
-- steps, so a clear can be read, explained, and undone by re-applying what it
-- says was in force.
--
-- STEPS, NOT THRESHOLDS. `steps` holds what a human chose ({"phonetic": 1}).
-- `deltas` holds what the scorer actually did with it, because R&D may re-tune
-- what "+1" means in a later package release and the stored run must still be
-- readable. `scoring_version` is what makes the pair legible: it names the
-- release whose sensitivity.py defined those steps.
--
-- BOTH OUTCOMES ARE KEPT. Without the standard one the log says what somebody
-- did but not whether it helped, which is the difference between R&D evidence
-- and an audit trail.
--
-- ROLLBACK
--   drop table audit.run_sensitivity;
--   -- and, to restore what this migration replaced:
--   create table audit.score_overrides (...);   -- full DDL in db/009_settings_controls.sql
-- audit.score_overrides is dropped below. It was created on 19 Sep, was never
-- written to (verified: 0 rows), and its vocabulary predates tmh-scoring
-- 2.4.0 — it recorded one compartment per row with step as
-- 'stricter|standard|looser' over four compartments, where there are now six
-- compartments and five integer positions, and it had no way to express a
-- clear or to supersede a prior state. Leaving a dead table beside the live
-- one invites a future writer into the wrong one.

begin;

create table if not exists audit.run_sensitivity (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references audit.runs(id) on delete cascade,

    -- 1, 2, 3 ... within a run. The event log's reading order, independent of
    -- clock skew between app servers.
    version           integer not null,
    action            text not null,          -- applied | amended | cleared

    -- The full set in force AFTER this event, not the increment. A reader of
    -- one row must never have to replay the whole history to know what was
    -- scored. '{}' for a clear.
    steps             jsonb not null default '{}'::jsonb,
    prior_steps       jsonb not null default '{}'::jsonb,
    -- What the package made of those steps: sensitivity.deltas_for(steps).
    deltas            jsonb not null default '{}'::jsonb,

    -- Measured before the event commits, and shown to the person first.
    rows_moved        integer,
    moved_up          integer,
    moved_down        integer,
    band_before       jsonb,                  -- band -> count, as scored
    band_after        jsonb,                  -- band -> count, under the steps

    reason            text,
    created_by        text not null,
    scoring_version   text,
    created_at        timestamptz not null default now(),

    -- Null means "in force". Set when the next event supersedes this one.
    superseded_at     timestamptz,

    constraint run_sensitivity_action
        check (action in ('applied', 'amended', 'cleared')),

    -- A clear must say why, and the check is here rather than only in the app
    -- because this is the level that cannot be bypassed by a direct call.
    -- Ten characters is not a quality bar; it is enough to stop an empty box
    -- and a single keystroke from passing as an explanation.
    constraint run_sensitivity_clear_needs_reason
        check (action <> 'cleared'
               or (reason is not null and length(btrim(reason)) >= 10)),

    -- A clear restores the defaults, so there is nothing left in force.
    constraint run_sensitivity_clear_is_empty
        check (action <> 'cleared' or steps = '{}'::jsonb),

    constraint run_sensitivity_version_unique unique (run_id, version)
);

-- At most one row in force per run. A partial unique index rather than a
-- trigger: the database refuses a second live row outright, so a concurrent
-- double-apply fails loudly instead of leaving two answers to "what was this
-- scored at".
create unique index if not exists run_sensitivity_one_live
    on audit.run_sensitivity (run_id) where superseded_at is null;

create index if not exists run_sensitivity_run
    on audit.run_sensitivity (run_id, version desc);

create index if not exists run_sensitivity_recent
    on audit.run_sensitivity (created_at desc);

comment on table audit.run_sensitivity is
  'Per-run sensitivity, as an append-only event log. Steps are what a human '
  'chose; deltas are what the scorer did; both outcomes are kept. Never '
  'global - moving a default is a tmh-scoring release, R&D''s and Jonathan''s.';
comment on column audit.run_sensitivity.steps is
  'Full set in force after this event, e.g. {"phonetic": 1, "terms": -1}. '
  'Empty for a clear.';
comment on column audit.run_sensitivity.superseded_at is
  'Null = in force. One live row per run, enforced by run_sensitivity_one_live.';

drop table if exists audit.score_overrides;

commit;
