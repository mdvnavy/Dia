"""Access control and rate limiting for the live Gemini agent (``/api/agent``).

``/api/agent`` spends Gemini quota and, when MCP toolsets are configured, can
reach real side effects (Make scenarios, GCP APIs). It must never be callable
by anonymous internet traffic, so access is **secure by default**:

1. ``DIA_AGENT_TOKEN`` set: callers must send ``Authorization: Bearer <token>``
   (or ``X-DIA-Agent-Token: <token>``). Anything else gets 401. Fail closed.
2. ``DIA_IAP_AUDIENCE`` set: a request carrying a valid Identity-Aware Proxy
   JWT (``X-Goog-IAP-JWT-Assertion``) for that audience is accepted. The JWT
   is cryptographically verified; the plain ``X-Goog-Authenticated-User-*``
   headers are never trusted on their own because any client can forge them
   when the service is not actually behind IAP.
3. Neither set (local development): only loopback clients (127.0.0.1 / ::1)
   are allowed, and never on Cloud Run (``K_SERVICE`` present). A deployed
   service with no auth configured therefore refuses every agent call instead
   of silently exposing it.

Rate limiting is a simple in-memory sliding window per client IP
(``DIA_AGENT_RATE_LIMIT``, default ``10/60`` = 10 calls per 60 seconds;
``0`` disables). It is per-instance, which is enough to stop casual abuse;
a shared limiter would need Redis/Memorystore.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping

logger = logging.getLogger(__name__)

IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"
DEFAULT_RATE_LIMIT = "10/60"


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str = ""
    principal: str = ""


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    return value.strip() if isinstance(value, str) else ""


def configured_mode() -> str:
    """Describe the active auth mode: ``token``, ``iap``, ``token+iap`` or ``local-only``."""
    modes = []
    if os.environ.get("DIA_AGENT_TOKEN", "").strip():
        modes.append("token")
    if os.environ.get("DIA_IAP_AUDIENCE", "").strip():
        modes.append("iap")
    return "+".join(modes) or "local-only"


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


def _verify_iap_jwt(assertion: str, audience: str) -> str | None:
    """Return the verified principal email for an IAP JWT, or None."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        claims = id_token.verify_token(
            assertion,
            google_requests.Request(),
            audience=audience,
            certs_url=IAP_CERTS_URL,
        )
    except Exception as error:  # noqa: BLE001 - any verification failure denies
        logger.info("IAP JWT rejected: %s", type(error).__name__)
        return None
    if claims.get("iss") != IAP_ISSUER:
        return None
    return str(claims.get("email") or claims.get("sub") or "iap-user")


def authorize(
    headers: Mapping[str, str],
    client_ip: str,
    *,
    iap_verifier: Callable[[str, str], str | None] = _verify_iap_jwt,
) -> AuthDecision:
    """Decide whether a request may run a live agent turn."""
    token = os.environ.get("DIA_AGENT_TOKEN", "").strip()
    audience = os.environ.get("DIA_IAP_AUDIENCE", "").strip()

    if token:
        supplied = _header(headers, "X-DIA-Agent-Token")
        authorization = _header(headers, "Authorization")
        if not supplied and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if supplied and hmac.compare_digest(
            supplied.encode("utf-8"), token.encode("utf-8")
        ):
            return AuthDecision(True, principal="token")

    if audience:
        assertion = _header(headers, "X-Goog-IAP-JWT-Assertion")
        if assertion:
            principal = iap_verifier(assertion, audience)
            if principal:
                return AuthDecision(True, principal=principal)

    if token or audience:
        return AuthDecision(False, "agent access requires a valid token or IAP identity")

    # No auth configured: local development only.
    if _on_cloud_run():
        return AuthDecision(
            False,
            "agent access is disabled: set DIA_AGENT_TOKEN or DIA_IAP_AUDIENCE "
            "on this deployment",
        )
    if _is_loopback(client_ip):
        return AuthDecision(True, principal="local")
    return AuthDecision(
        False,
        "agent access from non-local clients requires DIA_AGENT_TOKEN or DIA_IAP_AUDIENCE",
    )


def client_ip(headers: Mapping[str, str], peer_ip: str) -> str:
    """Best-effort client IP for rate limiting.

    On Cloud Run the socket peer is Google's front end, and the real client is
    the *right-most* ``X-Forwarded-For`` entry (left-most entries are supplied
    by the client and can be forged). Off Cloud Run, the socket peer is used
    unless ``DIA_TRUST_FORWARDED_FOR`` is set (e.g. behind your own proxy).
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
    """Thread-safe sliding-window limiter keyed by client IP."""

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
