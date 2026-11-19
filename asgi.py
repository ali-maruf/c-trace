"""Vercel entrypoint for the C-Trace API (see vercel.json). Locally use `make api`."""
from ctrace_api.main import app

__all__ = ["app"]
