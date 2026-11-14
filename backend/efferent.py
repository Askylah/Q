"""
efferent.py -- the outbound path: what the organs are allowed to do to a reply.

dopamine_state and gaba_state regulate memory decay and the daemon. Until this
module, neither reached the chat turn: the persona spoke at the operator's
fixed temperature whatever its posture was. This is the wire.

Two channels, per turn, from one reading of (tonic, phasic, inhibition, streak,
alert):

- SAMPLING: a bounded delta on the operator's temperature and a bounded scale
  on max_tokens. The operator's settings are the baseline, never replaced.
- STANCE: one short block appended at the end of the system prompt, written
  as behaviour the persona should show, never as a readout.

DESIGN RULES (same standing as the organs' own):
1. Read-only. This module never writes dopamine or GABA state. It composes
   the two organs the way gaba_state rule 1 says callers must; neither organ
   imports the other, and neither imports this.
2. Silent at rest. At baseline tonic, no spike and no inhibition, both
   channels return the identity: zero delta, scale 1.0, empty stance. A brain
   at rest must produce the same request it produced before this file existed.
3. Numbers go to the sampler, behaviour goes to the prompt. No float is ever
   rendered into prompt text. A persona shown "inhibition=0.62" starts
   narrating its own dashboard; that failure is documented in the system this
   idea was taken from.
4. Lesionable. EFFERENT_OFF kills both channels, EFFERENT_SAMPLING_OFF and
   EFFERENT_STANCE_OFF kill one each, read per call. A replay can only say a
   channel did something if it can be cut.
5. Every constant is PROPOSED. POLICY_CALIBRATED stays False until a replay
   sets them. The mapping is a hand-tuned heuristic and says so in telemetry.

Mapping, and why each one:
- tonic -> temperature. Tonic IS the explore/exploit posture; sampling
  temperature is the explore/exploit knob the API exposes. Above baseline
  warms, below baseline cools.
- inhibition -> max_tokens down. Redundancy is the world not moving; the
  honest response to that is fewer words, not stranger ones.
- phasic -> max_tokens up, slightly. Something just landed; there is more to
  say about it for about one exchange (PHASIC_TAU_SEC).
- norepinephrine alert -> band "alert" FIRST, ahead of "inhibited"; temperature
  down (gain: focus, a different axis from tonic's warming, so they add); and
  inhibition's token cut lifted for the alert's lifetime (inhibition * (1 -
  alert)). Output only: GABA state is never touched, so the boredom is still there
  when the alert decays (lab_notes/norepinephrine.md §3). Only when
  norepinephrine_state.acting(); in shadow (the default) the request is unchanged
  and the band it WOULD have taken is recorded as band_shadow.
"""

import os

try:
    import dopamine_state as _ds
except Exception:
    _ds = None
try:
    import gaba_state as _gs
except Exception:
    _gs = None
try:
    import norepinephrine_state as _ne
except Exception:
    _ne = None
try:
    import telemetry as _tm
except Exception:  # telemetry must never be load-bearing
    _tm = None

POLICY = "efferent.hand_tuned.v1"
POLICY_CALIBRATED = False

# --- Tunables (env-overridable; all PROPOSED until a replay tunes them) ---
TEMP_DELTA_MAX = float(os.getenv("EFF_TEMP_DELTA_MAX", "0.15"))     # tonic=1.0 warms by this
TEMP_DELTA_MIN = float(os.getenv("EFF_TEMP_DELTA_MIN", "-0.10"))    # tonic=0.0 cools by this
TEMP_FLOOR = float(os.getenv("EFF_TEMP_FLOOR", "0.2"))
TEMP_CEIL = float(os.getenv("EFF_TEMP_CEIL", "1.3"))
TOKENS_INHIBIT_MIN = float(os.getenv("EFF_TOKENS_INHIBIT_MIN", "0.60"))  # inhibition=1.0 scale
TOKENS_PHASIC_MAX = float(os.getenv("EFF_TOKENS_PHASIC_MAX", "1.15"))    # phasic=1.0 scale
TOKENS_FLOOR = int(os.getenv("EFF_TOKENS_FLOOR", "512"))
TEMP_ALERT_DELTA = float(os.getenv("EFF_TEMP_ALERT_DELTA", "-0.15"))   # alert=1.0 cools (focuses) by this

