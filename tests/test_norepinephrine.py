"""
test_norepinephrine.py -- standalone suite for backend/norepinephrine_state.py.

lab_notes/norepinephrine.md §5 (verification plan). Every check names the failure it prevents.
Fake unit vectors stand in for embeddings (explicit `embedding=`, explicit `now=`), so this
needs no model, no LLM and no app. Writes only ne:_t_ne_*:* keys and deletes them.

    PYTHONPATH="backend;." python tests/test_norepinephrine.py
"""
import os, sys, math, ast, types

os.environ["TELEMETRY_OFF"] = "1"        # fixtures must never land in the real telemetry sink
for _v in ("NE_OFF", "NE_SHADOW"):
    os.environ.pop(_v, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(1, ROOT)
sys.path.insert(2, os.path.join(ROOT, "labs"))

import norepinephrine_state as ne       # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def approx(a, b, tol=1e-3):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


USERS = []


def user(tag):
    u = f"_t_ne_{tag}"
    USERS.append(u)
    _wipe_one(u)
    return u


def _wipe_one(u):
    k = ne._key(u, "p")
    ne._MEM_FALLBACK.pop(k, None)
    if ne._RCONN is not None:
        try:
            ne._RCONN.delete(k)
        except Exception:
            pass


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def basis(i, dim=8):
    v = [0.0] * dim
    v[i] = 1.0
    return v


def toward(conv, other, cos):
    """A unit vector at exactly `cos` to `conv`, leaning toward `other` (orthogonal part)."""
    c = unit(conv)
    o = [x - sum(a * b for a, b in zip(other, c)) * y for x, y in zip(other, c)]
    o = unit(o)
    s = math.sqrt(max(0.0, 1 - cos * cos))
    return [cos * a + s * b for a, b in zip(c, o)]


TXT = "five words in this message"
T0 = 1_800_000_000.0
A, B, C = basis(0), basis(1), basis(2)

# ── 0. rule 1: the organ does not know the other two exist ──────────────────
print("\n[0] isolation")
src = open(ne.__file__, encoding="utf-8").read()
imported = set()
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Import):
        imported |= {a.name for a in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module)
check("never imports dopamine_state (rule 1)", "dopamine_state" in imported, False)
check("never imports gaba_state (rule 1)", "gaba_state" in imported, False)
try:
    import dopamine_state as ds
    import gaba_state as gs
    u = user("iso")
    da0, ga0 = ds.get_state(u, "p"), gs.get_state(u, "p")
    for i, v in enumerate([A, A, A, A, B]):
        ne.on_user_message(u, "p", TXT, embedding=v, now=T0 + i * 30)
    check("dopamine state untouched by NE ops", ds.get_state(u, "p") == da0, True)
    check("GABA state untouched by NE ops", gs.get_state(u, "p") == ga0, True)
except ImportError as e:
    print(f"  SKIP  organs not importable here: {e}")

# ── 1. silent at rest ───────────────────────────────────────────────────────
print("\n[1] silent at rest")
u = user("rest")
check("a fresh account reads alert 0", ne.get_state(u, "p", now=T0)["alert"], 0.0)
check("alert_active is False at rest", ne.alert_active(u, "p"), False)
check("is_alert(0) is False", ne.is_alert(0.0), False)
check("is_alert(None) is False", ne.is_alert(None), False)

# ── 2. seed -> warmup -> evaluate (same order as ne_calibrate.replay) ──────
print("\n[2] seed, warmup, evaluate")
u = user("seq")
r1 = ne.on_user_message(u, "p", TXT, embedding=A, now=T0)
r2 = ne.on_user_message(u, "p", TXT, embedding=toward(A, B, 0.9), now=T0 + 30)
r3 = ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + 60)
check("message 1 seeds CONV and is never scored", (r1["status"], r1["s"]), ("seed", None))
check("message 2 is warm-up: scored, sets M, cannot fire", (r2["status"], r2["fired"]), ("warmup", False))
check_true("warm-up s is 1 - cos (0.1 for a 0.9-cosine message)", approx(r2["s"], 0.1))
check("message 3 is the first evaluated", r3["status"], "eval")
check_true("evaluated turns carry delta = s - M", approx(r3["delta"], r3["s"] - r3["m_before"]))

