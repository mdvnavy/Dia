"""Daily per-visitor question quota for the live agent (``/api/agent``).

Policy (interim, until a proper sign-in exists): anonymous visitors may ask
``DIA_AGENT_DAILY_LIMIT`` questions (default 10) per **calendar day in
America/New_York**; the owner (see ``agent_auth.is_owner``) is unlimited.

Visitor identity
    Every request is charged to two buckets and is allowed only if *both* are
    within the limit:

    * ``ip:``  the client IP (right-most ``X-Forwarded-For`` hop on Cloud Run;
      IPv6 collapsed to its /64, since one subscriber usually owns a whole /64);
    * ``vid:`` an optional random visitor cookie (``dia_vid``).

    Clearing cookies therefore doesn't reset the cap (the IP bucket is still
    full), and hopping networks with the same browser doesn't either (the
    cookie bucket is). Only changing both at once gets a fresh allowance.
    Keys are HMAC-SHA256 hashes (salted with ``DIA_VISITOR_SALT``), so raw IPs
    are never stored.

Store
    ``QuotaStore`` is a tiny interface (``incr``/``get``). ``DIA_QUOTA_STORE=
    firestore`` uses Firestore (REST + ADC, one atomic ``commit`` per request,
    documents carry ``expire_at`` for a TTL policy). Anything else uses the
    in-memory store, which is **per instance and lost on restart**: on Cloud
    Run a visitor can get up to ``limit x instances`` per day, plus a fresh
    allowance whenever an instance is recycled.

Store failure
    If Firestore errors, the request is counted in the in-memory store instead
    (fail-soft: still capped per instance, never unlimited, never locking
    visitors out because of an outage). See README "Live agent access".
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

logger = logging.getLogger(__name__)

DEFAULT_DAILY_LIMIT = 10
QUOTA_TZ_NAME = "America/New_York"
VISITOR_COOKIE = "dia_vid"
VISITOR_COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetime at 400 days
_VISITOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
DEFAULT_COLLECTION = "dia_agent_quota"


def quota_tz() -> dt.tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(QUOTA_TZ_NAME)
    except Exception:  # noqa: BLE001 - no tz database in the image
        logger.warning("tz database unavailable; using fixed UTC-5 for the quota day")
        return dt.timezone(dt.timedelta(hours=-5), "EST")


def daily_limit() -> int:
    raw = os.environ.get("DIA_AGENT_DAILY_LIMIT", "").strip()
    if not raw:
        return DEFAULT_DAILY_LIMIT
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("invalid DIA_AGENT_DAILY_LIMIT; using %d", DEFAULT_DAILY_LIMIT)
        return DEFAULT_DAILY_LIMIT


def quota_day(now: dt.datetime) -> tuple[str, dt.datetime]:
    """Return (day key 'YYYY-MM-DD', next local midnight as an aware datetime)."""
    local = now.astimezone(quota_tz())
    day = local.date()
    tomorrow = day + dt.timedelta(days=1)
    reset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=quota_tz())
    return day.isoformat(), reset


# --- visitor identity -------------------------------------------------------


def _salt() -> bytes:
    return (os.environ.get("DIA_VISITOR_SALT", "") or "dia-visitor-v1").encode("utf-8")


def _digest(kind: str, value: str) -> str:
    mac = hmac.new(_salt(), f"{kind}:{value}".encode("utf-8"), hashlib.sha256)
    return f"{kind}:{mac.hexdigest()[:32]}"


def normalise_ip(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip.strip())
    except ValueError:
        return ip.strip() or "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def valid_visitor_id(value: str | None) -> bool:
    return bool(value) and bool(_VISITOR_ID_RE.match(value or ""))


def new_visitor_id() -> str:
    return secrets.token_urlsafe(18)


def visitor_keys(ip: str, visitor_id: str | None) -> list[str]:
    keys = [_digest("ip", normalise_ip(ip))]
    if valid_visitor_id(visitor_id):
        keys.append(_digest("vid", str(visitor_id)))
    return keys


# --- stores -----------------------------------------------------------------


class QuotaStore(Protocol):
    def incr(self, keys: Sequence[str], day: str, expire_at: dt.datetime) -> list[int]:
        """Atomically add 1 to each (day, key) counter; return the new counts."""

    def get(self, keys: Sequence[str], day: str) -> list[int]:
        """Return the current counts (0 when absent) without changing them."""


class InMemoryQuotaStore:
    """Process-local counters. Per instance, lost on restart."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def incr(self, keys: Sequence[str], day: str, expire_at: dt.datetime) -> list[int]:
        with self._lock:
            stale = [k for k in self._counts if k[0] != day]
            for k in stale:
                del self._counts[k]
            result = []
            for key in keys:
                self._counts[(day, key)] = self._counts.get((day, key), 0) + 1
                result.append(self._counts[(day, key)])
            return result

    def get(self, keys: Sequence[str], day: str) -> list[int]:
        with self._lock:
            return [self._counts.get((day, key), 0) for key in keys]

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


