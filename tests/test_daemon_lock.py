"""
Daemon single-instance lock: TTL derivation, holder liveness, and the
dopamine_state fallback. Covers lab_notes/entropic_gap_livelock.md 39-42.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_daemon_lock.py

Exits non-zero on any failure. Safe to run against a live app: it touches only
q:test:* keys in Redis, never q:daemon:* or da:*, and its pid probes are
read-only by construction -- see _proc_probe, which does NOT use os.kill on
Windows because CPython implements that via TerminateProcess.

Each check names the failure it exists to prevent. Three of these shipped
broken at least once.
"""
import os, sys, threading, time, types

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


import stream_worker as sw
import dopamine_state as ds

# ── 0. the lock is a timer now, not a forecast ─────────────────────────────
print("\n[0] lock presence constants")
check_true("ttl comfortably exceeds the renewal period",
           sw.LOCK_TTL_SECS >= 3 * sw.LOCK_RENEW_SECS)
check_true("renewal is far shorter than any measured cycle (548-653s)",
           sw.LOCK_RENEW_SECS < 60)
check_true("_derive_ttls is gone -- lock ttl no longer predicted",
           not hasattr(sw, "_derive_ttls"))

# ── 1. staleness threshold vs the measured cycle table ─────────────────────
print("\n[1] _derive_stale_ttl(observed, interval=60)")
for obs, label in [(0.0, "cold boot"), (60.0, "gated idle"), (321.0, "cap-10 era"),
                   (548.0, "note 35 low"), (577.0, "note 35 high"),
                   (652.8, "measured 08-31"), (1008.0, "cap-1000")]:
    st = sw._derive_stale_ttl(obs, 60)
    print(f"  obs={obs:7.1f} ({label:14s}) -> stale_ttl={st:5d}  ({st/max(obs,60.0):.2f}x cycle)")

check("floor preserved at cold boot", sw._derive_stale_ttl(0.0, 60), 300)
check("floor preserved at idle", sw._derive_stale_ttl(60.0, 60), 300)
check("652.8s widens the threshold", sw._derive_stale_ttl(652.8, 60), 2611)
check("ceiling clamps a runaway", sw._derive_stale_ttl(99999.0, 60), sw.TTL_CEILING)
check("garbage -> floor", sw._derive_stale_ttl("nonsense", 60), 300)
check("None -> floor", sw._derive_stale_ttl(None, 60), 300)
for obs in (548.0, 577.0, 652.8):
    check_true(f"threshold outlives a {obs:.0f}s cycle (old 300s did not)",
               sw._derive_stale_ttl(obs, 60) > obs)

# ── 2. _observe_cycle ──────────────────────────────────────────────────────
print("\n[2] _observe_cycle")
sw.DAEMON_CYCLE_KEY = b"q:test:s7:cycle_secs"      # never touch the live key

w = sw.ConsciousnessWorker.__new__(sw.ConsciousnessWorker)
clock = [1000.0]
sw.time = types.SimpleNamespace(time=lambda: clock[0], sleep=time.sleep)
try:
    w._observe_cycle(60)
    check("first cycle has no measurement yet", getattr(w, "_observed_cycle", None), None)
    check("first cycle gets the floor threshold", w._stale_ttl, 300)
    check_true("_observe_cycle no longer touches the lock ttl",
               not hasattr(w, "_lock_ttl"))

    clock[0] += 652.8
    w._observe_cycle(60)
    check("652.8s cycle measured", round(w._observed_cycle, 1), 652.8)
    check("652.8s cycle widened the threshold", w._stale_ttl, 2611)

    clock[0] += 60.0
    w._observe_cycle(60)
    check("idle does not collapse the estimate", round(w._observed_cycle, 2), 587.52)
    check("threshold narrows only 10%", w._stale_ttl, 2350)

    for _ in range(30):
        clock[0] += 60.0
        w._observe_cycle(60)
    check_true("sustained idle decays toward the floor", w._stale_ttl < 500)
    check_true("but never below the floor", w._stale_ttl >= 300)

    clock[0] += 900.0
    w._observe_cycle(60)
    check("one slow cycle re-widens immediately", w._stale_ttl, 3600)
finally:
    sw.time = sys.modules["time"]