# ── 3. short messages are skipped but still count as presence ───────────────
print("\n[3] short messages")
u = user("short")
for i in range(3):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
before = ne.get_state(u, "p", now=T0 + 90)
r = ne.on_user_message(u, "p", "lol ok", embedding=B, now=T0 + 6000)
after = ne.get_state(u, "p", now=T0 + 6000)
check("under NE_MIN_WORDS -> status short", r["status"], "short")
check("a short message moves neither M nor SEEN", (after["m"], after["seen"]), (before["m"], before["seen"]))
r = ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + 6000 + 3000)
check("...but it refreshes LAST_TS: 9000 s after the last LONG message is no new session",
      r["session_start"], False)

# ── 4. a long gap is a new session ─────────────────────────────────────────
print("\n[4] session gap")
u = user("gap")
for i in range(4):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
r = ne.on_user_message(u, "p", TXT, embedding=B, now=T0 + 90 + ne.NE_SESSION_GAP_SEC + 1)
check("over NE_SESSION_GAP_SEC -> session_start and a fresh seed (no fire on a new day's topic)",
      (r["session_start"], r["status"], r["fired"]), (True, "seed", False))

# ── 5. the frame break (the reason the organ exists) ───────────────────────
print("\n[5] frame break")
u = user("break")
for i in range(5):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
r = ne.on_user_message(u, "p", TXT, embedding=B, now=T0 + 150)
check("an orthogonal turn after five same-topic turns fires", r["fired"], True)
check_true("...and the alert crosses NE_ALERT_MIN", r["alerted"] and r["alert"] >= ne.NE_ALERT_MIN)
check("...and opens the refractory window", ne.get_state(u, "p", now=T0 + 151)["refractory"], True)
check("alert_active reads it back", ne.alert_active(u, "p") or ne.is_alert(ne.get_state(u, "p", now=T0 + 151)["alert"]), True)

# ── 6. under the threshold nothing fires ───────────────────────────────────
print("\n[6] below threshold")
u = user("mild")
for i in range(5):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
r = ne.on_user_message(u, "p", TXT, embedding=toward(A, B, 0.9), now=T0 + 150)
check_true("a 0.9-cosine drift gives delta ~0.1", approx(r["delta"], 0.1, 1e-2))
check("...below NE_FIRE_DELTA 0.20: no fire, alert stays 0", (r["fired"], r["alert"]), (False, 0.0))

# ── 7. rule 3: a chaotic user stops being surprising ───────────────────────
print("\n[7] predicted volatility")
u = user("chaos")
fires = 0
for i in range(16):
    r = ne.on_user_message(u, "p", TXT, embedding=basis(i % 8), now=T0 + i * 30)
    fires += r["fired"]
check("16 topic jumps in a row: zero fires (calibration's A phase never fired either)", fires, 0)

# ── 8. rule 4: refractory gain ─────────────────────────────────────────────
print("\n[8] refractory")
seen_rows = []
_orig_emit = ne._emit
ne._emit = lambda *a, **k: seen_rows.append((a, k))
u = user("refr")
for i in range(5):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
r1 = ne.on_user_message(u, "p", TXT, embedding=toward(A, B, 0.72), now=T0 + 150)   # delta ~0.28
conv = ne._load(u, "p")["conv"]
r2 = ne.on_user_message(u, "p", TXT, embedding=toward(conv, C, 0.62), now=T0 + 180)
k2 = seen_rows[-1][1]
check("first fire is at full gain", (r1["fired"], r1["refractory"]), (True, False))
check("a second fire 30 s later is inside the refractory window", (r2["fired"], r2["refractory"]), (True, True))
check_true("...and pays NE_GAIN x NE_REFRACTORY_GAIN, not full gain",
           approx(k2["alert"] - k2["alert_before"], ne.NE_GAIN * ne.NE_REFRACTORY_GAIN * r2["delta"], 2e-3))
conv = ne._load(u, "p")["conv"]
r3 = ne.on_user_message(u, "p", TXT, embedding=toward(conv, basis(3), 0.55),
                        now=T0 + 150 + ne.NE_REFRACTORY_SEC + 1)
