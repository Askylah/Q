# GABA Inhibition — Lab Notes

## Status: DESIGN AGREED, NOTHING BUILT (2026-09-06)

> Origin: a scripted model-to-model trace against `dopamine_state.py` on 2026-09-06
> (isolated copy, `PersonaApp-GABA-Test`). Every line reconciled with the formulas in
> `tool_outcome_attribution.md` §1. Two things the trace *showed* that no rule states:
> phasic pinned at 1.0 after eight events with no decay between them, and nothing in
> the event vocabulary can move tonic down — the only brake is the 2700 s tau.
> Both are the same failure: excitation with no antagonist.
>
> Decision: build inhibition as a **second organ**, starting from **redundancy streaks
> only** (option 2 below). Satiety (option 1) is deferred to a hybrid *after* option 2
> has live data. Dopamine stays locked — this note does not touch `dopamine_state.py`
> rules or its suite.

---

### 1. Design rules **[D]**

Mirror of the dopamine module's rules, and as non-negotiable:

1. **GABA is not negative dopamine.** It never reads or writes `dopamine_state`
   values. Tonic keeps its ratchet. Phasic keeps its clamp. Inhibition is its own
   variable with its own dynamics, and it acts on what dopamine is *allowed to do*
   (gate, stamp, daemon posture), never on what dopamine *is*.
2. **Only the novelty channel feeds it** (for now). The nearest-cosine check that the
   novelty path already runs on every `DeepMemory.store` is the sole input. No new
   detector. No social valence — a brain that pulls back because someone frowned is
   approval-driven, and this one is interest-driven by brief.
3. **Ephemeral, like dopamine.** Redis-backed float per (username, persona) with a
   timestamped exponential drain to zero, plus the same in-memory fallback pattern.
4. **Its own smoke test and its own suite from day one**, standalone like the rest of
   `tests/`, every check naming the failure it prevents.

---

### 2. What raises it — the options, and the one we start from **[D]**

| # | Source | Verdict |
|---|---|---|
| 1 | **Satiety** — phasic has been high for a while, inhibition rises with it. Homeostatic, activity-dependent. The direct fix for phasic saturation. | **Deferred.** Hybrid with 2 once 2 has data. |
| 2 | **Redundancy streaks** — a *run* of predicted (non-novel) stores raises the brake. Gate closes because the world stopped being interesting. | **Start here.** |
| 3 | Repeated action failures — environment hostile, consolidate. | Not now. Too close to the learned-helplessness the DA docstring warns about, relocated to a second organ. |
| 4 | Negative social valence. | **No.** Approval-driven. |

---

### 3. Mechanism (option 2) **[PROPOSED — numbers are starting guesses, to be tuned on a slow replay]**

**State.** `inhibition` in [0.0, 1.0] per (username, persona). Plus a `streak` integer:
consecutive predicted stores since the last novel one.

**On a predicted store** (nearest-cosine match high, novelty paid 0):

    streak     += 1
    increment   = BASE * GROWTH ** (streak - 1)        # accelerating
    inhibition  = min(1.0, inhibition + increment)

    BASE   = 0.05     [PROPOSED]  one dull memory is noise
    GROWTH = 1.5      [PROPOSED]  ten in a row is a signal:  0.05, 0.075, 0.11, 0.17 ...

**On a novel store** (nearest none / low, novelty paid > 0):

    streak = 0                                          # resets the run
    # inhibition is NOT zeroed. It drains on its own tau.

That asymmetry is the whole point. A long dull stretch takes a while to shake off; one
shiny thing does not throw the gate back open. Without it this is just negative
novelty with extra steps.

**Drain.** Exponential toward 0 with `GABA_TAU_SEC = 600` [PROPOSED]: slower than
phasic (90 s), much faster than tonic (2700 s). Computed lazily on read from the stored
timestamp, same pattern as dopamine's return-to-baseline.

**Where it acts** (three read sites, all read-only against DA state):

1. **The explore gate.** Open requires `tonic >= EXPLORE_THRESHOLD` (0.50 today)
   **and** `inhibition < GATE_INHIBITION_MAX` (0.50 [PROPOSED]). The clock is no longer
   the only thing that can close the gate.
2. **The stamp bonus.** `memory_engine.store()` scales the phasic importance bonus by
   `(1 - inhibition)`. Memories born in a dull streak are not marked important even if
   phasic happens to be high. (This is the partial answer to saturation until option 1.)
3. **The daemon.** `stream_worker` reads inhibition next to tonic. High inhibition
   puts the gap picker in consolidation regardless of tonic, so the idle rest gate can
   fire mid-session after a boring stretch instead of waiting most of an hour for tonic
   to leak.

---

### 4. Non-goals **[D]**

- Does not change any dopamine rule, constant, or the DA suite.
- Does not add event types. No new hooks in `llm_engine.py` beyond reading the value.
- Does not touch tool-outcome attribution. (Its 33-case suite still needs lifting into
  `tests/` — separate task, `tool_outcome_attribution.md` §5.)
- Not lateral inhibition / winner-take-all over competing gaps. Possible later; not this.

---

### 5. Verification plan **[D]**

**Unit (standalone `tests/test_gaba_inhibition.py`):**
- ten predicted stores in a row → inhibition crosses `GATE_INHIBITION_MAX`; gate reads
  closed with tonic held at 0.60.
- one novel store after that → streak is 0, inhibition unchanged at that instant.
- drain: inhibition halves in `GABA_TAU_SEC * ln 2` with no events.
- DA isolation: `dopamine_state` values byte-identical before and after every GABA op.
- Redis offline → in-memory fallback, same numbers.

**Live (slow replay, not scripted against the module — the four traffic shapes):**
- genuine novelty: gate opens, inhibition stays low.
- rephrased repetition, 10+ turns: inhibition climbs on the streak curve; gate closes
  while tonic is still above threshold. **This is the headline result.** If the gate
  closes only because tonic leaked, option 2 is not doing anything.
- one novel turn after the streak: gate does *not* reopen immediately; reopens after
  drain. Measure how long. That number tunes `GABA_TAU_SEC`.
- daemon: after the dull streak, the rest gate logs "resting, no monologue" mid-session.

**Read afterwards:** `[GABA]` log lines next to `[DA]` lines (already timestamped),
gate state transitions, zettel node count vs. the traffic.

---

### Open

- Parameter values are guesses. `BASE`, `GROWTH`, `GABA_TAU_SEC`, `GATE_INHIBITION_MAX`
  all get set by the slow replay, not by argument.
- Should a novel store *release* inhibition slightly (disinhibition), or only reset the
  streak? Design above says reset only. Revisit with data.
- Phasic saturation is only partially addressed by the stamp scaling. Option 1 is the
  real fix. Do not forget it.

## Next session — start here

1. Slow replay of the four traffic shapes against the *current* system (no GABA yet)
   to get baseline timing for tool bursts vs. the 90 s phasic decay.
2. Then build §3 as `gaba_state.py`, suite first.
