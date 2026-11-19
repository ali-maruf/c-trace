# C-Trace API

Interactive docs: `make api`, then open http://localhost:8000/docs (OpenAPI at `/openapi.json`).

## Authentication
Every `/v1` endpoint except `/v1/factors` needs `Authorization: Bearer <token>`.

- **Production:** a Supabase Auth access token. It is verified against `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`
  (ES256 or RS256), with `aud = authenticated` and `iss = <SUPABASE_URL>/auth/v1`.
- **Development:** `make dev-token` prints a 12-hour token for the fictional demo user.

If the user belongs to more than one organisation, add `X-C-Trace-Org: <org id>`.
Every response carries an `X-Request-ID` header. Send your own (up to 64 characters from `[A-Za-z0-9._-]`) to
correlate logs.

| Role | Read | Upload / profile | Confirm / correct / reject | Original bills |
|---|---|---|---|---|
| owner, manager | ✓ | ✓ | ✓ | ✓ |
| reviewer | ✓ | | ✓ | ✓ |
| viewer | ✓ | | | ✓ |
| buyer, lender | ✓ | | | |

## Endpoints (Phase 2)
| Method & path | What it does |
|---|---|
| `GET /health` | Service, model and database status (no auth) |
| `GET /v1/me` | Verified user, active organisation, role, memberships |
| `GET/POST /v1/factories`, `GET /v1/factories/{id}` | Factory profile with assets and interventions |
| `POST /v1/factories/{id}/assets`, `/interventions` | Add solar/boiler/genset/chiller assets and declared interventions |
| `GET/PUT /v1/factories/{id}/production`, `POST …/production/csv` | Monthly production (pieces), with source |
| `POST /v1/factories/{id}/documents` | Upload bills (multipart `files`, PDF/JPEG/PNG, ≤ 20 MB each) → `202` with a job per new file; identical bytes return `duplicate: true` and "Already uploaded on …" |
| `GET /v1/factories/{id}/documents`, `GET /v1/documents/{id}`, `GET /v1/documents/{id}/file` | Documents, their extractions and evidence, original bytes |
| `GET /v1/jobs/{id}` | Extraction job status, attempts, last error |
| `GET /v1/factories/{id}/activity-records?status=` | What C-Trace read from each bill, with review status |
| `GET /v1/factories/{id}/suggestions` | Tariff-based correction suggestions. Each must be accepted explicitly |
| `POST /v1/activity-records/{id}/confirm` · `/correct` · `/reject` | Human review. `correct` and `reject` require a `reason`. All are written to the audit log |
| `GET /v1/activity-records/{id}/audit` | Audit trail (who, when, before, after, reason) |
| `GET /v1/factories/{id}/ledger?period=&activity=` | Trace ledger: trace ID, bill, SHA-256, factor ID, version, source URL, arithmetic |
| `GET /v1/factories/{id}/summary?period_from=&period_to=` | Monthly Scope 1/2 totals, intensity, data status |
| `GET /v1/factors?activity=` | Emission factor registry (public reference data): default and official factors, versions, source URLs |
| `POST /v1/admin/factors` | Platform admins: enter the official national grid factor (source URL, validity window; audit-logged) |
| `GET /v1/factories/{id}/weather` | Monthly T2M and cooling degree days, each labelled NASA POWER or climatology |
| `GET /v1/factories/{id}/benchmark` | Climate TRACE sector context for annualised Scope 1 (a signal, not a grade) |
| `GET /v1/data-sources` | Provenance of every public dataset (no auth) |
| `POST /v1/stateless/extract` · `/analyze` · `/report` | Prototype endpoints that store nothing |

Verification runs, reductions, reports and sharing, and the Copilot come in later phases.

## Example
```bash
TOKEN=$(make -s dev-token)
FID=$(curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/v1/factories | python3 -c "import json,sys;print(json.load(sys.stdin)[0]['id'])")
curl -s -H "Authorization: Bearer $TOKEN" "localhost:8000/v1/factories/$FID/summary?period_from=2026-01"
```
