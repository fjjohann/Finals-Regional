create table if not exists public.panel_state (
  id text primary key,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.panel_state enable row level security;

drop policy if exists "public read panel state" on public.panel_state;
create policy "public read panel state"
on public.panel_state
for select
using (id = 'global');

drop policy if exists "admin insert panel state" on public.panel_state;
create policy "admin insert panel state"
on public.panel_state
for insert
with check (
  id = 'global'
  and auth.jwt() ->> 'email' = 'fjjohann@gmail.com'
);

drop policy if exists "admin update panel state" on public.panel_state;
create policy "admin update panel state"
on public.panel_state
for update
using (
  id = 'global'
  and auth.jwt() ->> 'email' = 'fjjohann@gmail.com'
)
with check (
  id = 'global'
  and auth.jwt() ->> 'email' = 'fjjohann@gmail.com'
);

insert into public.panel_state (id, payload)
values (
  'global',
  '{"confirmations": {}, "releases": {}, "stateReleases": {}}'::jsonb
)
on conflict (id) do nothing;
