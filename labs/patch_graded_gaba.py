"""Graded boredom + gate edge events. Idempotent, preserves CRLF. Run from repo root."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def patch(path, pairs, marker):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    if marker in txt:
        print("SKIP (already patched)", path); return
    for old, new in pairs:
        n = txt.count(old)
        assert n == 1, f"{path}: expected 1 match, got {n} for:\n{old[:160]}"
        txt = txt.replace(old, new)
    open(path, "wb").write((txt.replace("\n", "\r\n") if crlf else txt).encode("utf-8"))
    print("patched", path, f"({len(pairs)} edits, {'CRLF' if crlf else 'LF'})")


# ---------------- reflect_gate.py: pure intensity ----------------
patch("reflect_gate.py", [
("""def window_limit(turn_threshold: int, prompt_rows: int = 20) -> int:""",
"""def boredom_intensity(nearest, sim_ceil: float = 0.85):
    \"\"\"How flat was the window, 0..1, or None when there is no similarity.
    0 at the shrug ceiling, 1 at cosine 1.0, clipped. Five windows at 0.86
    and five at 0.98 are different signals; this is the number that lets
    gaba_state tell them apart (it scales the increment, not the streak).\"\"\"
    if nearest is None:
        return None
    c = float(sim_ceil)
    if c >= 1.0:
        return 1.0 if float(nearest) >= c else 0.0
    return max(0.0, min(1.0, (float(nearest) - c) / (1.0 - c)))


def window_limit(turn_threshold: int, prompt_rows: int = 20) -> int:"""),
("""    Returns {"verdict": "wait" | "shrug" | "reflect", "new_events": int,
             "nearest": float | None, "reason": str}.""",
"""    Returns {"verdict": "wait" | "shrug" | "reflect", "new_events": int,
             "nearest": float | None, "intensity": float | None, "reason": str}.
    intensity is boredom_intensity(nearest, sim_ceil): >0 only on a shrug."""),
("""    nearest = float(nearest)
    if nearest >= float(sim_ceil):
        return {"verdict": "shrug", "new_events": len(new_raw), "nearest": nearest,
                "reason": f"newest {t} raw events sit at {nearest:.3f} >= {sim_ceil} to the {t} before them"}
    return {"verdict": "reflect", "new_events": len(new_raw), "nearest": nearest,
            "reason": f"conversation moved: {nearest:.3f} < {sim_ceil}"}""",
"""    nearest = float(nearest)
    intensity = boredom_intensity(nearest, sim_ceil)
    if nearest >= float(sim_ceil):
        return {"verdict": "shrug", "new_events": len(new_raw), "nearest": nearest,
                "intensity": intensity,
                "reason": f"newest {t} raw events sit at {nearest:.3f} >= {sim_ceil} to the {t} before them"}
    return {"verdict": "reflect", "new_events": len(new_raw), "nearest": nearest,
            "intensity": intensity,
            "reason": f"conversation moved: {nearest:.3f} < {sim_ceil}"}"""),
], marker="def boredom_intensity")

# ---------------- gaba_state.py: graded increment + crossing flag ----------------
patch("gaba_state.py", [
("""GABA_STREAK_TTL_SEC = float(os.getenv("GABA_STREAK_TTL_SEC", str(2 * 600)))
""", """GABA_STREAK_TTL_SEC = float(os.getenv("GABA_STREAK_TTL_SEC", str(2 * 600)))
# Graded redundancy (2026-09-13, Sky): a shrug carries an intensity 0..1 (how
# flat the window was; reflect_gate.boredom_intensity). The increment is scaled
# by GRADE_MIN + (GRADE_MAX - GRADE_MIN) * intensity, so a window barely over
# the ceiling pays half and a dead-flat one pays 1.5x. The STREAK still
# advances by one either way -- grading weights the pressure, not the count.
# intensity=None (daemon_gap, or no similarity) -> weight 1.0, the old fixed step.
GABA_GRADE_MIN = float(os.getenv("GABA_GRADE_MIN", "0.5"))    # PROPOSED
GABA_GRADE_MAX = float(os.getenv("GABA_GRADE_MAX", "1.5"))    # PROPOSED
"""),
("""def on_redundant(username: str, persona: str, nearest=None, source: str = "reflect") -> dict:
    \"\"\"
    A redundant event: the world did not move. streak += 1, inhibition rises
    by BASE * GROWTH**(streak-1) -- accelerating, so one dull memory is noise
    and a run is a signal. Clamped at 1.0.
    \"\"\"
    with _LOCK:
        cur = _drained(_load("inhibition", username, persona))
        stk = _streak(_load("streak", username, persona)) + 1
        inc = GABA_BASE * (GABA_GROWTH ** (stk - 1))
        new = min(1.0, cur + inc)
        _save("inhibition", username, persona, new)
        _save("streak", username, persona, stk)
    near = "none" if nearest is None else round(float(nearest), 3)
    print(f"[GABA] redundant({source}): streak={stk} nearest={near} "
          f"+{min(inc, 1.0 - cur):.4f} inhibition={new:.4f}"
          f"{' GATE_SHUT' if new >= GATE_INHIBITION_MAX else ''}", flush=True)
    _emit("gaba", "redundant", username, persona, source=source, streak=stk,
          nearest=nearest, before=cur, increment=min(inc, 1.0 - cur), inhibition=new,
          gate_shut=bool(new >= GATE_INHIBITION_MAX))
    return {"streak": stk, "increment": round(inc, 4), **get_state(username, persona)}
