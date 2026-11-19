"""Per-user sliding-window rate limit for endpoints that call an LLM synchronously.

In-process: each API instance counts separately, so the effective limit is N × instances. That is
enough to stop a runaway client from spending tokens; a shared limiter (Postgres or the gateway)
replaces it when the API runs on more than one instance.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from ctrace import config


class SlidingWindow:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                retry = int(60 - (now - q[0])) + 1
                raise HTTPException(429, f"Too many LLM requests; retry in {retry} s", headers={"Retry-After": str(retry)})
            q.append(now)


llm_limiter = SlidingWindow(config.CTRACE_LLM_RATE_PER_MIN)


def limit_llm(user_id: str) -> None:
    """Only counts when the LLM path is actually enabled."""
    if config.llm_enabled():
        llm_limiter.check(user_id)
