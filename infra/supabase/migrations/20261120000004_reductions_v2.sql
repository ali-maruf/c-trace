-- C-Trace reduction proof v2: per-intervention, ASHRAE-checked, reviewer-signed.
alter table interventions
  add column affects text[] not null default '{}'
  check (affects <@ array['natural_gas', 'grid_electricity', 'diesel']::text[]);

alter table reductions
  add column status text not null default 'draft' check (status in ('draft', 'signed', 'superseded')),
  add column unit text,
  add column n_baseline integer,
  add column n_reporting integer,
  add column nmbe numeric,
  add column predicted numeric,
  add column actual numeric,
  add column tco2e_low numeric,
  add column tco2e_high numeric,
  add column tariff_bdt numeric,
  add column bdt_saved numeric,
  add column checks jsonb not null default '{}',
  add column coefficients jsonb not null default '{}',
  add column excluded jsonb not null default '{}',
  add column curve jsonb not null default '[]',
  add column factor_ids jsonb not null default '[]',
  add column documents jsonb not null default '[]',      -- [{trace_id, doc_sha256, file_name}] the proof used
  add column status_reason text,
  add column verification_run_id uuid references verification_runs (id) on delete set null,
  add column computed_by uuid references users (id),
  add column signed_by uuid references users (id),
  add column signed_at timestamptz,
  add column sign_comment text;
