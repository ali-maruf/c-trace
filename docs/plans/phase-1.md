# Phase 1 — Monorepo restructure, Makefile, CI

## Goal
Move the working prototype into the target monorepo layout without changing behaviour.
Done when the 13 original tests pass from `packages/ctrace`, the Streamlit demo and the API
still run, and the CI workflow's steps pass locally.

## Layout after this phase
```
apps/demo-streamlit/streamlit_app.py   (was app/streamlit_app.py)
services/api/ctrace_api/main.py        (was api/main.py), installable package `ctrace-api`
packages/ctrace/ctrace/                (was ctrace/), installable package `ctrace`
packages/ctrace/tests/                 (was tests/)
ml/train.py                            unchanged location
scripts/                               unchanged location
data/, models/                         unchanged location (model registry layout is Phase 5)
```
Folders for later phases (`apps/web`, `services/worker`, `infra/`) are **not** created yet:
empty scaffolding would be a placeholder feature.

## Files touched
- `packages/ctrace/pyproject.toml`, `services/api/pyproject.toml`, root `pyproject.toml` (uv workspace)
- `uv.lock` (exact pins), `requirements.txt` (exported from the lock, for Streamlit Cloud)
- `ctrace/config.py`: `ROOT` can no longer be `parent.parent` of the package. Resolve it from
  `CTRACE_ROOT`, else by walking up to the folder that holds `data/samples`, else the cwd.
- Remove every `sys.path.insert` hack (app, api, ml, scripts, tests); imports come from the installed package.
- `Makefile`, `.github/workflows/ci.yml`, `Dockerfile`, `.dockerignore`, `.gitignore`
- `CLAUDE.md`, `README.md`, `docs/DECISIONS.md`

## Risks
- **Path resolution** after the move (models, samples, KB). Mitigation: KB stays inside the package
  (`KNOWLEDGE_DIR` is package-relative); data/model paths go through one resolver with a test.
- **Pickled model** references `ctrace.verify.Verifier` by module path. The module path is unchanged,
  so the existing joblib still loads; retraining is ~10 s anyway.
- **scikit-learn version drift** breaks the pickle. Mitigation: exact pins in `uv.lock`.
- **Lint/type debt** in the prototype. Ruff and mypy are configured to pass on the current code with
  real fixes, not blanket ignores; long-line rule (E501) is off to avoid a cosmetic rewrite.

## Tests
- The 13 original tests, moved unchanged except for removing the `sys.path` hack.
- New: path resolution test; API smoke tests (`/health`, `/v1/factors`, `/v1/extract` on a sample bill);
  Streamlit `AppTest` smoke test that the demo script runs without an exception.
