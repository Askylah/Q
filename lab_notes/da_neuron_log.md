# DA Neuron — Lab Notes

## Status: ALIVE — novelty channel and idle rest gate added 2026-09-01 (first live traffic 2026-08-27)

> **File naming**: earlier revisions of this doc referenced `reasoning_core_a` and
> `concurrency_daemon_c`. Those files do not exist and cost one wasted
> investigation. Real locations: **`llm_engine.py`** (tool loop, RPE emission)
> and **`stream_worker.py`** (consciousness daemon). Names below are literal.

### Architecture
- `dopamine_state.py` — two timescales, per (username, persona):
  - **Tonic**: 45-min leaky integrator, baseline 0.30, explore threshold 0.50
  - **Phasic**: 90-s spike envelope, write-time stamping only
  - **Novelty** (2026-09-01): third tonic input, fired from `DeepMemory.store()` — see below
- `provider_health.py` — world-model, deliberately separate organ:
  - reliability EMA + consecutive-failure streak + exponential backoff (1→2→4s, cap 20s)
  - separate Redis namespace; never reads or writes dopamine state
- NOT coupled to DeepMemory.importance (separate organs — correct)

### Live couplings
| Coupling | Status | Location |
|---|---|---|
| Phasic → write stamp | LIVE | `memory_engine.store()` |
| Tonic → decay multiplier | LIVE | `memory_engine.decay_cycle()` (0.6x engaged / 1.4x bored) |
| Tool outcome → RPE | LIVE | `llm_engine.py` intercepting_stream_generator → `tool_reward()` |
| Social valence → RPE | LIVE | `plugins/memory_plugin.py` Reflector (gain 0.6) |
| Tonic → daemon explore gate | LIVE | `stream_worker.py` run_cycle |
| Gap detect → boost_tonic(+0.04) | LIVE | `stream_worker.py` analyze_entropic_gaps |
| Gap re-flag cooldown + rotation | LIVE | `stream_worker.py` (cooldown filtering moved INTO the picker) |
| Infra failure → provider reliability | LIVE | `llm_engine.py` call_llm (429/5xx/transport only) |

### First session tape (2026-08-27)
- rpe +0.5122 → tonic 0.3491 (net −0.0002 across 4 events)
  - **Re-read**: the gain was 0.05×rpe (=+0.0255), not deaf. The 45-min leak ate it.
- phasic envelope: 0.5122 → 0.1596 → 0.0972 → 0.0 (clean 90-s reuptake)
- RPE sign pattern: +0.51, −0.28, −0.04, −0.13 → EMA expectation ratchet (hedonic adaptation confirmed)
- First event followed a 503 → infra failure eaten as personal failure (attribution bug, now fixed)

### Closed issues (2026-08-28)
1. ~~Tonic gain too low~~ — **FIXED**: tool channel raised to `0.15 × RPE`
   (social stays 0.04). Verified: four consecutive clean passes walked tonic
   0.300 → 0.375 → 0.436 → 0.477 → **0.5077**, crossing the explore threshold
   on competence alone. Mastery now opens the door; curiosity no longer holds a monopoly.
2. ~~Infra failures counted as negative reward~~ — **FIXED**: three-way attribution
   (`success` / `action_fail` / `infra_fail`) in `dopamine_state.classify_tool_outcome()`.
   Pure-infra passes update nothing (no EMA ratchet, no phasic dip, no tonic move).
   Mixed passes exclude infra from the denominator. Verified 22/22 on a real-world
   failure corpus (CamelCase exception names, provider prose, bare status codes,
   plus payloads that merely *mention* 503 in a code comment).
   > ⚠️ **PARTIALLY REOPENED 2026-08-29 — see `tool_outcome_attribution.md`.** The
   > *attribution* logic was right and still is. The **detection** was not: that 22/22
   > corpus was synthetic, and tracing the actually-wired tool surface found **10 of 13
   > real failure paths scoring as `success`**. Two causes — `mcp_router.py` never checked
   > MCP's `isError` (failures returned through the *success* branch as bare provider
   > prose: `Unknown tool:`, `Input validation error:`, `ENOENT:`), and its exception
   > wrapper read `"Error executing"`, which has no colon and matched no marker. Net effect
   > was the inverse of this issue: the neuron was being **rewarded for failed tool
   > calls**. Also `query_second_brain` was advertised to the model with no dispatch
   > branch — a guaranteed `action_fail` every time the agent obeyed its own system
   > prompt. **Lesson: validate a failure corpus against the strings the wired tools
   > actually emit, not against plausible-looking ones.**