class FirestoreQuotaStore:
    """Counters in Firestore via its REST API (no extra dependency).

    One document per (day, key): ``{collection}/{day}_{key}`` with ``count``,
    ``day`` and ``expire_at``. ``incr`` is a single ``documents:commit`` with an
    ``increment`` transform per document, which Firestore applies atomically
    and whose ``transformResults`` give the post-increment counts.
    """

    def __init__(
        self,
        project: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        session_factory: Callable[[], object] | None = None,
        timeout: float = 3.0,
    ) -> None:
        self._project = project or os.environ.get("DIA_FIRESTORE_PROJECT", "").strip()
        self._database = (
            database or os.environ.get("DIA_FIRESTORE_DATABASE", "").strip() or "(default)"
        )
        self._collection = (
            collection
            or os.environ.get("DIA_FIRESTORE_COLLECTION", "").strip()
            or DEFAULT_COLLECTION
        )
        self._session_factory = session_factory
        self._session = None
        self._timeout = timeout
        self._lock = threading.Lock()

    def _client(self):
        with self._lock:
            if self._session is None:
                if self._session_factory is not None:
                    self._session = self._session_factory()
                else:
                    import google.auth
                    from google.auth.transport.requests import AuthorizedSession

                    credentials, adc_project = google.auth.default(scopes=[FIRESTORE_SCOPE])
                    if not self._project:
                        self._project = (
                            os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
                            or adc_project
                            or ""
                        )
                    self._session = AuthorizedSession(credentials)
                if not self._project:
                    raise RuntimeError("no GCP project for the Firestore quota store")
            return self._session

    def _root(self) -> str:
        return f"projects/{self._project}/databases/{self._database}/documents"

    def _doc_name(self, key: str, day: str) -> str:
        return f"{self._root()}/{self._collection}/{day}_{key.replace(':', '-')}"

    def incr(self, keys: Sequence[str], day: str, expire_at: dt.datetime) -> list[int]:
        session = self._client()
        expire = expire_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        writes = [
            {
                "update": {
                    "name": self._doc_name(key, day),
                    "fields": {
                        "day": {"stringValue": day},
                        "expire_at": {"timestampValue": expire},
                    },
                },
                "updateMask": {"fieldPaths": ["day", "expire_at"]},
                "updateTransforms": [
                    {"fieldPath": "count", "increment": {"integerValue": "1"}}
                ],
            }
            for key in keys
        ]
        url = f"https://firestore.googleapis.com/v1/{self._root()}:commit"
        response = session.post(url, json={"writes": writes}, timeout=self._timeout)
        response.raise_for_status()
        results = response.json().get("writeResults", [])
        counts = []
        for result in results:
            value = (result.get("transformResults") or [{}])[0]
            counts.append(int(value.get("integerValue", 0)))
        if len(counts) != len(keys):
            raise RuntimeError("unexpected Firestore commit response")
        return counts

    def get(self, keys: Sequence[str], day: str) -> list[int]:
        session = self._client()
        names = [self._doc_name(key, day) for key in keys]
        url = f"https://firestore.googleapis.com/v1/{self._root()}:batchGet"
        response = session.post(
            url, json={"documents": names, "mask": {"fieldPaths": ["count"]}},
            timeout=self._timeout,
        )
        response.raise_for_status()
        found: dict[str, int] = {}
        for item in response.json():
            doc = item.get("found") if isinstance(item, dict) else None
            if doc:
                count = doc.get("fields", {}).get("count", {}).get("integerValue", 0)
                found[doc.get("name", "")] = int(count)
        return [found.get(name, 0) for name in names]


