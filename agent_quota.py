"""Daily per-user quotas: generations and Ask DIA (lockdown v2).

Policy (Ikenna, 2026-10-23): every signed-in free user gets, per calendar day
in **America/Toronto** (reset at local midnight):

* ``generations``: ``DIA_GENERATION_DAILY_LIMIT`` (default 10), charged by
  ``POST /api/process`` and by ``POST /api/pdf`` when it re-runs the pipeline
  from a questionnaire;
* ``ask``: ``DIA_ASK_DAILY_LIMIT`` (default 5), charged by ``POST /api/agent``.

Owners (``OWNER_EMAILS``) are uncapped. Signed-out visitors can't reach any
charged route (they get ``401 sign_in_required``), so there is no signed-out
bucket. Users are keyed on an HMAC (``DIA_VISITOR_SALT``) of the verified
Google ``sub``, so raw identifiers are never stored.

Anti-spoofing
    The server is the only source of truth. ``try_consume`` checks and charges
    in one atomic step *before* the work runs (a lock in memory; a Firestore
    read-write transaction, retried on contention), so parallel requests can
    never overspend. A server error after charging refunds the unit.

Store
    ``DIA_QUOTA_STORE=firestore`` uses Firestore Native mode over REST + ADC
    (``DIA_FIRESTORE_PROJECT``, ``DIA_FIRESTORE_DATABASE``, collection
    ``DIA_FIRESTORE_COLLECTION`` = ``dia_agent_quota``). Each document
    ``{day}_{bucket}-{key}`` holds ``count``, ``day``, ``bucket`` and
    ``expire_at`` (put the TTL policy on ``expire_at``). Anything else uses the
    in-memory store, which is **per instance and lost on restart**.

Store failure
    ``ask`` fails closed (no model call if the count can't be recorded).
    ``generations`` (deterministic, no model) fail soft to the in-memory store,
    so it stays capped per instance and an outage never locks people out.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

QUOTA_TZ_NAME = "America/Toronto"
FIRESTORE_SCOPE = "https://www.googleapis.com/auth/datastore"
DEFAULT_COLLECTION = "dia_agent_quota"
FIRESTORE_API = "https://firestore.googleapis.com/v1"

GENERATIONS = "generations"
ASK = "ask"
BUCKETS = {
    GENERATIONS: ("DIA_GENERATION_DAILY_LIMIT", 10),
    ASK: ("DIA_ASK_DAILY_LIMIT", 5),
}
# Buckets that must not fall back to per-instance memory when the shared
# store is down (each unit costs a model call).
FAIL_CLOSED = frozenset({ASK})


def quota_tz() -> dt.tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(QUOTA_TZ_NAME)
    except Exception:  # noqa: BLE001 - no tz database in the image
        logger.warning("tz database unavailable; using fixed UTC-5 for the quota day")
        return dt.timezone(dt.timedelta(hours=-5), "EST")


def daily_limit(bucket: str) -> int:
    env, default = BUCKETS[bucket]
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("invalid %s; using %d", env, default)
        return default


def quota_day(now: dt.datetime) -> tuple[str, dt.datetime]:
    """Return (day key 'YYYY-MM-DD' in Toronto, next Toronto midnight, aware)."""
    tz = quota_tz()
    local = now.astimezone(tz)
    day = local.date()
    tomorrow = day + dt.timedelta(days=1)
    reset = dt.datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)
    return day.isoformat(), reset


def user_key(sub: str) -> str:
    salt = (os.environ.get("DIA_VISITOR_SALT", "") or "dia-visitor-v1").encode("utf-8")
    return "sub-" + hmac.new(salt, f"sub:{sub}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# --- stores -----------------------------------------------------------------


class QuotaStore(Protocol):
    def try_consume(
        self, bucket: str, key: str, day: str, limit: int, expire_at: dt.datetime
    ) -> tuple[bool, int]:
        """Atomically: if count < limit, add 1. Return (charged, count after)."""

    def refund(self, bucket: str, key: str, day: str) -> None:
        """Give one unit back (never below zero)."""

    def get(self, bucket: str, key: str, day: str) -> int:
        """Current count (0 when absent) without changing it."""


class InMemoryQuotaStore:
    """Process-local counters guarded by one lock. Per instance, lost on restart."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, str], int] = {}
        self._lock = threading.Lock()

    def try_consume(self, bucket, key, day, limit, expire_at):
        with self._lock:
            for stale in [k for k in self._counts if k[0] != day]:
                del self._counts[stale]
            current = self._counts.get((day, bucket, key), 0)
            if current >= limit:
                return False, current
            self._counts[(day, bucket, key)] = current + 1
            return True, current + 1

    def refund(self, bucket, key, day):
        with self._lock:
            current = self._counts.get((day, bucket, key), 0)
            if current > 0:
                self._counts[(day, bucket, key)] = current - 1

    def get(self, bucket, key, day):
        with self._lock:
            return self._counts.get((day, bucket, key), 0)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


