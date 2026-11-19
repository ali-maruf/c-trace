-- C-Trace extraction v2: cache of model reads, keyed by document hash, model and prompt version.
-- Org-scoped: a cached read is only reused inside the organisation that paid for it.
create table llm_cache (
  org_id          uuid not null references organizations (id) on delete cascade,
  document_sha256 char(64) not null,
  model           text not null,
  prompt_version  text not null,
  payload         jsonb not null,
  created_at      timestamptz not null default now(),
  primary key (org_id, document_sha256, model, prompt_version)
);

alter table llm_calls add column stop_reason text, add column error text;

grant select, insert, update, delete on llm_cache to ctrace_app;
alter table llm_cache enable row level security;
create policy org_isolation on llm_cache to ctrace_app
  using (org_id = ctrace_current_org()) with check (org_id = ctrace_current_org());
