# Phase 3 — Extraction v2

## Goal
Bills, including Bangla bills and photos, are read by Claude into a strict schema with per-field verbatim
evidence and confidence. Results are cached by document SHA-256, low-confidence reads are escalated to a
stronger model, and every call is logged. A labelled eval set measures field accuracy per method.
Done when the eval report is generated and the offline (no key) path still works.

## API facts checked against current docs (Nov 2026; see DECISIONS D19–D21)
- `claude-sonnet-5-5` (escalation) rejects forced `tool_choice`, so both models use **structured outputs**
  (`output_config.format` with a JSON schema) instead of a forced tool. One mechanism for both models.
- Schema rules: every object needs `additionalProperties: false`. No min/max/length constraints. At most
  24 optional and 16 union-typed parameters per request. The C-Trace schema makes every field required, uses
  no unions, and returns the empty string for "not visible". Numbers come back as printed text and C-Trace
  parses them, so Bangla digits and lakh grouping are handled in code.
- Check `stop_reason` for `refusal` and `max_tokens` before parsing.
- Images: JPEG/PNG/GIF/WebP, ≤ 8000 px, 10 MB base64 per image on the Claude API (5 MB on Bedrock), and
  current models read up to 2576 px on the long edge. We downscale to 2576 px and stay under 5 MB.
- Thinking can't be disabled on Sonnet 5.5. Extraction runs at `effort: low` on both models.

## Design
- `ctrace/prompts/extract_bill/v2.md` (system prompt) and `v2.schema.json`, loaded by version from config.
- `ctrace/llm.py`: client factory, `LLMCall` records, the media preparation step, and the cache protocol.
- `ctrace/extraction_llm.py`: call → parse → normalise (Bangla digits/months/units) → **evidence check**
  (the value's digits must appear in its own evidence, else confidence is capped) → redact personal data
  from evidence → `finalize` (unit, tariff). The cascade is Haiku, then Sonnet when confidence < 0.7.
- Cross-check: for a PDF with a text layer, if the rules parser and the LLM disagree on quantity, the bill
  gets an issue for a human.
- Rules parser: Bangla digits, months, units and labels; unit-in-brackets labels; personal-data redaction.
- Store: migration `0002` adds an org-scoped `llm_cache` table. `llm_calls` rows are written for every
  real call, and `extractions.prompt_version` and `model` are recorded.
- API: per-user rate limit on the LLM-backed stateless endpoints; at most 50 files per upload request.

## Eval set (`data/eval/bills/`, all fictional)
36 bills. English text PDFs in three layouts, English traps (VAT/subtotal lines, meter readings, a dropped
digit), English phone photos, Bangla photos, Bangla PDFs with a text layer, and mixed English labels with
Bangla digits. Some carry a fictional customer name to test redaction. `labels.json` holds ground truth.
`ml/eval_extraction.py` scores each available method (rules; Haiku, Sonnet and the cascade when a key is set)
and writes `data/eval/results/<method>.json`. The model card's extraction section is rendered from those files.

## Honesty notes
- The eval set is synthetic and written by the same team as the rules parser, so rules accuracy is an
  optimistic upper bound for these layouts, not a field estimate. Stated in the model card.
- No API key is available while building this phase, so only the rules results are computed. The card says
  "not run" for the LLM methods until `make eval-extraction` runs with a key.

## Tests
Schema obeys the documented limits; fake-client tests for parsing, Bangla normalisation, the evidence check,
escalation, the cache, call logging, refusal and max_tokens; image downscaling; redaction; hypothesis
properties for units, digits and periods; store integration (cache and call log rows); eval script smoke
test; a live contract test that runs only with `ANTHROPIC_API_KEY`.