3. ~~`stamp_bonus(phasic: int)` mistyped~~ — **FIXED**: float.
4. ~~Daemon consolidation confirmed~~ — **INVALID DATA**. The daemon wasn't
   consolidating, it was dead. A hung orphan (PID 12108, 23h old) held the
   single-instance socket while cycling nothing; every respawn saw the port
   taken and exited silently. Killed, and the lock is now Redis `SET NX` + TTL
   with heartbeat-staleness takeover, renewed per cycle, socket demoted to
   Redis-offline fallback. **The explore gate remains genuinely untested.**

### Open issues
1. ~~**Explore gate never actually observed.**~~ — **OBSERVED 2026-08-29, end to end.**
   The daemon sat at baseline 0.30 for hours with the gate shut, writing nothing, while
   still running its full NLI sweep every cycle — *armed, not dormant*. Conversation alone
   barely moved it (`social_reward` gain 0.04 → +0.0077/turn). Tool-using turns walked it
   `0.3077 → 0.3827 → 0.4536 → 0.6397`, and **gap emission resumed the moment it crossed
   0.50** — three `entropic_gap` rows at 334s/360s spacing, one per cycle. The daemon
   cannot open its own gate: `boost_tonic(+0.04)` sits downstream of the gate it would
   open, and cold start is exactly baseline. Something external must cross it first.
2. **Calibration under the 0.15 gain — first real data.** It is *not* too shy: four
   tool-using turns crossed the threshold from baseline, matching the predicted RPE decay
   curve (rpe 0.500 → 0.375 → 0.281 → 0.211 as expectation catches up) to three decimals.
   It is also not too hot — it reached 0.6397, nowhere near pinning. Caveat: the 45-min
   leak means spread-out turns fight decay, so the walk only works when turns are close
   together. Still unknown: where it settles over a long mixed session.
3. **Main-generation infra failures never reach DA at all** (by design — they feed
   `provider_health` only). Decide whether the agent should feel *anything* when
   its own voice fails to render.
