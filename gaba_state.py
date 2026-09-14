"""
gaba_state.py -- inhibition: the antagonist the dopamine organ never had.

One variable, per (username, persona):

- INHIBITION (0.0..1.0): rises along a RUN of redundant events and drains on
  its own clock. Acts on what dopamine is *allowed to do* (open the explore
  gate, keep the daemon awake), never on what dopamine *is*.

Plus a STREAK integer: consecutive redundant events since the last novel one.

DESIGN RULES (mirror of dopamine_state's, and as non-negotiable):
1. GABA is NOT negative dopamine. This module never imports, reads or writes
   dopamine_state. Tonic keeps its ratchet, phasic keeps its clamp. Callers
   compose the two predicates: `ds.should_explore(t) and not gs.is_inhibited(i)`.
2. One input: redundancy. The Reflector looking at the window and declining to
   summarise it again (reflect_gate "shrug") is the redundancy event; the
   daemon paying itself a tonic bump for a gap nobody asked about is the other.
   No social valence -- a brain that pulls back because someone frowned is
   approval-driven, and this one is interest-driven by brief.
3. Ephemeral, like dopamine: Redis-backed floats with a timestamped exponential
   drain to zero, plus the same in-memory fallback pattern.
4. Asymmetric. A novel event resets the STREAK, not the inhibition. A long dull
   stretch takes a while to shake off; one shiny thing does not throw the gate
   back open. Without that asymmetry this is just negative novelty with extra
   steps.

Origin: lab_notes/gaba_inhibition.md. Phasic pinned at 1.0 after eight events,
nothing in the event vocabulary could move tonic down, the only brake was the
2700 s tau. Excitation with no antagonist. Every constant below is marked
PROPOSED in the lab note: the slow replay sets them, not argument.

Consumers:
- plugins/memory_plugin.Reflector: on_redundant() on shrug, on_novel() on store
- stream_worker: is_inhibited() folded into the explore gate; on_redundant() on
  each daemon gap boost
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

try:
    import telemetry as _tm
except Exception:  # telemetry must never be load-bearing
    _tm = None


def _emit(*a, **k):
    if _tm is not None:
        try:
            _tm.emit(*a, **k)
        except Exception:
            pass

# --- Tunables (env-overridable; all PROPOSED until the replay tunes them) ---
GABA_BASE = float(os.getenv("GABA_BASE", "0.05"))          # one dull event is noise
GABA_GROWTH = float(os.getenv("GABA_GROWTH", "1.5"))       # ten in a row is a signal
GABA_TAU_SEC = float(os.getenv("GABA_TAU_SEC", "600"))     # slower than phasic (90), faster than tonic (2700)
GATE_INHIBITION_MAX = float(os.getenv("GABA_GATE_MAX", "0.50"))  # at/above: explore gate reads shut
# A streak older than this is not a streak, it is history. Without a TTL a
# redundant event after an hour of silence would resume the run at its old
# exponent and pay a huge increment for one dull turn.
GABA_STREAK_TTL_SEC = float(os.getenv("GABA_STREAK_TTL_SEC", str(2 * 600)))
# Graded redundancy (2026-09-13, Sky): a shrug carries an intensity 0..1 (how
# flat the window was; reflect_gate.boredom_intensity). The increment is scaled
# by GRADE_MIN + (GRADE_MAX - GRADE_MIN) * intensity, so a window barely over
# the ceiling pays half and a dead-flat one pays 1.5x. The STREAK still
# advances by one either way -- grading weights the pressure, not the count.
# intensity=None (daemon_gap, or no similarity) -> weight 1.0, the old fixed step.
GABA_GRADE_MIN = float(os.getenv("GABA_GRADE_MIN", "0.5"))    # PROPOSED
GABA_GRADE_MAX = float(os.getenv("GABA_GRADE_MAX", "1.5"))    # PROPOSED

# Increment sequence for the record (BASE * GROWTH**k):
#   k=0 0.050  k=1 0.075  k=2 0.1125  k=3 0.169  k=4 0.253  k=5 0.380
# cumulative 0.050, 0.125, 0.2375, 0.406, 0.659 -> crosses 0.50 on the 5th.
# Ten in a row saturates at 1.0 (lab note §5 headline unit check).


def _key(username: str, persona: str, kind: str) -> str:
    return f"gaba:{username}:{persona}:{kind}"


def _load(kind: str, username: str, persona: str):
    """Return {'v': float, 'ts': epoch} or None. Same absent-vs-unreachable
    discipline as dopamine_state: a live Redis saying 'gone' is authoritative
    over the in-process cache, so the organ can be reset from outside."""
    k = _key(username, persona, kind)
    raw = None
    reachable = False
    if _RCONN is not None:
        try:
            raw = _RCONN.get(k.encode())
            reachable = True
        except Exception:
            reachable = False
    if raw is None:
        if reachable:
            _MEM_FALLBACK.pop(k, None)
            return None
        return _MEM_FALLBACK.get(k)
    try:
        d = json.loads(raw)
        return {"v": float(d["v"]), "ts": float(d["ts"])}
    except Exception:
        return None


def _save(kind: str, username: str, persona: str, v: float, ts: float = None):
    """ts override exists for the test suite (drain checks); production
    callers never pass it."""
    k = _key(username, persona, kind)
    ts = time.time() if ts is None else float(ts)
    # Round ONCE and store the same number on both paths. Redis used to get
    # round(v, 4) while the fallback kept full precision, so the two curves
    # drifted by 1e-4 after five compounding steps. Invisible on 2026-09-09
    # (Redis offline, both "paths" were the fallback); caught 2026-09-13 on
    # the first live run of the Redis path (test [6]).
    v = round(float(v), 4)
    _MEM_FALLBACK[k] = {"v": v, "ts": ts}
    if _RCONN is not None:
        try:
            _RCONN.set(k.encode(), json.dumps({"v": v, "ts": ts}).encode(),
                       ex=int(GABA_TAU_SEC * 6))
        except Exception:
            pass


def _drained(entry) -> float:
    """Exponential drain toward 0 over elapsed wall time."""
    if entry is None:
        return 0.0
    dt = max(0.0, time.time() - entry["ts"])
    return min(1.0, max(0.0, entry["v"] * math.exp(-dt / GABA_TAU_SEC)))


def _streak(entry) -> int:
    if entry is None:
        return 0
    if time.time() - entry["ts"] > GABA_STREAK_TTL_SEC:
        return 0
    return int(entry["v"])


# --- Public API -----------------------------------------------------------

def get_state(username: str, persona: str) -> dict:
    """Current inhibition (time-drained) and streak (0 if stale)."""
    with _LOCK:
        inh = _drained(_load("inhibition", username, persona))
        stk = _streak(_load("streak", username, persona))
    return {"inhibition": round(inh, 4), "streak": stk}


def grade_weight(intensity) -> float:
    """Pure: intensity 0..1 (or None) -> multiplier on the increment."""
    if intensity is None:
        return 1.0
    i = max(0.0, min(1.0, float(intensity)))
    return GABA_GRADE_MIN + (GABA_GRADE_MAX - GABA_GRADE_MIN) * i


def on_redundant(username: str, persona: str, nearest=None, source: str = "reflect",
                 intensity=None) -> dict:
    """
    A redundant event: the world did not move. streak += 1, inhibition rises
    by BASE * GROWTH**(streak-1) * grade_weight(intensity) -- accelerating, so
    one dull memory is noise and a run is a signal, and graded, so a run of
    flat windows is a louder signal than a run of mildly dull ones. Clamped at 1.0.
    """
    w = grade_weight(intensity)
    with _LOCK:
        cur = _drained(_load("inhibition", username, persona))
        stk = _streak(_load("streak", username, persona)) + 1
        inc = GABA_BASE * (GABA_GROWTH ** (stk - 1)) * w
        new = min(1.0, cur + inc)
        _save("inhibition", username, persona, new)
        _save("streak", username, persona, stk)
    near = "none" if nearest is None else round(float(nearest), 3)
    crossed = bool(cur < GATE_INHIBITION_MAX <= new)
    print(f"[GABA] redundant({source}): streak={stk} nearest={near} grade={w:.2f} "
          f"+{min(inc, 1.0 - cur):.4f} inhibition={new:.4f}"
          f"{' GATE_SHUT' if new >= GATE_INHIBITION_MAX else ''}"
          f"{' <-- closed it' if crossed else ''}", flush=True)
    _emit("gaba", "redundant", username, persona, source=source, streak=stk,
          nearest=nearest, intensity=intensity, weight=w, before=cur,
          increment=min(inc, 1.0 - cur), inhibition=new,
          gate_shut=bool(new >= GATE_INHIBITION_MAX), crossed=crossed)
    return {"streak": stk, "increment": round(inc, 4), "weight": round(w, 4),
            "crossed": crossed, **get_state(username, persona)}


def on_novel(username: str, persona: str, nearest=None, source: str = "reflect") -> dict:
    """
    A novel event: the world moved. Resets the STREAK only. Inhibition is
    deliberately left alone -- it keeps draining on its own tau from wherever
    it was. (Design rule 4.)
    """
    with _LOCK:
        _save("streak", username, persona, 0)
        inh = _drained(_load("inhibition", username, persona))
    near = "none" if nearest is None else round(float(nearest), 3)
    print(f"[GABA] novel({source}): streak reset, nearest={near} inhibition={inh:.4f} (draining)",
          flush=True)
    _emit("gaba", "novel", username, persona, source=source, nearest=nearest, inhibition=inh)
    return get_state(username, persona)


def is_inhibited(inhibition) -> bool:
    """Pure predicate: does this inhibition level hold the explore gate shut?
    Callers AND it with dopamine_state.should_explore(tonic); this module does
    not know what tonic is."""
    if inhibition is None:
        return False
    return float(inhibition) >= GATE_INHIBITION_MAX


def half_life_sec() -> float:
    """How long inhibition takes to halve with no events. For logs and tests."""
    return GABA_TAU_SEC * math.log(2)


if __name__ == "__main__":
    # Smoke test (works with or without Redis). Writes only gaba:_smoke:_test:*
    os.environ.setdefault("TELEMETRY_OFF", "1")   # keep the smoke out of the real sink
    u, p = "_smoke", "_test"
    print("initial:", get_state(u, p))
    for _ in range(6):
        on_redundant(u, p, nearest=0.91)
    s = get_state(u, p)
    print("after 6 redundant:", s, "| inhibited:", is_inhibited(s["inhibition"]))
    print("novel:", on_novel(u, p, nearest=0.42))
    print("half-life:", round(half_life_sec(), 1), "s")
    for kind in ("inhibition", "streak"):
        _MEM_FALLBACK.pop(_key(u, p, kind), None)
        if _RCONN is not None:
            try:
                _RCONN.delete(_key(u, p, kind))
            except Exception:
                pass
