"""
API key pool: auth-failure classification, strike counting, burn expiry and
manual restore. Covers lab_notes/entropic_gap_livelock.md 48-49.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_key_pool.py

Exits non-zero on any failure. Safe to run against a live app: every check runs
against an in-memory FakeRedis, so it never reads or writes a real q:pool:* key.

Each check names the failure it exists to prevent. The one this file exists for:
a single 403 permanently destroying a valid API key, with delete-and-re-add as
the only recovery.
"""
import os, sys, time, fnmatch, ast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


import redis_pool as rp


class FakeRedis:
    """Enough of redis-py for the pool, with decode_responses=False semantics."""

    def __init__(self, store=None):
        # `store or {}` would silently un-share an EMPTY dict, which is exactly
        # what the cursor-persistence checks pass in.
        self.store = {} if store is None else store

    def _h(self, key):
        return self.store.setdefault(key, {})

    def keys(self, pattern):
        return [k.encode() for k in self.store if fnmatch.fnmatch(k, pattern)]

    def exists(self, key):
        return 1 if key in self.store else 0

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0

    def hset(self, key, field=None, value=None, mapping=None):
        h = self._h(key)
        if mapping:
            for f, v in mapping.items():
                h[f] = str(v).encode()
            return len(mapping)
        h[field] = str(value).encode()
        return 1

    def hget(self, key, field):
        return self._h(key).get(field)

    def hgetall(self, key):
        return {f.encode(): v for f, v in self._h(key).items()}

    def get(self, key):
        val = self.store.get(key)
        return val if isinstance(val, bytes) else None

    def set(self, key, value):
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    def hincrby(self, key, field, amount=1):
        h = self._h(key)
        cur = int(h.get(field, b"0").decode() or 0)
        cur += amount
        h[field] = str(cur).encode()
        return cur


class TestPool(rp.RedisKeyPool):
    def __init__(self, store=None):
        self.redis = FakeRedis(store)

    def is_active(self):
        return True


def status_of(pool, provider, key_id):
    return pool.redis._h(f"q:pool:key:{provider}:{key_id}").get("status", b"").decode()


def failures_of(pool, provider, key_id):
    return int(pool.redis._h(f"q:pool:key:{provider}:{key_id}").get("failures", b"0").decode())


# -- 1. classify_auth_failure is pure, so pin the whole table ---------------
print("\n[1] classify_auth_failure(status, body)")
CASES = [
    # (status, body, want, why this case exists)
    (403, "", "COOL", "a bare 403 says nothing about the credential"),
    (403, '{"error":{"message":"Generative Language API has not been used in project 123 before or it is disabled"}}',
     "COOL", "API-not-enabled is a project problem, not a key problem"),
    (403, "User location is not supported for the API use.",
     "COOL", "a region block must not destroy the key"),
    (403, '{"error":{"message":"Your prompt was flagged by moderation"}}',
     "COOL", "a moderation refusal is about the prompt -- this one burned real keys"),
    (403, "Requests from referer are blocked.",
     "COOL", "a referrer/IP restriction is about where the call came from"),
    (401, "", "STRIKE", "an unexplained 401 is a hypothesis, not a verdict"),
    (401, "Gateway timeout while validating", "STRIKE", "transient 401 from a gateway"),
    (401, '{"error":{"message":"API key not valid. Please pass a valid API key."}}',
     "BURN", "the provider named the credential"),
    (401, '{"error":{"code":"invalid_api_key"}}', "BURN", "openai-style invalid key"),
    (403, '{"error":{"status":"API_KEY_INVALID"}}', "BURN", "google-style, and upper case"),
    (401, "invalid x-api-key", "BURN", "anthropic-style invalid key"),
    (429, "", "COOL", "an unexpected code never burns"),
]
for status, body, want, why in CASES:
    check(why, rp.classify_auth_failure(status, body), want)


# -- 2. note_auth_failure: what a 403 actually costs ------------------------
print("\n[2] note_auth_failure -- a 403 must not be terminal")
p = TestPool()
p.add_key("google", "sk-live-value", "abc123")
check("a fresh key starts HEALTHY", status_of(p, "google", "abc123"), "HEALTHY")
check("add_key seeds a strike counter", failures_of(p, "google", "abc123"), 0)

verdict = p.note_auth_failure("google", "abc123", 403, "User location is not supported")
check("a region-blocked 403 cools the key", verdict, "COOLDOWN")
check("...and does NOT burn it -- the whole point of this file",
      status_of(p, "google", "abc123"), "COOLDOWN")
check_true("the reason is recorded for the UI",
           b"403" in p.redis._h("q:pool:key:google:abc123")["last_error"])

