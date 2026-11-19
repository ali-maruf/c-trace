"""Live contract test against the Anthropic Messages API. Runs only with ANTHROPIC_API_KEY set
(it spends a few cents); skipped offline and in CI unless the secret is configured.

Checks that the request shape C-Trace sends (structured outputs, PDF and image blocks, effort) is accepted
by the configured models, and that a clean English PDF and a Bangla phone photo are read correctly.
"""
from __future__ import annotations

import json

import pytest

from ctrace import config

pytestmark = pytest.mark.skipif(not config.llm_enabled(), reason="needs ANTHROPIC_API_KEY (live API contract test)")
BILLS = config.DATA_DIR / "eval" / "bills"


def label(file: str) -> dict:
    return next(b for b in json.loads((BILLS / "labels.json").read_text(encoding="utf-8"))["bills"] if b["file"] == file)


@pytest.mark.parametrize("file,media_type", [("en_a_02.pdf", "application/pdf"), ("bn_photo_02.jpg", "image/jpeg")])
@pytest.mark.parametrize("model", [config.CTRACE_EXTRACTION_MODEL, config.CTRACE_EXTRACTION_ESCALATION_MODEL])
def test_live_extraction(file, media_type, model):
    from ctrace.extraction_llm import extract_with_llm

    calls: list = []
    rec = extract_with_llm((BILLS / file).read_bytes(), file, media_type, models=[model], on_call=calls.append)
    lb = label(file)
    assert len(calls) == 1 and calls[0].error is None and calls[0].input_tokens and calls[0].output_tokens
    assert (rec.activity, rec.period, rec.unit) == (lb["activity"], lb["period"], lb["unit"])
    assert rec.quantity == pytest.approx(lb["quantity"], rel=0.005)
    assert "রিনা" not in json.dumps(rec.evidence, ensure_ascii=False)