try:
    import redis_client as rc
    if rc.is_active():
        conn = rc.get_connection()
        check("published the observation to redis", conn.get(sw.DAEMON_CYCLE_KEY), b"900.0")
        check_true("published with a ttl", conn.ttl(sw.DAEMON_CYCLE_KEY) > 86000)
        conn.delete(sw.DAEMON_CYCLE_KEY)
    else:
        print("  SKIP  redis inactive")
except Exception as e:
    print(f"  SKIP  redis publication check: {e}")

# ── 3. the renewer thread ──────────────────────────────────────────────────
print("\n[3] _lock_renewer")
saved_period = sw.LOCK_RENEW_SECS
sw.LOCK_RENEW_SECS = 0.05
try:
    # happy path: renews repeatedly for as long as it is not stopped
    r = sw.ConsciousnessWorker.__new__(sw.ConsciousnessWorker)
    r.running = True
    calls = []
    r._renew_lock = lambda: calls.append(time.time())
    stop = threading.Event()
    t = threading.Thread(target=r._lock_renewer, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.6)
    stop.set()
    t.join(timeout=2)
    check_true(f"renewed repeatedly on its own timer ({len(calls)} calls)", len(calls) >= 5)
    check_true("stopped when asked", not t.is_alive())
    check_true("did not stop the daemon", r.running)

    # a real handoff: losing the lock stops the daemon
    r2 = sw.ConsciousnessWorker.__new__(sw.ConsciousnessWorker)
    r2.running = True

    def _lost():
        raise RuntimeError("lock lost to another instance")

    r2._renew_lock = _lost
    stop2 = threading.Event()
    t2 = threading.Thread(target=r2._lock_renewer, args=(stop2,), daemon=True)
    t2.start()
    t2.join(timeout=2)
    check_true("lock loss ends the renewer", not t2.is_alive())
    check("lock loss stops the daemon", r2.running, False)

    # a transient redis hiccup must NOT stop the brainstem
    r3 = sw.ConsciousnessWorker.__new__(sw.ConsciousnessWorker)
    r3.running = True
    hiccups = []

    def _hiccup():
        hiccups.append(1)
        raise ConnectionError("redis blip")

    r3._renew_lock = _hiccup
    stop3 = threading.Event()
    t3 = threading.Thread(target=r3._lock_renewer, args=(stop3,), daemon=True)
    t3.start()
    time.sleep(0.4)
    alive = t3.is_alive()
    stop3.set()
    t3.join(timeout=2)
    check_true(f"survives transient redis errors ({len(hiccups)} of them)", alive)
    check("a redis blip does not stop the daemon", r3.running, True)
finally:
    sw.LOCK_RENEW_SECS = saved_period

# ── 4. lock identity ───────────────────────────────────────────────────────
print("\n[4] lock identity")
me = os.getpid()
my_ctime = sw._proc_probe(me)[1]
mine = sw._lock_value("deadbeefcafe")
check("own lock value parses back to own pid", sw._parse_lock_val(mine)[0], me)
check("own lock value carries create time", sw._parse_lock_val(mine)[1], my_ctime)
check_true("own lock reads as alive", sw._holder_is_alive(mine))
check("legacy bare boot_id -> unidentifiable", sw._parse_lock_val(b"5fa9c69a9aa8"), (None, None))
check_true("legacy holder is left alone", sw._holder_is_alive(b"5fa9c69a9aa8"))
check("empty lock -> unidentifiable", sw._parse_lock_val(None), (None, None))
check("garbage lock -> unidentifiable", sw._parse_lock_val(b"::::"), (None, None))
check_true("garbage holder is left alone", sw._holder_is_alive(b"garbage:stuff:here"))
check_true("dead holder is evictable", not sw._holder_is_alive(b"999999:123456789:abc"))
check_true("dead holder with no ctime is evictable", not sw._holder_is_alive(b"999999::abc"))
check_true("recycled pid is evictable", not sw._holder_is_alive(f"{me}:1:abc".encode()))
check_true("live pid with unknown ctime is NOT evicted", sw._holder_is_alive(f"{me}::abc".encode()))

import subprocess
p = subprocess.Popen([sys.executable, "-c", "pass"])
p.wait()
time.sleep(0.5)
check_true("a just-exited process is evictable",
           not sw._holder_is_alive(f"{p.pid}:{my_ctime}:abc".encode()))

