# C-Trace — Trace every tonne.
# Requires uv (https://docs.astral.sh/uv/). `make setup` installs it with pip if it is missing.

UV ?= $(shell command -v uv 2>/dev/null || echo "$(HOME)/.local/bin/uv")
RUN := $(UV) run
MODEL := models/ctrace_verifier.joblib

.DEFAULT_GOAL := help
.PHONY: help setup train sample-data test lint typecheck check demo demo-reset demo-streamlit api worker db-start db-stop db-migrate dev-token fetch-data requirements docker-build compose-up clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python 3.12 and all pinned dependencies (uv.lock)
	@command -v uv >/dev/null 2>&1 || [ -x "$(HOME)/.local/bin/uv" ] || python3 -m pip install --user uv
	$(UV) sync --all-packages --frozen

$(MODEL):
	$(RUN) python ml/train.py

train: ## Train the C-Trace Verifier and regenerate models/MODEL_CARD.md
	$(RUN) python ml/train.py

sample-data: ## Regenerate the fictional demo factory's 65 sample bills
	$(RUN) python scripts/make_sample_data.py

test: $(MODEL) ## Run all tests
	$(RUN) pytest

lint: ## Ruff lint
	$(RUN) ruff check .

typecheck: ## mypy on the Python packages
	$(RUN) mypy

check: lint typecheck test ## Everything CI runs

demo: $(MODEL) ## Seed the fictional demo factory into Postgres (65 bills through the real upload + job path)
	$(RUN) python -m ctrace.store.seed
	@echo "Next: make api (and make worker for new uploads); token: make dev-token"

demo-reset: $(MODEL) ## Delete and re-seed the demo organisation
	$(RUN) python -m ctrace.store.seed --reset

demo-streamlit: $(MODEL) ## Streamlit demo at http://localhost:8501 (offline fallback)
	$(RUN) streamlit run apps/demo-streamlit/streamlit_app.py

api: $(MODEL) ## FastAPI with reload at http://localhost:8000/docs
	$(RUN) uvicorn ctrace_api.main:app --reload --port 8000

worker: ## Background worker (extraction jobs)
	$(RUN) python -m ctrace_worker

db-start: ## Start the local dev Postgres (pgserver, data in .pgdata/) and print its URL
	$(RUN) python -m ctrace.store.localdb start

db-stop: ## Stop the local dev Postgres
	$(RUN) python -m ctrace.store.localdb stop

db-migrate: ## Apply SQL migrations and sync emission factors (uses DATABASE_URL if set)
	$(RUN) python -m ctrace.store.migrate

dev-token: ## Print a development access token for the fictional demo user
	@$(RUN) python -m ctrace_api.devtoken

fetch-data: ## Refresh public datasets (needs internet)
	$(RUN) python scripts/fetch_public_data.py

requirements: ## Re-export requirements.txt from uv.lock (for Streamlit Community Cloud)
	$(UV) export --frozen --no-hashes --no-dev --all-packages --format requirements-txt -o requirements.txt

docker-build: ## Build the C-Trace image
	docker build -t c-trace .

compose-up: ## Postgres + API + worker + Streamlit via Docker (infra/docker-compose.yml)
	docker compose -f infra/docker-compose.yml up --build

clean: ## Remove caches (keeps the trained model)
	rm -rf .pytest_cache .mypy_cache .ruff_cache data/training_fleet.csv
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
