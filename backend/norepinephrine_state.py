"""
norepinephrine_state.py -- the interrupt: what happens when the conversation suddenly turns.

lab_notes/norepinephrine.md: option A agreed 2026-10-11, calibrated §6, daemon hold §7.

One fast variable per (username, persona):

- ALERT (0.0..1.0): rises when a user message lands unexpectedly far from where the
  conversation has been, decays over about one or two exchanges (NE_TAU_SEC).

Plus bookkeeping, stored together so it can never be half-updated: CONV (running
conversation vector, EMA of unit user-message embeddings), M (this user's expected shift),
SEEN (messages since the last session reset), LAST_TS, REFRACTORY_UNTIL.

Why it exists: efferent checks `inhibited` first, and GABA's stickiness is deliberate, so a
frame break inside a dull stretch went out with "be brief, your patience is spent". Observed
live on 2026-10-11 (gaba note §11): the D turn got band=inhibited and max_tokens 4096 -> 2577.
Nothing fast and valence-free reacted inside the turn. In the brain that is the locus
coeruleus: unexpected uncertainty (Yu & Dayan 2005), network reset (Bouret & Sara 2005).

DESIGN RULES (lab note §1, as non-negotiable as the other organs'):
1. NE is not phasic dopamine and not anti-GABA. This module never imports, reads or writes
   dopamine_state or gaba_state. Callers compose; NE changes what the OUTPUT does while it is
   up, never inhibition or tonic.
2. One input: the unexpected context shift of the USER message. No affect, no lexicon, no
   valence, no tool filter (§6 correction: a pasted log is the user's message).
3. Predicted volatility is not surprising: surprise = shift - M, where M is this user's usual
   shift. A user who jumps topic every message stops firing it (dopamine rule 2, applied to
   attention).
4. Refractory after a fire, like the LC's alpha-2 autoinhibition: fires inside the window get
   a fraction of the gain, so a chaotic stretch can't pin the organ at 1.0.
5. Ephemeral, like the other two: Redis-backed with a timestamped decay, plus the same
   in-memory fallback.
6. Silent at rest, lesionable, shadowable, recorded. NE_OFF kills the organ. NE_SHADOW
   (default ON) means compute and record, but consumers must not act: they ask acting().
   Every update emits ne/turn telemetry. Every constant is PROPOSED.

Consumers (NONE wired yet, 2026-10-11):
- llm_engine, once per user turn: on_user_message(username, persona, text, embedding=...),
  passing the zettel query embedding when there is one (lab note §3: zero extra encodes).
- efferent: band "alert" outranks "inhibited" when is_alert(state["alert"]) and acting().
- stream_worker, at the top of the per-persona sweep: if alert_active(u, p), hold the sweep
  when acting(), else emit daemon/would_hold and sweep anyway (lab note §7).
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


# --- Tunables (env-overridable; PROPOSED: lab note §6 set them from 35 real turns) ---
NE_FIRE_DELTA = float(os.getenv("NE_FIRE_DELTA", "0.20"))      # §6: 3/3 rick fires were real breaks
NE_MIN_WORDS = int(os.getenv("NE_MIN_WORDS", "4"))             # "ok", "lol" embed as noise
NE_SESSION_GAP_SEC = float(os.getenv("NE_SESSION_GAP_SEC", "7200"))
NE_WARMUP = int(os.getenv("NE_WARMUP", "2"))                   # messages after a reset that never fire
NE_ALPHA_M = float(os.getenv("NE_ALPHA_M", "0.25"))            # dopamine's Rescorla-Wagner rate
NE_ALPHA_C = float(os.getenv("NE_ALPHA_C", "0.35"))            # §6: 0.2..0.5 all gave the same fires
NE_TAU_SEC = float(os.getenv("NE_TAU_SEC", "150"))             # 1-2 exchanges: phasic DA 90 < NE < GABA 600
NE_GAIN = float(os.getenv("NE_GAIN", "3.0"))                   # delta 0.20 -> +0.60; >= 0.34 saturates
NE_ALERT_MIN = float(os.getenv("NE_ALERT_MIN", "0.30"))        # at/above: the band and the daemon hold
NE_REFRACTORY_SEC = float(os.getenv("NE_REFRACTORY_SEC", "300"))
NE_REFRACTORY_GAIN = float(os.getenv("NE_REFRACTORY_GAIN", "0.3"))

# How long an alert stays at/above NE_ALERT_MIN, for the record:
#   a fire at the threshold (+0.60):  150 * ln(0.60/0.30) ~ 104 s, about one exchange
#   a saturating break (D: delta 0.83 -> 1.0): 150 * ln(1.0/0.30) ~ 181 s


def _off(name: str) -> bool:
    """Read per call, like efferent._off: tests flip these after import."""
    return os.getenv(name, "") not in ("", "0", "false", "False")


def shadow() -> bool:
    """NE_SHADOW defaults ON until the §6 recalibration on ~200 real turns."""
    return os.getenv("NE_SHADOW", "1") not in ("0", "false", "False")


def acting() -> bool:
    """May consumers act on the alert? False when lesioned or in shadow."""
    return not _off("NE_OFF") and not shadow()


def _key(username: str, persona: str) -> str:
    return f"ne:{username}:{persona}:state"


def _blank() -> dict:
    return {"alert": 0.0, "alert_ts": 0.0, "conv": None, "m": None, "seen": 0,
            "last_ts": None, "refractory_until": 0.0}


def _load(username: str, persona: str) -> dict:
    """Same absent-vs-unreachable discipline as gaba_state: a live Redis saying 'gone' is
    authoritative over the in-process cache, so the organ can be reset from outside."""
    k = _key(username, persona)
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
            return _blank()
        return dict(_MEM_FALLBACK.get(k) or _blank())
    try:
        d = _blank()
        d.update(json.loads(raw))
        return d
    except Exception:
        return _blank()


def _save(username: str, persona: str, st: dict):
    """Round ONCE and store the same numbers on both paths (gaba_state 2026-09-13 lesson)."""
    k = _key(username, persona)
    st = dict(st)
    st["alert"] = round(float(st["alert"]), 4)
    if st.get("m") is not None:
        st["m"] = round(float(st["m"]), 6)
    if st.get("conv") is not None:
        st["conv"] = [round(float(x), 6) for x in st["conv"]]
    _MEM_FALLBACK[k] = st
    if _RCONN is not None:
        try:
            _RCONN.set(k.encode(), json.dumps(st).encode(),
                       ex=int(max(NE_SESSION_GAP_SEC * 2, NE_TAU_SEC * 6)))
        except Exception:
            pass


def _decayed(alert: float, alert_ts: float, now: float) -> float:
    dt = max(0.0, now - float(alert_ts or 0.0))
    return min(1.0, max(0.0, float(alert) * math.exp(-dt / NE_TAU_SEC)))


def _unit(v):
    n = math.sqrt(sum(float(x) * float(x) for x in v))
    return [float(x) / n for x in v] if n > 0 else None


def _embed(text: str):
    """Fallback when the caller has no embedding: the app's shared MiniLM."""
    try:
        from embedding_model import get_shared_model
        model = get_shared_model()
        if not model:
            return None
        return [float(x) for x in model.encode([text], convert_to_numpy=True)[0]]
    except Exception:
        return None