# Stance thresholds. INHIBITED reuses the organ's own gate so "bored" means
# the same thing here that it means to the daemon.
STANCE_RESTLESS_MIN = float(os.getenv("EFF_STANCE_RESTLESS_MIN", "0.25"))
STANCE_RESTLESS_STREAK = int(os.getenv("EFF_STANCE_RESTLESS_STREAK", "2"))
STANCE_SPIKE_MIN = float(os.getenv("EFF_STANCE_SPIKE_MIN", "0.40"))

# Behavioural disposition: affirmative posture, no negative constraints, no numbers, no feelings vocabulary to parrot.
# Generic on purpose: the persona file owns the voice, this owns the lean.
STANCES = {
    "inhibited": (
        "Recent dialogue traverses redundant ground and patience for repetition is spent. "
        "Maintain strict brevity. Meet genuinely novel input directly; otherwise compress "
        "statements and pivot toward unexplored territory."
    ),
    "restless": (
        "Recent dialogue circles established ground. Advance beyond covered points "
        "and direct focus toward unexplored angles."
    ),
    "exploring": (
        "High engagement drives the exchange forward. Pursue active threads aggressively, "
        "surface high-priority inquiries, and drive the dialogue into fresh intellectual territory."
    ),
    "spike": (
        "A critical point just landed. Lock focus here and extract further depth."
    ),
    "alert": (
        "The exchange just broke sharply onto new ground. Meet the new direction head-on "
        "with full attention and full depth."
    ),
}
STANCE_FOOTER = (
    "Internal disposition only: manifest directly through dialogue and action with zero meta-commentary or self-explanation."
)


def _off(name: str) -> bool:
    """Read per call, like telemetry._off: tests flip these after import."""
    return os.getenv(name, "") not in ("", "0", "false", "False")


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, float(v)))


# --- Pure mappings ---------------------------------------------------------

def temperature_delta(tonic: float, baseline: float) -> float:
    """Piecewise linear through zero at baseline. The two sides have different
    room (baseline sits at 0.30, not 0.50), so each is scaled to its own span
    and tonic=baseline is exactly 0.0 whatever the constants are."""
    t = _clamp(tonic, 0.0, 1.0)
    b = _clamp(baseline, 0.0, 1.0)
    if t >= b:
        span = 1.0 - b
        return 0.0 if span <= 0 else TEMP_DELTA_MAX * (t - b) / span
    return 0.0 if b <= 0 else TEMP_DELTA_MIN * (b - t) / b


def tokens_scale(phasic: float, inhibition: float) -> float:
    """Inhibition shortens, a spike lengthens, and the two multiply: a spike
    during a dull run is still a spike, it just has less room."""
    p = _clamp(phasic, 0.0, 1.0)
    i = _clamp(inhibition, 0.0, 1.0)
    return (1.0 + (TOKENS_INHIBIT_MIN - 1.0) * i) * (1.0 + (TOKENS_PHASIC_MAX - 1.0) * p)


def stance_band(tonic: float, phasic: float, inhibition: float, streak: int,
                explore: bool, inhibited: bool, alert: bool = False) -> str:
    """One band or ''. A norepinephrine alert comes first: a frame break is the
    one thing allowed past inhibition at the OUTPUT (norepinephrine.md §3, seen
    live in gaba note §11). After that, inhibition: gaba_state rule 4 says one
    shiny thing does not throw the gate back open, so it does not get to
    rewrite the stance either."""
    if alert:
        return "alert"
    if inhibited:
        return "inhibited"
    if inhibition >= STANCE_RESTLESS_MIN and int(streak) >= STANCE_RESTLESS_STREAK:
        return "restless"
    if phasic >= STANCE_SPIKE_MIN:
        return "spike"
    if explore:
        return "exploring"
    return ""


