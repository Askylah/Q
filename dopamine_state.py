"""
dopamine_state.py — global neuromodulator state for the brain simulation.

Two timescales, two variables, per (username, persona):

- TONIC: slow baseline posture (0.0..1.0). Sets exploration vs consolidation.
  High tonic -> chase voids, protect memories from decay, favor novelty.
  Low tonic  -> consolidation; let the 30-day decay leak run cold.
- PHASIC: fast spike envelope (0.0..1.0), decays in ~minutes. Fired by
  reward-prediction-error events. Gates write-time stamping: memories born
  during a spike get an importance bonus.

DESIGN RULES (do not violate):
1. Dopamine is NOT per-memory salience. Never read/write
   DeepMemory.importance here. That is a different organ.
2. RPE, not raw novelty. fire_rpe() takes expected vs observed; positive
   deltas spike, negative deltas dip. Predicted novelty is not rewarding.
3. State is ephemeral context, not a record: Redis-backed floats with a
   timestamped exponential return-to-baseline, plus an in-memory fallback
   mirroring redis_client's graceful degradation when Redis is offline.

Consumers:
- memory_engine.decay_cycle(): decay_rate *= decay_modulator(tonic)
- memory_engine.store():      importance += stamp_bonus(phasic)
- future daemon/gap-picker:   explore if tonic >= explore_threshold
"""

import os
import json
import math
import time
import threading

try:
    import redis_client
    _RCONN = redis_client.get_connection() if redis_client.is_active() else None
except Exception:
    _RCONN = None

_LOCK = threading.Lock()
_MEM_FALLBACK = {}

# ─── Tunables (env-overridable) ──────────────────────────────
TONIC_BASELINE = float(os.getenv("DA_TONIC_BASELINE", "0.30"))
TONIC_TAU_SEC = float(os.getenv("DA_TONIC_TAU_SEC", str(45 * 60)))    # 45 min return-to-baseline
PHASIC_TAU_SEC = float(os.getenv("DA_PHASIC_TAU_SEC", "90"))          # spike lasts ~1 exchange
PHASIC_MAX = 1.0

# Output mappings
DECAY_MULT_FLOOR = 0.6   # tonic=1.0 -> decay runs at 60% speed (engaged brains keep memories)
DECAY_MULT_CEIL = 1.4    # tonic=0.0 -> decay runs at 140% speed (bored brains forget)
EXPLORE_THRESHOLD = 0.5  # tonic above this -> daemon/gap-picker should explore


def _key(username: str, persona: str, kind: str) -> str:
    return f"da:{username}:{persona}:{kind}"


def _load(kind: str, username: str, persona: str):
    """Return {'v': float, 'ts': epoch} or None."""
    k = _key(username, persona, kind)
    raw = None
    if _RCONN is not None:
        try:
            raw = _RCONN.get(k.encode() if isinstance(k, str) else k)
        except Exception:
            pass
    if raw is None:
        return _MEM_FALLBACK.get(k)
    try:
        d = json.loads(raw)
        return {"v": float(d["v"]), "ts": float(d["ts"])}
    except Exception:
        return None


def _save(kind: str, username: str, persona: str, v: float):
    k = _key(username, persona, kind)
    payload = json.dumps({"v": round(v, 4), "ts": time.time()})
    _MEM_FALLBACK[k] = {"v": v, "ts": time.time()}
    if _RCONN is not None:
        try:
            _RCONN.set(k.encode() if isinstance(k, str) else k,
                       payload.encode(), ex=int(TONIC_TAU_SEC * 4))
        except Exception:
            pass


def _decay_toward_baseline(entry, baseline: float, tau: float):
    """Exponential relaxation toward baseline over elapsed wall time."""
    if entry is None:
        return baseline
    dt = max(0.0, time.time() - entry["ts"])
    relaxed = baseline + (entry["v"] - baseline) * math.exp(-dt / tau)
    return min(1.0, max(0.0, relaxed))


# ─── Public API ──────────────────────────────────────────────

def get_state(username: str, persona: str) -> dict:
    """Current tonic + phasic values, time-relaxed toward baseline."""
    with _LOCK:
        tonic = _decay_toward_baseline(_load("tonic", username, persona), TONIC_BASELINE, TONIC_TAU_SEC)
        phasic = _decay_toward_baseline(_load("phasic", username, persona), 0.0, PHASIC_TAU_SEC)
    return {"tonic": round(tonic, 4), "phasic": round(phasic, 4)}


def fire_rpe(username: str, persona: str, expected: float, observed: float, gain: float = 1.0) -> dict:
    """
    Reward-prediction-error event. rpe = observed - expected (both nominally
    0..1). Positive errors spike phasic DA; negative errors produce a dip
    (omission signal). Large positive errors also nudge tonic upward.
    Returns the computed rpe and the new state.
    """
    rpe = (float(observed) - float(expected)) * float(gain)
    with _LOCK:
        cur_phasic = _decay_toward_baseline(_load("phasic", username, persona), 0.0, PHASIC_TAU_SEC)
        cur_tonic = _decay_toward_baseline(_load("tonic", username, persona), TONIC_BASELINE, TONIC_TAU_SEC)

        new_phasic = min(PHASIC_MAX, max(0.0, cur_phasic + max(-0.3, rpe)))
        # Only strong positive surprises raise the posture; dips relax naturally.
        new_tonic = min(1.0, max(0.0, cur_tonic + (0.05 * rpe if rpe > 0 else 0.0)))

        _save("phasic", username, persona, new_phasic)
        _save("tonic", username, persona, new_tonic)

    return {"rpe": round(rpe, 4), **get_state(username, persona)}


