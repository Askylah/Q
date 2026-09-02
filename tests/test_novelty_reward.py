"""
Novelty channel of the dopamine neuron: the interest-based drive that lets a
conversation open the explore gate. Covers lab_notes/da_neuron_log.md
(novelty input, 2026-09-01).

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_novelty_reward.py

Exits non-zero on any failure. Safe against a live app: it writes only
da:_test:_novelty:* keys (mirroring dopamine_state's own smoke test) and
deletes them on exit. It never touches a real persona or the database.

Each check names the failure it exists to prevent.
"""
import os, sys, ast, time

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


def approx(a, b, eps=1e-3):
    return abs(float(a) - float(b)) <= eps


import dopamine_state as ds

U, P = "_test", "_novelty"


def _wipe():
    for kind in ("tonic", "phasic", "novelty_spent", "tool_ema", "valence_ema"):
        k = ds._key(U, P, kind)
        ds._MEM_FALLBACK.pop(k, None)
        if ds._RCONN is not None:
            try:
                ds._RCONN.delete(k)
            except Exception:
                pass


_wipe()

# ── 0. the arithmetic that makes this channel necessary ──────────────────
print("\n[0] why the channel exists: the other two inputs cannot open the gate")
# geometric RPE series from neutral expectation: 0.5 * 0.75^n, sum -> 2.0
tool_ceiling = ds.TONIC_BASELINE + ds._TOOL_TONIC_GAIN * 2.0
social_ceiling = ds.TONIC_BASELINE + ds._SOCIAL_TONIC_GAIN * ds._SOCIAL_RPE_GAIN * 2.0
print(f"  tool channel asymptote from baseline: {tool_ceiling:.3f}")
print(f"  social channel asymptote from baseline: {social_ceiling:.3f}")
check_true("tool success alone can cross the explore threshold",
           tool_ceiling >= ds.EXPLORE_THRESHOLD)
check_true("social valence alone can NEVER cross it (this is the gap the channel fills)",
           social_ceiling < ds.EXPLORE_THRESHOLD)

# ── 1. the similarity -> novelty map ──────────────────────────────────────
print("\n[1] novelty_from_similarity")
check("nothing to compare against is fully novel (newborn persona)",
      ds.novelty_from_similarity(None), 1.0)
check("at or below the floor is fully novel", ds.novelty_from_similarity(ds.NOVELTY_SIM_FLOOR), 1.0)
check("well below the floor stays clamped at 1", ds.novelty_from_similarity(0.0), 1.0)
check("at the ceiling is predicted, pays nothing", ds.novelty_from_similarity(ds.NOVELTY_SIM_CEIL), 0.0)
check("the live corpus median (0.89) pays nothing", ds.novelty_from_similarity(0.89), 0.0)
check("an exact duplicate (1.0) pays nothing", ds.novelty_from_similarity(1.0), 0.0)
mid = (ds.NOVELTY_SIM_FLOOR + ds.NOVELTY_SIM_CEIL) / 2
check_true("halfway between floor and ceiling is half novel",
           approx(ds.novelty_from_similarity(mid), 0.5))
check_true("the map is monotone decreasing in similarity",
           all(ds.novelty_from_similarity(a) >= ds.novelty_from_similarity(b)
               for a, b in zip([0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9],
                               [0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])))
check_true("bounds sit inside the measured corpus (p10=0.59 pays, p25=0.80 does not)",
           ds.novelty_from_similarity(0.59) > 0.0 and ds.novelty_from_similarity(0.80) == 0.0)

# ── 2. paying: scaled, never dips, budgeted ───────────────────────────────
print("\n[2] novelty_reward")
base = ds.get_state(U, P)
check_true("starts at baseline", approx(base["tonic"], ds.TONIC_BASELINE, 1e-3))

r = ds.novelty_reward(U, P, 0.95)
check("a predicted memory pays 0", r["paid"], 0.0)
check_true("and leaves tonic untouched", approx(r["tonic"], ds.TONIC_BASELINE, 1e-3))
check_true("and leaves phasic untouched", approx(r["phasic"], 0.0, 1e-3))

r = ds.novelty_reward(U, P, None)
check_true("a fully novel memory pays the full gain",
           approx(r["paid"], ds.NOVELTY_TONIC_GAIN))
check_true("tonic rose by exactly that",
           approx(r["tonic"], ds.TONIC_BASELINE + ds.NOVELTY_TONIC_GAIN, 2e-3))
check_true("and phasic spiked, so the NEXT memory is stamped hotter",
           approx(r["phasic"], ds.NOVELTY_PHASIC_GAIN, 2e-3))

