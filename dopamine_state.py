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

# ─── Novelty channel (interest-based drive) ──────────────────
# The third tonic input, and the only one a conversation can open the explore
# gate with. Arithmetic on the other two: tool success asymptotes at +0.30
# (0.15 gain x a geometric RPE series summing to 2.0), social valence at
# +0.048 (0.04 x 0.6 x 2.0) -- so from baseline 0.30 the social channel can
# NEVER reach 0.50, and cold start is exactly baseline. This channel pays when
# the persona learns something it did not already hold: a new deep memory
# whose nearest existing memory (cosine, MiniLM-384) is far away. Measured
# on the live corpus (217 memories, 2026-09-01): nearest-neighbour p10=0.59,
# p25=0.80, p50=0.89; 16% under 0.70, 4% under 0.50. So at these bounds
# roughly one Reflector observation in seven pays anything and most pay
# nothing -- novelty is rare by construction, which is docstring rule 2
# ("predicted novelty is not rewarding") made quantitative.
NOVELTY_SIM_FLOOR = float(os.getenv("DA_NOVELTY_SIM_FLOOR", "0.45"))  # <= : fully novel
NOVELTY_SIM_CEIL = float(os.getenv("DA_NOVELTY_SIM_CEIL", "0.75"))    # >= : predicted, pays 0
NOVELTY_TONIC_GAIN = float(os.getenv("DA_NOVELTY_TONIC_GAIN", "0.12"))
NOVELTY_PHASIC_GAIN = float(os.getenv("DA_NOVELTY_PHASIC_GAIN", "0.30"))
# Leaky bucket: at most this much tonic from novelty per ~tau window. Bounds
# the channel at the same +0.30 the tool channel already asymptotes to, so a
# dense session of genuinely new material tops out near 0.60 -- hyperfocus
# with a ceiling -- and cannot pin the neuron at 1.0.
NOVELTY_BUDGET = float(os.getenv("DA_NOVELTY_BUDGET", "0.30"))


def _key(username: str, persona: str, kind: str) -> str:
    return f"da:{username}:{persona}:{kind}"


def _load(kind: str, username: str, persona: str):
    """Return {'v': float, 'ts': epoch} or None."""
    k = _key(username, persona, kind)
    raw = None
    reachable = False
    if _RCONN is not None:
        try:
            raw = _RCONN.get(k.encode() if isinstance(k, str) else k)
            reachable = True
        except Exception:
            reachable = False
    if raw is None:
        # An absent key and an unreachable Redis are NOT the same event, and the
        # fallback was answering both. _MEM_FALLBACK is a write-through cache
        # that is never invalidated, so a live daemon ignored every DEL/FLUSHDB
        # aimed at it, kept decaying its own in-process copy, and wrote that
        # stale value back over Redis on its next _save. The neuron could not be
        # reset from outside its own process, and a wiped Redis restored stale
        # posture instead of baseline.
        if reachable:
            _MEM_FALLBACK.pop(k, None)   # live Redis says gone; gone is authoritative
            return None
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


def stamp_bonus(phasic: float) -> int:
    """
    Importance points added by DeepMemory.store() for memories born during
    a phasic spike. 0 at rest, up to +2 at full spike.
    """
    p = min(1.0, max(0.0, float(phasic)))
    return int(round(p * 2))


def should_explore(tonic: float) -> bool:
    """Posture gate for the future gap-picker/daemon."""
    return float(tonic) >= EXPLORE_THRESHOLD


def novelty_from_similarity(max_similarity) -> float:
    """Map 'how close is the nearest thing I already know' to novelty in 0..1.
    None (nothing to compare against) is fully novel: a newborn finds
    everything new. At or above NOVELTY_SIM_CEIL the memory was predicted."""
    if max_similarity is None:
        return 1.0
    s = float(max_similarity)
    span = NOVELTY_SIM_CEIL - NOVELTY_SIM_FLOOR
    if span <= 0:
        return 1.0 if s < NOVELTY_SIM_CEIL else 0.0
    return min(1.0, max(0.0, (NOVELTY_SIM_CEIL - s) / span))