# A probe must be strictly read-only. The obvious implementation of "does this
# pid exist" -- os.kill(pid, 0) -- TERMINATES the process on Windows, where
# CPython routes os.kill through TerminateProcess. This check exists to make
# that regression loud, because its symptom would be the daemon dying whenever
# a replacement looked at it.
#
# It deliberately does NOT probe the live daemon: writing any .py under this
# repo triggers a uvicorn reload, so the pid in q:daemon:lock is quite possibly
# a corpse by the time this suite runs. That is expected behaviour, not a fault.
probe_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"])
try:
    survived = all(sw._proc_probe(probe_child.pid)[0] for _ in range(50))
    check_true("50 probes did not kill a live process", survived)
    check("the probed process is still running", probe_child.poll(), None)
finally:
    probe_child.terminate()
    probe_child.wait(timeout=5)
check_true("and it becomes evictable once it really exits",
           not sw._holder_is_alive(f"{probe_child.pid}:{my_ctime}:abc".encode()))

# ── 5. dopamine_state._load ────────────────────────────────────────────────
print("\n[5] dopamine_state._load")


class FakeRedis:
    def __init__(self, store=None, blow_up=False):
        self.store, self.blow_up = (store or {}), blow_up

    def get(self, k):
        if self.blow_up:
            raise ConnectionError("redis down")
        return self.store.get(k)

    def set(self, k, v, ex=None):
        if self.blow_up:
            raise ConnectionError("redis down")
        self.store[k] = v


saved_conn, saved_fb = ds._RCONN, dict(ds._MEM_FALLBACK)
try:
    key = ds._key("u", "rick", "tonic")

    ds._RCONN = FakeRedis({key.encode(): b'{"v": 0.9, "ts": 1.0}'})
    ds._MEM_FALLBACK.clear()
    check("live redis value is read", ds._load("tonic", "u", "rick"), {"v": 0.9, "ts": 1.0})

    ds._save("tonic", "u", "rick", 0.9)
    check_true("save populated the in-process cache", key in ds._MEM_FALLBACK)
    ds._RCONN.store.pop(key.encode(), None)              # the DEL
    check("live redis + absent key -> None, not the stale copy",
          ds._load("tonic", "u", "rick"), None)
    check_true("stale copy evicted so it cannot come back later", key not in ds._MEM_FALLBACK)
    check("get_state relaxes to baseline",
          ds.get_state("u", "rick")["tonic"], round(ds.TONIC_BASELINE, 4))
    check("and the explore gate closes",
          ds.should_explore(ds.get_state("u", "rick")["tonic"]), False)

    ds._MEM_FALLBACK.clear()
    ds._RCONN = FakeRedis({})
    ds._save("tonic", "u", "rick", 0.85)
    ds._RCONN.blow_up = True
    got = ds._load("tonic", "u", "rick")
    check("unreachable redis still degrades to the in-process copy",
          got is not None and round(got["v"], 2), 0.85)

    ds._RCONN = None
    ds._MEM_FALLBACK.clear()
    ds._save("tonic", "u", "rick", 0.7)
    got = ds._load("tonic", "u", "rick")
    check("no-redis mode still uses the in-process copy",
          got is not None and round(got["v"], 2), 0.7)
finally:
    ds._RCONN = saved_conn
    ds._MEM_FALLBACK.clear()
    ds._MEM_FALLBACK.update(saved_fb)

# -- 6. F6: the monologue idempotency mark is independent of the fan-out ----
# Note 14.1 / F6. Before the fix the mark was the last two statements of the
# same try that wraps the LLM call and the entire write fan-out, so anything
# raising in between skipped both durability layers and re-armed the monologue
# on the next cycle. Measured on the live DB before the fix: 87 of 106
# Reflection nodes (82%) were redundant fires against an already-processed
# triple, 13 of Sky/v's inter-node gaps under 90s -- one refire per cycle.
print()
print("[6] monologue idempotency mark (note 14.1, F6)")
import ast

_src = open(os.path.join(ROOT, "stream_worker.py"), "rb").read().decode("utf-8")
_fn = next(n for n in ast.walk(ast.parse(_src))
           if isinstance(n, ast.FunctionDef) and n.name == "generate_idle_monologue")
