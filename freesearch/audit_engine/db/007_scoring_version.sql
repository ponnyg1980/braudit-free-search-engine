-- 007 — which code scored this run (18 Sep 2026).
-- Until now nothing recorded the scorer: uk_monitor stamped two hard-coded
-- strings ("uk_monitor/two-layer", "bands-1.2.0") that had not moved since
-- before the 17 Sep consolidation, and audit runs recorded nothing at all.
-- A stored band you cannot trace to a version cannot be re-scored safely, and
-- cannot be used as evidence when someone later tunes the thing that produced
-- it — which is the whole point of the Search & Score Settings log.
alter table audit.runs add column if not exists scoring_version text;

-- Backfill is deliberately NOT attempted. We do not know which version scored
-- the existing rows; writing one in would be inventing the provenance this
-- column exists to record. Null means "scored before this column existed".
comment on column audit.runs.scoring_version is
  'tmh_scoring.__version__ in force when this run was scored; null = pre-18 Sep 2026, provenance unknown';
