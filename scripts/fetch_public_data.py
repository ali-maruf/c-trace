"""Refresh C-Trace's Our World in Data extracts (needs internet).   uv run python scripts/fetch_public_data.py

  * OWID energy dataset (Ember electricity data) → Bangladesh grid carbon intensity → location-based Scope 2 factor
  * OWID CO2 dataset → Bangladesh national emissions context
Provenance is written to data/public/PROVENANCE.json. If a new year changes a grid factor, the new year gets
its own factor id (ember-bgd-<year>-v1); existing years keep their values, or `sync_factors` refuses.
"""
from ctrace.config import PUBLIC_DIR
from ctrace.public_data import fetch_owid

if __name__ == "__main__":
    fetch_owid()
    print("[C-Trace] OWID data refreshed:", PUBLIC_DIR)
