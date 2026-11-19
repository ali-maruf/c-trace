-- C-Trace initial schema (brief §6). Applied by `python -m ctrace.store.migrate` or the Supabase CLI.
--
-- Tenancy: every organisation-owned row carries org_id. Row-level security policies compare it with
-- ctrace_current_org(), which reads the per-transaction setting `ctrace.org_id` set by the C-Trace API
-- after it has verified the caller's token and membership. The API runs each request as the
-- non-owner role ctrace_app (SET LOCAL ROLE), so the policies apply.

create extension if not exists vector;

-- ------------------------------------------------------------------ application role
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'ctrace_app') then
    create role ctrace_app nologin;
  end if;
end $$;
-- The connecting (migration) user must be able to SET ROLE ctrace_app.
do $$
begin
  execute format('grant ctrace_app to %I', current_user);
exception when others then
  raise notice 'C-Trace: could not grant ctrace_app to %: %', current_user, sqlerrm;
end $$;

create or replace function ctrace_current_org() returns uuid
  language sql stable as
$$ select nullif(current_setting('ctrace.org_id', true), '')::uuid $$;

create or replace function ctrace_current_user() returns uuid
  language sql stable as
$$ select nullif(current_setting('ctrace.user_id', true), '')::uuid $$;

-- ------------------------------------------------------------------ enums
create type membership_role as enum ('owner', 'manager', 'viewer', 'reviewer', 'buyer', 'lender');
create type record_status   as enum ('extracted', 'confirmed', 'corrected', 'rejected');
create type confidence_grade as enum ('High', 'Medium', 'Low');
create type job_status      as enum ('queued', 'running', 'done', 'failed');

-- ------------------------------------------------------------------ tenancy
create table organizations (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now()
);

create table users (
  id            uuid primary key,          -- = Supabase auth.users.id (token `sub`)
  email         text not null unique,
  display_name  text,
  created_at    timestamptz not null default now()
);

create table memberships (
  org_id      uuid not null references organizations (id) on delete cascade,
  user_id     uuid not null references users (id) on delete cascade,
  role        membership_role not null,
  created_at  timestamptz not null default now(),
  primary key (org_id, user_id)
);

-- ------------------------------------------------------------------ factories
create table factories (
  id                  uuid primary key default gen_random_uuid(),
  org_id              uuid not null references organizations (id) on delete cascade,
  name                text not null,
  location            text,
  lat                 double precision check (lat between -90 and 90),
  lon                 double precision check (lon between -180 and 180),
  factory_type        text not null check (factory_type in ('knit_composite', 'woven', 'denim_wash', 'sweater')),
  workers             integer not null check (workers > 0),
  has_wet_processing  boolean not null default false,
  captive_gas_gen     boolean not null default false,
  created_at          timestamptz not null default now(),
  unique (id, org_id)
);