def novelty_reward(username: str, persona: str, max_similarity, gain: float = None) -> dict:
    """
    Interest-based drive: pay tonic and phasic DA for learning something new.
    Fired by DeepMemory.store() with the new memory's max cosine similarity to
    the persona's existing memories. Pays once per memory by construction
    (store runs once), is scaled by novelty, and is capped by a leaky bucket
    ('novelty_spent', relaxing to 0 on TONIC_TAU_SEC) so repeated novelty
    cannot pin the neuron. Never dips: an unsurprising memory pays 0.
    """
    gain = NOVELTY_TONIC_GAIN if gain is None else float(gain)
    novelty = novelty_from_similarity(max_similarity)
    with _LOCK:
        spent = _decay_toward_baseline(_load("novelty_spent", username, persona), 0.0, TONIC_TAU_SEC)
        room = max(0.0, NOVELTY_BUDGET - spent)
        paid = min(gain * novelty, room)
        cur_tonic = _decay_toward_baseline(_load("tonic", username, persona), TONIC_BASELINE, TONIC_TAU_SEC)
        cur_phasic = _decay_toward_baseline(_load("phasic", username, persona), 0.0, PHASIC_TAU_SEC)
        if paid > 0.0:
            _save("tonic", username, persona, min(1.0, cur_tonic + paid))
            _save("phasic", username, persona, min(PHASIC_MAX, cur_phasic + NOVELTY_PHASIC_GAIN * novelty))
            _save("novelty_spent", username, persona, spent + paid)
    return {"novelty": round(novelty, 4), "paid": round(paid, 4),
            "spent": round(spent + paid, 4), "budget_left": round(max(0.0, room - paid), 4),
            **get_state(username, persona)}


# ─── Attribution: whose fault was it? ─────────────────────────
# Infrastructure failures carry ZERO information about the agent's competence.
# Getting this wrong means the neuron learns helplessness over other people's
# servers, so the vocabulary here has to match what failures actually LOOK
# like in the wild (CamelCase exception names, provider prose, status codes),
# not what a tidy universe would emit.
import re as _re

# High-confidence infrastructure vocabulary. Multi-word/unambiguous only —
# these phrases are themselves proof of failure. Bare status codes are NOT
# here: "TIMEOUT_503 = 30" in a grep hit must never read as an outage.
_HIGH_CONF_INFRA = _re.compile(
    r"(?:"
    r"bad\s*gateway|service\s*unavailable|temporarily\s*unavailable"
    r"|max\s*retries\s*exceeded|connection\s*(?:reset|refused|aborted|error|closed|failed)"
    r"|connectionreseterror|connectionrefusederror|connectionaborted|newconnectionerror"
    r"|econnreset|epipe|getaddrinfo|gaierror|name\s*resolution\s*fail"
    r"|timed\s*out|read\s*timeout|connect(?:ion)?\s*timeout|request\s*timeout"
    r"|timeout\s*(?:error|exceeded)|deadline\s*exceeded|did\s*not\s*respond"
    r"|too\s*many\s*requests|rate[\s_-]*limit|overloaded|at\s*capacity"
    r"|upstream\s*(?:request\s*)?fail|transient\s*gateway|gateway\s*timeout"
    r"|forcibly\s*closed|ssl\s*error|handshake\s*fail|try\s*again\s*later"
    # --- added after an audit found these scoring as the AGENT's fault ---
    # The list had econnreset/epipe but not the prose forms the same failures
    # print in, and nothing at all for MCP transport death or a dead subprocess.
    # Transport-only. Every term here names a CONNECTION dying, never a remote
    # program misbehaving. An earlier draft of this list also had "process
    # exited", "unreachable", "cannot connect", "quota exceeded", "resource
    # exhausted" and "no response from" — all removed, because mcp_router now
    # wraps the provider's own prose as "Error: MCP tool 'x/y' failed: <body>",
    # so that body gets judged here, and those words describe the agent's own
    # broken code far more often than they describe an outage:
    # "process exited with code 1: SyntaxError" is a bug the agent wrote.
    # Adding them re-created, at the router, the exact misattribution the
    # secure_runner timeout message was just reworded to remove.
    r"|broken\s*pipe|brokenpipeerror|socket\s*hang\s*up"
    r"|server\s*disconnected|disconnected\s*without|remoteprotocolerror"
    r"|is\s*not\s*connected|is\s*offline|could\s*not\s*reconnect"
    r"|proxyerror|cannot\s*connect\s*to\s*proxy"
    r"|resource\s*has\s*been\s*exhausted"
    r")",
    _re.IGNORECASE,
)

# Status codes only REINFORCE a diagnosis once something else signalled failure.
_STATUS_CODE_INFRA = _re.compile(r"\b(?:429|500|502|503|504|522|524)\b")

