-- C-Trace Verifier v2: verification runs carry their inputs' state; drift checks across the fleet.
alter table verification_runs
  add column created_by uuid references users (id),
  add column inputs_as_of timestamptz,         -- latest activity-record change the run saw (stale detection)
  add column weather_source jsonb not null default '{}';

-- Fleet-level model monitoring. Aggregates only (no factory data), written by the worker as owner.
create table model_drift_checks (
  id             bigserial primary key,
  model_version  text not null,
  checked_at     timestamptz not null default now(),
  n_months       integer not null,
  n_factories    integer not null,
  z_spread       jsonb not null,     -- carrier -> current robust spread of z
  ratio          jsonb not null,     -- carrier -> current / baseline
  alert          boolean not null,
  details        text
);
grant select on model_drift_checks to ctrace_app;
alter table model_drift_checks enable row level security;
create policy public_read on model_drift_checks for select to ctrace_app using (true);
