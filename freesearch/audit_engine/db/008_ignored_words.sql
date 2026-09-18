-- 008 — what each run refused to search on (18 Sep 2026, Search & Score Settings).
-- The weak-token filter has always been silent: staff could see the criteria
-- that WERE searched and never the words removed before searching, so "why
-- didn't this find X" had no answer on the screen. Recording only — no
-- behaviour change; the same words are dropped as before.
create table if not exists audit.ignored_words (
    id             uuid primary key default gen_random_uuid(),
    run_id         uuid not null references audit.runs(id) on delete cascade,
    word           text not null,
    -- structural: never part of a mark (ltd, plc) — shown, never un-ignorable
    -- weak:       usually noise, occasionally the mark (DIRECT LINE) — un-ignorable
    -- industry:   noise for this client only (courier) — added per run by staff
    -- client:     from Client_Search_Exclusions
    kind           text not null,
    source         text not null,
    where_applied  text not null,
    rows_removed   integer,
    restored_by    text,
    restored_at    timestamptz,
    created_at     timestamptz not null default now()
);

create index if not exists ignored_words_run  on audit.ignored_words (run_id);
create index if not exists ignored_words_word on audit.ignored_words (lower(word));

comment on table audit.ignored_words is
  'Per-run record of words the engine did not search on. Feeds the Search & Score Settings panel and, in aggregate, tells R&D which words staff keep restoring.';