k3 = seen_rows[-1][1]
check("after NE_REFRACTORY_SEC the gain is full again", (r3["fired"], r3["refractory"]), (True, False))
check_true("...full-gain increment (or clamped at 1.0)",
           approx(k3["alert"], min(1.0, k3["alert_before"] + ne.NE_GAIN * r3["delta"]), 2e-3))
ne._emit = _orig_emit

# ── 9. decay ───────────────────────────────────────────────────────────────
print("\n[9] decay")
u = user("decay")
for i in range(5):
    ne.on_user_message(u, "p", TXT, embedding=A, now=T0 + i * 30)
r = ne.on_user_message(u, "p", TXT, embedding=toward(A, B, 0.75), now=T0 + 150)
half = ne.get_state(u, "p", now=T0 + 150 + ne.half_life_sec())["alert"]
check_true("the alert halves in NE_TAU_SEC * ln 2 with no fires", approx(half, r["alert"] / 2, 2e-3))
check_true("a threshold fire stays above NE_ALERT_MIN for about one exchange (> 60 s)",
           ne.is_alert(ne.get_state(u, "p", now=T0 + 150 + 60)["alert"]))

# ── 10/11. lesion and shadow ───────────────────────────────────────────────
print("\n[10] lesion and shadow")
check("shadow is the default (NE_SHADOW unset)", (ne.shadow(), ne.acting()), (True, False))
os.environ["NE_SHADOW"] = "0"
check("NE_SHADOW=0 -> consumers may act", ne.acting(), True)
os.environ["NE_OFF"] = "1"
u = user("off")
r = ne.on_user_message(u, "p", TXT, embedding=A, now=T0)
check("NE_OFF -> status off", r["status"], "off")
check("NE_OFF -> nothing stored", ne.get_state(u, "p", now=T0)["seen"], 0)
check("NE_OFF -> acting False even with shadow off", ne.acting(), False)
check("NE_OFF -> alert_active False", ne.alert_active(u, "p"), False)
os.environ.pop("NE_OFF", None)
os.environ.pop("NE_SHADOW", None)

# ── 12. Redis offline -> in-memory fallback, same numbers ───────────────────
print("\n[12] fallback")
seq = [A, A, A, A, toward(A, B, 0.7)]
ua, ub = user("redis"), user("mem")
res_a = [ne.on_user_message(ua, "p", TXT, embedding=v, now=T0 + i * 30) for i, v in enumerate(seq)]
_rc = ne._RCONN
ne._RCONN = None
res_b = [ne.on_user_message(ub, "p", TXT, embedding=v, now=T0 + i * 30) for i, v in enumerate(seq)]
ne._RCONN = _rc
check("same deltas with and without Redis", [r["delta"] for r in res_a] == [r["delta"] for r in res_b], True)
check("same final alert with and without Redis", res_a[-1]["alert"], res_b[-1]["alert"])

# ── 13. no model, bad input: the chat turn never breaks ────────────────────
print("\n[13] failure paths")
u = user("nomodel")
_orig_embed = ne._embed
ne._embed = lambda text: None
r = ne.on_user_message(u, "p", TXT, embedding=None, now=T0)
ne._embed = _orig_embed
check("no embedding and no model -> status no_model, no crash", r["status"], "no_model")
r = ne.on_user_message(user("bad"), "p", TXT, embedding=["not", "numbers"], now=T0)
check("garbage embedding -> status error, alert 0, no raise", (r["status"], r["alert"]), ("error", 0.0))
u = user("dim")
ne.on_user_message(u, "p", TXT, embedding=A, now=T0)
r = ne.on_user_message(u, "p", TXT, embedding=[1.0, 0.0, 0.0], now=T0 + 30)
check("an embedding of a different size reseeds instead of crashing", r["status"], "seed")

# ── 14. telemetry ──────────────────────────────────────────────────────────
print("\n[14] telemetry")
rows = []
ne._emit = lambda *a, **k: rows.append((a, k))
u = user("tm")
for i, v in enumerate([A, A, A]):
    ne.on_user_message(u, "p", TXT, embedding=v, now=T0 + i * 30)