""", """def grade_weight(intensity) -> float:
    \"\"\"Pure: intensity 0..1 (or None) -> multiplier on the increment.\"\"\"
    if intensity is None:
        return 1.0
    i = max(0.0, min(1.0, float(intensity)))
    return GABA_GRADE_MIN + (GABA_GRADE_MAX - GABA_GRADE_MIN) * i


def on_redundant(username: str, persona: str, nearest=None, source: str = "reflect",
                 intensity=None) -> dict:
    \"\"\"
    A redundant event: the world did not move. streak += 1, inhibition rises
    by BASE * GROWTH**(streak-1) * grade_weight(intensity) -- accelerating, so
    one dull memory is noise and a run is a signal, and graded, so a run of
    flat windows is a louder signal than a run of mildly dull ones. Clamped at 1.0.
    \"\"\"
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
"""),
], marker="def grade_weight")

# ---------------- plugins/memory_plugin.py: pass intensity, record tonic ----------------
patch("plugins/memory_plugin.py", [
("""        _near = plan["nearest"]
        _near_s = "none" if _near is None else f"{_near:.3f}"
        # Every verdict lands on disk, including "wait": the wait rows are the
        # cadence trace (one per user turn) and cost nothing.
        _emit("reflect", plan["verdict"], self.username, self.persona,
              nearest=_near, new_events=plan["new_events"], threshold=turn_threshold,
              sim_ceil=REFLECT_SIM_CEIL, novelty_gate=REFLECT_NOVELTY_GATE,
              window_rows=len(full), reason=plan["reason"])
""", """        _near = plan["nearest"]
        _near_s = "none" if _near is None else f"{_near:.3f}"
        _intensity = plan.get("intensity")
        # Dopamine posture at the moment of the verdict, recorded (NOT acted
        # on): lets the calibration split shrugs by whether the explore gate
        # was even open. Gating the watermark on tonic is a later decision,
        # taken from these rows, not before them.
        _tonic = None
        _gate_open = None
        if _DA_AVAILABLE:
            try:
                _tonic = dopamine_state.get_state(self.username, self.persona)["tonic"]
                _gate_open = bool(dopamine_state.should_explore(_tonic))
            except Exception:
                _tonic = None
        # Every verdict lands on disk, including "wait": the wait rows are the
        # cadence trace (one per user turn) and cost nothing.
        _emit("reflect", plan["verdict"], self.username, self.persona,
              nearest=_near, intensity=_intensity, tonic=_tonic, gate_open=_gate_open,
              new_events=plan["new_events"], threshold=turn_threshold,
              sim_ceil=REFLECT_SIM_CEIL, novelty_gate=REFLECT_NOVELTY_GATE,
              window_rows=len(full), reason=plan["reason"])
