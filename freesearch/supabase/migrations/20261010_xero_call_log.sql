-- 10 Oct 2026 (Jonathan): per-caller logging of every Xero call made by the journey billing link.
create table if not exists public.xero_call_log (id bigserial primary key, at timestamptz not null default now(),
  route text, caller text, method text, xero_path text, status int, day_remaining int, minute_remaining int, detail text);
create index if not exists xero_call_log_at on public.xero_call_log(at);
alter table public.xero_call_log enable row level security;
