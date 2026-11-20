"""Central configuration for C-Trace. Everything tunable lives here, not scattered in code."""
from __future__ import annotations

import os
from pathlib import Path


def find_root() -> Path:
    """Locate the C-Trace workspace (the folder holding `data/` and `models/`).

    Order: the CTRACE_ROOT environment variable; then the nearest parent of this package that
    contains `data/samples` (editable/monorepo install); then the current working directory.
    """
    env = os.getenv("CTRACE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "data" / "samples").is_dir():
            return parent
    return Path.cwd()


ROOT = find_root()
DATA_DIR = ROOT / "data"
PUBLIC_DIR = DATA_DIR / "public"
SAMPLES_DIR = DATA_DIR / "samples"
BILLS_DIR = SAMPLES_DIR / "bills"
MODELS_DIR = ROOT / "models"
# C-Trace Verifier model registry: models/ctrace-verifier/<version>/{model.joblib, metrics.json, MODEL_CARD.md}
CTRACE_VERIFIER_VERSION = os.getenv("CTRACE_VERIFIER_VERSION", "v2")


def verifier_dir(version: str | None = None) -> Path:
    return MODELS_DIR / "ctrace-verifier" / (version or CTRACE_VERIFIER_VERSION)


def verifier_path(version: str | None = None) -> Path:
    return verifier_dir(version) / "model.joblib"


KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

# ---- LLM settings (optional: C-Trace runs fully offline without a key) ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CTRACE_EXTRACTION_MODEL = os.getenv("CTRACE_EXTRACTION_MODEL", "claude-haiku-5-5")
CTRACE_REPORT_MODEL = os.getenv("CTRACE_REPORT_MODEL", "claude-sonnet-5-5")
# Extraction cascade: the first model reads every document; a read whose confidence is below the threshold
# is re-read by the escalation model (set it to "" to disable escalation).
CTRACE_EXTRACTION_ESCALATION_MODEL = os.getenv("CTRACE_EXTRACTION_ESCALATION_MODEL", "claude-sonnet-5-5")
CTRACE_ESCALATE_BELOW = float(os.getenv("CTRACE_ESCALATE_BELOW", "0.7"))
CTRACE_EXTRACTION_EFFORT = os.getenv("CTRACE_EXTRACTION_EFFORT", "low")
CTRACE_EXTRACTION_PROMPT = os.getenv("CTRACE_EXTRACTION_PROMPT", "extract_bill/v2")
CTRACE_COPILOT_PROMPT = os.getenv("CTRACE_COPILOT_PROMPT", "copilot/v1")
# Server-side refusal fallback for models that support it ("default" or "off").
CTRACE_LLM_FALLBACKS = os.getenv("CTRACE_LLM_FALLBACKS", "default")
CTRACE_LLM_TIMEOUT_S = float(os.getenv("CTRACE_LLM_TIMEOUT_S", "120"))
CTRACE_COPILOT_TIMEOUT_S = float(os.getenv("CTRACE_COPILOT_TIMEOUT_S", "30"))  # per model step; then the offline answer
CTRACE_NARRATIVE_TIMEOUT_S = float(os.getenv("CTRACE_NARRATIVE_TIMEOUT_S", "20"))  # report narrative; then the template
CTRACE_LLM_RATE_PER_MIN = int(os.getenv("CTRACE_LLM_RATE_PER_MIN", "20"))  # per user, LLM-backed endpoints
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


# OpenAI-compatible provider (any /chat/completions endpoint: OpenRouter, Groq, Cerebras, OpenAI). Used only when
# ANTHROPIC_API_KEY is not set. CTRACE_OPENAI_MODEL is one model id or a comma-separated list: the first is asked, and
# on OpenRouter the rest are its fallbacks when the first is unavailable. Defaults: models free on OpenRouter in Nov 2026
# (Nemotron reads images, so photo bills work; set CTRACE_OPENAI_VISION=0 for a text-only model).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
CTRACE_OPENAI_BASE_URL = os.getenv("CTRACE_OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
CTRACE_OPENAI_MODEL = os.getenv("CTRACE_OPENAI_MODEL", "nvidia/nemotron-3.5-super-vl:free,google/gemma-4-31b-it:free")
CTRACE_OPENAI_VISION = os.getenv("CTRACE_OPENAI_VISION", "1") == "1"
CTRACE_OPENAI_REASONING = os.getenv("CTRACE_OPENAI_REASONING", "low")  # OpenRouter reasoning effort


def openai_models() -> list[str]:
    return [m.strip() for m in CTRACE_OPENAI_MODEL.split(",") if m.strip()]

def llm_provider() -> str:
    """'anthropic', 'openai' or '' (offline). Claude wins when both keys are set."""
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if OPENAI_API_KEY:
        return "openai"
    return ""


def llm_enabled() -> bool:
    return bool(llm_provider())


def report_model() -> str:
    return openai_models()[0] if llm_provider() == "openai" else CTRACE_REPORT_MODEL


# ---- Tariff sanity ranges (BDT per unit). Used to catch mis-read quantities.
# Configurable assumptions — update to the current BERC-approved industrial tariffs.
TARIFF_RANGES_BDT = {
    "natural_gas": (20.0, 60.0),   # BDT per m3 (industrial / captive power)
    "grid_electricity": (8.0, 18.0),  # BDT per kWh (industrial)
    "diesel": (90.0, 130.0),       # BDT per litre
}

# ---- Verification thresholds
Z_WARN = 2.5      # |z| of log-residual vs expected-energy model -> Medium
Z_FLAG = 3.0      # -> Low (human review required)
# Diesel depends on grid outages that C-Trace cannot observe, so its signal is advisory:
# only gross errors (e.g. unit mistakes) drop a month to Low.
Z_THRESHOLDS = {"gas_m3": (Z_WARN, Z_FLAG), "grid_kwh": (Z_WARN, Z_FLAG), "diesel_l": (3.0, 5.0)}
