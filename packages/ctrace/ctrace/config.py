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
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

# ---- LLM settings (optional: C-Trace runs fully offline without a key) ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CTRACE_EXTRACTION_MODEL = os.getenv("CTRACE_EXTRACTION_MODEL", "claude-haiku-5-5")
CTRACE_REPORT_MODEL = os.getenv("CTRACE_REPORT_MODEL", "claude-sonnet-5-5")


def llm_enabled() -> bool:
    return bool(ANTHROPIC_API_KEY)


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