print("\n[3] note_auth_failure -- an unexplained 401 burns only on repetition")
p = TestPool()
p.add_key("openrouter", "sk-or-value", "k1")
v1 = p.note_auth_failure("openrouter", "k1", 401, "")
check("strike 1 cools", (v1, status_of(p, "openrouter", "k1")), ("COOLDOWN", "COOLDOWN"))
v2 = p.note_auth_failure("openrouter", "k1", 401, "")
check("strike 2 cools", (v2, failures_of(p, "openrouter", "k1")), ("COOLDOWN", 2))
v3 = p.note_auth_failure("openrouter", "k1", 401, "")
check("strike 3 burns", (v3, status_of(p, "openrouter", "k1")), ("BURNED", "BURNED"))
check_true("a burn records when it happened, so it can expire",
           float(p.redis._h("q:pool:key:openrouter:k1")["burned_at"].decode()) > 0)

print("\n[4] a success clears the strike history")
p = TestPool()
p.add_key("openrouter", "sk-or-value", "k1")
p.note_auth_failure("openrouter", "k1", 401, "")
p.note_auth_failure("openrouter", "k1", 401, "")
p.release_key("openrouter", "k1", "HEALTHY")          # what a 200 does
check("a 200 resets the counter", failures_of(p, "openrouter", "k1"), 0)
p.note_auth_failure("openrouter", "k1", 401, "")
check("so an intermittent 401 never accumulates to a burn",
      status_of(p, "openrouter", "k1"), "COOLDOWN")

print("\n[5] a body that names the key still burns immediately")
p = TestPool()
p.add_key("google", "sk-dead", "dead1")
check("no waiting when the provider is explicit",
      p.note_auth_failure("google", "dead1", 401, "API key not valid"), "BURNED")

print("\n[6] one request cannot empty the pool")
p = TestPool()
p.add_key("google", "sk-a", "a")
check("allow_burn=False downgrades even a hard verdict",
      p.note_auth_failure("google", "a", 401, "API key not valid", allow_burn=False),
      "COOLDOWN")
check("...and the key survives", status_of(p, "google", "a"), "COOLDOWN")


# -- 7. checkout_key: burns expire, cooldowns still expire ------------------
print("\n[7] checkout_key -- a burn is a hypothesis with a shelf life")
now = time.time()
p = TestPool()
p.add_key("google", "sk-burned", "recent")
p.release_key("google", "recent", "BURNED")
check("a freshly burned key is not handed out",
      p.checkout_key("google"), (None, None, None))

p.redis.hset("q:pool:key:google:recent", "burned_at",
             str(now - rp.BURN_RETRY_SECS - 10))
kid, val, _ = p.checkout_key("google")
check("an expired burn is re-probed", (kid, val), ("recent", "sk-burned"))
check("...and comes back HEALTHY", status_of(p, "google", "recent"), "HEALTHY")
check("...with its strikes cleared", failures_of(p, "google", "recent"), 0)

print("\n[8] keys burned by the OLD one-strike rule come back on upgrade")
p = TestPool({"q:pool:key:google:legacy": {
    "value": b"sk-legacy", "provider": b"google",
    "status": b"BURNED", "cooldown_until": b"0", "proxy": b"",
}})   # no burned_at, no failures -- exactly what the old code wrote
kid, val, _ = p.checkout_key("google")
check("a legacy burn with no burned_at revives", (kid, val), ("legacy", "sk-legacy"))

print("\n[9] cooldown expiry still works (regression on existing behaviour)")
p = TestPool()
p.add_key("google", "sk-cool", "c1")
p.release_key("google", "c1", "COOLDOWN", cooldown_duration=300)
check("a live cooldown is skipped", p.checkout_key("google"), (None, None, None))
p.redis.hset("q:pool:key:google:c1", "cooldown_until", str(now - 1))
check("an expired cooldown is handed out", p.checkout_key("google")[0], "c1")


# -- 10. manual restore ----------------------------------------------------
print("\n[10] unburn_key -- the recovery that did not exist")
p = TestPool()
p.add_key("google", "sk-x", "x1")
p.note_auth_failure("google", "x1", 401, "API key not valid")
check("precondition: burned", status_of(p, "google", "x1"), "BURNED")
check("restore reports success", p.unburn_key("google", "x1"), True)
check("...and the key is usable again", p.checkout_key("google")[0], "x1")
check("...with a clean slate", failures_of(p, "google", "x1"), 0)
check("restoring a key that isn't there is not a silent success",
      p.unburn_key("google", "nope"), False)