_tries = [n for n in _fn.body if isinstance(n, ast.Try)]
check("exactly one top-level try in generate_idle_monologue", len(_tries), 1)
_t = _tries[0]
_fin = ast.dump(ast.Module(body=_t.finalbody, type_ignores=[]))
_body = ast.dump(ast.Module(body=_t.body, type_ignores=[]))
check_true("the mark is written from a finally", "_mark_monologue_processed" in _fin)
check_true("the mark is NOT left in the try body -- that was the bug",
           "_mark_monologue_processed" not in _body)
check_true("no raw last_monologue_time assignment survives in the try body",
           "last_monologue_time" not in _body)
check_true("bulk_append_to_zettel_cache -- the one fan-out call that can "
           "propagate -- is guarded",
           any(isinstance(n, ast.Try) and n.handlers
               and "bulk_append_to_zettel_cache" in ast.dump(n)
               for n in ast.walk(_t)))

_U, _P, _TS = "F6Probe", "probe", "2026-08-31 17:00:00.000001"
_KEY = "daemon:last_monologue:%s:%s" % (_U, _P)
import redis_client as _rc
_saved_rc = (_rc.is_active, _rc.set_val)
try:
    _writes = {}
    _rc.is_active = lambda: True
    _rc.set_val = lambda k, v, ex=None: _writes.__setitem__(k, v) or True

    _w = types.SimpleNamespace(last_monologue_time={})
    sw.ConsciousnessWorker._mark_monologue_processed(_w, _U, _P, _TS)
    check("in-process mark written", _w.last_monologue_time.get((_U, _P)), _TS)
    check("redis mirror written under the key the idle gate reads",
          _writes.get(_KEY), _TS.encode("utf-8"))
    check("the faked writer saw exactly one key -- no live daemon:* was touched",
          sorted(_writes), [_KEY])

    def _boom(*a, **k):
        raise RuntimeError("redis down")
    _rc.set_val = _boom
    _w2 = types.SimpleNamespace(last_monologue_time={})
    sw.ConsciousnessWorker._mark_monologue_processed(_w2, _U, _P, _TS)
    check("an unreachable redis does not propagate out of the mark",
          _w2.last_monologue_time.get((_U, _P)), _TS)

    # the property the whole fix exists for
    _rc.set_val = lambda k, v, ex=None: True
    class _Stand:
        last_monologue_time = {}
        _mark_monologue_processed = sw.ConsciousnessWorker._mark_monologue_processed
        def run(self, u, p, ts):
            try:
                raise RuntimeError("simulated fan-out failure")
            except Exception:
                pass
            finally:
                self._mark_monologue_processed(u, p, ts)
    _Stand().run(_U, "probe2", _TS)
    check("a fan-out that raises still arms the guard exactly once",
          _Stand.last_monologue_time.get((_U, "probe2")), _TS)
finally:
    _rc.is_active, _rc.set_val = _saved_rc


# -- 7. the monologue must not persist a truncated stub --------------------
# Note 47. 58 of 106 Reflection nodes were 56-97 chars and cut MID-WORD, against
# 48 real bodies of 1682-2140 with nothing in between. Those fragments were
# written permanently to zettel_nodes and then fed back to the gap picker and the
# NLI sweep as INPUT -- the writer poisoning the corpus it reads.
print()
print("[7] monologue stub floor (note 47)")

check_true("stream_worker exposes a floor", hasattr(sw, "MIN_MONOLOGUE_CHARS"))
check_true("the floor sits between the two MEASURED populations -- above every "
           "observed stub (97) and far below the smallest real body (1682)",
           97 < sw.MIN_MONOLOGUE_CHARS < 1682)

_gm = next(n for n in ast.walk(ast.parse(_src))
           if isinstance(n, ast.FunctionDef) and n.name == "generate_idle_monologue")

# the guard: len(monologue_text) < MIN_MONOLOGUE_CHARS  ->  monologue_text = ""
_guards = [n for n in ast.walk(_gm)
           if isinstance(n, ast.If) and "MIN_MONOLOGUE_CHARS" in ast.dump(n.test)]
check("exactly one stub guard", len(_guards), 1)
_guard = _guards[0]
check_true("a body under the floor is discarded, not written",
           any(isinstance(s, ast.Assign)
               and isinstance(s.value, ast.Constant) and s.value.value == ""
               and any(getattr(t, "id", None) == "monologue_text" for t in s.targets)
               for s in ast.walk(_guard)))