4. ~~**NLI conflict scan is O(n²) per cycle, ungated, 139 calls.**~~ — **PARTLY FIXED
   2026-08-30, and the stated reason for the zero rows was WRONG.** See
   `entropic_gap_livelock.md` §27, §32, §33.
   - **Gated.** `analyze_semantic_conflicts` now stands down on `exploring=False`,
     mirroring the gap picker. Measured on an idle app: cycle **1009s → 60.0s**, zero
     LLM calls. The "runs at full cost while the gate is shut" half of this issue is
     closed.
   - **"Zero `semantic_contradiction` rows in the table's entire history" was not
     evidence about the graph.** `call_nli_gate` hardcoded `max_tokens=10` against a
     **reasoning model**, so `max_tokens` was consumed entirely by reasoning tokens,
     content came back `''`, and the gate returned `NEUTRAL` on 100% of calls. Raised
     to 256 (`finish='stop'` at 219 tokens) and verified returning real verdicts —
     6 live graph pairs → `{ENTAIL: 2, NEUTRAL: 4}`. **Nothing is known about how many
     contradictions the graph actually holds.** Do not re-derive the old conclusion.
   - **Corrected counts.** 153 calls per sweep, not 139 (graph growth), across
     **2 contexts, not 4** — `run_cycle` takes one active persona per user and both
     users resolve to `rick`.
   - **Corrected cycle time.** The ~334–360s figure here was inferred from inter-gap
     spacing; measured directly it is **321.0s** at the old cap. At `max_tokens=1000`
     it was 1008.8s, which is why the cap sits at 256 — see §33 for why the argument
     was the TTL mismatch (§14.7) and not token cost, which actually *fell*.
   - **Still open:** the O(n²) shape itself. A per-pair watermark ("check each pair at
     most weekly") remains the structural fix; the posture gate only makes it free
     while idle, not cheap while active. ~~An **active** cycle time at the 256 cap has
     not yet been measured.~~ **Measured**: 548 s and 577 s (livelock §35), then 652.8 s
     (§39). 153 calls per sweep.
6. ~~**Conversation alone cannot open the gate.**~~ — **FIXED 2026-09-01**, novelty channel
   (below). Was arithmetic: social channel asymptote 0.348.
7. **No hysteresis on `should_explore`.** Bare `>= 0.5`. A social bump of +0.0077 near
   the boundary opens a full active cycle that decays shut in ~3 min and reopens on the
   next turn. Fix: open 0.50 / close 0.45 with a stored posture key. Small.
8. **75 legacy idle-branch monologue nodes** in `zettel_nodes` (July 69, Aug 5, Sep 1),
   all zero-link, all retrievable as the persona's own memory. Removal is a live-graph
   operation; owner's call. Livelock §57.
5. **401 burns keys on model errors.** Zen returns `401` for "Model X is not supported";
   `llm_engine.py:595` and `:844` mark the key BURNED on any 401. Harmless while the
   key pool is empty, dangerous the moment it isn't.

### Next
- ~~Re-verify explore gate.~~ **Done** — observed end-to-end 2026-08-29 (issue #1), and
  the gate now also governs the NLI sweep (issue #4). ~~Outstanding measurement: an
  **active** (`exploring=True`) cycle time at the 256 cap.~~ **Measured 2026-08-31**:
  548/577/652.8 s (livelock §35, §39); the lock is now a 30 s renewer thread, so §14.7
  no longer bites (§39–§41).
- **Hysteresis on `should_explore`** — open 0.50, close 0.45, stored posture. Bare
  `>= 0.5` flaps on the boundary and each flap is a full active cycle. Issue #7.
- **Watch `[DA] novelty:`** on the first conversation that teaches the persona something
  new; and `resting, no monologue` on an idle persona with the gate shut.
- Receptor #2 = **GABA (inhibition)**, not serotonin. Excitation without inhibition
  = hallucination loop. Note the system already contains five improvised brakes
  (duplicate-call guard, loop budget, gap cooldown, explore threshold, key cooldowns)
  with no shared state — GABA's job is to unify them into one measurable quantity.
  - Rises on: perseveration, unresolved phasic spikes, sustained action-failure rate
  - Acts by subtraction: `effective_drive = tonic − gaba`
  - Timescale: minutes (between phasic seconds and tonic 45-min)
  - Key observable: **E/I ratio** (`tonic/gaba`) — runaway = mania, inverted = catatonia

### Novelty channel (2026-09-01)

**Why.** Every tonic input is measured against a Rescorla-Wagner expectation
(`_EMA_ALPHA = 0.25`), so from neutral a pure streak yields a geometric RPE series
0.500 → 0.375 → 0.281 → … summing to 2.0. A channel's lifetime lift is therefore
`tonic_gain × 2.0`:

| input | gain | max lift | asymptote from 0.30 | opens the gate? |
|---|---|---|---|---|
| tool outcomes | 0.15 | +0.30 | 0.60 | yes (4 turns, measured 08-29) |
| social valence | 0.04 × 0.6 | +0.048 | 0.348 | **never** |
| daemon gap boost | +0.04 flat | — | — | downstream of the gate; extends only |

So conversation alone could not open the explore gate, and cold start is exactly
baseline. Issue #1's "something external must cross it first" was an arithmetic fact,
not an observation. Raising the social gain was considered and rejected: the brief is
an ADHD-shaped brain, which is interest-driven, not approval-driven. The lever is
**novelty as reward**, and docstring rule 2 ("predicted novelty is not rewarding")
survives as a quantitative threshold rather than a prohibition.

**Where.** Conversation does not create zettel nodes; it creates `deep_memories`
through the Reflector every five turns. `DeepMemory.store()` now computes the new
memory's max cosine similarity to the persona's existing active memories
(`_max_similarity_to_existing`, MiniLM-384, 500 most recent) and calls
`dopamine_state.novelty_reward(username, persona, max_sim)`. After the durable write
and `_auto_associate`, wrapped so a neuron fault cannot lose a memory.

**Bounds, measured on the live corpus** (217 memories with embeddings, nearest
earlier neighbour per persona): p10=0.59, p25=0.80, p50=0.89, p75=0.97; 16% under
0.70, 4% under 0.50. Two Reflector memories were exact duplicates at 1.00.

| constant | value | env |
|---|---|---|
| fully novel at or below | 0.45 | `DA_NOVELTY_SIM_FLOOR` |
| predicted at or above | 0.75 | `DA_NOVELTY_SIM_CEIL` |
| tonic gain at full novelty | 0.12 | `DA_NOVELTY_TONIC_GAIN` |
| phasic spike at full novelty | 0.30 | `DA_NOVELTY_PHASIC_GAIN` |
| leaky bucket per tau | 0.30 | `DA_NOVELTY_BUDGET` |

Novelty is linear between floor and ceiling; `None` (nothing to compare against) is
1.0 — a newborn finds everything new. Pay is `gain × novelty`, capped by a
`novelty_spent` bucket that relaxes to 0 on `TONIC_TAU_SEC`, so the channel's ceiling
is baseline + 0.30 = 0.60, the same as the tool channel. It cannot pin the neuron. It
never dips. Phasic spikes so the *next* memory is stamped hotter (a surprise primes
encoding of what follows). Three observations at similarity 0.50 take tonic 0.30 →
0.60 from cold; ten at 0.80 pay nothing. `tests/test_novelty_reward.py`, 34 checks.

**Unverified live.** Fires on the next Reflector store. The line is
`[DA] novelty: nearest=… novelty=… paid=+… tonic=… budget_left=…` in the app's stdout,
printed only when it pays.

### Idle rest gate (2026-09-01)

The daemon's idle-branch monologue — no conflict, no gap — asked "reflect on your
current state of existence, your beliefs, and what you want next" and wrote the answer
into the graph. 69 of July's 74 monologues were that branch (the gap detector was
broken all month, livelock §11). Low tonic shuts the gap picker, which guarantees the
idle branch, which produces rumination: the coupling ran backwards. Now: gate shut and
nothing to integrate → no monologue, mark armed, rest. Gate open and nothing to bridge
→ a forward-facing prompt, and the output is an observation, not a node. Livelock §56.
This is also the first place the GABA spec's "perseveration" input will be measurable:
the rest gate removes the DA-side cause, the legacy nodes (issue #8) are the residue.

### Calibration check against the four-dial protocol (2026-09-01)

| dial | state | evidence |
|---|---|---|
| 1. saturation | already bounded, no sigmoid needed | RPE series sums to 2.0 → tool asymptote 0.60, measured 0.6397 peak |
| 2. reuptake | already right | τ 2700 s, half-life 31 min; 0.6397 → 0.50 in 24 min; decay matched wall-clock (attribution §1) |
| 3. hysteresis | **missing** | bare `>= 0.5`; social +0.0077/turn can flap it; issue #7 |
| 4. RPE asymmetry | not a DA dial | negative RPE never touches tonic by design (attribution §1); failure-rate suppression is GABA's first input |
