"""Google Sign-In owner sessions, client identification and burst limiting.

Interim policy for ``/api/agent`` (see ``agent_quota`` for the daily cap):

* **Visitors** use Ask DIA anonymously, capped per calendar day by
  ``agent_quota`` and by a short per-IP burst guard (``DIA_AGENT_RATE_LIMIT``,
  default ``10/60``) that keeps scripted clients off the quota store.
* **Owners** sign in with Google. The browser gets a Google ID token from
  Google Identity Services and POSTs it once to ``/api/auth/session``. The
  server verifies it (signature, ``aud`` = ``GOOGLE_CLIENT_ID``, ``iss``,
  ``exp``, ``email_verified``) and sets a 12-hour HttpOnly session cookie
  signed with ``DIA_SESSION_SECRET``. Emails in ``OWNER_EMAILS`` (comma list,
  case-insensitive) are unlimited and skip the burst guard; any other
  signed-in account is treated exactly like an anonymous visitor.

Owner status is re-derived from ``OWNER_EMAILS`` on every request, so removing
an address takes effect immediately; the cookie only carries the verified
email. Sign-in is offered only when both ``GOOGLE_CLIENT_ID`` and a
``DIA_SESSION_SECRET`` of at least ``MIN_SESSION_SECRET_LENGTH`` characters
are set; otherwise the button is hidden and everyone gets the visitor cap.
Neither the ID token nor the secret is ever logged or echoed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = "10/60"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SESSION_COOKIE = "dia_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
SESSION_VERSION = "v1"
MIN_SESSION_SECRET_LENGTH = 32
# Leeway for clock skew between this instance and Google when checking exp.
CLOCK_SKEW_SECONDS = 60


# --- kill switch ---------------------------------------------------------------

ASK_DIA_UNAVAILABLE_MESSAGE = (
    "Ask DIA is temporarily unavailable. The intake tools still work, "
    "so you can keep going and try Ask DIA again later."
)
ASK_DIA_RETRY_AFTER_SECONDS = 300
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def ask_dia_enabled() -> bool:
    """``ASK_DIA_ENABLED`` kill switch for ``/api/agent`` (unset = enabled).

    Explicit falsy values switch Ask DIA off for everyone, owners included.
    An unrecognised value (a typo such as ``flase``) also switches it off, so
    a mistyped kill switch fails closed rather than leaving the endpoint open.
    """
    raw = os.environ.get("ASK_DIA_ENABLED")
    if raw is None or not raw.strip():
        return True
    value = raw.strip().lower()
    if value in _ENABLED_VALUES:
        return True
    if value not in _DISABLED_VALUES:
        logger.warning("unrecognised ASK_DIA_ENABLED value; treating Ask DIA as disabled")
    return False


class SignInError(ValueError):
    """A Google ID token was rejected. The message is safe to show users."""


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    return value.strip() if isinstance(value, str) else ""


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def owner_emails() -> frozenset[str]:
    raw = os.environ.get("OWNER_EMAILS", "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def is_owner_email(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in owner_emails()


def session_secret() -> bytes:
    secret = os.environ.get("DIA_SESSION_SECRET", "").strip()
    if secret and len(secret) < MIN_SESSION_SECRET_LENGTH:
        logger.warning(
            "DIA_SESSION_SECRET is shorter than %d characters; Google sign-in disabled",
            MIN_SESSION_SECRET_LENGTH,
        )
        return b""
    return secret.encode("utf-8")


def sign_in_enabled() -> bool:
    return bool(google_client_id() and session_secret())


def configured_mode() -> str:
    """``google`` when Google sign-in is fully configured, else ``visitors-only``."""
    return "google" if sign_in_enabled() else "visitors-only"


# --- Google ID token verification ----------------------------------------------


def _google_verify(token: str, audience: str) -> Mapping[str, Any]:
    """Signature + standard-claim check by google-auth (fetches Google's certs)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(
        token, google_requests.Request(), audience=audience, clock_skew_in_seconds=10
    )


# Tests replace this with a fake; production always uses google-auth.
google_verifier: Callable[[str, str], Mapping[str, Any]] = _google_verify


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    sub: str
    name: str = ""


def verify_google_credential(
    credential: str, *, now: Callable[[], float] = time.time
) -> GoogleIdentity:
    """Verify a GIS ``credential`` and return the identity, or raise SignInError.

    google-auth already checks the signature, ``aud`` and ``exp``; the claims
    are re-checked here so the policy holds regardless of library defaults.
    """
    client_id = google_client_id()
    if not client_id:
        raise SignInError("Google sign-in is not configured.")
    token = (credential or "").strip()
    if not token or len(token) > 4096:
        raise SignInError("Missing Google credential.")
    try:
        claims = google_verifier(token, client_id)
    except Exception as error:  # noqa: BLE001 - any verifier failure is a rejection
        logger.info("Google ID token rejected: %s", type(error).__name__)
        raise SignInError("Google sign-in failed. Try again.") from None

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise SignInError("Google sign-in failed: unexpected issuer.")
    aud = claims.get("aud")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise SignInError("Google sign-in failed: token was issued for another app.")
    try:
        exp = float(claims.get("exp", 0))
    except (TypeError, ValueError):
        exp = 0.0
    if exp + CLOCK_SKEW_SECONDS < now():
        raise SignInError("Google sign-in expired. Sign in again.")
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise SignInError("Google account has no email address.")
    verified = claims.get("email_verified")
    if verified is not True and str(verified).lower() != "true":
        raise SignInError("Google account email is not verified.")
    sub = str(claims.get("sub") or "")
    if not sub:
        raise SignInError("Google sign-in failed: missing subject.")
    return GoogleIdentity(email=email, sub=sub, name=str(claims.get("name") or ""))


# --- signed session cookie -----------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: bytes, body: str) -> str:
    return _b64(hmac.new(secret, f"{SESSION_VERSION}.{body}".encode("ascii"), hashlib.sha256).digest())


def make_session(identity: GoogleIdentity, *, now: Callable[[], float] = time.time) -> str:
    secret = session_secret()
    if not secret:
        raise SignInError("Google sign-in is not configured.")
    issued = int(now())
    body = _b64(
        json.dumps(
            {"e": identity.email, "s": identity.sub, "iat": issued, "exp": issued + SESSION_TTL_SECONDS},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{SESSION_VERSION}.{body}.{_mac(secret, body)}"


def read_session(value: str | None, *, now: Callable[[], float] = time.time) -> str | None:
    """Return the signed-in email for a valid, unexpired session cookie value."""
    secret = session_secret()
    if not value or not secret or not google_client_id():
        return None
    try:
        version, body, mac = value.split(".")
    except ValueError:
        return None
    if version != SESSION_VERSION or not hmac.compare_digest(mac, _mac(secret, body)):
        return None
    try:
        data = json.loads(_unb64(body))
        if not isinstance(data, dict) or int(data.get("exp", 0)) <= now():
            return None
        email = str(data.get("e") or "").strip().lower()
    except (ValueError, TypeError):
        return None
    return email or None


def session_cookie_header(value: str, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}={value}",
        "Path=/",
        f"Max-Age={SESSION_TTL_SECONDS}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie_header(secure: bool) -> str:
    parts = [f"{SESSION_COOKIE}=", "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def session_email(headers: Mapping[str, str]) -> str | None:
    from agent_quota import parse_cookie

    return read_session(parse_cookie(headers.get("Cookie"), SESSION_COOKIE))


def is_owner(headers: Mapping[str, str]) -> bool:
    return is_owner_email(session_email(headers))


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
