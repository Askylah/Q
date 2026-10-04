"""
GABA inhibition: the antagonist organ, and the reflector cadence that feeds it.
Covers lab_notes/gaba_inhibition.md sec.3 (mechanism), sec.5 (unit list), sec.6.12 (the
two rules the replay demanded) and sec.7 (the shrug as the redundancy event).

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_gaba_inhibition.py

Exits non-zero on any failure. Safe against a live app: it writes only
gaba:_test:_gaba:* and da:_test:_gaba:* keys and deletes them on exit. It never
touches a real persona, the database, the embedding model or an LLM.

Each check names the failure it exists to prevent.
"""
import os, sys, math, time, json, tempfile, shutil, atexit
os.environ["TELEMETRY_OFF"] = "1"   # fixtures must never land in the real telemetry sink


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")     # the modules live here since 2026-10-03
sys.path.insert(0, BACKEND)
sys.path.insert(1, ROOT)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
# Section [10] imports stream_worker, and that import opens the DB (llm_engine
# -> plugin_manager -> memory_engine._ensure_table). A throwaway file keeps
# "never touches the database" true; the guard runs before any import that can.
TMP = tempfile.mkdtemp(prefix="gaba_inhibition_")
os.environ["PERSONAAPP_DATA_DIR"] = TMP
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
import app_paths
if not os.path.normcase(os.path.realpath(app_paths.DB_PATH)).startswith(
        os.path.normcase(os.path.realpath(TMP)) + os.sep):
    raise SystemExit(f"REFUSING TO RUN: DB path resolved to {app_paths.DB_PATH!r}, "
                     f"outside the temp dir {TMP!r}. This suite would touch a real database.")

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def approx(a, b, eps=1e-3):
    return abs(float(a) - float(b)) <= eps


import gaba_state as gs
import dopamine_state as ds
import reflect_gate as rg

U, P = "_test", "_gaba"


def _wipe():
    for kind in ("inhibition", "streak"):
        k = gs._key(U, P, kind)
        gs._MEM_FALLBACK.pop(k, None)
        if gs._RCONN is not None:
            try:
                gs._RCONN.delete(k)
            except Exception:
                pass
    for kind in ("tonic", "phasic", "novelty_spent", "tool_ema", "valence_ema"):
        k = ds._key(U, P, kind)
        ds._MEM_FALLBACK.pop(k, None)
        if ds._RCONN is not None:
            try:
                ds._RCONN.delete(k)
            except Exception:
                pass


_wipe()

# ── 0. the organ does not know dopamine exists ───────────────────────────
print("\n[0] isolation by construction")
src = open(os.path.join(BACKEND, "gaba_state.py"), encoding="utf-8").read()
check_true("gaba_state.py never imports dopamine_state (rule 1: not negative dopamine)",
           "import dopamine_state" not in src and "from dopamine_state" not in src)
check("cold state is zero inhibition, zero streak", gs.get_state(U, P), {"inhibition": 0.0, "streak": 0})
check("zero inhibition does not hold the gate", gs.is_inhibited(0.0), False)
check("None (organ unavailable) does not hold the gate", gs.is_inhibited(None), False)

# ── 1. the streak curve (sec.3, numbers checked in sec.6.12) ────────────────────
print("\n[1] redundancy streak: accelerating, clamped")
_wipe()
expected_cum = []
acc = 0.0
for k in range(10):
    acc = min(1.0, acc + gs.GABA_BASE * gs.GABA_GROWTH ** k)
    expected_cum.append(acc)
seen = []
for k in range(10):
    r = gs.on_redundant(U, P, nearest=0.9)
    seen.append(r["inhibition"])
check("streak counts every redundant event", gs.get_state(U, P)["streak"], 10)
check_true("first dull event is noise (0.05, well under the gate)",
           approx(seen[0], gs.GABA_BASE) and not gs.is_inhibited(seen[0]))
check_true("cumulative curve matches BASE*GROWTH**k (0.05, 0.125, 0.2375, 0.406, 0.659 ...)",
           all(approx(a, b, 2e-3) for a, b in zip(seen, expected_cum)))
first_shut = next((i + 1 for i, v in enumerate(seen) if gs.is_inhibited(v)), None)
check("gate shuts on the 5th consecutive redundant event (sec.6.12 prediction)", first_shut, 5)
check_true("ten in a row saturates at 1.0, never above (sec.5 headline unit check)",
           approx(seen[-1], 1.0) and max(seen) <= 1.0)