def render_stance(band: str, overrides: dict = None) -> str:
    """overrides: band -> text in the persona's own idiom. Nothing in the chat
    path passes any since persona_tail.py was removed (2026-10-28); the generic
    wording always applies. This module decides when a stance fires; a persona
    may only decide how it reads.
    A band the persona did not write falls back to the generic wording."""
    if band not in STANCES:
        return ""
    text = ((overrides or {}).get(band) or "").strip() or STANCES[band]
    return f"\n[DISPOSITION]\n{text}\n{STANCE_FOOTER}\n[/DISPOSITION]\n"


# --- Composition -----------------------------------------------------------

def read_organs(username: str, persona: str) -> dict:
    """One time-relaxed reading of both organs. A missing or failing organ
    reads as rest, so the turn degrades to the pre-efferent request."""
    tonic = _ds.TONIC_BASELINE if _ds is not None else 0.30
    out = {"tonic": tonic, "phasic": 0.0, "inhibition": 0.0, "streak": 0,
           "baseline": tonic, "alert": 0.0}
    if _ds is not None:
        try:
            out.update(_ds.get_state(username, persona))
        except Exception:
            pass
    if _gs is not None:
        try:
            out.update(_gs.get_state(username, persona))
        except Exception:
            pass
    if _ne is not None:
        try:
            out["alert"] = float(_ne.get_state(username, persona)["alert"])
        except Exception:
            pass
    return out


def compute(state: dict, stances: dict = None) -> dict:
    """Pure: an organ reading -> what to do to the request. No I/O."""
    tonic = float(state.get("tonic", 0.30))
    phasic = float(state.get("phasic", 0.0))
    inhibition = float(state.get("inhibition", 0.0))
    streak = int(state.get("streak", 0))
    baseline = float(state.get("baseline", 0.30))

    alert = _clamp(state.get("alert", 0.0) or 0.0, 0.0, 1.0)

    explore = _ds.should_explore(tonic) if _ds is not None else tonic >= 0.5
    inhibited = _gs.is_inhibited(inhibition) if _gs is not None else inhibition >= 0.5
    alerted = bool(_ne is not None and _ne.is_alert(alert))
    ne_acting = bool(_ne is not None and _ne.acting())

    all_off = _off("EFFERENT_OFF")
    sampling_on = not (all_off or _off("EFFERENT_SAMPLING_OFF"))
    stance_on = not (all_off or _off("EFFERENT_STANCE_OFF"))

    # In shadow the alert changes nothing; what it WOULD have done is recorded.
    a_eff = alert if ne_acting else 0.0
    band = stance_band(tonic, phasic, inhibition, streak, explore, inhibited,
                       alert=alerted and ne_acting) if stance_on else ""
    band_shadow = "alert" if (stance_on and alerted and not ne_acting) else ""
    t_delta = temperature_delta(tonic, baseline) + TEMP_ALERT_DELTA * a_eff
    t_scale = tokens_scale(phasic, inhibition * (1.0 - a_eff))
    return {
        "temperature_delta": round(t_delta, 4) if sampling_on else 0.0,
        "tokens_scale": round(t_scale, 4) if sampling_on else 1.0,
        "band": band,
        "stance": render_stance(band, stances),
        "band_shadow": band_shadow,
        "alert": round(alert, 4),
        "ne_acting": ne_acting,
    }