# --- Public API -----------------------------------------------------------

def on_user_message(username: str, persona: str, text: str, embedding=None, now: float = None) -> dict:
    """
    One user turn through the §3 arithmetic, in the same order as labs/ne_calibrate.py
    replay() (that is what the constants were measured on):
      gap check (every message) -> short skip -> seed -> warmup -> evaluate -> fire.
    Returns what happened; never raises on the chat path.
    """
    now = time.time() if now is None else float(now)
    if _off("NE_OFF"):
        return {"status": "off", "alert": 0.0, "fired": False}
    text = text or ""
    words = len(text.split())
    rec = {"status": "", "words": words, "s": None, "delta": None, "m_before": None,
           "fired": False, "refractory": False, "session_start": False, "embedding": None}
    try:
        with _LOCK:
            st = _load(username, persona)
            alert_before = _decayed(st["alert"], st["alert_ts"], now)
            if st["last_ts"] is not None and now - float(st["last_ts"]) > NE_SESSION_GAP_SEC:
                st.update(conv=None, m=None, seen=0)
                rec["session_start"] = True
            st["last_ts"] = now
            st["alert"], st["alert_ts"] = alert_before, now

            if words < NE_MIN_WORDS:
                rec["status"] = "short"
            else:
                if embedding is not None:
                    e, rec["embedding"] = _unit(embedding), "given"
                else:
                    e, rec["embedding"] = _unit(_embed(text) or []), "computed"
                if e is None:
                    rec["status"] = "no_model"
                elif st["conv"] is None or len(st["conv"]) != len(e):
                    st["conv"], st["seen"] = e, 1
                    rec["status"] = "seed"
                else:
                    s = 1.0 - sum(a * b for a, b in zip(e, st["conv"]))
                    rec["s"] = s
                    if st["m"] is None or st["seen"] < NE_WARMUP:
                        rec["status"] = "warmup"
                        st["m"] = s if st["m"] is None else st["m"] + NE_ALPHA_M * (s - st["m"])
                    else:
                        rec["status"] = "eval"
                        rec["m_before"] = st["m"]
                        delta = s - st["m"]
                        rec["delta"] = delta
                        st["m"] = st["m"] + NE_ALPHA_M * (s - st["m"])
                        if delta > NE_FIRE_DELTA:
                            in_ref = now < float(st["refractory_until"] or 0.0)
                            gain = NE_GAIN * (NE_REFRACTORY_GAIN if in_ref else 1.0)
                            st["alert"] = min(1.0, st["alert"] + gain * delta)
                            rec["fired"], rec["refractory"] = True, in_ref
                            if not in_ref and st["alert"] >= NE_ALERT_MIN:
                                st["refractory_until"] = now + NE_REFRACTORY_SEC
                    mixed = [(1 - NE_ALPHA_C) * c + NE_ALPHA_C * x for c, x in zip(st["conv"], e)]
                    st["conv"] = _unit(mixed) or st["conv"]
                    st["seen"] = int(st["seen"]) + 1
            _save(username, persona, st)
            alert_after = round(float(st["alert"]), 4)
    except Exception as ex:   # the organ must never take a chat turn down
        print(f"[NE] on_user_message failed (non-fatal): {ex}", flush=True)
        return {"status": "error", "alert": 0.0, "fired": False}

    if rec["fired"]:
        print(f"[NE] fired: delta={rec['delta']:.3f} (expected {rec['m_before']:.3f}) "
              f"alert {alert_before:.3f} -> {alert_after:.3f}"
              f"{' (refractory)' if rec['refractory'] else ''}"
              f"{' [shadow]' if shadow() else ''}", flush=True)
    _emit("ne", "turn", username, persona, status=rec["status"], words=words, s=rec["s"],
          delta=rec["delta"], m_before=rec["m_before"], fired=rec["fired"],
          refractory=rec["refractory"], session_start=rec["session_start"],
          embedding=rec["embedding"], alert_before=round(alert_before, 4), alert=alert_after,
          alerted=is_alert(alert_after), shadow=shadow(), fire_delta=NE_FIRE_DELTA)
    return {**rec, "alert": alert_after, "alerted": is_alert(alert_after), "acting": acting()}