print("\n[11] get_pool_status surfaces why a key is down")
p = TestPool()
p.add_key("google", "sk-y", "y1")
p.note_auth_failure("google", "y1", 403, "User location is not supported")
row = p.get_pool_status()["keys"]["google"][0]
check("status reported", row["status"], "COOLDOWN")
check("failure count reported", row["failures"], 1)
check_true("last error reported", "403" in row["last_error"])


# -- 12. the call sites cannot regress to an unconditional burn ------------
print("\n[12] llm_engine no longer burns on sight")
_eng = open(os.path.join(ROOT, "llm_engine.py"), "rb").read().decode("utf-8")
check("no unconditional BURNED release survives",
      _eng.count('release_key(provider, key_id, "BURNED")'), 0)
check("both auth branches route through the classifier",
      _eng.count("key_pool.note_auth_failure("), 2)
check("both auth branches now blame the proxy too",
      _eng.count('key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=300)'), 2)
check_true("one request can burn at most one key", "_auth_burns" in _eng)

_dummy = next((n for n in ast.walk(ast.parse(_eng))
               if isinstance(n, ast.ClassDef) and n.name == "DummyPool"), None)
check_true("the no-redis DummyPool answers the new call, so an import failure "
           "does not become an AttributeError",
           _dummy is not None and any(
               isinstance(f, ast.FunctionDef) and f.name == "note_auth_failure"
               for f in _dummy.body))

# -- 13. round-robin: the pool has to actually spread the load -------------
# checkout_key used to return the FIRST healthy key in redis.keys() order on
# every call, so one credential absorbed every request until it cooled or burned
# while the rest of the pool sat idle -- and every rate limit and auth failure
# landed on that one key. The proxy half of redis_pool has rotated since it was
# written; keys never did.
print()
print("[13] checkout_key rotates")


def drain(pool, provider, n):
    return [pool.checkout_key(provider)[0] for _ in range(n)]


p = TestPool()
for kid in ("k1", "k2", "k3"):
    p.add_key("google", "sk-" + kid, kid)
check("three healthy keys are handed out in turn, then wrap",
      drain(p, "google", 6), ["k1", "k2", "k3", "k1", "k2", "k3"])

print()
print("[14] rotation under partial failure")
p = TestPool()
for kid in ("k1", "k2", "k3"):
    p.add_key("google", "sk-" + kid, kid)
p.checkout_key("google")                                   # cursor -> k2
p.note_auth_failure("google", "k2", 403, "region blocked")  # k2 cools
check("a cooling key is skipped, the rest keep rotating",
      drain(p, "google", 4), ["k3", "k1", "k3", "k1"])
check("...and the cooled key is untouched, not burned",
      status_of(p, "google", "k2"), "COOLDOWN")

p = TestPool()
p.add_key("google", "sk-only", "solo")
check("a one-key pool still works", drain(p, "google", 3), ["solo", "solo", "solo"])
check("an empty provider is still (None, None, None)",
      p.checkout_key("anthropic"), (None, None, None))

print()
print("[15] the rotation order does not depend on redis.keys() ordering")


class ReversingRedis(FakeRedis):
    """redis.keys() has no defined order; the rotation must not inherit it."""

    def keys(self, pattern):
        return list(reversed(FakeRedis.keys(self, pattern)))


shared = {}
p = TestPool(shared)
for kid in ("k1", "k2", "k3"):
    p.add_key("google", "sk-" + kid, kid)
forward = drain(p, "google", 6)

q = TestPool(shared)
q.redis = ReversingRedis(shared)
q.redis.set("q:pool:key_cursor:google", b"0")
reversed_order = drain(q, "google", 6)
check("same cycle whichever order the backend enumerates in",
      reversed_order, forward)

print()
print("[16] the cursor survives a new pool object")
shared = {}
p = TestPool(shared)
for kid in ("k1", "k2", "k3"):
    p.add_key("google", "sk-" + kid, kid)
p.checkout_key("google")
p.checkout_key("google")
check("a fresh RedisKeyPool resumes where the last one left off",
      TestPool(shared).checkout_key("google")[0], "k3")
check("the cursor lives in redis, not in the process "
      "(three checkouts happened above, so it sits past k3)",
      shared.get("q:pool:key_cursor:google"), b"3")

_pool_src = open(os.path.join(ROOT, "redis_pool.py"), "rb").read().decode("utf-8")
check_true("checkout_key no longer returns the first match it finds",
           "q:pool:key_cursor:" in _pool_src)
check_true("the candidate list is sorted, so the cycle is stable",
           "sorted(" in _pool_src)


print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
sys.exit(1 if FAILS else 0)