def boost_tonic(username: str, persona: str, amount: float) -> dict:
    """Direct tonic bump (e.g., unresolved-gap detection in the daemon)."""
    amount = float(amount)
    with _LOCK:
        cur = _decay_toward_baseline(_load("tonic", username, persona), TONIC_BASELINE, TONIC_TAU_SEC)
        _save("tonic", username, persona, min(1.0, max(0.0, cur + amount)))
    return get_state(username, persona)


def decay_modulator(tonic: float) -> float:
    """
    Multiplier applied to DECAY_RATE_* in memory_engine.decay_cycle().
    High tonic protects (floor 0.6x); low tonic accelerates forgetting (ceil 1.4x).
    """
    t = min(1.0, max(0.0, float(tonic)))
    return DECAY_MULT_CEIL + (DECAY_MULT_FLOOR - DECAY_MULT_CEIL) * t


def stamp_bonus(phasic: int) -> int:
    """
    Importance points added by DeepMemory.store() for memories born during
    a phasic spike. 0 at rest, up to +2 at full spike.
    """
    p = min(1.0, max(0.0, float(phasic)))
    return int(round(p * 2))


def should_explore(tonic: float) -> bool:
    """Posture gate for the future gap-picker/daemon."""
    return float(tonic) >= EXPLORE_THRESHOLD


# ─── Convergent RPE inputs ───────────────────────────────────
# Real dopaminergic signaling is a convergence organ: multiple evaluative
# systems (task outcome, social valence) dump into the same pool with
# different weights and timescales.

_EMA_ALPHA = 0.25          # how fast expectations track reality
_TOOL_RPE_GAIN = 1.0       # objective task reward, full weight
_SOCIAL_RPE_GAIN = 0.6     # social valence moves the pool less per event
_EMA_TAU_SEC = TONIC_TAU_SEC  # expectations relax toward neutral very slowly


def _update_ema_and_fire(username: str, persona: str, kind: str,
                         observed: float, gain: float,
                         phasic_weight: float = 1.0) -> dict:
    """
    Rescorla-Wagner: rpe = observed - expected; expectation then drifts
    toward observed. Positive errors spike phasic DA; negative ones dip it.
    Expectations are stored as another state key and time-relaxed.
    """
    with _LOCK:
        entry = _load(kind, username, persona)
        expected = entry["v"] if entry else 0.5
        # Relax stale expectations toward neutral before comparing
        expected = _decay_toward_baseline({"v": expected, "ts": entry["ts"] if entry else time.time()}, 0.5, _EMA_TAU_SEC)

        rpe = (float(observed) - expected) * gain

        cur_phasic = _decay_toward_baseline(_load("phasic", username, persona), 0.0, PHASIC_TAU_SEC)
        cur_tonic = _decay_toward_baseline(_load("tonic", username, persona), TONIC_BASELINE, TONIC_TAU_SEC)

        new_phasic = min(PHASIC_MAX, max(0.0, cur_phasic + max(-0.3, rpe) * phasic_weight))
        new_tonic = min(1.0, max(0.0, cur_tonic + (0.05 * rpe if rpe > 0 else 0.0)))

        new_expected = expected + _EMA_ALPHA * (float(observed) - expected)

        _save(kind, username, persona, new_expected)
        _save("phasic", username, persona, new_phasic)
        _save("tonic", username, persona, new_tonic)

    return {"rpe": round(rpe, 4), "expected": round(expected, 4),
            **get_state(username, persona)}


def tool_reward(username: str, persona: str, success_ratio: float) -> dict:
    """
    Fast objective channel: fraction of agent-loop tool calls that succeeded
    this pass (0.0..1.0). Fires RPE against the rolling success expectation.
    Call once per tool-executing pass of intercepting_stream_generator.
    """
    ratio = min(1.0, max(0.0, float(success_ratio)))
    return _update_ema_and_fire(username, persona, "tool_ema", ratio, _TOOL_RPE_GAIN)


def social_reward(username: str, persona: str, valence_observed: float) -> dict:
    """
    Slow social channel: emotional valence of the exchange from the user's
    side (0.0..1.0), as judged by the periodic Reflector. Fires RPE against
    the rolling valence expectation — mood *shifts* are the signal, not mood.
    """
    valence = min(1.0, max(0.0, float(valence_observed)))
    return _update_ema_and_fire(username, persona, "valence_ema", valence, _SOCIAL_RPE_GAIN)


if __name__ == "__main__":
    # Smoke test (works with or without Redis)
    u, p = "_smoke", "_test"
    print("initial:", get_state(u, p))
    print("rpe +0.6:", fire_rpe(u, p, expected=0.2, observed=0.8))
    print("state:", get_state(u, p))
    print("boost:", boost_tonic(u, p, 0.25))
    s = get_state(u, p)
    print("decay_mod:", decay_modulator(s["tonic"]), "| stamp_bonus:", stamp_bonus(s["phasic"]), "| explore:", should_explore(s["tonic"]))