# Structural shapes our own tools emit on SUCCESS. Checked first so payloads
# about error handling ("server.py:88: raise ConnectionResetError") aren't
# mistaken for the errors they describe.
#
# FIX(json-success-hole): the JSON alternative used to be
#   \{\s*"?(?:status|data|result|files)\b
# which matched the KEY and ignored the VALUE entirely, so
# {"status": "error", "message": "upstream 502"} won outright as a success —
# the agent was rewarded for a failed call. It now requires the key-colon shape
# and refuses when the value is itself an error sentinel.
# Split into two, because they need different treatment. These are OUR tools
# reporting a hit — a grep match, a file-read header, an empty search. Everything
# after the "path:NN:" prefix is quoted FOREIGN text, so it routinely contains
# error vocabulary as payload and must never be judged. These win outright.
_TOOL_OUTPUT_SHAPES = _re.compile(
    r"^(?:\[[^\]]+\]\s+lines\s+\d+|[\w./\\-]+:\d+:|NO MATCHES\s*$)"
)

# A JSON envelope, which unlike the above CAN carry the failure in its payload.
# The old single regex matched the KEY and ignored the VALUE, so
# {"status": "error", "message": "upstream 502"} won outright as a success and
# the agent was rewarded for a failed call. This alternative alone is subject to
# the _JSON_ERROR veto below.
_JSON_OK_SHAPE = _re.compile(
    r"^\{\s*\"?(?:status|data|result|files)\"?\s*:\s*(?!\"?(?:error|fail|false|null))"
)

# Error-shaped JSON. Tool wrappers report failure in the payload rather than in
# prose, and none of that vocabulary ("error" as a bare word, a false status)
# appears in the marker lists below — so without this an error object reads as
# a clean success.
_JSON_ERROR = _re.compile(
    r"\"(?:error|errors|stderr)\"\s*:\s*(?!\s*(?:null|\"\"|\[\s*\]))"
    r"|\"(?:status|result|ok|success)\"\s*:\s*\"?(?:error|fail\w*|false)",
    _re.IGNORECASE,
)

_FAILURE_MARKERS_EXACT = ("ERROR:", "Error:", "⚠️", "[REDACTED", "Failsafe.")
_FAILURE_MARKERS_LOWER = (
    "traceback", "exception", "failed", "failure", "unavailable",
    "refused", "not supported", "blocked", "denied", "invalid",
)


def classify_tool_outcome(text: str) -> str:
    """
    Classify a tool result as 'success' | 'action_fail' | 'infra_fail'.

    Only the FIRST LINE is judged (plus the last line of a traceback, where the
    real exception type lives). Failures announce themselves immediately;
    payloads bury scary vocabulary on line 40. Getting this wrong in either
    direction is expensive: miss an outage and the neuron blames itself for
    someone else's server, over-detect one and real incompetence goes unlearned.
    """
    lines = (text or "").splitlines()
    if not lines:
        return "success"

    # secure_runner and workspace_engine wrap payloads in an envelope line, which
    # matches no shape and no marker — so a sandboxed script that died with a
    # traceback read as a clean success and the agent was rewarded for crashing.
    while lines and lines[0].strip().lower() in (
            "[untrusted_tool_output]", "<untrusted_tool_output>"):
        lines = lines[1:]
    if not lines:
        return "success"

    probe = lines[0][:200]

    # A pretty-printed payload puts nothing but "{" on line one. Widen to reach
    # the first key — but ONLY for the JSON-error test, and never for the infra
    # vocabulary or the failure markers. Those are calibrated for a first line;
    # turned loose on payload interior they read a SUCCESSFUL list_mcp_tools
    # response as an outage the moment any tool's *description* happens to
    # contain a word like "unreachable".
    json_probe = probe
    if probe.strip() in ("{", "[") and len(lines) > 1:
        json_probe = " ".join(l.strip() for l in lines[:3])[:200]
    json_error = bool(_JSON_ERROR.search(json_probe))

    # Structural tool output wins outright, veto or not: everything after the
    # "path:NN:" prefix is quoted foreign text. Vetoing these on their payload
    # made a successful grep of this very file score as an outage, because the
    # comment above mentions {"status": "error"} and a 502.
    if _TOOL_OUTPUT_SHAPES.search(probe.strip()):
        return "success"

    # A JSON envelope is only a success if it is not itself reporting an error.
    if _JSON_OK_SHAPE.search(probe.strip()) and not json_error:
        return "success"

    # Once the payload IS known to be error-shaped, reading a little further to
    # attribute it is safe — we only widen for things already established as
    # failures, never to decide whether something failed.
    if json_error:
        probe = json_probe

    # Tracebacks hide the real cause on the final line.
    if "traceback" in probe.lower() and len(lines) > 1:
        probe = f"{probe} || {lines[-1][:200]}"

    probe_l = probe.lower()
    high_conf_infra = bool(_HIGH_CONF_INFRA.search(probe_l))

    looks_failed = (
        high_conf_infra
        or json_error
        or any(m in probe for m in _FAILURE_MARKERS_EXACT)
        or any(m in probe_l for m in _FAILURE_MARKERS_LOWER)
    )
    if not looks_failed:
        return "success"

    if high_conf_infra or _STATUS_CODE_INFRA.search(probe_l):
        return "infra_fail"
    return "action_fail"