check_true("with tonic held at 0.60 the composed gate reads SHUT (the clock is no longer the only brake)",
           ds.should_explore(0.60) and not (ds.should_explore(0.60) and not gs.is_inhibited(seen[-1])))

# ── 2. the asymmetry (rule 4) ─────────────────────────────────────────────
print("\n[2] novel event: streak resets, inhibition does NOT")
before = gs.get_state(U, P)["inhibition"]
r = gs.on_novel(U, P, nearest=0.4)
check("streak is 0 after one novel event", r["streak"], 0)
check_true("inhibition unchanged at that instant (one shiny thing does not reopen the gate)",
           approx(r["inhibition"], before, 2e-3))
r2 = gs.on_redundant(U, P)
check("the next redundant event restarts the run at streak 1", r2["streak"], 1)

# ── 3. drain ──────────────────────────────────────────────────────────────
print("\n[3] drain: exponential to zero on GABA_TAU_SEC")
_wipe()
gs._save("inhibition", U, P, 0.8)
check_true("no time elapsed: reads back what was written", approx(gs.get_state(U, P)["inhibition"], 0.8, 2e-3))
gs._save("inhibition", U, P, 0.8, ts=time.time() - gs.half_life_sec())
check_true("after TAU*ln2 seconds inhibition has halved (sec.5 drain check)",
           approx(gs.get_state(U, P)["inhibition"], 0.4, 5e-3))
gs._save("inhibition", U, P, 0.8, ts=time.time() - 10 * gs.GABA_TAU_SEC)
check_true("after 10 tau it is effectively zero, never negative",
           0.0 <= gs.get_state(U, P)["inhibition"] < 1e-3)
check_true("drain is slower than phasic and faster than tonic (sits between the two clocks)",
           ds.PHASIC_TAU_SEC < gs.GABA_TAU_SEC < ds.TONIC_TAU_SEC)

# ── 4. stale streak ───────────────────────────────────────────────────────
print("\n[4] a streak older than its TTL is history, not a run")
_wipe()
for _ in range(6):
    gs.on_redundant(U, P)
gs._save("streak", U, P, 6, ts=time.time() - gs.GABA_STREAK_TTL_SEC - 1)
check("stale streak reads as 0", gs.get_state(U, P)["streak"], 0)
r = gs.on_redundant(U, P)
check("a redundant event after a long silence restarts at streak 1 (no giant increment)", r["streak"], 1)
check_true("...and paid only BASE for it", approx(r["increment"], gs.GABA_BASE))

# ── 5. DA isolation at runtime (sec.5) ───────────────────────────────────────
print("\n[5] dopamine state byte-identical across every GABA op")
_wipe()
ds.fire_rpe(U, P, expected=0.2, observed=0.9)
ds.novelty_reward(U, P, 0.3)
ds.social_reward(U, P, 0.8)
DA_KINDS = ("tonic", "phasic", "novelty_spent", "tool_ema", "valence_ema")


def _da_snapshot():
    return json.dumps({k: ds._load(k, U, P) for k in DA_KINDS}, sort_keys=True)


snap = _da_snapshot()
for _ in range(12):
    gs.on_redundant(U, P, nearest=0.95)
gs.on_novel(U, P)
gs.get_state(U, P)
gs.is_inhibited(gs.get_state(U, P)["inhibition"])
check("raw DA entries (value AND timestamp) unchanged after 14 GABA operations", _da_snapshot() == snap, True)
check_true("GABA keys live in their own namespace (gaba:*), never under da:*",
           gs._key(U, P, "inhibition").startswith("gaba:") and not gs._key(U, P, "inhibition").startswith("da:"))

# ── 6. Redis offline -> in-memory fallback, same numbers ──────────────────
print("\n[6] Redis offline: fallback path gives the same curve")
_wipe()
online = [gs.on_redundant(U, P)["inhibition"] for _ in range(5)]
_wipe()
_saved_conn = gs._RCONN
gs._RCONN = None
try:
    offline = [gs.on_redundant(U, P)["inhibition"] for _ in range(5)]
    check_true("identical inhibition sequence with Redis unreachable",
               all(approx(a, b, 1e-6) for a, b in zip(online, offline)))
    check("streak survives in the fallback", gs.get_state(U, P)["streak"], 5)
finally:
    gs._RCONN = _saved_conn
    _wipe()

# ── 7. reflect_gate: the cadence bug and the shrug ────────────────────────
print("\n[7] reflect_gate.plan_reflection")


def ev(t, c="x"):
    return {"type": t, "content": c}


def turn(i):
    return [ev("user_message", f"question {i}"), ev("assistant_response", f"answer {i}")]


