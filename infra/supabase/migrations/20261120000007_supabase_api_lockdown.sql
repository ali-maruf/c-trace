-- C-Trace is reached only through its own API. On Supabase, the Data API (PostgREST) roles `anon` and `authenticated`
-- get default privileges on everything in `public`. RLS already gives them no rows (every policy is `to ctrace_app`), but
-- C-Trace does not use the Data API, so remove the grants as well. A no-op on plain Postgres, where these roles don't exist.
do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke all on all functions in schema public from %I', r);
      execute format('alter default privileges in schema public revoke all on tables from %I', r);
      execute format('alter default privileges in schema public revoke all on sequences from %I', r);
      execute format('alter default privileges in schema public revoke all on functions from %I', r);
    end if;
  end loop;
end $$;