r2 = ds.novelty_reward(U, P, mid)
check_true("half novelty pays half the gain", approx(r2["paid"], ds.NOVELTY_TONIC_GAIN / 2, 2e-3))

# budget: keep paying fully-novel memories until the bucket is dry
paid_total = r["paid"] + r2["paid"]
for _ in range(20):
    rr = ds.novelty_reward(U, P, None)
    paid_total += rr["paid"]
check_true("the leaky bucket caps total novelty pay at NOVELTY_BUDGET",
           approx(paid_total, ds.NOVELTY_BUDGET, 2e-3))
check("once dry, a fully novel memory pays 0", rr["paid"], 0.0)
check_true("budget_left reports empty", approx(rr["budget_left"], 0.0))
check_true("tonic from novelty alone tops out at baseline + budget (no pinning)",
           approx(rr["tonic"], ds.TONIC_BASELINE + ds.NOVELTY_BUDGET, 5e-3))
check_true("which is the same ceiling the tool channel already has",
           approx(ds.TONIC_BASELINE + ds.NOVELTY_BUDGET, tool_ceiling, 1e-6))

# the bucket leaks: age the 'spent' entry by one tau and it should refill ~63%
k = ds._key(U, P, "novelty_spent")
entry = ds._load("novelty_spent", U, P)
aged = {"v": entry["v"], "ts": entry["ts"] - ds.TONIC_TAU_SEC}
ds._MEM_FALLBACK[k] = aged
if ds._RCONN is not None:
    import json
    ds._RCONN.set(k, json.dumps({"v": aged["v"], "ts": aged["ts"]}))
rr = ds.novelty_reward(U, P, None)
import math
expected_room = ds.NOVELTY_BUDGET * (1 - math.exp(-1))
check_true("after one tau of quiet the bucket has refilled by 1 - 1/e",
           approx(rr["paid"], min(ds.NOVELTY_TONIC_GAIN, expected_room), 3e-3))

# ── 3. gate opening from conversation alone ───────────────────────────────
print("\n[3] three genuinely new observations open the gate from cold")
_wipe()
t = None
for i in range(3):
    t = ds.novelty_reward(U, P, 0.50)["tonic"]   # p10-ish: clearly new material
print(f"  tonic after 3 novel observations at sim 0.50: {t}")
check_true("crosses EXPLORE_THRESHOLD (the whole point of the channel)",
           ds.should_explore(t))
_wipe()
t = None
for i in range(10):
    t = ds.novelty_reward(U, P, 0.80)["tonic"]   # p25: ordinary Reflector rehash
check_true("ten ordinary observations do NOT (predicted novelty is not rewarding)",
           not ds.should_explore(t))

# ── 4. wiring: DeepMemory.store fires it, non-fatally, after the write ────
print("\n[4] memory_engine.store wiring")
_src = open(os.path.join(ROOT, "memory_engine.py"), "rb").read().decode("utf-8")
_tree = ast.parse(_src)
_cls = next(n for n in ast.walk(_tree) if isinstance(n, ast.ClassDef) and n.name == "DeepMemory")
_store = next(n for n in _cls.body if isinstance(n, ast.FunctionDef) and n.name == "store")
_store_src = ast.get_source_segment(_src, _store) or ""
check_true("store() calls dopamine_state.novelty_reward", "dopamine_state.novelty_reward(" in _store_src)
check_true("with the nearest-neighbour similarity from the new vector",
           "_max_similarity_to_existing(vec" in _store_src)
_calls = [n for n in ast.walk(_store) if isinstance(n, ast.Call)
          and getattr(n.func, "attr", None) in ("novelty_reward", "execute", "_auto_associate")]
_order = [(n.lineno, n.func.attr) for n in _calls]
_order.sort()
_names = [a for _, a in _order]
check_true("the reward fires AFTER the memory is written and associated (never before a durable write)",
           _names.index("novelty_reward") > _names.index("_auto_associate"))
_guards = [n for n in ast.walk(_store) if isinstance(n, ast.Try)
           and any("novelty_reward" in ast.dump(s) for s in n.body)]
check("the call is wrapped so a neuron fault cannot lose a memory", len(_guards), 1)
_helper = next((n for n in _cls.body if isinstance(n, ast.FunctionDef)
                and n.name == "_max_similarity_to_existing"), None)
check_true("the similarity helper exists on DeepMemory", _helper is not None)
_helper_src = ast.get_source_segment(_src, _helper) or "" if _helper else ""
check_true("it excludes the memory just written from its own comparison",
           "id != ?" in _helper_src and "exclude_id" in _helper_src)
check_true("it returns None for a newborn persona rather than 0.0 (both mean novel, but None is honest)",
           "return None" in _helper_src)

_wipe()
print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
sys.exit(1 if FAILS else 0)