T = 5
# 7a. the old bug: threshold met once, then every turn forever
logs = [ev("dense_observation", "summary")]
for i in range(3):
    logs += turn(i)     # 6 raw events since the reflection
plan = rg.plan_reflection(logs, T, similarity=lambda a, b: 0.1)
check("6 new raw events after a reflection, threshold 5 -> reflect", plan["verdict"], "reflect")
logs2 = logs + [ev("dense_observation", "summary 2")] + turn(3)   # reflected, then ONE more turn
plan = rg.plan_reflection(logs2, T, similarity=lambda a, b: 0.1)
check("2 new raw events after the latest reflection -> wait (the every-turn bug)", plan["verdict"], "wait")
check("...and it reports how many it saw", plan["new_events"], 2)

# 7b. cold start: not enough history to compare against -> reflect, no shrug possible
plan = rg.plan_reflection(turn(0) + turn(1) + turn(2), T, similarity=lambda a, b: 0.99)
check("cold start (6 raw, need 10 to compare) reflects even at sim 0.99", plan["verdict"], "reflect")
check("cold start carries no nearest", plan["nearest"], None)

# 7c. the shrug and the go, on the same window
logs = []
for i in range(6):
    logs += turn(i)     # 12 raw events, no reflection ever
calls = []


def sim_recording(a, b):
    calls.append((a, b))
    return 0.93


plan = rg.plan_reflection(logs, T, similarity=sim_recording, sim_ceil=0.85)
check("newest-5 vs prior-5 at 0.93 >= 0.85 -> shrug", plan["verdict"], "shrug")
check_true("shrug reports the similarity it saw", approx(plan["nearest"], 0.93))
check("similarity was asked exactly once (one embedding pass, no LLM call)", len(calls), 1)
check_true("compared chunks are the newest 5 raw events vs the 5 before them (question 5 vs question 3)",
           "question 5" in calls[0][0] and "question 3" in calls[0][1]
           and "question 5" not in calls[0][1] and "question 0" not in calls[0][0])
plan = rg.plan_reflection(logs, T, similarity=lambda a, b: 0.5, sim_ceil=0.85)
check("same window at 0.50 -> reflect (the conversation moved)", plan["verdict"], "reflect")
plan = rg.plan_reflection(logs, T, similarity=lambda a, b: 0.85, sim_ceil=0.85)
check("exactly at the ceiling counts as redundant (>=)", plan["verdict"], "shrug")

# 7d. livelock guard: after a shrug the watermark did not move; a novel turn must still get through
dull = []
for i in range(8):
    dull += turn(0)     # 16 raw events, all "question 0"
fresh = dull + [ev("user_message", "something completely different"), ev("assistant_response", "whoa")]


def sim_by_content(a, b):
    # crude stand-in for the embedding: identical chunks 1.0, a chunk with the new line 0.3
    return 0.3 if "completely different" in a else 1.0


check("dull stretch alone -> shrug", rg.plan_reflection(dull, T, similarity=sim_by_content)["verdict"], "shrug")
check("one novel turn on top of the dull stretch -> reflect (N-vs-N bounds the dilution)",
      rg.plan_reflection(fresh, T, similarity=sim_by_content)["verdict"], "reflect")

# 7e. failing open
plan = rg.plan_reflection(logs, T, similarity=None)
check("no similarity function (gate disabled) -> reflect", plan["verdict"], "reflect")
plan = rg.plan_reflection(logs, T, similarity=lambda a, b: None)
check("model unavailable (None) -> reflect, never shrug on a guess", plan["verdict"], "reflect")


def boom(a, b):
    raise RuntimeError("embedding died")


plan = rg.plan_reflection(logs, T, similarity=boom)
check("similarity raising -> reflect (the pre-check can never take the reflector down)", plan["verdict"], "reflect")

# 7f. threshold 1, and the window sizing that makes larger thresholds comparable
plan = rg.plan_reflection(turn(0) + turn(1), 1, similarity=lambda a, b: 0.9)
check("threshold 1 with 4 raw events can compare and shrug", plan["verdict"], "shrug")
check("window_limit keeps the 20-row prompt for small thresholds", rg.window_limit(5), 20)
check("window_limit grows for large thresholds so 2N raw events fit among daemon rows",
      rg.window_limit(10), 30)
check_true("daemon rows and reflections are not raw events",
           rg.plan_reflection([ev("entropic_gap")] * 30 + [ev("dense_observation")], T,
                              similarity=lambda a, b: 0.0)["verdict"] == "wait")

