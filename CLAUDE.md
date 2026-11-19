# CLAUDE.md — C-Trace

Product: **C-Trace — Trace every tonne.** Verifiable carbon MRV for Bangladeshi factories. The full brief is in `docs/CLAUDE_CODE_PROMPT.md`.

## Rules
- Brand is always "C-Trace" (capital C, hyphen, capital T). Package `ctrace`, slug `c-trace`.
- AI reads and explains; deterministic code calculates. No LLM output is ever an emissions number.
- Every ledger row stays traceable: trace_id, document SHA-256, factor version and source, arithmetic.
- LLM text must pass `number_guard` before it reaches a user or a PDF.
- Simulated data is labelled simulated; the demo factory is fictional.
- No placeholder features, fake data in the UI, or invented API endpoints. Check the docs first.
- Never regress `pytest -q`. The demo factory must flag exactly 2025-09 (dropped-digit gas bill) and 2026-07 (missing diesel).

## Commands
- `pip install -r requirements.txt`
- `python ml/train.py` — train the C-Trace Verifier and regenerate the model card
- `python scripts/make_sample_data.py` — regenerate the demo bills
- `streamlit run app/streamlit_app.py` — prototype UI
- `uvicorn api.main:app --reload` — API
- `pytest -q` — tests
