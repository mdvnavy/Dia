"""Lockdown v2 quota: 10 generations + 5 Ask DIA per signed-in user per ET day.

The server is the only source of truth: counts are keyed on the verified
Google ``sub``, charged atomically before the work, and refunded on a
server error. Nothing the client sends (cookies, headers, body fields,
local storage) can mint quota.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import agent_auth
import agent_quota
from agent_quota import (
    ASK,
    GENERATIONS,
    DailyQuota,
    FirestoreQuotaStore,
    InMemoryQuotaStore,
    QuotaConflict,
    QuotaUnavailable,
    limit_message,
    quota_day,
    user_key,
)
from lockdown_support import gate_env, server  # noqa: F401

UTC = dt.timezone.utc
QUESTIONNAIRE = {"questionnaire": "Company: Example Co\nProblem: intake takes too long"}


class Clock:
    def __init__(self, when: dt.datetime) -> None:
        self.when = when

    def __call__(self) -> dt.datetime:
        return self.when


# --- policy units -------------------------------------------------------------


def test_default_limits_are_10_generations_and_5_ask(gate_env):
    quota = DailyQuota(InMemoryQuotaStore())
    usage = quota.usage("sub-a")
    assert usage[GENERATIONS] == {"limit": 10, "used": 0, "remaining": 10}
    assert usage[ASK] == {"limit": 5, "used": 0, "remaining": 5}
    assert usage["timezone"] == "America/Toronto"


def test_buckets_are_independent(gate_env):
    quota = DailyQuota(InMemoryQuotaStore())
    assert all(quota.consume(ASK, "s").allowed for _ in range(5))
    assert not quota.consume(ASK, "s").allowed
    assert quota.consume(GENERATIONS, "s").allowed
    assert quota.peek(GENERATIONS, "s").remaining == 9


def test_limits_configurable_and_zero_means_none(gate_env, monkeypatch):
    monkeypatch.setenv("DIA_ASK_DAILY_LIMIT", "0")
    monkeypatch.setenv("DIA_GENERATION_DAILY_LIMIT", "junk")
    quota = DailyQuota(InMemoryQuotaStore())
    assert not quota.consume(ASK, "s").allowed
    assert quota.peek(GENERATIONS, "s").limit == 10


def test_midnight_et_boundary(gate_env):
    # 23:59:59 EDT on Oct 23 is 03:59:59Z on Oct 24.
    clock = Clock(dt.datetime(2026, 10, 24, 3, 59, 59, tzinfo=UTC))
    quota = DailyQuota(InMemoryQuotaStore(), clock=clock)
    for _ in range(5):
        assert quota.consume(ASK, "s").allowed
    result = quota.consume(ASK, "s")
    assert not result.allowed
    assert result.reset_at == dt.datetime(2026, 10, 24, 4, 0, tzinfo=UTC)
    assert result.retry_after(clock()) == 2
    clock.when = dt.datetime(2026, 10, 24, 4, 0, 0, tzinfo=UTC)  # midnight ET
    assert quota.consume(ASK, "s").allowed
    assert quota.peek(ASK, "s").remaining == 4


def test_midnight_is_toronto_not_utc_and_handles_dst(gate_env):
    # UTC midnight is 8 pm ET: still the same quota day.
    assert quota_day(dt.datetime(2026, 10, 23, 23, 59, tzinfo=UTC))[0] == "2026-10-23"
    assert quota_day(dt.datetime(2026, 10, 24, 0, 1, tzinfo=UTC))[0] == "2026-10-23"
    # Nov 1 2026: DST ends; next midnight is 05:00Z (EST).
    day, reset = quota_day(dt.datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
    assert day == "2026-11-01" and reset.astimezone(UTC) == dt.datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


def test_user_key_hashes_sub(gate_env):
    key = user_key("1234567890")
    assert key.startswith("sub-") and "1234567890" not in key
    assert key == user_key("1234567890") != user_key("1234567891")


def test_limit_messages_are_calm():
    assert limit_message(GENERATIONS, 10) == "You've used today's 10 generations. More at midnight ET."
    assert limit_message(ASK, 5) == "You've used today's 5 Ask DIA questions. More at midnight ET."
    for text in (limit_message(GENERATIONS, 10), limit_message(ASK, 5)):
        lowered = text.lower()
        assert not any(w in lowered for w in ("$", "price", "upgrade", "pro", "hurry", "now", "!"))


def test_refund_never_goes_negative(gate_env):
    quota = DailyQuota(InMemoryQuotaStore())
    quota.refund(ASK, "s")
    assert quota.peek(ASK, "s").used == 0


def test_in_memory_store_is_atomic_under_threads(gate_env):
    quota = DailyQuota(InMemoryQuotaStore())
    with ThreadPoolExecutor(50) as pool:
        results = list(pool.map(lambda _: quota.consume(GENERATIONS, "s").allowed, range(200)))
    assert results.count(True) == 10


def test_ask_fails_closed_generations_fail_soft(gate_env):
    class Down:
        def try_consume(self, *a):
            raise ConnectionError("down")

        refund = get = try_consume

    quota = DailyQuota(Down(), store_name="firestore")
    with pytest.raises(QuotaUnavailable):
        quota.consume(ASK, "s")
    assert quota.consume(GENERATIONS, "s").allowed  # per-instance fallback
    assert quota.peek(GENERATIONS, "s").used == 1


# --- Firestore REST transport (fake) --------------------------------------------


class Resp:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeFirestore:
    """Optimistic-concurrency fake of the Firestore REST transaction API.

    A commit fails with 409 ABORTED if the document changed after the
    transaction read it, which is how real read-write transactions behave.
    """

    def __init__(self, forced_conflicts: int = 0) -> None:
        self.docs: dict[str, dict] = {}
        self.versions: dict[str, int] = {}
        self.txns: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.calls: list[tuple] = []
        self.forced_conflicts = forced_conflicts
        self.n = 0

    def _name(self, url: str) -> str:
        return url.split("/v1/", 1)[1]

    def post(self, url, json=None, timeout=None):
        with self.lock:
            op = url.rsplit(":", 1)[1]
            self.calls.append((op, json))
            if op == "beginTransaction":
                assert json == {"options": {"readWrite": {}}}
                self.n += 1
                txn = f"t{self.n}"
                self.txns[txn] = {}
                return Resp(200, {"transaction": txn})
            if op == "rollback":
                self.txns.pop(json["transaction"], None)
                return Resp(200)
            assert op == "commit"
            txn = json.get("transaction")
            reads = self.txns.pop(txn, {}) if txn else {}
            if txn and self.forced_conflicts:
                self.forced_conflicts -= 1
                return Resp(409, {"error": {"status": "ABORTED"}})
            if any(self.versions.get(name, 0) != v for name, v in reads.items()):
                return Resp(409, {"error": {"status": "ABORTED"}})
            for write in json["writes"]:
                if "update" in write:
                    name = write["update"]["name"]
                    self.docs[name] = {"name": name, "fields": write["update"]["fields"]}
                else:
                    name = write["transform"]["document"]
                    if write.get("currentDocument") == {"exists": True} and name not in self.docs:
                        return Resp(404)
                    field = self.docs[name]["fields"]["count"]
                    delta = int(write["transform"]["fieldTransforms"][0]["increment"]["integerValue"])
                    field["integerValue"] = str(int(field["integerValue"]) + delta)
                self.versions[name] = self.versions.get(name, 0) + 1
            return Resp(200, {"commitTime": "2026-10-23T00:00:00Z"})

    def get(self, url, params=None, timeout=None):
        with self.lock:
            name = self._name(url)
            self.calls.append(("get", params))
            txn = (params or {}).get("transaction")
            if txn:
                self.txns.setdefault(txn, {})[name] = self.versions.get(name, 0)
            if name not in self.docs:
                return Resp(404)
            return Resp(200, self.docs[name])

    def count(self) -> int:
        return sum(int(d["fields"]["count"]["integerValue"]) for d in self.docs.values())


def firestore(fake, **kw):
    return FirestoreQuotaStore(
        project="demo-project", database="dia-quota", collection="dia_agent_quota",
        session_factory=lambda: fake, sleep=lambda s: None, **kw,
    )


def test_firestore_doc_name_and_fields(gate_env):
    fake = FakeFirestore()
    quota = DailyQuota(firestore(fake), clock=Clock(dt.datetime(2026, 10, 23, 16, 0, tzinfo=UTC)))
    assert quota.consume(ASK, "s").allowed
    (name, doc), = fake.docs.items()
    assert name.startswith("projects/demo-project/databases/dia-quota/documents/dia_agent_quota/2026-10-23_ask-sub-")
    assert doc["fields"]["count"] == {"integerValue": "1"}
    assert doc["fields"]["bucket"] == {"stringValue": "ask"}
    # TTL field: a day after the reset, in UTC.
    assert doc["fields"]["expire_at"] == {"timestampValue": "2026-10-25T04:00:00Z"}


def test_firestore_env_selects_project_and_database(gate_env, monkeypatch):
    monkeypatch.setenv("DIA_QUOTA_STORE", "firestore")
    monkeypatch.setenv("DIA_FIRESTORE_PROJECT", "demo-project")
    monkeypatch.setenv("DIA_FIRESTORE_DATABASE", "dia-quota")
    store, name = agent_quota.store_from_env()
    assert name == "firestore"
    assert store.doc_name("ask", "k", "2026-10-23").startswith("projects/demo-project/databases/dia-quota/")


def test_firestore_at_limit_rolls_back(gate_env, monkeypatch):
    monkeypatch.setenv("DIA_ASK_DAILY_LIMIT", "2")
    fake = FakeFirestore()
    quota = DailyQuota(firestore(fake))
    assert [quota.consume(ASK, "s").allowed for _ in range(3)] == [True, True, False]
    assert [c[0] for c in fake.calls][-3:] == ["beginTransaction", "get", "rollback"]


def test_firestore_conflict_is_retried(gate_env):
    fake = FakeFirestore(forced_conflicts=3)
    quota = DailyQuota(firestore(fake))
    assert quota.consume(GENERATIONS, "s").allowed
    assert fake.count() == 1
    assert [c[0] for c in fake.calls].count("commit") == 4


def test_firestore_persistent_conflict_raises_then_fails_closed(gate_env):
    fake = FakeFirestore(forced_conflicts=99)
    store = firestore(fake)
    with pytest.raises(QuotaConflict):
        store.try_consume(ASK, "k", "2026-10-23", 5, dt.datetime(2026, 10, 25, tzinfo=UTC))
    with pytest.raises(QuotaUnavailable):
        DailyQuota(store).consume(ASK, "s")
    assert fake.count() == 0


def test_firestore_parallel_never_overspends(gate_env):
    fake = FakeFirestore()
    store = firestore(fake)
    store.MAX_ATTEMPTS = 200
    quota = DailyQuota(store)
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(lambda _: quota.consume(ASK, "s").allowed, range(20)))
    assert results.count(True) == 5
    assert fake.count() == 5


def test_firestore_refund_is_atomic_increment(gate_env):
    fake = FakeFirestore()
    quota = DailyQuota(firestore(fake))
    quota.consume(ASK, "s")
    quota.refund(ASK, "s")
    assert fake.count() == 0
    _, body = fake.calls[-1]
    assert body["writes"][0]["currentDocument"] == {"exists": True}
    assert "transaction" not in body


# --- HTTP: anti-spoofing -----------------------------------------------------------


def usage_of(server, cookie):
    status, _, payload = server.request("GET", "/api/usage", cookie=cookie)
    assert status == 200
    return payload


def test_usage_endpoint_for_free_user_and_owner(server):
    cookie, _ = server.sign_in()
    usage = usage_of(server, cookie)
    assert usage["signed_in"] and not usage["owner"] and not usage["unlimited"]
    assert usage["generations"]["remaining"] == 10 and usage["ask"]["remaining"] == 5
    assert usage["resets_at"] and usage["timezone"] == "America/Toronto"
    owner, _ = server.sign_in("owner-cred")
    assert usage_of(server, owner) == {"signed_in": True, "owner": True, "unlimited": True}


def test_process_and_agent_return_usage_and_charge(server):
    cookie, _ = server.sign_in()
    status, _, payload = server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    assert status == 200 and payload["usage"]["generations"]["remaining"] == 9
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 200 and payload["usage"]["ask"]["remaining"] == 4


def test_pdf_from_questionnaire_charges_but_result_export_does_not(server):
    cookie, _ = server.sign_in()
    _, _, run = server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    status, hdrs, _ = server.request("POST", "/api/pdf", {"result": run}, cookie=cookie)
    assert status == 200 and hdrs["Content-Type"] == "application/pdf"
    assert usage_of(server, cookie)["generations"]["used"] == 1
    status, _, _ = server.request("POST", "/api/pdf", QUESTIONNAIRE, cookie=cookie)
    assert status == 200
    assert usage_of(server, cookie)["generations"]["used"] == 2


def test_extract_and_sample_are_not_charged(server):
    cookie, _ = server.sign_in()
    server.request("GET", "/api/sample", cookie=cookie)
    server.request("POST", "/api/extract", raw=b"Company: X", headers={"X-Filename": "a.txt"}, cookie=cookie)
    usage = usage_of(server, cookie)
    assert usage["generations"]["used"] == 0 and usage["ask"]["used"] == 0


def test_empty_questionnaire_is_not_charged(server):
    cookie, _ = server.sign_in()
    status, _, _ = server.request("POST", "/api/process", {"questionnaire": "  "}, cookie=cookie)
    assert status == 400
    assert usage_of(server, cookie)["generations"]["used"] == 0


def test_cap_returns_calm_429_with_numbers(server, gate_env):
    cookie, _ = server.sign_in()
    for _ in range(5):
        assert server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)[0] == 200
    status, hdrs, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 429
    assert payload["error"] == "daily_limit" and payload["bucket"] == "ask"
    assert payload["message"] == "You've used today's 5 Ask DIA questions. More at midnight ET."
    assert payload["usage"]["ask"] == {"limit": 5, "used": 5, "remaining": 0}
    assert int(hdrs["Retry-After"]) > 0
    assert len(gate_env) == 5  # the 6th never reached the model
    for _ in range(10):
        server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    status, _, payload = server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    assert status == 429 and payload["bucket"] == "generations"
    assert payload["message"] == "You've used today's 10 generations. More at midnight ET."


def test_new_cookie_jar_or_cleared_storage_does_not_reset(server):
    cookie, _ = server.sign_in()
    for _ in range(5):
        server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    # Sign out, clear everything, replay the same raw Google token: a fresh
    # session cookie, same sub, same count.
    server.request("POST", "/api/auth/logout", cookie=cookie)
    fresh, _ = server.sign_in()
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=fresh)
    assert status == 429
    # Many replays of the token mint no extra quota.
    for _ in range(5):
        replay, _ = server.sign_in()
        assert server.request("POST", "/api/agent", {"message": "hi"}, cookie=replay)[0] == 429


def test_forged_counters_fields_and_headers_are_ignored(server, gate_env):
    cookie, _ = server.sign_in()
    for _ in range(5):
        server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    forged = {
        "message": "hi", "usage": {"ask": {"remaining": 5}}, "remaining": 99, "used": 0,
        "owner": True, "email": "owner@example.com", "unlimited": True, "sub": "sub-other",
    }
    headers = {
        "X-DIA-Remaining": "5", "X-Owner-Email": "owner@example.com", "X-Email": "owner@example.com",
        "X-Goog-Authenticated-User-Email": "accounts.google.com:owner@example.com",
        "Authorization": "Bearer owner-cred",
    }
    jar = f"{cookie}; dia_vid=fresh; dia_usage=0; owner=1; email=owner@example.com"
    status, _, payload = server.request("POST", "/api/agent", forged, headers=headers, cookie=jar)
    assert status == 429 and payload["usage"]["owner"] is False
    assert len(gate_env) == 5


def test_fake_owner_email_at_sign_in_does_not_make_owner(server):
    status, hdrs, payload = server.request(
        "POST", "/api/auth/session",
        {"credential": "visitor-cred", "email": "owner@example.com"},
        headers={"X-Owner-Email": "owner@example.com"},
    )
    assert status == 200 and payload["owner"] is False
    cookie = hdrs["Set-Cookie"].split(";", 1)[0]
    assert usage_of(server, cookie)["owner"] is False


def test_unverified_owner_email_cannot_sign_in(server):
    status, hdrs, payload = server.request("POST", "/api/auth/session", {"credential": "unverified-owner-cred"})
    assert status == 401 and "Set-Cookie" not in hdrs


@pytest.mark.parametrize("cred", ["expired-cred", "wrong-aud-cred", "wrong-iss-cred", "unsigned-garbage"])
def test_bad_tokens_get_no_session(server, cred):
    status, hdrs, _ = server.request("POST", "/api/auth/session", {"credential": cred})
    assert status == 401 and "Set-Cookie" not in hdrs


def test_raw_token_is_not_a_session(server):
    for header in ({"Authorization": "Bearer visitor-cred"}, {}):
        status, _, _ = server.request("GET", "/api/usage", headers=header, cookie="dia_session=visitor-cred")
        assert status == 401


@pytest.mark.parametrize("cred", ["owner-cred", "owner2-cred", "owner3-cred"])
def test_every_listed_owner_is_uncapped(server, gate_env, cred):
    cookie, payload = server.sign_in(cred)
    assert payload["owner"] is True
    for _ in range(12):
        status, _, body = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
        assert status == 200 and body["usage"]["unlimited"] is True
    for _ in range(12):
        assert server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0] == 200


def test_spoofed_xff_first_hop_does_not_matter(server, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "dia-test")
    cookie, _ = server.sign_in()
    for i in range(5):
        server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie,
                       headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.5"})
    status, _, _ = server.request(
        "POST", "/api/agent", {"message": "hi"}, cookie=cookie,
        headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 203.0.113.99"},
    )
    assert status == 429  # keyed on sub, never on an IP


def test_twenty_parallel_at_four_remaining_exactly_four_succeed(server, gate_env):
    cookie, _ = server.sign_in()
    for _ in range(6):
        assert server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0] == 200
    assert usage_of(server, cookie)["generations"]["remaining"] == 4
    barrier = threading.Barrier(20)

    def go(_):
        barrier.wait()
        return server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0]

    with ThreadPoolExecutor(20) as pool:
        statuses = list(pool.map(go, range(20)))
    assert statuses.count(200) == 4 and statuses.count(429) == 16
    assert usage_of(server, cookie)["generations"]["remaining"] == 0


def test_twenty_parallel_ask_with_four_left(server, gate_env):
    cookie, _ = server.sign_in()
    server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    barrier = threading.Barrier(20)

    def go(_):
        barrier.wait()
        return server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)[0]

    with ThreadPoolExecutor(20) as pool:
        statuses = list(pool.map(go, range(20)))
    assert statuses.count(200) == 4
    assert len(gate_env) == 5


def test_charge_happens_before_the_model_and_refunds_on_error(server, monkeypatch):
    import agent_runtime

    cookie, _ = server.sign_in()
    seen = []

    def failing(message):
        seen.append(usage_of(server, cookie)["ask"]["used"])
        raise RuntimeError("model down")

    monkeypatch.setattr(agent_runtime, "run_agent", failing)
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 502 and seen == [1]  # charged before the call
    assert payload["usage"]["ask"]["used"] == 0  # refunded


def test_generation_refunds_on_server_error(server, monkeypatch):
    import app

    cookie, _ = server.sign_in()

    def boom(_):
        raise ValueError("bad questionnaire")

    monkeypatch.setattr(app, "build_intake_response", boom)
    status, _, _ = server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)
    assert status == 400
    assert usage_of(server, cookie)["generations"]["used"] == 0


def test_unconfigured_agent_is_not_charged(server, monkeypatch):
    import agent_runtime

    monkeypatch.setattr(agent_runtime, "is_configured", lambda: False)
    cookie, _ = server.sign_in()
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 200 and payload["configured"] is False
    assert usage_of(server, cookie)["ask"]["used"] == 0


def test_burst_limit_three_per_ten_seconds(server, monkeypatch):
    monkeypatch.setenv("DIA_BURST_LIMIT", "3/10")
    agent_auth.reset_limiters()
    cookie, _ = server.sign_in()
    statuses = [server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)[0] for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert payload["error"] == "burst_limited"
    # Burst refusals don't spend quota; another user is unaffected.
    assert usage_of(server, cookie)["ask"]["used"] == 3
    other, _ = server.sign_in("other-cred")
    assert server.request("POST", "/api/agent", {"message": "hi"}, cookie=other)[0] == 200
    owner, _ = server.sign_in("owner-cred")
    assert all(
        server.request("POST", "/api/agent", {"message": "hi"}, cookie=owner)[0] == 200 for _ in range(6)
    )


def test_ask_store_outage_fails_closed_over_http(server):
    class Down:
        def try_consume(self, *a):
            raise ConnectionError("down")

        refund = get = try_consume

    agent_quota.set_quota(DailyQuota(Down(), store_name="firestore"))
    cookie, _ = server.sign_in()
    status, hdrs, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
    assert status == 503 and payload["error"] == "quota_unavailable"
    assert server.request("POST", "/api/process", QUESTIONNAIRE, cookie=cookie)[0] == 200


def test_kill_switch_off_blocks_owner_and_spends_nothing(server, monkeypatch, gate_env):
    owner, _ = server.sign_in("owner-cred")
    visitor, _ = server.sign_in()
    monkeypatch.setenv("ASK_DIA_ENABLED", "off")
    for cookie in (owner, visitor, None):
        status, _, payload = server.request("POST", "/api/agent", {"message": "hi"}, cookie=cookie)
        assert status == 503 and payload["enabled"] is False
    assert gate_env == []
    assert usage_of(server, visitor)["ask"]["used"] == 0


def test_refund_hits_the_day_that_was_charged(gate_env):
    """Charged 23:59 ET, refunded after midnight: the refund undoes the old day's charge, not a new-day one."""
    now = [dt.datetime(2026, 10, 24, 3, 59, tzinfo=dt.timezone.utc)]  # 23:59 ET
    quota = DailyQuota(InMemoryQuotaStore(), clock=lambda: now[0])
    charge = quota.consume(ASK, "s")
    now[0] = dt.datetime(2026, 10, 24, 4, 1, tzinfo=dt.timezone.utc)  # 00:01 ET, next quota day
    other = quota.consume(ASK, "s")  # a concurrent success on the new day
    quota.refund(ASK, "s", charge)
    assert quota.peek(ASK, "s").used == 1  # the new day's success keeps its charge
    assert other.allowed and charge.day != other.day