ne._emit = _orig_emit
check("one ne/turn row per user message", [a[:2] for a, _ in rows], [("ne", "turn")] * 3)
check_true("rows carry status, delta, alert, shadow and the fire threshold",
           all(k in rows[-1][1] for k in ("status", "delta", "alert", "shadow", "fire_delta")))

# ── 15. calibration parity: the organ IS the arithmetic §6 measured ────────
print("\n[15] calibration parity")
try:
    import ne_calibrate as cal
    import numpy as np
    vecs = [A, A, toward(A, B, 0.8), A, B, toward(B, C, 0.6), C, A]
    times = [T0 + i * 40 for i in range(len(vecs))]
    events = [{"role": "user", "text": TXT, "ts": t, "ts_raw": str(t)} for t in times]
    args = types.SimpleNamespace(min_words=ne.NE_MIN_WORDS, warmup=ne.NE_WARMUP,
                                 session_gap=ne.NE_SESSION_GAP_SEC, alpha_m=ne.NE_ALPHA_M,
                                 alpha_c=ne.NE_ALPHA_C, keep_m=False, skip_tool_markers=False)
    rows_cal = cal.replay(events, [np.array(unit(v)) for v in vecs], "user", args)
    u = user("parity")
    rows_ne = [ne.on_user_message(u, "p", TXT, embedding=v, now=t) for v, t in zip(vecs, times)]
    check("same status sequence as ne_calibrate.replay",
          [r["status"] for r in rows_ne], [r["status"] for r in rows_cal])
    check_true("same s and delta on every turn (to 1e-5)",
               all((a["s"] is None and b["s"] is None) or approx(a["s"], b["s"], 1e-5)
                   for a, b in zip(rows_ne, rows_cal))
               and all((a["delta"] is None and b["delta"] is None) or approx(a["delta"], b["delta"], 1e-5)
                       for a, b in zip(rows_ne, rows_cal)))
except ImportError as e:
    print(f"  SKIP  labs/ne_calibrate.py not importable: {e}")

# ── 16. the daemon hold (lab note §7) ──────────────────────────────────────
print("\n[16] daemon hold (stream_worker.ne_hold)")
try:
    import stream_worker as sw
    emitted = []
    _orig_sw_emit, _orig_sw_ne = sw._emit, sw.norepinephrine_state
    sw._emit = lambda *a, **k: emitted.append((a, k))

    def fake_ne(active, acting, boom=False):
        def alert_active(u, p):
            if boom:
                raise RuntimeError("organ on fire")
            return active
        return types.SimpleNamespace(alert_active=alert_active, acting=lambda: acting,
                                     get_state=lambda u, p: {"alert": 0.9 if active else 0.0})

    sw.norepinephrine_state = fake_ne(False, True)
    check("no alert -> no hold, nothing emitted", (sw.ne_hold("u", "p"), len(emitted)), ("", 0))
    sw.norepinephrine_state = fake_ne(True, True)
    check("alert while acting -> hold", sw.ne_hold("u", "p"), "hold")
    check("...and records daemon/sweep_held with the alert", (emitted[-1][0][:2], emitted[-1][1].get("alert")),
          (("daemon", "sweep_held"), 0.9))
    sw.norepinephrine_state = fake_ne(True, False)
    check("alert in shadow -> would_hold, the sweep runs", sw.ne_hold("u", "p"), "would_hold")
    check("...and records daemon/would_hold", emitted[-1][0][:2], ("daemon", "would_hold"))
    sw.norepinephrine_state = fake_ne(True, True, boom=True)
    check("a failing organ never stops the daemon", sw.ne_hold("u", "p"), "")
    sw.norepinephrine_state = None
    check("no organ installed -> no hold", sw.ne_hold("u", "p"), "")
    sw._emit, sw.norepinephrine_state = _orig_sw_emit, _orig_sw_ne
    check_true("the real organ is what stream_worker imported", sw.norepinephrine_state is ne)
except ImportError as e:
    print(f"  SKIP  stream_worker not importable here: {e}")

# ── cleanup ───────────────────────────────────────────────────────────────
for u in USERS:
    _wipe_one(u)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