# ── 8. the composed gate the daemon uses ──────────────────────────────────
print("\n[8] gate composition: tonic says open, inhibition says shut, shut wins")
check("tonic 0.60 / inhibition 0.10 -> open", ds.should_explore(0.60) and not gs.is_inhibited(0.10), True)
check("tonic 0.60 / inhibition 0.60 -> shut", ds.should_explore(0.60) and not gs.is_inhibited(0.60), False)
check("tonic 0.40 / inhibition 0.00 -> shut (dopamine still has its own say)",
      ds.should_explore(0.40) and not gs.is_inhibited(0.00), False)

# ── 9. graded boredom (2026-09-13): flat windows push harder than mild ones ──
print("\n[9] graded redundancy: intensity scales the increment, not the streak")
check("intensity at the ceiling is 0", rg.boredom_intensity(0.85, 0.85), 0.0)
check("intensity at cosine 1.0 is 1", rg.boredom_intensity(1.0, 0.85), 1.0)
check_true("halfway between ceiling and 1.0 is 0.5", approx(rg.boredom_intensity(0.925, 0.85), 0.5, 1e-9))
check("below the ceiling clips to 0", rg.boredom_intensity(0.5, 0.85), 0.0)
check("no similarity -> None", rg.boredom_intensity(None, 0.85), None)
plan = rg.plan_reflection(sum((turn(i) for i in range(5)), []), T, similarity=lambda a, b: 0.97, sim_ceil=0.85)
check("plan_reflection reports intensity on a shrug", round(plan["intensity"], 3), 0.8)
check("weight: None -> 1.0 (the old fixed step)", gs.grade_weight(None), 1.0)
check("weight: 0 -> GRADE_MIN", gs.grade_weight(0.0), gs.GABA_GRADE_MIN)
check("weight: 1 -> GRADE_MAX", gs.grade_weight(1.0), gs.GABA_GRADE_MAX)
_wipe()
r_mild = gs.on_redundant(U, P, nearest=0.86, intensity=0.0)
check_true("first mild shrug pays BASE * GRADE_MIN", approx(r_mild["increment"], gs.GABA_BASE * gs.GABA_GRADE_MIN, 1e-6))
check("...and still advances the streak by one", r_mild["streak"], 1)
_wipe()
r_flat = gs.on_redundant(U, P, nearest=1.0, intensity=1.0)
check_true("first flat shrug pays BASE * GRADE_MAX", approx(r_flat["increment"], gs.GABA_BASE * gs.GABA_GRADE_MAX, 1e-6))
_wipe()
r_gap = gs.on_redundant(U, P, source="daemon_gap")
check_true("daemon_gap (no intensity) pays the old fixed BASE", approx(r_gap["increment"], gs.GABA_BASE, 1e-6))
_wipe()
mild5 = [gs.on_redundant(U, P, intensity=0.1)["inhibition"] for _ in range(5)][-1]
_wipe()
flat5 = [gs.on_redundant(U, P, intensity=1.0)["inhibition"] for _ in range(5)][-1]
check_true("five flat windows > five mildly-dull windows (they used to be identical)", flat5 > mild5)
check_true("five mild windows do NOT close the gate", not gs.is_inhibited(mild5))
check_true("five flat windows DO close the gate", gs.is_inhibited(flat5))
_wipe()
seq = [gs.on_redundant(U, P, intensity=1.0) for _ in range(5)]
check("exactly one event reports crossing the gate threshold", sum(1 for r in seq if r["crossed"]), 1)

# ── 10. gate edges (stream_worker.gate_edge is pure) ─────────────────────
print("\n[10] gate edge detection: who closed it, how long was it open")
try:
    import stream_worker as sw
    st, e = sw.gate_edge(None, True, True, False, 100.0)
    check("first sighting emits nothing", e, None)
    st, e = sw.gate_edge(st, True, True, False, 110.0)
    check("no change -> no edge", e, None)
    st, e = sw.gate_edge(st, False, True, True, 130.0)
    check("closed while tonic ok and gaba shut -> closed_by gaba", e["closed_by"], "gaba")
    check("...with the open duration", e["open_for_s"], 30.0)
    st, e = sw.gate_edge(st, True, True, False, 140.0)
    check("reopening -> gate_open", e["event"], "gate_open")
    st, e = sw.gate_edge(st, False, False, False, 150.0)
    check("closed by tonic alone", e["closed_by"], "tonic")
    st, e = sw.gate_edge({"open": True, "since": 0.0}, False, False, True, 5.0)
    check("closed by both", e["closed_by"], "both")
except ImportError as _sw_err:
    print(f"  SKIP  stream_worker not importable here: {_sw_err}")

_wipe()
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