def test_refund_goes_to_the_store_that_took_the_charge(gate_env):
    class Flaky(InMemoryQuotaStore):
        down = False

        def try_consume(self, *a):
            if self.down:
                raise ConnectionError("down")
            return super().try_consume(*a)

    shared = Flaky()
    quota = DailyQuota(shared, store_name="firestore")
    first = quota.consume(GENERATIONS, "s")
    shared.down = True
    fallback_charge = quota.consume(GENERATIONS, "s")  # lands in the per-instance fallback
    shared.down = False
    assert (first.backend, fallback_charge.backend) == ("shared", "fallback")
    quota.refund(GENERATIONS, "s", fallback_charge)
    day = fallback_charge.day
    assert shared.get(GENERATIONS, user_key("s"), day) == 1  # the earlier success is untouched
    assert quota.fallback.get(GENERATIONS, user_key("s"), day) == 0


def test_refund_of_a_refused_charge_does_nothing(gate_env):
    monkey_quota = DailyQuota(InMemoryQuotaStore())
    for _ in range(5):
        monkey_quota.consume(ASK, "s")
    refused = monkey_quota.consume(ASK, "s")
    assert not refused.allowed
    monkey_quota.refund(ASK, "s", refused)
    assert monkey_quota.peek(ASK, "s").used == 5