def is_infrastructure_failure(text: str) -> bool:
    """True when a failure string is the world's fault rather than the agent's."""
    return classify_tool_outcome(text) == "infra_fail"


# ─── Convergent RPE inputs ───────────────────────────────────
# Real dopaminergic signaling is a convergence organ: multiple evaluative
# systems (task outcome, social valence) dump into the same pool with
# different weights and timescales.

_EMA_ALPHA = 0.25          # how fast expectations track reality
_TOOL_RPE_GAIN = 1.0       # objective task reward, full weight
_TOOL_TONIC_GAIN = 0.15    # competence must be able to drive exploration
_SOCIAL_RPE_GAIN = 0.6     # social valence moves the pool less per event
_SOCIAL_TONIC_GAIN = 0.04  # mood shifts nudge posture gently
_EMA_TAU_SEC = TONIC_TAU_SEC  # expectations relax toward neutral very slowly


def _update_ema_and_fire(username: str, persona: str, kind: str,
                         observed: float, gain: float,
                         phasic_weight: float = 1.0,
                         tonic_gain: float = 0.05) -> dict:
    """
    Rescorla-Wagner: rpe = observed - expected; expectation then drifts
    toward observed. Positive errors spike phasic DA; negative ones dip it.
    tonic_gain scales how much positive surprise moves the exploration
    posture (tool mastery uses a higher gain than social valence).
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
        new_tonic = min(1.0, max(0.0, cur_tonic + (tonic_gain * rpe if rpe > 0 else 0.0)))

        new_expected = expected + _EMA_ALPHA * (float(observed) - expected)

        _save(kind, username, persona, new_expected)
        _save("phasic", username, persona, new_phasic)
        _save("tonic", username, persona, new_tonic)

    return {"rpe": round(rpe, 4), "expected": round(expected, 4),
            **get_state(username, persona)}


def tool_reward(username: str, persona: str, successes: int,
                action_failures: int, infra_failures: int) -> dict:
    """
    Fast objective channel, with infra attribution: gateway 5xx / rate limits /
    connection failures carry ZERO information about the agent's competence.
    They are excluded from the expectation update and phasic dip entirely —
    a nervous system should not develop learned helplessness over someone
    else's server.

    successes:       tool calls that returned usable results
    action_failures: failures caused by the agent's own choices (bad args,
                     blocked calls, duplicates, missing files it should have found)
    infra_failures:  failures caused by the world (upstream 500s, rate limits)
    """
    s, a, i = max(0, int(successes)), max(0, int(action_failures)), max(0, int(infra_failures))
    total = s + a + i
    if total <= 0:
        return {"rpe": 0.0, "expected": None, **get_state(username, persona), "infra_discounted": False}
    informative = s + a
    if informative == 0:
        # Pure infrastructure failure: the world broke, not the agent.
        return {"rpe": 0.0, "expected": None, **get_state(username, persona), "infra_discounted": True}
    observed = s / informative
    res = _update_ema_and_fire(username, persona, "tool_ema", observed, _TOOL_RPE_GAIN,
                               phasic_weight=informative / total,
                               tonic_gain=_TOOL_TONIC_GAIN)
    res["infra_discounted"] = i > 0
    return res


def social_reward(username: str, persona: str, valence_observed: float) -> dict:
    """
    Slow social channel: emotional valence of the exchange from the user's
    side (0.0..1.0), as judged by the periodic Reflector. Fires RPE against
    the rolling valence expectation — mood *shifts* are the signal, not mood.
    """
    valence = min(1.0, max(0.0, float(valence_observed)))
    return _update_ema_and_fire(username, persona, "valence_ema", valence,
                                _SOCIAL_RPE_GAIN, tonic_gain=_SOCIAL_TONIC_GAIN)


if __name__ == "__main__":
    # Smoke test (works with or without Redis)
    u, p = "_smoke", "_test"
    print("initial:", get_state(u, p))
    print("rpe +0.6:", fire_rpe(u, p, expected=0.2, observed=0.8))
    print("state:", get_state(u, p))
    print("boost:", boost_tonic(u, p, 0.25))
    s = get_state(u, p)
    print("decay_mod:", decay_modulator(s["tonic"]), "| stamp_bonus:", stamp_bonus(s["phasic"]), "| explore:", should_explore(s["tonic"]))
