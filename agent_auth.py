"""Google Sign-In sessions, the Ask DIA kill switch, and burst limiting.

Access policy (lockdown v2, Ikenna 2026-10-23: "No use without signing in"):

* **Signed-out visitors** may read the public pages (landing, How it works,
  static assets, ``/api/config``, health) and nothing else. Every functional
  route answers ``401 {"error": "sign_in_required"}`` before doing any work.
* **Signing in**: the browser gets a Google ID token from Google Identity
  Services and POSTs it once to ``/api/auth/session``. The server verifies it
  (signature, ``aud`` = ``GOOGLE_CLIENT_ID``, ``iss``, ``exp``,
  ``email_verified``) and sets a short-lived HttpOnly session cookie, signed
  with ``DIA_SESSION_SECRET`` and bound to the token's ``sub``.
* **Identity** for quotas is the verified ``sub`` carried in the signed
  cookie. Nothing else the client sends (body fields, headers, other cookies,
  localStorage) is ever used to identify a user or an owner.
* **Owners** are the comma-separated ``OWNER_EMAILS`` (trimmed, lowercased),
  matched only against the verified token's email with ``email_verified``
  true. Owners are uncapped. Owner status is re-derived on every request.

Replaying a raw ID token at ``/api/auth/session`` just mints another session
for the same ``sub``, which shares the same quota, so a replay buys nothing.
Neither the ID token nor any secret is ever logged or echoed.
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

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
SESSION_COOKIE = "dia_session"
DEFAULT_SESSION_TTL_SECONDS = 2 * 60 * 60
MIN_SESSION_TTL_SECONDS = 5 * 60
MAX_SESSION_TTL_SECONDS = 12 * 60 * 60
SESSION_VERSION = "v2"
MIN_SESSION_SECRET_LENGTH = 32
# Leeway for clock skew between this instance and Google when checking exp.
CLOCK_SKEW_SECONDS = 60
_TRUE = frozenset({"1", "true", "yes", "on"})

SIGN_IN_REQUIRED = "sign_in_required"
SIGN_IN_MESSAGE = "Sign in with Google to use DIA."


# --- kill switch ---------------------------------------------------------------

ASK_DIA_UNAVAILABLE_MESSAGE = (
    "Ask DIA is temporarily unavailable. The intake tools still work, "
    "so you can keep going and try Ask DIA again later."
)
ASK_DIA_RETRY_AFTER_SECONDS = 3600


def ask_dia_enabled() -> bool:
    """``ASK_DIA_ENABLED`` kill switch for ``/api/agent``: OFF unless explicitly on.

    Same semantics as hotfix/ask-dia-off: only ``1``/``true``/``yes``/``on``
    (any case) enable Ask DIA. Unset, empty, ``false`` or a typo all leave it
    off, for owners too, so the switch always fails closed.
    """
    return os.environ.get("ASK_DIA_ENABLED", "").strip().lower() in _TRUE


class SignInError(ValueError):
    """A Google ID token was rejected. The message is safe to show users."""


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    return value.strip() if isinstance(value, str) else ""


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def owner_emails() -> frozenset[str]:
    """``OWNER_EMAILS``: comma-separated, trimmed and lowercased."""
    raw = os.environ.get("OWNER_EMAILS", "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def session_secret() -> bytes:
    secret = os.environ.get("DIA_SESSION_SECRET", "").strip()
    if secret and len(secret) < MIN_SESSION_SECRET_LENGTH:
        logger.warning(
            "DIA_SESSION_SECRET is shorter than %d characters; Google sign-in disabled",
            MIN_SESSION_SECRET_LENGTH,
        )
        return b""
    return secret.encode("utf-8")


def session_ttl_seconds() -> int:
    raw = os.environ.get("DIA_SESSION_TTL_SECONDS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SESSION_TTL_SECONDS
    except ValueError:
        value = DEFAULT_SESSION_TTL_SECONDS
    return max(MIN_SESSION_TTL_SECONDS, min(MAX_SESSION_TTL_SECONDS, value))


def sign_in_enabled() -> bool:
    return bool(google_client_id() and session_secret())


def configured_mode() -> str:
    """``google`` when Google sign-in is fully configured, else ``unconfigured``."""
    return "google" if sign_in_enabled() else "unconfigured"


def local_dev_open() -> bool:
    """``DIA_LOCAL_DEV_NO_SIGN_IN``: local development / test escape hatch.

    Lets a developer run DIA on their own machine without a Google OAuth
    client. It is **ignored on Cloud Run** (``K_SERVICE`` set), so it can never
    open the live service, and it is a server env var, so no client can set it.
    """
    if os.environ.get("K_SERVICE"):
        return False
    return os.environ.get("DIA_LOCAL_DEV_NO_SIGN_IN", "").strip().lower() in _TRUE


# --- Google ID token verification ----------------------------------------------


# Ported from Tia (mdvnavy/tia-sia packages/auth/tokens.py GoogleTokenVerifier):
# the same google-auth call, the same "Bearer " handling, the same claim checks
# and error classes. ``google_request_adapter`` is Tia's ``request_adapter``:
# None means google-auth's real HTTP transport to Google's certs endpoint;
# tests inject a transport that serves a locally generated key set.
google_request_adapter: Any = None


class TokenVerificationError(ValueError):
    """Raised when an ID token fails verification (Tia's hierarchy)."""


class MissingTokenError(TokenVerificationError):
    pass


class ExpiredTokenError(TokenVerificationError):
    pass


class InvalidIssuerError(TokenVerificationError):
    pass


class InvalidAudienceError(TokenVerificationError):
    pass


class TamperedTokenError(TokenVerificationError):
    pass


class UnverifiedEmailError(TokenVerificationError):
    pass


def _strip_bearer(value: str | None) -> str:
    """Drop an optional "Bearer" scheme (Tia's rule; a bare "Bearer" is empty)."""
    parts = (value or "").strip().split(None, 1)
    if parts and parts[0].lower() == "bearer":
        return parts[1].strip() if len(parts) > 1 else ""
    return (value or "").strip()


def _google_verify(token: str, audience: str) -> Mapping[str, Any]:
    """Signature (Google's published keys), aud and exp via google-auth, as in Tia."""
    clean = _strip_bearer(token)
    if not clean:
        raise MissingTokenError("missing authentication token")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    transport = google_request_adapter or google_requests.Request()
    try:
        claims = id_token.verify_oauth2_token(
            clean, transport, audience=audience, clock_skew_in_seconds=10
        )
    except Exception as exc:  # noqa: BLE001 - classified like Tia
        msg = str(exc).lower()
        if "expired" in msg:
            raise ExpiredTokenError("token has expired") from None
        if "audience" in msg or "wrong recipient" in msg:
            raise InvalidAudienceError("invalid token audience") from None
        if "issuer" in msg:
            raise InvalidIssuerError("invalid token issuer") from None
        raise TamperedTokenError("token verification failed") from None
    if claims.get("iss", "") not in GOOGLE_ISSUERS:
        raise InvalidIssuerError("untrusted token issuer")
    if not str(claims.get("email", "")).strip():
        raise TokenVerificationError("token missing email claim")
    if not claims.get("email_verified", False):
        raise UnverifiedEmailError("email is not verified by Google")
    if not str(claims.get("sub", "")).strip():
        raise TokenVerificationError("token missing sub claim")
    return claims


# Tests replace this with a fake; production always uses google-auth.
google_verifier: Callable[[str, str], Mapping[str, Any]] = _google_verify


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    sub: str
    name: str = ""
    email_verified: bool = True


@dataclass(frozen=True)
class Identity:
    """Who is making this request, derived only from the signed session."""

    sub: str
    email: str
    email_verified: bool
    local_dev: bool = False

    @property
    def owner(self) -> bool:
        return is_owner_identity(self)

    @property
    def uncapped(self) -> bool:
        return self.local_dev or self.owner


def is_owner_identity(identity: "Identity | GoogleIdentity | None") -> bool:
    if identity is None or not getattr(identity, "email_verified", False):
        return False
    email = (identity.email or "").strip().lower()
    return bool(email) and email in owner_emails()


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
    token = _strip_bearer(credential)
    if not token or len(token) > 4096:
        raise SignInError("Missing Google credential.")
    try:
        claims = google_verifier(token, client_id)
    except ExpiredTokenError:
        raise SignInError("Google sign-in expired. Sign in again.") from None
    except UnverifiedEmailError:
        raise SignInError("Google account email is not verified.") from None
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
            {
                "s": identity.sub,
                "e": identity.email,
                "v": bool(identity.email_verified),
                "aud": google_client_id(),
                "iat": issued,
                "exp": issued + session_ttl_seconds(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{SESSION_VERSION}.{body}.{_mac(secret, body)}"


def read_session(value: str | None, *, now: Callable[[], float] = time.time) -> Identity | None:
    """Return the identity for a valid, unexpired, untampered session cookie."""
    secret = session_secret()
    client_id = google_client_id()
    if not value or not secret or not client_id or len(value) > 4096:
        return None
    try:
        version, body, mac = value.split(".")
    except ValueError:
        return None
    if version != SESSION_VERSION or not hmac.compare_digest(mac, _mac(secret, body)):
        return None
    try:
        data = json.loads(_unb64(body))
        if not isinstance(data, dict):
            return None
        if int(data.get("exp", 0)) <= now():
            return None
        # A session minted for another OAuth client (or before a client ID
        # rotation) is not accepted.
        if data.get("aud") != client_id:
            return None
        sub = str(data.get("s") or "")
        email = str(data.get("e") or "").strip().lower()
        verified = data.get("v") is True
    except (ValueError, TypeError):
        return None
    if not sub:
        return None
    return Identity(sub=sub, email=email, email_verified=verified)


def session_cookie_header(value: str, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}={value}",
        "Path=/",
        f"Max-Age={session_ttl_seconds()}",
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


def parse_cookie(header: str | None, name: str) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip() or None
    return None


LOCAL_DEV_IDENTITY = Identity(sub="local-dev", email="", email_verified=False, local_dev=True)


def request_identity(headers: Mapping[str, str]) -> Identity | None:
    """The caller's identity: the signed session cookie, else None.

    With ``DIA_LOCAL_DEV_NO_SIGN_IN`` (never on Cloud Run) a request without a
    valid session is treated as an uncapped local developer.
    """
    identity = read_session(parse_cookie(headers.get("Cookie"), SESSION_COOKIE))
    if identity is None and local_dev_open():
        return LOCAL_DEV_IDENTITY
    return identity


# --- client IP (for the sign-in burst guard only) -------------------------------


def _on_cloud_run() -> bool:
    return bool(os.environ.get("K_SERVICE"))


def client_ip(headers: Mapping[str, str], peer_ip: str) -> str:
    """Best-effort client IP.

    On Cloud Run the socket peer is Google's front end, which *appends* the
    address it saw to ``X-Forwarded-For``. Everything to the left of that last
    entry came from the client and can be forged, so only the **right-most**
    entry is used. Off Cloud Run, the socket peer is used unless
    ``DIA_TRUST_FORWARDED_FOR`` is set (e.g. behind your own proxy).
    """
    trust = _on_cloud_run() or os.environ.get(
        "DIA_TRUST_FORWARDED_FOR", ""
    ).strip().lower() in _TRUE
    if trust:
        forwarded = [
            part.strip()
            for part in _header(headers, "X-Forwarded-For").split(",")
            if part.strip()
        ]
        if forwarded:
            return forwarded[-1]
    return peer_ip


def hashed_ip(ip: str) -> str:
    """Salted (``DIA_VISITOR_SALT``) hash of an IP, so raw IPs are never stored."""
    import ipaddress

    try:
        address = ipaddress.ip_address(ip.strip())
        if isinstance(address, ipaddress.IPv6Address):
            # One subscriber usually owns a whole /64.
            ip = (
                str(address.ipv4_mapped)
                if address.ipv4_mapped
                else str(ipaddress.ip_network(f"{address}/64", strict=False))
            )
    except ValueError:
        ip = ip.strip() or "unknown"
    salt = (os.environ.get("DIA_VISITOR_SALT", "") or "dia-visitor-v1").encode("utf-8")
    return "ip:" + hmac.new(salt, f"ip:{ip}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# --- burst limiting -------------------------------------------------------------


def parse_rate_limit(raw: str, default: str) -> tuple[int, float]:
    raw = (raw or default).strip()
    try:
        if raw.lower() in {"0", "off", "none"}:
            return 0, 0.0
        count, _, window = raw.partition("/")
        limit = int(count)
        seconds = float(window) if window else 60.0
        if limit < 0 or seconds <= 0:
            raise ValueError
        return limit, seconds
    except ValueError:
        logger.warning("invalid rate limit %r; using %s", raw, default)
        return parse_rate_limit(default, default)


class RateLimiter:
    """Thread-safe in-memory sliding-window burst guard.

    ``env`` names the variable holding ``count/seconds`` (``0``/``off``
    disables it); ``default`` applies when it is unset.
    """

    MAX_KEYS = 10_000

    def __init__(
        self,
        env: str = "DIA_BURST_LIMIT",
        default: str = "3/10",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._env = env
        self._default = default
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit; return (allowed, retry_after_seconds)."""
        limit, window = parse_rate_limit(os.environ.get(self._env, ""), self._default)
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


# Per-user burst guards (keyed on the verified sub), one per bucket, so a burst
# of generations doesn't block an Ask DIA question and vice versa.
generation_burst = RateLimiter("DIA_BURST_LIMIT", "3/10")
ask_burst = RateLimiter("DIA_BURST_LIMIT", "3/10")
utility_burst = RateLimiter("DIA_UTILITY_BURST_LIMIT", "10/10")
# Sign-in attempts: keyed on the salted IP hash (each may fetch Google certs).
auth_burst = RateLimiter("DIA_AUTH_RATE_LIMIT", "10/60")


def reset_limiters() -> None:
    for limiter in (generation_burst, ask_burst, utility_burst, auth_burst):
        limiter.reset()