def get_state(username: str, persona: str, now: float = None) -> dict:
    """Current alert (time-decayed) and bookkeeping, without the vector."""
    now = time.time() if now is None else float(now)
    with _LOCK:
        st = _load(username, persona)
    return {"alert": round(_decayed(st["alert"], st["alert_ts"], now), 4),
            "m": st["m"], "seen": int(st["seen"] or 0),
            "refractory": now < float(st["refractory_until"] or 0.0)}


def is_alert(alert) -> bool:
    """Pure predicate, like gaba_state.is_inhibited: is this alert high enough to act on?"""
    if alert is None:
        return False
    return float(alert) >= NE_ALERT_MIN


def alert_active(username: str, persona: str) -> bool:
    """Convenience for the daemon (lab note §7): read the state, apply the predicate.
    Callers still check acting() before holding anything."""
    if _off("NE_OFF"):
        return False
    return is_alert(get_state(username, persona)["alert"])


def half_life_sec() -> float:
    """How long the alert takes to halve with no fires. For logs and tests."""
    return NE_TAU_SEC * math.log(2)


if __name__ == "__main__":
    # Smoke test (works with or without Redis). Writes only ne:_smoke:_test:state
    os.environ.setdefault("TELEMETRY_OFF", "1")   # keep the smoke out of the real sink
    u, p = "_smoke", "_test"
    t0 = time.time()
    a, b = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    for i, v in enumerate([a, a, a, a, a, b]):
        r = on_user_message(u, p, "one two three four five", embedding=v, now=t0 + i * 30)
        print(f"msg {i + 1}: {r['status']:7} s={r['s']} delta={r['delta']} fired={r['fired']} alert={r['alert']}")
    print("state:", get_state(u, p, now=t0 + 150), "| half-life:", round(half_life_sec(), 1), "s",
          "| acting:", acting())
    _MEM_FALLBACK.pop(_key(u, p), None)
    if _RCONN is not None:
        try:
            _RCONN.delete(_key(u, p))
        except Exception:
            pass
