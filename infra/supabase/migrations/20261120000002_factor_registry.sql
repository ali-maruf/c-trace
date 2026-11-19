-- C-Trace factor registry v2: default vs official factors, validity windows, who entered a factor.
alter table emission_factors
  add column kind text not null default 'default' check (kind in ('default', 'official')),
  add column entered_by uuid references users (id),
  add constraint emission_factors_validity check (valid_to is null or valid_from is null or valid_to >= valid_from),
  add constraint emission_factors_official_sourced check (kind <> 'official' or (valid_from is not null and source_url like 'http%'));

create index emission_factors_lookup on emission_factors (activity, region, kind, valid_from);

-- weather rows remember where each value came from (NASA POWER or the climatology fallback)
alter table weather_monthly alter column source set default 'NASA POWER monthly T2M (MERRA-2)';
