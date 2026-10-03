"""
Efferent path: organ state -> sampling bias + prompt stance.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_efferent.py

Exits non-zero on any failure. Safe against a live app: the pure mappings are
tested with hand-built readings, and the one composed check writes only
da:_test:_eff:* and gaba:_test:_eff:* keys and deletes them on exit. It never
touches a real persona, the database, the embedding model or an LLM.

Each check names the failure it exists to prevent.
"""
import os, sys, re
os.environ["TELEMETRY_OFF"] = "1"   # fixtures must never land in the real telemetry sink

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
for _k in ("EFFERENT_OFF", "EFFERENT_SAMPLING_OFF", "EFFERENT_STANCE_OFF"):
    os.environ.pop(_k, None)

import efferent as ef
import dopamine_state as ds
import gaba_state as gs

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def reading(tonic=None, phasic=0.0, inhibition=0.0, streak=0):
    b = ds.TONIC_BASELINE
    return {"tonic": b if tonic is None else tonic, "phasic": phasic,
            "inhibition": inhibition, "streak": streak, "baseline": b}


BASE = {"temperature": 0.9, "top_p": 1.0, "max_tokens": 4096, "thinking_level": "low"}

print("[1] rest is the identity")
# Prevents: a brain at rest sending a different request than it did before
# this module existed (rule 2). Any drift here is an unmeasured behaviour change
# for every persona on every turn.
eff = ef.compute(reading())
check("temperature_delta at rest", eff["temperature_delta"], 0.0)
check("tokens_scale at rest", eff["tokens_scale"], 1.0)
check("stance at rest", eff["stance"], "")
check("kwargs untouched at rest", ef.apply_sampling(BASE, eff), BASE)

print("[2] tonic moves temperature, both directions, bounded")
# Prevents: the mapping not passing through zero at a baseline that is not 0.5,
# and a pinned tonic pushing temperature past its declared band.
check("tonic=1.0 warms by exactly the max", ef.temperature_delta(1.0, 0.30), ef.TEMP_DELTA_MAX)
check("tonic=0.0 cools by exactly the min", ef.temperature_delta(0.0, 0.30), ef.TEMP_DELTA_MIN)
check_true("above baseline warms", ef.temperature_delta(0.5, 0.30) > 0)
check_true("below baseline cools", ef.temperature_delta(0.1, 0.30) < 0)
check("out-of-range tonic is clamped", ef.temperature_delta(7.0, 0.30), ef.TEMP_DELTA_MAX)
out = ef.apply_sampling(BASE, ef.compute(reading(tonic=1.0)))
check("applied on top of the operator's value", out["temperature"], round(0.9 + ef.TEMP_DELTA_MAX, 4))

print("[3] the operator's setting is a baseline, not a suggestion")
# Prevents: an operator who set temperature 1.5 on purpose being dragged to
# TEMP_CEIL by a brain at rest-plus-epsilon, and a missing value being invented.
hot = dict(BASE, temperature=1.5)
out = ef.apply_sampling(hot, ef.compute(reading(tonic=1.0)))
check("a deliberate 1.5 is never lowered by warming", out["temperature"], 1.5)
out = ef.apply_sampling(hot, ef.compute(reading(tonic=0.0)))
check("cooling still applies to it", out["temperature"], round(1.5 + ef.TEMP_DELTA_MIN, 4))
none_t = dict(BASE, temperature=None)
check("temperature=None is left alone", ef.apply_sampling(none_t, ef.compute(reading(tonic=1.0)))["temperature"], None)
check("input dict is not mutated", BASE["temperature"], 0.9)

print("[4] inhibition shortens, a spike lengthens, neither runs away")
check("full inhibition scale", round(ef.tokens_scale(0.0, 1.0), 4), ef.TOKENS_INHIBIT_MIN)
check("full spike scale", round(ef.tokens_scale(1.0, 0.0), 4), ef.TOKENS_PHASIC_MAX)
check_true("a spike inside a dull run is still shorter than rest", ef.tokens_scale(1.0, 1.0) < 1.0)
out = ef.apply_sampling(BASE, ef.compute(reading(inhibition=1.0)))
check("max_tokens scaled", out["max_tokens"], int(4096 * ef.TOKENS_INHIBIT_MIN))
# Prevents: an operator's small budget being RAISED to the floor. The floor
# stops us cutting below it; it must never add tokens nobody asked for.
small = dict(BASE, max_tokens=300)
check("a budget already under the floor is not raised", ef.apply_sampling(small, ef.compute(reading(inhibition=1.0)))["max_tokens"], 300)

print("[5] stance bands and their priority")
check("exploring", ef.compute(reading(tonic=0.62))["band"], "exploring")
check("spike outranks exploring", ef.compute(reading(tonic=0.62, phasic=0.7))["band"], "spike")
check("restless needs a streak, not one dull event",
      ef.compute(reading(inhibition=0.30, streak=1))["band"], "")