_writes = [n.lineno for n in ast.walk(_gm)
           if isinstance(n, ast.Call)
           and getattr(n.func, "attr", None) in ("add_zettel_entry", "add_zettel_node")]
check_true("the guard runs BEFORE the first durable write",
           _writes and _guard.lineno < min(_writes))

_gm_src = ast.get_source_segment(_src, _gm) or ""
check("max_tokens raised off the value that starved the content",
      ("max_tokens=1000" in _gm_src, "max_tokens=4000" in _gm_src), (False, True))
check_true("the provider's token usage is captured, so the next session can "
           "confirm the reasoning-budget hypothesis with numbers",
           "_usage" in _gm_src and 'res.get("usage")' in _gm_src)
check_true("and it is reported when a stub is rejected",
           "usage={_usage!r}" in _gm_src)

# ── 8. daemon model selection (session 9) ──────────────────────────────────
print()
print("[8] daemon model selection (session 9)")

check_true("gemini-2.5-flash is gone from the daemon", "gemini-2.5-flash" not in _src)
check("NLI gate on OpenRouter gets the smartest flash, not the cheapest",
      sw.DAEMON_NLI_MODEL_OPENROUTER, "google/gemini-3.5-flash")
check("monologue on OpenRouter gets 3.7-flash",
      sw.DAEMON_MONOLOGUE_MODEL_OPENROUTER, "google/gemini-3.7-flash")
check("the Vertex id did not move -- that route was never the problem (note 28)",
      sw.DAEMON_MODEL_VERTEX, "google/gemini-3-flash-preview")

_saved_env = os.environ.pop("DAEMON_NLI_MODEL", None)
check("no OpenRouter key -> Vertex id (matches llm_engine.has_openrouter_key)",
      sw._daemon_model("DAEMON_NLI_MODEL", "or-default", {"openrouter": ""}), sw.DAEMON_MODEL_VERTEX)
check("OpenRouter key present -> the OpenRouter default",
      sw._daemon_model("DAEMON_NLI_MODEL", "or-default", {"openrouter": "sk-or-x"}), "or-default")
os.environ["DAEMON_NLI_MODEL"] = "  z-ai/glm-5.3-flash  "
check("an env override wins on either route, whitespace stripped",
      (sw._daemon_model("DAEMON_NLI_MODEL", "or-default", {"openrouter": ""}),
       sw._daemon_model("DAEMON_NLI_MODEL", "or-default", {"openrouter": "sk-or-x"})),
      ("z-ai/glm-5.3-flash", "z-ai/glm-5.3-flash"))
os.environ["DAEMON_NLI_MODEL"] = "   "
check("a blank override is not an override",
      sw._daemon_model("DAEMON_NLI_MODEL", "or-default", {"openrouter": "sk-or-x"}), "or-default")
if _saved_env is None:
    os.environ.pop("DAEMON_NLI_MODEL", None)
else:
    os.environ["DAEMON_NLI_MODEL"] = _saved_env

_nli = next(n for n in ast.walk(ast.parse(_src))
            if isinstance(n, ast.FunctionDef) and n.name == "call_nli_gate")
_nli_src = ast.get_source_segment(_src, _nli) or ""
check_true("call_nli_gate selects through the resolver with its own env name",
           '_daemon_model("DAEMON_NLI_MODEL", DAEMON_NLI_MODEL_OPENROUTER' in _nli_src)
check_true("generate_idle_monologue selects through the resolver with its own env name",
           '_daemon_model("DAEMON_MONOLOGUE_MODEL", DAEMON_MONOLOGUE_MODEL_OPENROUTER' in _gm_src)
check_true("no daemon call site hardcodes a model id any more",
           'model_id="google/' not in _nli_src and 'model_id="google/' not in _gm_src)

# ── 9. idle monologue rest gate (session 9: the rumination loop) ──────────
print()
print("[9] idle monologue rest gate (session 9)")

# re-read: sections 6-8 parsed the file before this session's edit
_src = open(os.path.join(ROOT, "stream_worker.py"), "rb").read().decode("utf-8")
_tree = ast.parse(_src)
_gm = next(n for n in ast.walk(_tree)
           if isinstance(n, ast.FunctionDef) and n.name == "generate_idle_monologue")
