# ctrace — the C-Trace core

Python package behind C-Trace. Deterministic emissions calculation, document extraction,
the C-Trace Verifier, reduction proof and C-Trace Reports. Imported by the API
(`services/api`), the Streamlit demo (`apps/demo-streamlit`) and `ml/train.py`.

Data and model paths are resolved from `CTRACE_ROOT`, or the nearest parent folder that holds
`data/samples` (see `ctrace.config.find_root`).

Tests: `uv run pytest packages/ctrace` from the repo root.
