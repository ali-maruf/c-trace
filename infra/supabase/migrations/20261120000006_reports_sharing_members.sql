-- C-Trace Reports sharing (Phase 8) and organisation invitations.
alter table reports
  add column share_token_hash char(64) unique,          -- sha256 of the share token; the token itself is never stored
  add column share_created_by uuid references users (id),
  add column share_created_at timestamptz,
  add column include_documents boolean not null default false,
  add column pdf_bytes integer;

-- every view of a shared report, for the owner to see who looked (no personal data: hashed address, agent family)
create table report_share_views (
  id          bigserial primary key,
  org_id      uuid not null references organizations (id) on delete cascade,
  report_id   uuid not null references reports (id) on delete cascade,
  viewed_at   timestamptz not null default now(),
  what        text not null,                            -- 'page' | 'pdf' | 'document'
  client_hash char(16),
  user_agent  text
);
grant select on report_share_views to ctrace_app;
alter table report_share_views enable row level security;
create policy org_isolation on report_share_views to ctrace_app
  using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org());

create table org_invites (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references organizations (id) on delete cascade,
  email       text not null,
  role        membership_role not null,
  created_by  uuid references users (id),
  created_at  timestamptz not null default now(),
  accepted_at timestamptz,
  unique (org_id, email)
);
grant select, insert, update, delete on org_invites to ctrace_app;
alter table org_invites enable row level security;
create policy org_isolation on org_invites to ctrace_app
  using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org());

-- deleting a factory removes its stored originals too
grant delete on document_blobs to ctrace_app;
