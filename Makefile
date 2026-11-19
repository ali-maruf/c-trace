# C-Trace — Trace every tonne.
# Requires uv (https://docs.astral.sh/uv/). `make setup` installs it with pip if it is missing.

UV ?= $(shell command -v uv 2>/dev/null || echo "$(HOME)/.local/bin/uv")
RUN := $(UV) run
MODEL := models/ctrace_verifier.joblib

.DEFAULT_GOAL := help
.PHONY: help setup train sample-data test lint typecheck check demo demo-streamlit api fetch-data requirements docker-build clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

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

demo: demo-streamlit ## Run the demo (Phase 1: Streamlit; later phases seed the database)

demo-streamlit: $(MODEL) ## Streamlit demo at http://localhost:8501 (offline fallback)
	$(RUN) streamlit run apps/demo-streamlit/streamlit_app.py

api: $(MODEL) ## FastAPI with reload at http://localhost:8000/docs
	$(RUN) uvicorn ctrace_api.main:app --reload --port 8000

fetch-data: ## Refresh public datasets (needs internet)
	$(RUN) python scripts/fetch_public_data.py

requirements: ## Re-export requirements.txt from uv.lock (for Streamlit Community Cloud)
	$(UV) export --frozen --no-hashes --no-dev --all-packages --format requirements-txt -o requirements.txt

docker-build: ## Build the C-Trace image
	docker build -t c-trace .

clean: ## Remove caches (keeps the trained model)
	rm -rf .pytest_cache .mypy_cache .ruff_cache data/training_fleet.csv
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