"""),
("""                    gaba_state.on_redundant(self.username, self.persona, nearest=_near, source="reflect")
""", """                    gaba_state.on_redundant(self.username, self.persona, nearest=_near,
                                            source="reflect", intensity=_intensity)
"""),
], marker="_intensity = plan.get(\"intensity\")")

# ---------------- stream_worker.py: gate edge events ----------------
patch("stream_worker.py", [
("""    def __init__(self):
""", """    def __init__(self):
        # (username, persona) -> {"open": bool, "since": epoch}; feeds gate_edge().
        self._gate_state = {}
"""),
("""    def run_cycle(self):
        \"\"\"Executes a single consciousness evaluation sweep across all active personas.\"\"\"
""", """    def _track_gate(self, username, persona, exploring, tonic_ok, gaba_shut, tonic, inhibition):
        \"\"\"Emit daemon/gate_open and daemon/gate_close edges with the closing
        source, so the calibration can answer "who closes the gate, and how
        long was it open" without reconstructing it from per-cycle rows.\"\"\"
        key = (username, persona)
        now = time.time()
        new_state, edge = gate_edge(self._gate_state.get(key), exploring, tonic_ok, gaba_shut, now)
        self._gate_state[key] = new_state
        if edge is not None:
            _emit("daemon", edge["event"], username, persona, tonic=tonic, inhibition=inhibition,
                  **{k: v for k, v in edge.items() if k != "event"})

    def run_cycle(self):
        \"\"\"Executes a single consciousness evaluation sweep across all active personas.\"\"\"
"""),
("""            _emit("daemon", "gate", username, persona, tonic=_da_tonic, inhibition=_gaba_inh,
                  gaba_shut=_gaba_shut, exploring=_exploring)
""", """            _emit("daemon", "gate", username, persona, tonic=_da_tonic, inhibition=_gaba_inh,
                  gaba_shut=_gaba_shut, exploring=_exploring)
            _tonic_ok = True if _da_tonic is None else bool(dopamine_state.should_explore(_da_tonic))
            try:
                self._track_gate(username, persona, _exploring, _tonic_ok, _gaba_shut, _da_tonic, _gaba_inh)
            except Exception:
                pass
"""),
("""class ConsciousnessWorker:""", """def gate_edge(prev, exploring: bool, tonic_ok: bool, gaba_shut: bool, now: float):
    \"\"\"Pure. prev is {"open": bool, "since": epoch} or None (first sighting).
    Returns (new_state, edge) where edge is None or a dict with "event" in
    {"gate_open", "gate_close"}; gate_close carries open_for_s and closed_by in
    {"tonic", "gaba", "both"}. First sighting after a daemon start records
    state and emits nothing -- there is no edge to report yet.\"\"\"
    if prev is None:
        return {"open": bool(exploring), "since": now}, None
    if bool(exploring) == bool(prev.get("open")):
        return prev, None
    if exploring:
        return {"open": True, "since": now}, {"event": "gate_open"}
    if gaba_shut and not tonic_ok:
        closed_by = "both"
    elif gaba_shut:
        closed_by = "gaba"
    else:
        closed_by = "tonic"
    return ({"open": False, "since": now},
            {"event": "gate_close", "closed_by": closed_by,
             "open_for_s": round(now - float(prev.get("since", now)), 1)})


class ConsciousnessWorker:"""),
], marker="def gate_edge")

# ---------------- tests ----------------
patch("tests/test_gaba_inhibition.py", [
("""_wipe()
print()
if FAILS:""", """# ── 9. graded boredom (2026-09-13): flat windows push harder than mild ones ──
print("\\n[9] graded redundancy: intensity scales the increment, not the streak")
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
print("\\n[10] gate edge detection: who closed it, how long was it open")
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
if FAILS:"""),
], marker="[9] graded redundancy")
print("done")
