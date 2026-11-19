-- C-Trace live demo sandboxes and self-service onboarding.
alter table organizations
  add column is_demo boolean not null default false,
  add column created_by uuid references users (id);

-- Server-side secrets the API generates for itself (e.g. the demo-token signing key). Owner-only: the app role
-- has no privileges on this table, and RLS denies it too.
create table app_secrets (
  name        text primary key,
  value       text not null,
  created_at  timestamptz not null default now()
);
revoke all on app_secrets from ctrace_app;
alter table app_secrets enable row level security;

create index organizations_demo_age on organizations (created_at) where is_demo;

-- Original documents stored in Postgres (CTRACE_STORAGE=db) for hosts without a persistent disk.
-- Content-addressed and org-scoped like the local file store.
create table document_blobs (
  storage_key  text primary key,
  org_id       uuid not null references organizations (id) on delete cascade,
  data         bytea not null,
  created_at   timestamptz not null default now()
);
grant select, insert on document_blobs to ctrace_app;
alter table document_blobs enable row level security;
create policy org_isolation on document_blobs to ctrace_app
  using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org());
