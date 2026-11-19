# C-Trace image: Streamlit demo by default; the API runs from the same image.
#   docker build -t c-trace .
#   docker run -p 8501:8501 c-trace                                   # Streamlit demo
#   docker run -p 8000:8000 -e DATABASE_URL=... c-trace uvicorn ctrace_api.main:app --host 0.0.0.0 --port 8000
#   docker run -e DATABASE_URL=... c-trace python -m ctrace_worker
# The image has no embedded Postgres: API and worker need DATABASE_URL (see infra/docker-compose.yml).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# Dependencies first (cached layer), then the code.
COPY pyproject.toml uv.lock ./
COPY packages/ctrace/pyproject.toml packages/ctrace/README.md packages/ctrace/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN mkdir -p packages/ctrace/ctrace services/api/ctrace_api services/worker/ctrace_worker \
 && uv sync --frozen --no-dev --all-packages --no-install-workspace

COPY . .
# The trained C-Trace Verifier (models/ctrace-verifier/<version>/) and the demo bills are committed; CI retrains to
# check that they are reproducible.
RUN uv sync --frozen --no-dev --all-packages

ENV CTRACE_ROOT=/app
EXPOSE 8501 8000
CMD ["streamlit", "run", "apps/demo-streamlit/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