class QuotaConflict(RuntimeError):
    """Firestore kept aborting the transaction (heavy contention)."""


class FirestoreQuotaStore:
    """Counters in Firestore Native mode via its REST API (no extra dependency).

    ``try_consume`` runs a read-write transaction: ``beginTransaction``, read
    the document inside it, and either ``rollback`` (at the limit) or
    ``commit`` ``count + 1``. Firestore aborts a commit whose read was
    invalidated by a concurrent writer (HTTP 409 ``ABORTED``); that attempt is
    retried with jittered backoff, so concurrent requests serialise and the
    limit can't be overspent.
    """

    MAX_ATTEMPTS = 6

    def __init__(
        self,
        project: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        session_factory: Callable[[], object] | None = None,
        timeout: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
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
        self._sleep = sleep
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

    def doc_name(self, bucket: str, key: str, day: str) -> str:
        return f"{self._root()}/{self._collection}/{day}_{bucket}-{key.replace(':', '-')}"

    @staticmethod
    def _count(doc: dict) -> int:
        value = (doc.get("fields") or {}).get("count") or {}
        return int(value.get("integerValue", 0))

    def try_consume(self, bucket, key, day, limit, expire_at):
        session = self._client()
        name = self.doc_name(bucket, key, day)
        root = f"{FIRESTORE_API}/{self._root()}"
        expire = expire_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for attempt in range(self.MAX_ATTEMPTS):
            begin = session.post(
                f"{root}:beginTransaction",
                json={"options": {"readWrite": {}}},
                timeout=self._timeout,
            )
            begin.raise_for_status()
            txn = begin.json()["transaction"]
            read = session.get(
                f"{FIRESTORE_API}/{name}", params={"transaction": txn}, timeout=self._timeout
            )
            if read.status_code == 404:
                current = 0
            else:
                read.raise_for_status()
                current = self._count(read.json())
            if current >= limit:
                session.post(f"{root}:rollback", json={"transaction": txn}, timeout=self._timeout)
                return False, current
            commit = session.post(
                f"{root}:commit",
                json={
                    "transaction": txn,
                    "writes": [
                        {
                            "update": {
                                "name": name,
                                "fields": {
                                    "count": {"integerValue": str(current + 1)},
                                    "day": {"stringValue": day},
                                    "bucket": {"stringValue": bucket},
                                    "expire_at": {"timestampValue": expire},
                                },
                            }
                        }
                    ],
                },
                timeout=self._timeout,
            )
            if commit.status_code == 409:  # ABORTED: contention, retry
                self._sleep(min(0.5, 0.02 * (2**attempt)) * (0.5 + random.random()))
                continue
            commit.raise_for_status()
            return True, current + 1
        raise QuotaConflict("Firestore quota transaction kept aborting")

    def refund(self, bucket, key, day):
        """Atomic server-side ``increment -1``, only if the document exists."""
        session = self._client()
        response = session.post(
            f"{FIRESTORE_API}/{self._root()}:commit",
            json={
                "writes": [
                    {
                        "transform": {
                            "document": self.doc_name(bucket, key, day),
                            "fieldTransforms": [
                                {"fieldPath": "count", "increment": {"integerValue": "-1"}}
                            ],
                        },
                        "currentDocument": {"exists": True},
                    }
                ]
            },
            timeout=self._timeout,
        )
        response.raise_for_status()

    def get(self, bucket, key, day):
        session = self._client()
        response = session.get(
            f"{FIRESTORE_API}/{self.doc_name(bucket, key, day)}",
            params={"mask.fieldPaths": "count"},
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return self._count(response.json())


def store_from_env() -> tuple[QuotaStore | None, str]:
    kind = os.environ.get("DIA_QUOTA_STORE", "").strip().lower()
    if kind == "firestore":
        return FirestoreQuotaStore(), "firestore"
    if kind not in {"", "memory"}:
        logger.warning("unknown DIA_QUOTA_STORE %r; using in-memory quota", kind)
    return None, "memory"


# --- policy -----------------------------------------------------------------


class QuotaUnavailable(RuntimeError):
    """The shared store failed for a fail-closed bucket."""


@dataclass(frozen=True)
class BucketState:
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def as_json(self) -> dict[str, int]:
        return {"limit": self.limit, "used": min(self.used, self.limit), "remaining": self.remaining}


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    bucket: str
    state: BucketState
    reset_at: dt.datetime
    # Where the charge landed, so a refund undoes exactly that charge: the quota day it was charged to (not the
    # day at refund time, which may be after midnight ET) and the store that took it ("shared" or "fallback").
    day: str | None = None
    backend: str | None = None

    def retry_after(self, now: dt.datetime) -> int:
        return max(1, int((self.reset_at - now).total_seconds()) + 1)


def limit_message(bucket: str, limit: int) -> str:
    """Calm, no price, no urgency (Glyph lockdown v2)."""
    if bucket == ASK:
        noun = "Ask DIA question" if limit == 1 else "Ask DIA questions"
    else:
        noun = "generation" if limit == 1 else "generations"
    return f"You've used today's {limit} {noun}. More at midnight ET."


class DailyQuota:
    """Charge users against per-day bucket limits using a shared store."""

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

    def now(self) -> dt.datetime:
        return self._clock()

    def _call_with_backend(self, bucket: str, method: str, *args):
        if self.store is not None:
            try:
                return getattr(self.store, method)(*args), "shared"
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "quota store %s failed (%s)", method, type(error).__name__
                )
                if bucket in FAIL_CLOSED and method == "try_consume":
                    raise QuotaUnavailable(str(type(error).__name__)) from None
        return getattr(self.fallback, method)(*args), "fallback"

    def _call(self, bucket: str, method: str, *args):
        return self._call_with_backend(bucket, method, *args)[0]

    def consume(self, bucket: str, sub: str) -> QuotaResult:
        limit = daily_limit(bucket)
        day, reset_at = quota_day(self.now())
        if limit == 0:
            return QuotaResult(False, bucket, BucketState(0, 0), reset_at)
        (charged, used), backend = self._call_with_backend(
            bucket, "try_consume", bucket, user_key(sub), day, limit, reset_at + dt.timedelta(days=1)
        )
        return QuotaResult(charged, bucket, BucketState(limit, used), reset_at, day=day, backend=backend)

    def refund(self, bucket: str, sub: str, charge: QuotaResult | None = None) -> None:
        """Undo one charge. Pass the consume() result so the refund hits the charged day and store."""
        if charge is not None and not charge.allowed:
            return
        day = charge.day if charge is not None and charge.day else quota_day(self.now())[0]
        try:
            if charge is not None and charge.backend == "fallback":
                self.fallback.refund(bucket, user_key(sub), day)
            elif charge is not None and charge.backend == "shared" and self.store is not None:
                self.store.refund(bucket, user_key(sub), day)  # never onto the fallback: that charge wasn't there
            else:
                self._call(bucket, "refund", bucket, user_key(sub), day)
        except Exception as error:  # noqa: BLE001 - a failed refund only costs the user
            logger.warning("quota refund failed (%s)", type(error).__name__)

    def peek(self, bucket: str, sub: str) -> BucketState:
        day, _ = quota_day(self.now())
        try:
            used = self._call(bucket, "get", bucket, user_key(sub), day)
        except Exception:  # noqa: BLE001
            used = self.fallback.get(bucket, user_key(sub), day)
        return BucketState(daily_limit(bucket), used)

    def usage(self, sub: str) -> dict[str, object]:
        _, reset_at = quota_day(self.now())
        return {
            GENERATIONS: self.peek(GENERATIONS, sub).as_json(),
            ASK: self.peek(ASK, sub).as_json(),
            "resets_at": reset_at.isoformat(),
            "timezone": QUOTA_TZ_NAME,
        }

    def reset(self) -> None:
        self.fallback.reset()
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
