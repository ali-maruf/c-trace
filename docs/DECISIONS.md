# C-Trace — Decisions and assumptions

Each entry: what we decided, why, and what would change it. Newest last.

## Phase 1 — repository structure

### D1. uv workspace with a committed lockfile
`pyproject.toml` at the root declares a uv workspace with two members, `packages/ctrace` (core) and
`services/api` (FastAPI). `uv.lock` pins every transitive version; `make setup` runs `uv sync --frozen`.
**Why:** the brief requires pinned, reproducible installs on a clean machine, and one lockfile for
the core, API, demo and dev tools avoids drift between them. scikit-learn is additionally constrained
to `1.9.x` in `packages/ctrace` because the pickled C-Trace Verifier is tied to that minor version.
**Revisit if:** the worker (Phase 2) needs a dependency set that conflicts with the API.

### D2. `requirements.txt` is generated, not edited
It is exported from `uv.lock` (`make requirements`) because Streamlit Community Cloud installs from
`requirements.txt`. CI fails if it drifts from the lockfile.

### D3. Workspace root is discovered, not hard-coded
After the move, `ctrace/config.py` can no longer assume `parent.parent` is the repo root.
`find_root()` uses `CTRACE_ROOT`, else the nearest parent of the package containing `data/samples`,
else the cwd. The Docker image sets `CTRACE_ROOT=/app`. The knowledge base stays package-relative
because it ships with the package.

### D4. `models/` and `scripts/` stay at the root for now
The versioned model registry (`models/ctrace-verifier/<version>/`) is Phase 5. Moving the model now
would be a second move of the same file. The pickled class path `ctrace.verify.Verifier` is unchanged,
so the existing model loads as-is.

### D5. No empty folders for later phases
`apps/web`, `services/worker` and `infra/` are created in the phases that implement them. Empty
scaffolding would look like features that don't exist (brief §3.6).

### D6. Lint and type checks
Ruff (`E, F, W, I, B, UP, SIM`) and mypy run in CI. E501 (line length) is disabled: the prototype uses long,
readable single-line f-strings and reflowing them would be churn without a behaviour change. Everything else
was fixed in code (ambiguous names, `zip(strict=True)`, FastAPI `Annotated[UploadFile, File()]`, typed `Any`
for the sklearn IsolationForest field). One `type: ignore` remains on the Anthropic `messages.create` call in
`extraction.py`; that function is rewritten with strict tool-use schemas in Phase 3.

### D7. Tests live with the code they test
`packages/ctrace/tests` (the 13 original tests + config tests), `services/api/tests` (API smoke tests),
`apps/demo-streamlit/tests` (Streamlit `AppTest`: the demo factory loads and only 2025-09 and 2026-07 are Low).
pytest runs with `--import-mode=importlib` so the three `tests/` folders don't collide.

### D8. Local verification limits
Docker is not installed on the development machine used for Phase 1, so the Dockerfile is verified by the
CI `docker` job only. The CI workflow's Python steps were run locally with the same commands.