_gm_src = ast.get_source_segment(_src, _gm) or ""

check_true("the intake-form prompt is gone from the daemon",
           "Reflect on your current state of existence" not in _src)
check_true("generate_idle_monologue takes the posture",
           "exploring" in [a.arg for a in _gm.args.args])
_calls = [n for n in ast.walk(_tree) if isinstance(n, ast.Call)
          and getattr(n.func, "attr", None) == "generate_idle_monologue"]
check("exactly one call site", len(_calls), 1)
check_true("and it passes the cycle's own _exploring",
           any(k.arg == "exploring" and getattr(k.value, "id", None) == "_exploring"
               for k in _calls[0].keywords))

_gate = next((n for n in ast.walk(_gm) if isinstance(n, ast.If)
              and "exploring" in ast.dump(n.test) and "idle" in ast.dump(n.test)), None)
check_true("a rest gate exists on (idle and not exploring)", _gate is not None)
if _gate is not None:
    _gate_calls = [getattr(n.func, "attr", None) for n in ast.walk(_gate) if isinstance(n, ast.Call)]
    check_true("the rest gate still arms the idempotency mark (note 45) before leaving",
               "_mark_monologue_processed" in _gate_calls
               and any(isinstance(s, ast.Return) for s in _gate.body))
    _llm_line = min(n.lineno for n in ast.walk(_gm) if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "call_llm")
    _db_line = min((n.lineno for n in ast.walk(_gm) if isinstance(n, ast.Call)
                    and getattr(n.func, "attr", None) == "get_db_connection"), default=10**9)
    check_true("the gate sits before any DB or LLM work",
               _gate.lineno < _llm_line and _gate.lineno < _db_line)

_write_if = next((n for n in ast.walk(_gm) if isinstance(n, ast.If)
                  and "monologue_text" in ast.dump(n.test) and "idle" in ast.dump(n.test)), None)
check_true("idle output takes its own write branch", _write_if is not None)
if _write_if is not None:
    _body_calls = {getattr(n.func, "attr", None) for s in _write_if.body for n in ast.walk(s)
                   if isinstance(n, ast.Call)}
    _else_calls = {getattr(n.func, "attr", None) for s in _write_if.orelse for n in ast.walk(s)
                   if isinstance(n, ast.Call)}
    check_true("an idle reflection is NOT written to the graph (no entry, no node, no link)",
               not ({"add_zettel_node", "add_zettel_entry", "add_zettel_link"} & _body_calls))
    check_true("but it is kept as an observation for the Reflector",
               "add_observation" in _body_calls)
    check_true("gap and conflict monologues still go to the graph",
               "add_zettel_node" in _else_calls and "add_zettel_link" in _else_calls)

# functional: gate shut + nothing to integrate -> no work, mark armed, LLM never called
_w = sw.ConsciousnessWorker.__new__(sw.ConsciousnessWorker)
_marks = []
_w._mark_monologue_processed = lambda u, p, t: _marks.append((u, p, t))
def _db_touched(*a, **k):
    raise RuntimeError("DB touched")
_w.get_db_connection = _db_touched
_orig_call = sw.llm_engine.call_llm
def _llm_touched(*a, **k):
    raise AssertionError("LLM touched")
sw.llm_engine.call_llm = _llm_touched
try:
    _ret = _w.generate_idle_monologue("_t", "_p", "2000-01-01 00:00:00", None, None, exploring=False)
    check("rest: returns nothing", _ret, None)
    check("rest: the mark is armed exactly once with the user timestamp",
          _marks, [("_t", "_p", "2000-01-01 00:00:00")])
    # a gap with the gate shut is NOT rest -- it must proceed (and hit our DB stub)
    _passed = False
    try:
        _w.generate_idle_monologue("_t", "_p", "2000-01-01 00:00:00",
                                   {"node": {"id": "x", "title": "t", "content": "c"}}, None,
                                   exploring=False)
    except RuntimeError as e:
        _passed = "DB touched" in str(e)
    check_true("a gap with the gate shut still proceeds (the gate is for idle only)", _passed)
finally:
    sw.llm_engine.call_llm = _orig_call


print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
sys.exit(1 if FAILS else 0)
