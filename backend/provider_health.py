"""
provider_health.py — operational learning about the WORLD.

Deliberate counterpart to dopamine_state.py. The two must never be confused:

    dopamine_state  -> "how competent am I?"      (agent self-model)
    provider_health -> "how reliable is that server?" (world model)

When an upstream provider 503s, exactly one of those should update. The router
needs to learn that a server is flaky so it can back off or rotate; the neuron
must NOT learn that the agent is incompetent. Keeping the two in separate
modules with separate Redis namespaces makes the mistake structurally hard.

Tracked per provider:
  - reliability: EMA of successful calls (0.0..1.0), slowly forgiving
  - consecutive_failures: drives exponential backoff
  - last_failure_ts / last_error: operator-facing diagnostics

Nothing here writes to the dopamine namespace. Ever.
"""

import os
import json
import time
import threading

try:
    import redis_client
    _RCONN = redis_client.get_connection() if redis_client.is_active() else None
except Exception:
    _RCONN = None

_LOCK = threading.Lock()
_MEM_FALLBACK = {}

# ─── Tunables ────────────────────────────────────────────────
RELIABILITY_ALPHA = float(os.getenv("PROVIDER_HEALTH_ALPHA", "0.15"))  # EMA speed
BACKOFF_BASE_SEC = float(os.getenv("PROVIDER_BACKOFF_BASE", "1.0"))
BACKOFF_MAX_SEC = float(os.getenv("PROVIDER_BACKOFF_MAX", "20.0"))
UNRELIABLE_THRESHOLD = 0.5   # below this, the provider is considered degraded
STATE_TTL_SEC = 7 * 24 * 3600


def _key(provider: str) -> str:
    return f"q:provider:health:{provider}"


def _load(provider: str) -> dict:
    k = _key(provider)
    raw = None
    if _RCONN is not None:
        try:
            raw = _RCONN.get(k.encode())
        except Exception:
            pass
    if raw is None:
        return dict(_MEM_FALLBACK.get(k) or {})
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save(provider: str, state: dict):
    k = _key(provider)
    _MEM_FALLBACK[k] = dict(state)
    if _RCONN is not None:
        try:
            _RCONN.set(k.encode(), json.dumps(state).encode(), ex=STATE_TTL_SEC)
        except Exception:
            pass


def record_outcome(provider: str, ok: bool, error: str = "") -> dict:
    """
    Record one call outcome against a provider's reliability model.

    ok=True  -> reliability climbs, consecutive failure streak resets
    ok=False -> reliability drops, streak grows (drives backoff)

    Call this for INFRASTRUCTURE outcomes only (transport/gateway health).
    Auth failures and agent mistakes say nothing about server reliability.
    """
    provider = provider or "unknown"
    with _LOCK:
        st = _load(provider)
        rel = float(st.get("reliability", 1.0))
        streak = int(st.get("consecutive_failures", 0))
        samples = int(st.get("samples", 0)) + 1

        target = 1.0 if ok else 0.0
        rel = rel + RELIABILITY_ALPHA * (target - rel)

        if ok:
            streak = 0
        else:
            streak += 1
            st["last_failure_ts"] = time.time()
            if error:
                st["last_error"] = str(error)[:200]

        st.update({
            "reliability": round(min(1.0, max(0.0, rel)), 4),
            "consecutive_failures": streak,
            "samples": samples,
        })
        _save(provider, st)
        return dict(st)


def get_health(provider: str) -> dict:
    """Current reliability model for a provider."""
    st = _load(provider or "unknown")
    return {
        "reliability": float(st.get("reliability", 1.0)),
        "consecutive_failures": int(st.get("consecutive_failures", 0)),
        "samples": int(st.get("samples", 0)),
        "last_failure_ts": st.get("last_failure_ts"),
        "last_error": st.get("last_error"),
    }


def suggested_backoff(provider: str) -> float:
    """
    Seconds to wait before retrying this provider. Grows exponentially with the
    consecutive failure streak so a flapping upstream isn't hammered.
    """
    streak = get_health(provider)["consecutive_failures"]
    if streak <= 0:
        return 0.0
    return min(BACKOFF_MAX_SEC, BACKOFF_BASE_SEC * (2 ** (streak - 1)))


def is_degraded(provider: str) -> bool:
    """True when a provider has been failing enough to distrust it."""
    h = get_health(provider)
    return h["samples"] >= 3 and h["reliability"] < UNRELIABLE_THRESHOLD


def snapshot() -> dict:
    """All known provider health, for diagnostics/UI."""
    out = {}
    if _RCONN is not None:
        try:
            for k in _RCONN.keys(b"q:provider:health:*"):
                name = k.decode().split(":")[-1]
                out[name] = get_health(name)
            return out
        except Exception:
            pass
    for k in _MEM_FALLBACK:
        name = k.split(":")[-1]
        out[name] = get_health(name)
    return out


if __name__ == "__main__":
    p = "_smoke_provider"
    print("fresh:", get_health(p))
    for i in range(3):
        st = record_outcome(p, False, error=f"503 Service Unavailable #{i+1}")
        print(f"  fail {i+1}: reliability={st['reliability']} streak={st['consecutive_failures']} backoff={suggested_backoff(p)}s")
    print("degraded:", is_degraded(p))
    for i in range(4):
        st = record_outcome(p, True)
    print("after recovery:", get_health(p), "backoff:", suggested_backoff(p), "degraded:", is_degraded(p))