def apply_sampling(kwargs_dict: dict, effect: dict) -> dict:
    """Return a copy of kwargs_dict with the bias applied and clamped. The
    operator's value is the baseline; a missing or non-numeric one is left
    alone rather than invented."""
    out = dict(kwargs_dict)
    delta = float(effect.get("temperature_delta", 0.0))
    scale = float(effect.get("tokens_scale", 1.0))
    temp = out.get("temperature")
    if delta and isinstance(temp, (int, float)):
        # Clamp to the wider of our band and the operator's own setting: an
        # operator who chose 1.5 on purpose is not pulled back to TEMP_CEIL.
        lo, hi = min(TEMP_FLOOR, float(temp)), max(TEMP_CEIL, float(temp))
        out["temperature"] = round(_clamp(float(temp) + delta, lo, hi), 4)
    mt = out.get("max_tokens")
    if scale != 1.0 and isinstance(mt, int) and mt > 0:
        out["max_tokens"] = max(min(TOKENS_FLOOR, mt), int(mt * scale))
    return out


def for_turn(username: str, persona: str, kwargs_dict: dict, stances: dict = None) -> tuple:
    """The one call the chat path makes. Returns (kwargs_dict', band, stance).
    The band is returned so the caller can gate persona content on it without
    reading the organs a second time. Never raises: any failure returns the
    inputs untouched, no band and no stance."""
    try:
        state = read_organs(username, persona)
        effect = compute(state, stances)
        new_kwargs = apply_sampling(kwargs_dict, effect)
        if _tm is not None:
            try:
                _tm.emit("efferent", "turn", username, persona,
                         policy=POLICY, calibrated=POLICY_CALIBRATED,
                         tonic=state["tonic"], phasic=state["phasic"],
                         inhibition=state["inhibition"], streak=state["streak"],
                         temperature_in=kwargs_dict.get("temperature"),
                         temperature_out=new_kwargs.get("temperature"),
                         max_tokens_in=kwargs_dict.get("max_tokens"),
                         max_tokens_out=new_kwargs.get("max_tokens"),
                         band=effect["band"], alert=effect["alert"],
                         ne_acting=effect["ne_acting"], band_shadow=effect["band_shadow"])
            except Exception:
                pass
        if (effect["band"] or effect["band_shadow"] or effect["temperature_delta"]
                or effect["tokens_scale"] != 1.0):
            print(f"[EFFERENT] tonic={state['tonic']:.3f} phasic={state['phasic']:.3f} "
                  f"inhibition={state['inhibition']:.3f} streak={state['streak']} "
                  f"alert={effect['alert']:.3f} -> "
                  f"temp {kwargs_dict.get('temperature')}->{new_kwargs.get('temperature')} "
                  f"max_tokens {kwargs_dict.get('max_tokens')}->{new_kwargs.get('max_tokens')} "
                  f"band={effect['band'] or 'none'}"
                  f"{' (shadow: would be ' + effect['band_shadow'] + ')' if effect['band_shadow'] else ''}",
                  flush=True)
        return new_kwargs, effect["band"], effect["stance"]
    except Exception as e:
        print(f"[EFFERENT] disabled for this turn: {e}", flush=True)
        return kwargs_dict, "", ""


if __name__ == "__main__":
    # Smoke: pure mappings only, no organ state touched.
    os.environ.setdefault("TELEMETRY_OFF", "1")
    for name, st in (
        ("rest", {"tonic": 0.30, "phasic": 0.0, "inhibition": 0.0, "streak": 0, "baseline": 0.30}),
        ("exploring", {"tonic": 0.62, "phasic": 0.1, "inhibition": 0.0, "streak": 0, "baseline": 0.30}),
        ("spike", {"tonic": 0.40, "phasic": 0.7, "inhibition": 0.1, "streak": 0, "baseline": 0.30}),
        ("restless", {"tonic": 0.35, "phasic": 0.0, "inhibition": 0.30, "streak": 3, "baseline": 0.30}),
        ("inhibited", {"tonic": 0.62, "phasic": 0.7, "inhibition": 0.66, "streak": 5, "baseline": 0.30}),
    ):
        eff = compute(st)
        print(name, {k: v for k, v in eff.items() if k != "stance"},
              apply_sampling({"temperature": 0.9, "max_tokens": 4096}, eff))