def store_from_env() -> tuple[QuotaStore | None, str]:
    kind = os.environ.get("DIA_QUOTA_STORE", "").strip().lower()
    if kind == "firestore":
        return FirestoreQuotaStore(), "firestore"
    if kind not in {"", "memory"}:
        logger.warning("unknown DIA_QUOTA_STORE %r; using in-memory quota", kind)
    return None, "memory"


# --- policy -----------------------------------------------------------------


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    limit: int
    used: int
    reset_at: dt.datetime

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def retry_after(self, now: dt.datetime) -> int:
        return max(1, int((self.reset_at - now).total_seconds()) + 1)

    def as_json(self) -> dict[str, object]:
        return {
            "limit": self.limit,
            "used": min(self.used, self.limit),
            "remaining": self.remaining,
            "resets_at": self.reset_at.isoformat(),
        }


def limit_message(limit: int) -> str:
    noun = "question" if limit == 1 else "questions"
    return f"You've used today's {limit} {noun}. Come back tomorrow!"


class DailyQuota:
    """Charge visitors against a per-day limit using a shared store.

    ``store`` None means in-memory only. When the shared store raises, the
    request is counted in the in-memory fallback store instead (fail-soft).
    Visitors already over the limit are remembered in memory for the rest of
    the day so repeat requests don't hit the shared store.
    """

    MAX_DENIED = 50_000

    def __init__(
        self,
        store: QuotaStore | None = None,
        *,
        clock: Callable[[], dt.datetime] | None = None,
        store_name: str = "memory",
    ) -> None:
        self.store = store
        self.store_name = store_name if store is not None else "memory"
        self.fallback = InMemoryQuotaStore()
        self._clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self._denied: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def now(self) -> dt.datetime:
        return self._clock()

    def _call(self, method: str, *args):
        if self.store is not None:
            try:
                return getattr(self.store, method)(*args)
            except Exception as error:  # noqa: BLE001 - fail soft to memory
                logger.warning(
                    "quota store %s failed (%s); using in-memory fallback",
                    method,
                    type(error).__name__,
                )
        return getattr(self.fallback, method)(*args)

    def consume(self, keys: Sequence[str]) -> QuotaResult:
        limit = daily_limit()
        now = self.now()
        day, reset_at = quota_day(now)
        if limit == 0:
            return QuotaResult(False, 0, 0, reset_at)
        with self._lock:
            if any((day, key) in self._denied for key in keys):
                return QuotaResult(False, limit, limit, reset_at)
        counts = self._call("incr", list(keys), day, reset_at + dt.timedelta(days=1))
        used = max(counts) if counts else 0
        allowed = used <= limit
        if not allowed:
            # Remember only the buckets that are actually over the limit.
            over = [key for key, count in zip(keys, counts) if count > limit]
            with self._lock:
                if len(self._denied) > self.MAX_DENIED:
                    self._denied = {d for d in self._denied if d[0] == day}
                self._denied.update((day, key) for key in over)
        return QuotaResult(allowed, limit, used, reset_at)

    def peek(self, keys: Sequence[str]) -> QuotaResult:
        limit = daily_limit()
        now = self.now()
        day, reset_at = quota_day(now)
        counts = self._call("get", list(keys), day)
        used = max(counts) if counts else 0
        return QuotaResult(used < limit, limit, used, reset_at)

    def reset(self) -> None:
        self.fallback.reset()
        with self._lock:
            self._denied.clear()
        if isinstance(self.store, InMemoryQuotaStore):
            self.store.reset()


_quota_lock = threading.Lock()
_quota: DailyQuota | None = None


def get_quota() -> DailyQuota:
    """Process-wide quota, built lazily from env on first use."""
    global _quota
    with _quota_lock:
        if _quota is None:
            store, name = store_from_env()
            _quota = DailyQuota(store, store_name=name)
        return _quota


def set_quota(quota: DailyQuota | None) -> None:
    """Replace the process-wide quota (tests)."""
    global _quota
    with _quota_lock:
        _quota = quota


def parse_cookie(header: str | None, name: str = VISITOR_COOKIE) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip() or None
    return None


def visitor_cookie_header(visitor_id: str, secure: bool) -> str:
    attributes = [
        f"{VISITOR_COOKIE}={visitor_id}",
        "Path=/",
        f"Max-Age={VISITOR_COOKIE_MAX_AGE}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)