check("restless with a streak", ef.compute(reading(inhibition=0.30, streak=3))["band"], "restless")
# Prevents: one shiny thing rewriting the stance of a bored brain (gaba rule 4).
check("inhibited outranks a spike and high tonic",
      ef.compute(reading(tonic=0.9, phasic=1.0, inhibition=0.66, streak=5))["band"], "inhibited")
check("inhibited uses the organ's own gate",
      ef.compute(reading(inhibition=gs.GATE_INHIBITION_MAX))["band"], "inhibited")

print("[6] no number ever reaches the prompt")
# Prevents: the dashboard-narration failure (rule 3). Checked over every band
# rather than trusted, because the next stance someone writes may forget.
for band in ef.STANCES:
    text = ef.render_stance(band)
    check_true(f"{band}: wrapped in a DISPOSITION block", text.strip().startswith("[DISPOSITION]") and text.strip().endswith("[/DISPOSITION]"))
    check(f"{band}: no digits", re.findall(r"\d", text), [])
    check(f"{band}: no organ vocabulary",
          [w for w in ("dopamine", "gaba", "tonic", "phasic", "inhibition", "streak") if w in text.lower()], [])
check("unknown band renders nothing", ef.render_stance("nope"), "")

print("[7] each channel can be cut on its own")
# Prevents: a replay being unable to attribute an effect to a channel (rule 4).
loud = reading(tonic=1.0, phasic=1.0, inhibition=0.3, streak=3)
os.environ["EFFERENT_SAMPLING_OFF"] = "1"
eff = ef.compute(loud)
check("sampling lesion: no delta", (eff["temperature_delta"], eff["tokens_scale"]), (0.0, 1.0))
check_true("sampling lesion: stance survives", eff["stance"])
os.environ.pop("EFFERENT_SAMPLING_OFF")
os.environ["EFFERENT_STANCE_OFF"] = "1"
eff = ef.compute(loud)
check("stance lesion: no stance", eff["stance"], "")
check_true("stance lesion: sampling survives", eff["temperature_delta"] > 0)
os.environ.pop("EFFERENT_STANCE_OFF")
os.environ["EFFERENT_OFF"] = "1"
eff = ef.compute(loud)
check("full lesion is the identity", (eff["temperature_delta"], eff["tokens_scale"], eff["stance"]), (0.0, 1.0, ""))
os.environ.pop("EFFERENT_OFF")

print("[8] composed against the live organs, and read-only")
U, P = "_test", "_eff"


def _wipe():
    for mod, kinds in ((ds, ("tonic", "phasic", "novelty_spent", "tool_ema", "valence_ema")),
                       (gs, ("inhibition", "streak"))):
        for kind in kinds:
            k = mod._key(U, P, kind)
            mod._MEM_FALLBACK.pop(k, None)
            if mod._RCONN is not None:
                try:
                    mod._RCONN.delete(k.encode())
                except Exception:
                    pass


_wipe()
try:
    kw, band, stance = ef.for_turn(U, P, BASE)
    check("cold start is the identity", (kw, band, stance), (BASE, "", ""))
    for _ in range(6):
        gs.on_redundant(U, P, nearest=0.95, source="test")
    before = (ds.get_state(U, P), gs.get_state(U, P))
    kw, band, stance = ef.for_turn(U, P, BASE)
    after = (ds.get_state(U, P), gs.get_state(U, P))
    check_true("six dull events shorten the reply", kw["max_tokens"] < BASE["max_tokens"])
    check("and report the band to the caller", band, "inhibited")
    check_true("and produce the inhibited stance", "patience for repetition" in stance)
    # Prevents: a persona's wording changing WHEN a stance fires, or losing the
    # footer that stops the model narrating it.
    _, band2, mine = ef.for_turn(U, P, BASE, stances={"inhibited": "Scroll up.", "spike": "unused"})
    check("an override does not change the band", band2, "inhibited")
    check_true("the persona's wording is used", "Scroll up." in mine and "patience for repetition" not in mine)
    check_true("the footer survives an override", ef.STANCE_FOOTER in mine)
    _, _, fallback = ef.for_turn(U, P, BASE, stances={"spike": "only this one"})
    check_true("a band the persona did not write falls back to generic", "patience for repetition" in fallback)
    _, _, blank = ef.for_turn(U, P, BASE, stances={"inhibited": "   "})
    check_true("a blank override falls back to generic", "patience for repetition" in blank)
    # Prevents: the wire writing back into the organs it reads (rule 1).
    # Streak is compared exactly; levels only drain, so allow the clock.
    check("streak unchanged by reading", after[1]["streak"], before[1]["streak"])
    check_true("inhibition not raised by reading", after[1]["inhibition"] <= before[1]["inhibition"])
    check_true("tonic not raised by reading", after[0]["tonic"] <= before[0]["tonic"])
finally:
    _wipe()

print("[9] for_turn never raises")
# Prevents: a broken organ taking the chat turn down with it.
_real = ef.read_organs
ef.read_organs = lambda u, p: (_ for _ in ()).throw(RuntimeError("organ on fire"))
try:
    check("failure returns the inputs untouched", ef.for_turn(U, P, BASE), (BASE, "", ""))
finally:
    ef.read_organs = _real

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("all efferent checks passed")
