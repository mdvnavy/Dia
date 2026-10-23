"""Owner sign-in, client identification and burst limiting for ``/api/agent``.

Interim policy (see ``agent_quota`` for the daily visitor cap):

* **Visitors** may use Ask DIA anonymously, capped per day by ``agent_quota``,
  and by a short per-IP burst guard (``DIA_AGENT_RATE_LIMIT``, default
  ``10/60``) that keeps scripted clients from hammering the quota store.
* **The owner** is unlimited. The owner signs in once by pasting the owner key
  into *Help & settings* (kept in the browser's localStorage) and the UI sends
  it as ``Authorization: Bearer <key>``. The server compares it to
  ``DIA_OWNER_TOKEN`` in constant time. The key is never logged or echoed.

``DIA_OWNER_TOKEN`` shorter than ``MIN_OWNER_TOKEN_LENGTH`` characters is
ignored (with a warning) so a weak key can't become an unlimited bypass.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Mapping

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = "10/60"
MIN_OWNER_TOKEN_LENGTH = 32
OWNER_HEADER = "X-DIA-Owner-Token"


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    return value.strip() if isinstance(value, str) else ""


def owner_token() -> str:
    token = os.environ.get("DIA_OWNER_TOKEN", "").strip()
    if token and len(token) < MIN_OWNER_TOKEN_LENGTH:
        logger.warning(
            "DIA_OWNER_TOKEN is shorter than %d characters; owner bypass disabled",
            MIN_OWNER_TOKEN_LENGTH,
        )
        return ""
    return token


def supplied_owner_token(headers: Mapping[str, str]) -> str:
    """The owner key the client sent, if any (bearer or ``X-DIA-Owner-Token``)."""
    supplied = _header(headers, OWNER_HEADER)
    if supplied:
        return supplied
    authorization = _header(headers, "Authorization")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def is_owner(headers: Mapping[str, str]) -> bool:
    expected = owner_token()
    supplied = supplied_owner_token(headers)
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def configured_mode() -> str:
    """``owner-key`` when an owner key is configured, else ``visitors-only``."""
    return "owner-key" if owner_token() else "visitors-only"


def _on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


def client_ip(headers: Mapping[str, str], peer_ip: str) -> str:
    """Best-effort client IP for limits.

    On Cloud Run the socket peer is Google's front end, and the real client is
    the *right-most* ``X-Forwarded-For`` entry (Google appends the address it
    saw; anything to the left was supplied by the client and can be forged).
    Off Cloud Run, the socket peer is used unless ``DIA_TRUST_FORWARDED_FOR``
    is set (e.g. behind your own proxy).
    """
    trust = _on_cloud_run() or os.environ.get(
        "DIA_TRUST_FORWARDED_FOR", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if trust:
        forwarded = [
            part.strip()
            for part in _header(headers, "X-Forwarded-For").split(",")
            if part.strip()
        ]
        if forwarded:
            return forwarded[-1]
    return peer_ip


def _parse_rate_limit(raw: str) -> tuple[int, float]:
    raw = (raw or DEFAULT_RATE_LIMIT).strip()
    try:
        if raw in {"0", "off", "none"}:
            return 0, 0.0
        count, _, window = raw.partition("/")
        limit = int(count)
        seconds = float(window) if window else 60.0
        if limit < 0 or seconds <= 0:
            raise ValueError
        return limit, seconds
    except ValueError:
        logger.warning("invalid DIA_AGENT_RATE_LIMIT %r; using %s", raw, DEFAULT_RATE_LIMIT)
        return _parse_rate_limit(DEFAULT_RATE_LIMIT)


class RateLimiter:
    """Thread-safe in-memory sliding-window burst guard keyed by client IP."""

    MAX_KEYS = 10_000

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit; return (allowed, retry_after_seconds)."""
        limit, window = _parse_rate_limit(os.environ.get("DIA_AGENT_RATE_LIMIT", ""))
        if limit == 0:
            return True, 0
        now = self._clock()
        with self._lock:
            if len(self._hits) > self.MAX_KEYS:
                self._prune(now, window)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, int(window - (now - hits[0])) + 1)
                return False, retry_after
            hits.append(now)
            return True, 0

    def _prune(self, now: float, window: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= window]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


agent_rate_limiter = RateLimiter()