create table assets (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  factory_id   uuid not null,
  kind         text not null check (kind in ('solar', 'boiler', 'genset', 'chiller')),
  capacity     numeric not null check (capacity >= 0),
  unit         text not null,
  from_period  char(7) not null check (from_period ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

create table interventions (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  factory_id   uuid not null,
  measure      text not null,
  from_period  char(7) not null check (from_period ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  capex_bdt    numeric check (capex_bdt >= 0),
  notes        text,
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

-- ------------------------------------------------------------------ documents → extraction → activity
create table documents (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null,
  factory_id    uuid not null,
  storage_path  text not null,
  sha256        char(64) not null check (sha256 ~ '^[0-9a-f]{64}$'),
  file_name     text not null,
  media_type    text not null,
  size_bytes    integer not null check (size_bytes >= 0),
  uploaded_by   uuid references users (id),
  uploaded_at   timestamptz not null default now(),
  unique (factory_id, sha256),
  unique (id, org_id),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

create table extractions (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null,
  document_id     uuid not null,
  method          text not null,           -- 'rules' | 'llm'
  model           text,                    -- LLM model id when method = 'llm'
  prompt_version  text,
  raw_json        jsonb not null,
  field_evidence  jsonb not null default '{}',
  issues          jsonb not null default '[]',
  confidence      numeric check (confidence between 0 and 1),
  created_at      timestamptz not null default now(),
  foreign key (document_id, org_id) references documents (id, org_id) on delete cascade
);

create table activity_records (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  factory_id     uuid not null,
  document_id    uuid not null,
  extraction_id  uuid references extractions (id) on delete set null,
  activity       text check (activity in ('natural_gas', 'grid_electricity', 'diesel')),
  period         char(7) check (period ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  quantity       numeric,
  unit           text,
  amount_bdt     numeric,
  bill_no        text,
  supplier       text,
  status         record_status not null default 'extracted',
  confirmed_by   uuid references users (id),
  confirmed_at   timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (document_id),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade,
  foreign key (document_id, org_id) references documents (id, org_id) on delete cascade
);
create index activity_records_factory_period on activity_records (factory_id, period);

-- ------------------------------------------------------------------ factors and ledger
create table emission_factors (
  id               text primary key,         -- e.g. 'ipcc2006-natural_gas-v1', 'ember-bgd-2024-v1'
  activity         text not null,
  region           text not null,
  year             integer,
  unit             text not null,
  scope            smallint not null check (scope in (1, 2, 3)),
  kgco2e_per_unit  numeric not null check (kgco2e_per_unit >= 0),
  breakdown        jsonb not null default '{}',
  source           text not null,
  source_url       text not null,
  version          text not null,
  valid_from       date,
  valid_to         date,
  notes            text,
  created_at       timestamptz not null default now()
);

create table ledger_entries (
  factory_id          uuid not null,
  trace_id            text not null,
  org_id              uuid not null,
  activity_record_id  uuid not null references activity_records (id) on delete cascade,
  factor_id           text not null references emission_factors (id),
  scope               smallint not null,
  period              char(7) not null,
  activity            text not null,
  quantity            numeric not null,
  unit                text not null,
  energy_gj           numeric not null,
  kgco2e              numeric not null,
  tco2e               numeric not null,
  calculation         text not null,
  doc_sha256          char(64) not null,
  created_at          timestamptz not null default now(),
  primary key (factory_id, trace_id),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);
create index ledger_entries_factory_period on ledger_entries (factory_id, period);

create table production_records (
  org_id      uuid not null,
  factory_id  uuid not null,
  period      char(7) not null check (period ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  pieces      numeric not null check (pieces >= 0),
  source      text not null,
  primary key (factory_id, period),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

create table weather_monthly (
  lat           numeric(6, 3) not null,
  lon           numeric(6, 3) not null,
  period        char(7) not null,
  t2m_c         numeric,
  cdd           numeric,
  source        text not null,
  retrieved_at  timestamptz not null default now(),
  primary key (lat, lon, period)
);

-- ------------------------------------------------------------------ verification, reductions, reports
create table verification_runs (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null,
  factory_id     uuid not null,
  model_version  text not null,
  created_at     timestamptz not null default now(),
  unique (id, org_id),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

create table verification_results (
  run_id      uuid not null,
  org_id      uuid not null,
  period      char(7) not null,
  z           jsonb not null default '{}',
  iso_score   numeric,
  confidence  confidence_grade not null,
  reasons     jsonb not null default '[]',
  resolved_by uuid references users (id),
  resolved_at timestamptz,
  resolution  text,
  primary key (run_id, period),
  foreign key (run_id, org_id) references verification_runs (id, org_id) on delete cascade
);

create table reductions (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null,
  factory_id       uuid not null,
  intervention_id  uuid references interventions (id) on delete set null,
  baseline_from    char(7) not null,
  baseline_to      char(7) not null,
  reporting_from   char(7) not null,
  reporting_to     char(7) not null,
  activity         text not null,
  avoided          numeric not null,
  low              numeric not null,
  high             numeric not null,
  tco2e            numeric not null,
  r2               numeric,
  cv_rmse          numeric,
  verified         boolean not null,
  created_at       timestamptz not null default now(),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

create table reports (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null,
  factory_id   uuid not null,
  format       text not null check (format in ('buyer', 'ifrs_s2', 'bsec')),
  period_from  char(7) not null,
  period_to    char(7) not null,
  facts        jsonb not null,
  narrative    text not null,
  method       text not null,
  pdf_path     text,
  share_token  text unique,
  expires_at   timestamptz,
  revoked_at   timestamptz,
  created_by   uuid references users (id),
  created_at   timestamptz not null default now(),
  foreign key (factory_id, org_id) references factories (id, org_id) on delete cascade
);

-- ------------------------------------------------------------------ knowledge base, audit, LLM log, jobs
create table kb_chunks (
  id          text primary key,
  title       text not null,
  source      text not null,
  source_url  text,
  text        text not null,
  embedding   vector(384)
);

create table audit_log (
  id         bigserial primary key,
  org_id     uuid not null references organizations (id) on delete cascade,
  user_id    uuid references users (id),
  action     text not null,
  entity     text not null,
  entity_id  text not null,
  before     jsonb,
  after      jsonb,
  at         timestamptz not null default now()
);
create index audit_log_entity on audit_log (org_id, entity, entity_id);

create table llm_calls (
  id               bigserial primary key,
  org_id           uuid references organizations (id) on delete cascade,
  purpose          text not null,
  model            text not null,
  prompt_version   text,
  input_tokens     integer,
  output_tokens    integer,
  latency_ms       integer,
  document_sha256  char(64),
  created_at       timestamptz not null default now()
);

create table jobs (
  id            bigserial primary key,
  org_id        uuid not null references organizations (id) on delete cascade,
  kind          text not null,
  payload       jsonb not null default '{}',
  status        job_status not null default 'queued',
  attempts      integer not null default 0,
  max_attempts  integer not null default 3,
  run_after     timestamptz not null default now(),
  locked_at     timestamptz,
  locked_by     text,
  last_error    text,
  result        jsonb,
  created_at    timestamptz not null default now(),
  finished_at   timestamptz
);
create index jobs_queue on jobs (run_after, id) where status = 'queued';

-- ------------------------------------------------------------------ privileges
grant usage on schema public to ctrace_app;
grant select, insert, update, delete on all tables in schema public to ctrace_app;
grant usage, select on all sequences in schema public to ctrace_app;
-- reference data: read-only for the app role (admin factor entry goes through a privileged path)
revoke insert, update, delete on emission_factors, weather_monthly, kb_chunks from ctrace_app;
-- the audit log is append-only for the app role
revoke update, delete on audit_log from ctrace_app;

-- ------------------------------------------------------------------ row-level security
alter table organizations enable row level security;
create policy org_self on organizations to ctrace_app
  using (id = ctrace_current_org()) with check (id = ctrace_current_org());

alter table memberships enable row level security;
create policy org_isolation on memberships to ctrace_app
  using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org());

alter table users enable row level security;
create policy users_in_org on users for select to ctrace_app
  using (id = ctrace_current_user()
         or id in (select user_id from memberships where org_id = ctrace_current_org()));

do $$
declare t text;
begin
  foreach t in array array['factories', 'assets', 'interventions', 'documents', 'extractions',
                           'activity_records', 'ledger_entries', 'production_records',
                           'verification_runs', 'verification_results', 'reductions', 'reports',
                           'audit_log', 'llm_calls', 'jobs']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy org_isolation on %I to ctrace_app '
                   'using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org())', t);
  end loop;
end $$;

do $$
declare t text;
begin
  foreach t in array array['emission_factors', 'weather_monthly', 'kb_chunks']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy public_read on %I for select to ctrace_app using (true)', t);
  end loop;
end $$;
