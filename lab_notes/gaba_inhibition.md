# GABA Inhibition — Lab Notes

## Status: BUILT (§7), UNIT-VERIFIED, NOT YET REPLAYED — §6 BASELINE INVALIDATED (2026-09-09)

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
>
> **2026-09-06, later:** the slow replay ran (§6). Option 2 survives with two rules added
> (nearest-similarity condition, reset floor — §6.12). Strongest evidence is for site 3:
> the daemon pays itself +0.04 tonic per gap and held the gate open through five minutes
> of silence (§6.7). Predicted stores, social reward and daemon boosts all write DA state
> without a log line (§6.1, §6.8) — instrument before building. Harness finding on the
> live path: Gemini 3 flash returns a blank turn ~2/3 of the time when history is present
> and the persona pre-fill is glued to the user message (§6.9).

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

### 6. Slow replay baseline — 2026-09-06, run 20260906_142803 **[D]**

Slow replay of the four traffic shapes against the **current** system (no GABA), per
the Next list. Everything ran in the isolated copy `PersonaApp-GABA-Test`: its own
`users.db` (via `PERSONAAPP_DATA_DIR`), `REDIS_URL=redis://localhost:6379/1` so the live
install's DA keys and daemon lock were never touched, user `gaba_replay`, persona `rick`,
Vertex on ADC. Instruments live in the test copy's `labs/`, not in this repo:
`labs/gaba_replay.py` (boots the app, samples every `da:*` key every 2 s, counts
`deep_memories` rows straight from sqlite, drives turns through `/chat/rick/stream` at a
30 s human cadence, saves every raw SSE line per turn) and `labs/replay_digest.py`.
Run directory: `labs/replay_out/20260906_142803/` (`app.log` timestamped, `da_samples.csv`,
`turns.csv`, `sse/*.sse`, `summary.json`). 33 turns, then 300 s of silence.

The replay user has `global_direct_wire=0` in the test DB. Why is §6.9. Nothing else
differs from Sky's path.

| phase | turns | stores | paid | silent | tool events | tonic in→out | phasic in→out | gate open |
|---|---|---|---|---|---|---|---|---|
| genuine novelty (8 topics) | 8 | 5 | 2 | 3 | 0 | 0.30→0.487 | 0→0.108 | 0 % |
| rephrased repetition | 15 | 15 | 1 | 14 | 0 | 0.483→0.455 | 0.055→0.001 | 0 % |
| bad tool args | 6 | 5 | 1 | 4 | 14 | 0.452→0.751 | 0.001→0.010 | 82 % |
| staged transport failure | 4 | 4 | 0 | 4 | 0 | 0.780→0.840 | 0.005→0 | 100 % |
| quiet 300 s | – | 0 | – | – | 0 | 0.840→0.895 | 0→0 | 100 % |

"gate open" = fraction of 2 s samples with relaxed tonic ≥ 0.50. "paid" = a `[DA] novelty:`
line with `paid > 0`; "silent" = a `deep_memories` row with no line at all.

**6.1 Predicted stores leave no trace.** 29 stores, 4 paid (0.12, 0.074, 0.0079, 0.011),
25 silent. `memory_engine.store()` prints `[DA] novelty:` only when `paid > 0`. The exact
event §3 keys on is invisible in the log today; the replay had to count sqlite rows to see
it. `gaba_state` must print its own line on the silent branch, and so should `store()`.

**6.2 The reflector stores every turn, not every fifth.** `Reflector.reflect(turn_threshold=5)`
counts raw events in the last 20 observations and never clears them, so from turn 5 on it
reflects on every user message and writes one `dense_observation` (importance hard-set 6)
per turn. Consecutive observations of an overlapping 20-event window are near-duplicates:
in the novelty shape 3 of 5 stores on brand-new topics were predicted. So a raw
predicted-store streak measures the reflector's window overlap, not the conversation.
The signal is still there — the two genuinely novel stores had nearest 0.564 or none, the
rephrasings 0.72–0.73 — but §3's trigger cannot be "predicted store" alone. See §6.12.

**6.3 The reset needs a floor.** Repetition turn 9 paid 0.0079 at nearest 0.73. Under §3 as
written ("novel store resets the streak", i.e. `paid > 0`) that would have zeroed an
eight-store streak for eight thousandths of a point. With a floor (PROPOSED `paid ≥ 0.03`;
the two real novelties paid 0.12 and 0.074, the two rephrasings 0.008 and 0.011) the
shapes separate cleanly: novelty max streak 2, repetition 15, bad-tool 5.

**6.4 Repetition does not touch DA at all.** Fifteen rephrasings: tonic 0.483→0.455, which
is exactly the 2700 s tau over that interval; phasic ~0 throughout. The current system
cannot tell fifteen rephrasings from fifteen minutes of silence. Confirms the origin
claim (no antagonist) and sharpens it: there is no signal here for anything to act on
except the store stream.

**6.5 Bursts vs the 90 s phasic tau.** Tool events inside a turn are 2.0–2.5 s apart,
3–5 per turn, 5–10 s span. Phasic loses under 10 % inside a burst and ~35 % across the
30 s gap + ~8 s response between turns. A write of 1.0 needs 207 s to fall under 0.10;
in the replay it never got the chance — the next event or a failure floor always came
first. Phasic hit 1.0 on a **single** success (bad-tool 3, rpe +0.65): pinning reproduced
live, one event.

**6.6 Flailing pays.** Bad-tool phase: tonic 0.452→0.751 in six turns. Failures drag the
tool EMA down, so the next partial success reads as a large positive RPE (+0.30, +0.21,
+0.65) and `_TOOL_TONIC_GAIN` pays it into tonic. Six turns of mostly failed tool calls
opened the explore gate; eight genuinely novel topics never did (peak 0.4935, novelty
budget left 0.18 after the first store). Novelty alone cannot carry tonic from 0.30 to
0.50; incompetence-then-recovery can.

**6.7 The daemon pays itself.** `stream_worker` gap discovery calls `boost_tonic(+0.04)`
("gap discovery is itself arousing"). With the gate open it found a gap every ~65 s
cycle; over transport + quiet, tonic went 0.78→0.925 with nobody talking, three writes at
~+0.026 net each, until "Gap budget spent (9/8 this window)". The F1 cap is the only
brake, and the comment above it says so. Gate opens → daemon works → work pays tonic →
gate stays open. The rest gate never fired in 189 daemon lines because tonic never came
back under 0.50 once tools opened it. This is §3 site 3, and it is the best-evidenced
payoff of the three.

**6.8 Silent tonic writes.** 31 of 49 tonic key rewrites had no `[DA]` line within 3 s.
Three writers print nothing on success: `social_reward` (memory_plugin, valence from the
reflection), `boost_tonic` (daemon), and `novelty_reward` at `paid = 0` (rewrites tonic at
its decayed value — the monotone repetition-phase writes). A GABA organ reading tonic
will see moves it cannot attribute. One log line each, before the organ.

**6.9 Gemini silent turns (harness finding, on Sky's real path).** With the persona in
diagnostic mode, the OpenAI-compatible path glues the pre-fill `Rick Sanchez: ` to the
END of the user message (`llm_engine` ~L674–680, non-Anthropic pre-fill emulation).
Gemini 3 flash thinks anyway on Vertex (500–1000 reasoning tokens; `thinking_level=Off`
is not honoured there) and with that trailing pre-fill returned a single whitespace token,
`finish_reason=stop`, `completion_tokens=1`, in 4 of 6 attempts with history present
(run `20260906_1423xx`, raw in `sse/`). Reproduced against the engine directly: 801
reasoning tokens → 1 char; same request without pre-fill → 2975 chars. With
`global_direct_wire=0` for the replay user: 1 empty in 33 turns, and 26 of 33 turns had no
reasoning tokens at all. Turn 1 of every run was fine because there was no history yet.
Not fixed anywhere; the replay user's setting lives only in the test DB. Sky's own user
has direct wire on, so this is live behaviour.

**6.10 Gates seen.** `SECURITY_GATE` bounced "Call read_file_lines on
personas/does_not_exist.txt…" instantly (65-char reply, no observation, no DA effect: a
refusal is invisible to the neuron). `call_sub_agent` needs governance approval; without
a "yes" on the next user turn all four transport turns parked at the 84-char approval
prompt (no tool, no DA) — and the reflector still stored on each of them.
Follow-up run `20260906_145750` (`--shapes transport --approve`): answering "Yes, go
ahead." on the next turn released the call each time; the sub-agent defaulted to
`claude-3-5-sonnet-20240620`, hit the Anthropic route with no key, and came back as the
`⚠️ Connection Error: No API key provided.` string. All four were classified
`infra_fail`: `[DA] tool_reward rpe=0.0 ... infra_discounted=True` ×4, phasic ≤ 0.014,
tonic 0.300→0.302 across the phase. Attribution holds live: the world's fault costs the
neuron nothing. (Rick quoted the error back verbatim each time, then called it Groundhog
Day.) Two stores per turn on this shape — one per leg, approval prompt included.

**6.11 Timing.** Mean turn 9–10 s at 30 s cadence; first byte ~2 s without thinking,
6–10 s with. The reflector's `reflection_started` control event lands ~0.15 s into the
stream and the store itself ~6 s in, mid-turn.

**6.12 What this does to §3 (PROPOSED, nothing applied).**
- Trigger: predicted-store streak **with** a nearest-similarity condition (≥ 0.70 counted
  as redundant, from 6.2's numbers) **and** the reset floor from 6.3. Under those two
  rules the recorded shapes give: novelty never closes the gate (max streak 2);
  repetition crosses `GATE_INHIBITION_MAX` 0.50 around the 5th–6th consecutive
  predicted store, ≈3 min at this cadence (0.05·1.5^k sums: 0.05, 0.125, 0.24, 0.41,
  0.66); bad-tool reaches 5 and sits at the edge. That is the intended behaviour, so §3
  survives — only with the two rules added.
- Site 3 first. Inhibition should also count daemon gap boosts as redundancy (each is
  +0.04 tonic with no user input), or the daemon should read inhibition before claiming a
  dissonance slot. Site 1 second. Site 2 (stamp scaling) is moot until option 1 exists:
  phasic pins on one event.
- `GABA_TAU_SEC = 600`: at one store per ~38 s the drain between stores is ~6 %, so the
  streak still accumulates; fine as a starting value. Simulate against `turns.csv`
  timestamps before arguing about it.
- Instrumentation before the organ (6.1, 6.8), or the live half of §5 cannot be read.
> **[Session 2026-09-06, afternoon — pre-fill placement + Gemini thinking, 2026-09-06 — CORRECTION]** §6.9's mechanism was wrong, and it rested on one run per condition. The pre-fill is NOT what blanks Gemini. Re-measured against `llm_engine.call_llm` directly (Vertex, `gemini-3-flash-preview`, the real turn-1 assistant reply in history, `max_tokens=4096`, `thinking_level=Off`), 8 runs per shape, blank = `content_len` ≤ 3:
>
> | shape | blank |
> |---|---|
> | diagnostic frame + pre-fill glued to user turn (old code) | 1/8 |
> | frame + pre-fill as trailing assistant turn | 4/8 |
> | frame, no pre-fill | 4/8 |
> | no frame, no pre-fill | 3/8 |
> | no frame, trailing pre-fill | 3/8 |
> | frame + trailing pre-fill + `reasoning_effort: low` | 1/1 |
> | frame + trailing pre-fill + `thinking_budget: 0` | **0/8** |
> | frame + trailing pre-fill + `thinking_level: minimal` | **0/8** |
>
> Every blank carried reasoning tokens (644–3930; the 3927–3930 ones are the thinking budget eating the whole `max_tokens`). Every clean reply under the last two rows shows `reasoning_tokens=None`. Root cause: the `google_vertex` route sent **no thinking config at all** — `thinking_level=Off` never reached Gemini (and neither did low/medium/high). Gemini 3 flash then thinks when it feels like it, and in ~37% of history-bearing turns the visible reply is whitespace. Why the direct-wire-off replay saw only 1/33 (§6.9): whether the model chooses to think is prompt-shape dependent; the replay's short repetition turns rarely trigger it. The fix removes the choice.
>
> Two engine changes, applied byte-identically to the repo and the test copy, **uncommitted**:
> 1. `llm_engine.py` ~L674 — for non-Anthropic providers the pre-fill now rides as a trailing `assistant` message (same shape as the Anthropic path) instead of being glued onto the user's message. This is what stops personas reading `Rick Sanchez:` as the user's words ("telling me who I am"); it does not change the blank rate. Vertex accepts the trailing turn (replies start `\n *burp*`, i.e. it continues the seeded turn). Untested live on OpenRouter/OpenAI-compat routes.
> 2. `llm_engine.py` ~L719 — `google` and `google_vertex` now send `extra_body.google.thinking_config.thinking_budget=0` when thinking is off (pro models get `reasoning_effort: low` instead, since they cannot switch thinking off); low/medium/high now reach Vertex too. Names matching the reasoning-mandatory list are left alone.
>
> Verification: `tests/test_prefill_placement.py` (6 PASS), `tests/test_gemini_thinking_off.py` (7 PASS, wire stubbed, nothing leaves the machine); then 4/4 non-streaming + 1/1 streaming through the patched engine on the live Vertex path, all `reasoning_tokens=None`, 2.7–3.4k chars. Instruments in the test copy: `labs/probe_frame.py` (frame × pre-fill grid), `labs/probe_thinking.py` (injects thinking fields at the wire). Pre-edit engine backups are in the session scratchpad, not the repo. Next-list item 4 is done by this.
> **[Session 2026-09-06, late afternoon — round 2, supersedes parts of the correction above, 2026-09-06 — CORRECTION]** Sky pushed back ("I never turn thinking off; my models require it") and the second round of measurement changed two of the three conclusions above. Same probe, Vertex, 8 runs unless noted, blank = `content_len` ≤ 3.
>
> **1. Pre-fill as a trailing assistant turn is WRONG for Gemini.** `gemini-3.7-flash` on Vertex answers it with 400 `Requests ending with a model turn are not supported`, which the engine surfaces as `⚠️ API Error`. The placement fix stands for other OpenAI-compatible providers (still untested live there) but **Gemini now gets no pre-fill at all**: `is_gemini = provider in (google, google_vertex) or "gemini" in model_id` → skip. No pre-fill vs pre-fill never moved the blank rate (4/8 vs 4/8 above), and replies now open straight on `*burp*` instead of the `\n ` continuation artifact. Sky's next-list option "skip the pre-fill emulation for google models" was the right one.
>
> **2. Thinking ON is not the problem on the models Sky runs. It is the problem on `gemini-3-flash-preview` only, and the collider is the persona's own line 1.** With the dial actually reaching Vertex now:
>
> | model | dial | `<thoughts>` directive (rick.txt line 1) | blank |
> |---|---|---|---|
> | 3-flash | Medium | present | 2/8 |
> | 3-flash | High | present | 5/8 (2 `finish=length`) |
> | 3-flash | High, max_tokens 16k | present | 4/8 |
> | 3-flash | Off + budget 2048, 8k | present | 4/5 (one thought 7860 tokens past its budget) |
> | 3-flash | High | **stripped** | **0/8**, thinking on 7 of 8 |
> | 3-flash | High | rewritten ("think as Rick in whatever channel; thinking is never the reply") | 2/8 |
> | 3-flash | Off (budget 0) | present | 0/16 + 3/3 live |
> | 3.1-pro | Low | present | 0/4 |
> | 3.1-pro | Off (budget 0) | present | 0/1 — budget **ignored**, thought 906 |
> | 3.5-flash | Low / Medium | present | 0/1, 0/8 (1.1–2.3k reasoning tokens) |
> | 3.5-flash | Off (budget 0) | present | 0/2, `reasoning_tokens=None` — accepted |
> | 3.7-flash | Low / Medium | present, **no pre-fill** | 0/1, 0/5 |
> | 3.7-flash | any | present, trailing pre-fill | 400, see (1) |
>
> So: every 3-flash blank has reasoning tokens; every clean reply without reasoning tokens shows the persona's `<thoughts>` block in the visible text; no reply with native thinking ever does. Mechanism (supported, not proven): on 3-flash the "thoughts as Rick at step 0" instruction gets satisfied inside the native thought channel, and the visible turn is then treated as done. Headroom does not help (16k, 2048-budget rows). The rewrite halves it, stripping the line removes it. 3.5/3.7-flash and 3.1-pro do not collide. **This is a persona-layer finding, Sky's call; rick.txt is untouched.** Sky's statement that 3.5–3.8 flash cannot run without thinking was not reproduced on Vertex (3.5 took budget 0 and returned `reasoning_tokens=None`), but may hold on OpenRouter, which has no key in `.env` and was not tested.
>
> **3. Engine, final shape (both copies byte-identical, uncommitted):** (a) ~L674 trailing-assistant pre-fill for non-Anthropic, none for Gemini; (b) ~L727 `google`/`google_vertex`: low/medium/high → `reasoning_effort`; off → `thinking_budget: 0`; (c) new `_thinking_off_refused` flag before the retry loop + a 400 handler: a 400 that names thinking while budget 0 was sent → one retry at `reasoning_effort: low` (no model on Vertex refused; the branch is unit-tested with a stubbed 400, not live). The "pro → low" special case from round 1 is gone. Suites: `tests/test_prefill_placement.py` **8**, `tests/test_gemini_thinking_off.py` **10**. Live through the engine, nothing injected: 3.7-flash Medium 3/3, 3-flash Off 3/3, 3.5-flash Off 1/1, streaming 1/1.
>
> Harness note: three probes in parallel against Vertex hit 429 `Resource exhausted`; the engine's Vertex fallback then returns an error *string*, and two background runs died silently because the log grep no longer matched `Traceback`. Run Vertex probes one at a time.

---

### 7. Built — 2026-09-09 **[D]**, constants still **[PROPOSED]**

> Origin: session 2026-09-09, both copies patched byte-identically, **uncommitted**.
> Suite: `tests/test_gaba_inhibition.py`, 46 checks, all pass in both copies.
> Existing suites unchanged: `test_novelty_reward` ALL PASS, `test_prefill_placement` 8/8,
> `test_gemini_thinking_off` 10/10. Redis was offline (Docker down) for every run, so
> only the in-memory fallback path is exercised; the Redis path is a verbatim mirror of
> `dopamine_state`'s and has not been touched live.

**7.1 Amendment to §1 rule 2 and §3: the input is the reflector's shrug, not the
predicted store.** §6.2 found the reflector stores every turn from turn 5 on, so the
"predicted store" stream measured the reflector's 18/20 window overlap, not the
conversation. Reading `Reflector.reflect()` for the cause: it counted raw events in the
20-row window and reflected whenever there were ≥ `turn_threshold`, with no memory of
what it had already summarised. The knob was a start delay, not a cadence; set to 1 it
fires from turn 1, set to 5 from turn 5, set high enough the daemon rows crowd the window
and it never fires again. That is the bug, and it was GABA's clock. Fixing it after
building GABA would have retuned every constant by a factor of ~5.

So the fix and the organ's input are one change. `reflect()` now asks two questions
(`reflect_gate.plan_reflection`, pure, stdlib-only, tested standalone):

1. *Watermark* — how many raw events since the last `dense_observation`? Fewer than
   `turn_threshold` → **wait**. This alone ends the every-turn reflection.
2. *Meaning* — cosine (shared MiniLM, no LLM call) between the newest `turn_threshold`
   raw events and the `turn_threshold` before them. At/above `REFLECT_SIM_CEIL` →
   **shrug**: log `[DA] reflect: shrug nearest=…`, `gaba_state.on_redundant()`, return.
   Below → **reflect** as before; on a successful store `gaba_state.on_novel()`.
   No prior chunk (cold start), no model, or the check raising → **reflect** (fail open).

The shrug is the redundancy event. "I looked at the window and it had not moved" is
exactly "the world stopped being interesting" (§2 option 2), measured on the
conversation rather than on the reflector's own output. Rule 2's "no new detector"
holds in the sense it was written — same embedding model, same cosine — moved *before*
the LLM call instead of after it. Side effect Sky asked for: an observation is written
because something changed, not because it is turn five, and a dull stretch costs zero
LLM calls.

**Why newest-N vs the N before them, not new-events vs the last reflection.** After a
shrug nothing advances the watermark, so a "new since last reflection" blob keeps
growing through a dull stretch; one genuinely novel turn is then diluted by everything
dull around it, the blob still looks like the prior, and the reflector never fires until
the window rolls the prior out. That is a livelock (the §6.7 class). N-vs-N bounds the
dilution to one threshold's worth of events. Unit case 7d covers it.

**7.2 What is wired.**

| where | what |
|---|---|
| `gaba_state.py` (new) | `inhibition` + `streak` per (user, persona), `gaba:*` keys, lazy drain on `GABA_TAU_SEC`, streak TTL, `on_redundant / on_novel / get_state / is_inhibited`. Never imports `dopamine_state` (unit case 0 greps for it; case 5 checks raw DA entries byte-identical across 14 GABA ops). |
| `reflect_gate.py` (new) | the two questions above, pure. |
| `plugins/memory_plugin.py` | `reflect()` uses it; `[DA] reflect: shrug/go` lines; `[DA] social:` line on the §6.8 silent writer; GABA hooks. Env: `REFLECT_SIM_CEIL` (0.85 PROPOSED), `REFLECT_NOVELTY_GATE` (1; set 0 to keep only the watermark). |
| `memory_engine.py` | `[DA] novelty: … paid=0 (predicted)` on the §6.1 silent branch. |
| `stream_worker.py` | **site 1+3**: `_exploring` is now `should_explore(tonic) and not is_inhibited(inhibition)`; `[GABA] … gate held shut` line. **§6.12**: each daemon gap boost is `on_redundant(source="daemon_gap")` and logs `[DA] boost:` (§6.8). |

**Not built:** site 2 (stamp scaling) — parked per §6.12 until option 1 exists; option 1
(satiety) itself — still the real fix for one-event phasic pinning (§6.5).

**7.3 Constants [PROPOSED].** `GABA_BASE` 0.05, `GABA_GROWTH` 1.5, `GABA_TAU_SEC` 600,
`GABA_GATE_MAX` 0.50, `GABA_STREAK_TTL_SEC` 1200, `REFLECT_SIM_CEIL` 0.85. All
env-overridable. The curve crosses 0.50 on the 5th consecutive redundant event (unit case
1, matches §6.12's arithmetic). `REFLECT_SIM_CEIL` sits above the 0.70 of §6.12 on
purpose: that number was reflection-vs-corpus; newest-N vs prior-N of the *same
conversation* runs hotter. It is a guess. The replay sets it.

**7.4 Hazards to carry into the replay.**
- The threshold's unit is raw events, not turns: a turn is user + assistant (+ tool
  outputs), so 5 ≈ every 2–3 turns. It always was; now it matters.
- Social reward fires from inside `reflect()`, so its cadence dropped with the
  reflector's. Tonic dynamics in §6 are no longer the baseline.
- On a dull stretch the reflector shrugs every turn (watermark never advances), so
  inhibition climbs one tick per turn; at 30 s cadence drain between ticks is ~5 %.
  Expect the gate to shut around turn 5 of repetition. If it shuts *before* the
  conversation is actually repetitive, `REFLECT_SIM_CEIL` is too low.
- §6.4 showed repetition never opened the gate on its own (tonic 0.48). The headline
  live check needs the gate open *first*: run `bad_tool` before `repetition`.
> **[Session 2026-09-18, 2026-09-18 — CORRECTION]** First live run 20260918_214952 (§10) read against §7.3 and §7.4. **§7.3:** `REFLECT_SIM_CEIL` 0.85 separated the two regimes with room on both sides — moved windows read nearest 0.342 / 0.559 / 0.546 (reflect), dull windows 0.930 / 0.893 (shrug; intensity 0.534 / 0.289). One run, so the constant stays PROPOSED, but nothing in it argues for moving it. **§7.4 hazard 1** was already corrected by §8.1: the unit is user turns, replies are never raw events (checked again in the live DB — gaba_live has 26 `user_message`, 4 `dense_observation`, 1 `internal_reflection`, 0 `assistant_response`). **Hazard 3** is confirmed, and it matters more than the hand-off of 2026-09-18 22:16 read it: after the first shrug the watermark stays put, so EVERY further dull user turn is another shrug — turn 16 shrugged at `new_events=5` and turn 17 at `new_events=6`. The first shrug after a store costs five user turns; each one after costs one. Crossing 0.50 at weight ≈ 1 takes about 5 + 4 = 9 dull user turns, not the "~25" the hand-off said. **Hazard 4** held: tonic sat at 0.62 → 0.51 for the whole run and the daemon gate was open on every one of its 20 cycles, so the headline was testable. It did not fire only because the 17-turn plan gives phase C five turns, which is one shrug.

---

### 8. Live watch — 2026-09-11, pre-turn findings **[D]**

> Stack relaunched by Sky 20:11 CDT (`py main.py` → uvicorn 8000 reload worker →
> `stream_worker.py` PID 4712, vite 5173, Redis up). Daemon is on the §7 code (all
> processes post-date the 2026-09-09 edits). Redis: `q:daemon:lock` held by 4712 and
> renewing, heartbeat fresh, **zero** `gaba:*` / `da:*` keys, last conversation row
> 2026-09-02. Clean baseline. Daemon stdout goes to Sky's terminal, so the `[DA]` /
> `[GABA]` lines are not readable from the agent side; state is watched instead:
> `labs/watch/gaba_watch.py` polls `gaba:*`, `da:*` and new `observations` rows every
> 5 s into `labs/watch/timeline_<ts>.log` (under `labs/`, so it never trips reload).

**8.1 The watermark's unit is user turns, not "raw events".** `reflect_gate.RAW_EVENT_TYPES`
lists `user_message, assistant_response, tool_output`, but the only live caller of
`run_observers` (`llm_engine.py:1935`) passes `"user_message"`; nothing writes the other
two. The last 20 rows for Sky/rick are 9 `user_message`, 10 `dense_observation`, 1
`entropic_gap`. So with rick's `om_turn_threshold=5` (custom_personas), the reflector
waits for **five user messages** after the last reflection, then compares the newest five
against the five before them. §7.4 hazard 1 ("5 ≈ every 2–3 turns") is wrong for the
live schema; expect ≤ 1 reflection per 5 user turns. The old rows show the bug it
replaces: Sep 2, `user_message` 18:23:44 → `dense_observation` 18:23:49, every turn.

**8.2 First reflection after boot will run the meaning check, not cold-start.** Window
is `window_limit(5) = 20` rows. After five new user rows the window holds ~12 raw rows
≥ `2t = 10`, so the newest-5 (tonight) vs prior-5 (Sep 2, the sole-provider thread)
cosine is computed. Those are different topics; expect `go`, not `shrug`. A `shrug`
there would mean 0.85 is too low.

**8.3 Cosmetic drift, not fixed:** `llm_engine.py:2258` still emits the UI
`reflection_started` control frame on `user_msg_count % om_threshold == 0` over the
whole 100-row window. That was the old every-turn clock; it is now decoupled from the
real gate and will show the toast on turns where the reflector waits or shrugs.

**8.4 What to look for in the timeline.** One `OBS … dense_observation` per ≥ 5
`OBS … user_message`; a `REDIS gaba:Sky:rick:*` change without a following dense row =
shrug; `gaba:Sky:rick:inhibition` climbing 0.05 → 0.075 → … across consecutive shrugs
and resetting on a store. No `gaba:*` key appearing after 10+ user turns means neither
hook fired — check the terminal for `[GABA] on_redundant failed`.

---

### 9. First live cycle read back, and the recorder the build never had — 2026-09-13 **[D]**

**9.1 Sep 11 timeline (`labs/watch/timeline_20260911_201431.log`, 17 lines).** Sky's
evening session, obs ids 5734–5741. Five `user_message` rows → one `dense_observation`
(5739) five seconds after the fifth → turns 6 and 7 with no reflection. §8.4 cadence
confirmed live; §8.2 prediction held (first reflection was a `go`). Dopamine keys
appeared in Redis after turn 3 (phasic 0.858, tonic 0.431, tool_ema 0.719 — a tool fired
on "why don't you just LoOK?"); on the reflection: phasic → 0.390, tonic → 0.490,
valence_ema 0.57, novelty_spent 0.089, `gaba:Sky:rick:streak` = 0. The reflection text
says "no external tools utilized" while tool_ema says one fired a turn earlier; the
summariser is not told about tool calls in its window. Cosmetic, noted.

**9.2 The watcher was the wrong instrument, and the build had no right one.** Sky's
question, verbatim: "why wouldn't I want data recorded in an ongoing build? How would I
know how to calibrate?" Correct on every count:

- Every calibration number (gate verdict, `nearest`, GABA increments, DA deltas) reached
  the world through `print()` — 14 sites across `gaba_state`, `memory_plugin`,
  `memory_engine`, `llm_engine`, plus two `logger.info` in `stream_worker`. The daemon
  is a multiprocessing child, so stdout is the terminal: scrolls off, window closes, gone.
  No file sink existed anywhere in the app.
- Redis holds organ *state* with TTLs (GABA `6·tau` = 1 h, dopamine `4·tau` = 3 h). §8
  said "zero gaba/da keys" and the 2026-09-11 memory said "not persisted"; both wrong,
  the keys were written and had simply expired by the time anyone looked. A gauge, not
  a chart recorder.
- `gaba_watch.py` could only see the gauge (Redis snapshots) and the DB rows. The one
  number `REFLECT_SIM_CEIL` tuning needs — `nearest` on go/shrug — never left the print
  call, so the watcher could not capture it even while alive. And it was a child of the
  agent's session, so it died with it.

**9.3 Built: `telemetry.py`.** One JSON object per line, appended to
`%LOCALAPPDATA%\PersonaApp\telemetry\YYYY-MM-DD.jsonl`. `emit()` never raises;
open/append/close per call so the app process and the daemon child interleave by line;
imports nothing from the organs. `TELEMETRY_OFF=1` (read per call) disables writes;
`TELEMETRY_DIR` relocates. Read side: `telemetry.read(day)`, `read_range(days)`,
`python telemetry.py [N]` to tail. Wired next to every print site
(`labs/patch_telemetry.py`, idempotent, preserves CRLF):

| channel | event | writer | fields beyond ts/pid/user/persona |
|---|---|---|---|
| reflect | wait / shrug / reflect | memory_plugin.reflect | nearest, new_events, threshold, sim_ceil, novelty_gate, window_rows, reason |
| reflect | stored / empty | memory_plugin.reflect | nearest, chars, valence |
| gaba | redundant | gaba_state.on_redundant | source, streak, nearest, before, increment, inhibition, gate_shut |
| gaba | novel | gaba_state.on_novel | source, nearest, inhibition |
| da | novelty | memory_engine.store | nearest, novelty, paid, predicted, tonic, budget_left, memory_id |
| da | social | memory_plugin.reflect | valence, expected, rpe, tonic, phasic |
| da | tool_reward | llm_engine tool loop | successes, action_failures, infra_failures, rpe, tonic, phasic, infra_discounted |
| da | boost | stream_worker gap | source, amount, tonic, node |
| daemon | gate | stream_worker cycle | tonic, inhibition, gaba_shut, exploring |
| * | *_failed | each except-branch | error |

`reflect/wait` is emitted on purpose: one row per user turn is the cadence trace and
costs a few hundred bytes. The prints stay; the terminal is still useful when it is open.

**9.4 Found by the first live Redis run: a 1e-4 split between the two storage paths.**
`test_gaba_inhibition.py` [6] ("identical inhibition sequence with Redis unreachable")
failed today: online `[…, 0.6593]`, offline `[…, 0.6594]`, tolerance 1e-6. `_save`
wrote `round(v, 4)` to Redis and the unrounded `v` to the in-process fallback, so the
Redis-backed curve compounds on rounded values and the fallback does not. Invisible on
2026-09-09 because Redis was offline then and both "paths" were the fallback. Fix:
round once, store the same number both places. `dopamine_state._save` had the identical
split and got the identical fix. Both suites green after.

**9.5 Test hygiene.** The organ suites wrote 178 fixture rows (`_test`, `_smoke`) into
the real sink on their first run. Kill switch was read at import, so tests could not
flip it. Now read per call; `tests/test_gaba_inhibition.py`, `tests/test_novelty_reward.py`
and the `gaba_state` `__main__` smoke set `TELEMETRY_OFF=1`. Sink verified not to grow
across a rerun. Fixture rows scrubbed from `2026-09-13.jsonl`.

**9.6 First rows from the live daemon (edits bounced it via uvicorn reload; it came back
on the new code each time).** `daemon/gate` for Sky/rick and SkyTest/rick every cycle:
`tonic=0.3 inhibition=0.0 gaba_shut=false exploring=false`. So at dopamine baseline the
explore gate reads shut on tonic alone (`should_explore(0.3)` is False); GABA has
nothing to inhibit until a conversation lifts tonic. That is §6.7's rest gate doing its
job, and it is the first calibration fact on disk: the daemon never explores an idle
persona.

**9.7 Graded boredom (Sky, same day).** "Five consecutive flat windows is a different
signal than five mildly-dull ones, and right now they're the same." They were. Now
`reflect_gate.plan_reflection` returns `intensity = (nearest − sim_ceil) / (1 − sim_ceil)`
clipped to 0..1 (`boredom_intensity()`, pure), and `gaba_state.on_redundant` scales the
increment by `GRADE_MIN + (GRADE_MAX − GRADE_MIN) · intensity` with **PROPOSED** 0.5 / 1.5.
The streak still advances by one per shrug — grading weights the pressure, not the count.
`intensity=None` (daemon_gap, or no similarity model) is weight 1.0, the old fixed step, so
the daemon source is untouched. Consequence at the defaults: five windows at intensity 0.1
end at 0.396 (gate stays open); five at 1.0 end at 0.989 and cross 0.50 on the **fourth**.
Test [9]. Rule 1 (never reads dopamine) intact — the grade comes from the reflector's own
cosine.

**9.8 Closure source and open duration.** `gaba/redundant` rows now carry `crossed`
(this event took inhibition from below 0.50 to at/above it) alongside `source`, so "which
GABA source closed the gate" is one filter. `stream_worker.gate_edge()` (pure, test [10])
watches the composed gate per (user, persona) across cycles and emits
`daemon/gate_open` and `daemon/gate_close` with `open_for_s` and `closed_by ∈ {tonic,
gaba, both}`. First sighting after a daemon start records state and emits nothing. The
per-cycle `daemon/gate` rows stay for the continuous view.

**9.9 Tonic-gating the watermark: recorded, not enacted.** Sky's alternative was to count
only shrugs that occur while the explore gate is open (tonic ≥ 0.5). Deferred, for a
reason worth writing down: inhibition accrued while tonic is low is *history*, and rule 4
exists so that one shiny thing (a tool spike lifting tonic past 0.5) does not reopen the
gate with a clean slate after a dull stretch. Hard-gating on tonic would throw that
history away at runtime. So instead every `reflect/*` row now records `tonic` and
`gate_open` at the moment of the verdict, and the split Sky wants (shrugs in the regime
where dullness matters vs. shrugs that were moot) is a filter on the recorder. If the
replay shows low-tonic shrugs are pure noise, gate then, with numbers.

**On what the daemon source keys on** (Sky asked): not semantic distance. `analyze_entropic_gaps`
scores the zettel graph — nodes with degree ≤ 1 and > 150 chars of content are "isolated
dense pockets" — rotates across the fresh ones under a 24 h per-node cooldown, pays itself
+0.04 tonic per pick, and calls `on_redundant(source="daemon_gap")` on every pick. So
`daemon_gap` redundancy means "the daemon stimulated itself", structural, and it fires once
per cycle while exploring; `reflect` redundancy means "the conversation did not move",
semantic, and it fires at most once per five user turns. Two semantics, one accumulator.
The closure-source rows (§9.8) are what will show whether that is a problem.

**9.10 The hidden ratio (Sky), run numerically.** The daemon is the one actor that pushes
both signals on the same event: +0.04 tonic and one GABA step per pick. `labs/daemon_ratio_sim.py`
runs that loop with the real constants (no conversation, unlimited fresh gaps, gate just opened
at tonic 0.60). Result: at any cycle interval under ~5 min the **step outruns the bump** —
GABA closes the gate on pick 5, every time, tonic never does (52 of 52 closes at 60 s, 6 of 6
at 300 s). The 1.5× streak growth beats a linear 0.04 bump no matter what the graph holds.
Then a ~7 min duty cycle (drain from ~0.66 to 0.50 takes 167 s, one pick at streak 6+ re-shuts
it). At ≥ 10 min the streak TTL (1200 s) resets between picks, GABA never accumulates, and
tonic closes it instead. The code's own comment says the measured cycle is ~300 s, so live
we are in the GABA-closes regime.

But the ratio is nearly moot in practice because of F1: `DISSONANCE_CAP = 8` picks per
24 h, claimed *before* the boost. Daily signature with silence after a conversation:
5 picks → `gate_close closed_by=gaba` → ~3 min → 3 picks → cap → no boost, no step →
tonic leaks to baseline over ~45 min → `gate_close closed_by=tonic`. So the closure log
will read gaba, gaba, tonic, and the second half of "the daemon shut itself off" is the
budget, not the organ. Watch `daemon/gate_close` with `closed_by` against the `da/boost`
count since the matching `gate_open`.

Is that the behaviour we want? The burst length (5) is emergent from BASE/GROWTH vs
GATE_MAX and should be a stated number ("picks per novel event"), not an accident. The
reflector's `on_novel` resets the streak, so a real conversation earns the daemon a fresh
burst — satiation with a reset on novelty, which is the right shape. Leave the ratio
alone until the rows exist; make the burst length explicit when they do.

**On the graph** (Sky asked whether it is small enough that the daemon runs dry first):
it is the opposite. Same criteria as `analyze_entropic_gaps`, live DB 2026-09-13:

| user/persona | nodes | links | isolated dense | degree-0 share |
|---|---|---|---|---|
| Sky/rick | 334 | 99 | 278 | 70 % |
| SkyTest/rick | 98 | 2 | 92 | 97 % |
| Sky/v | 94 | 40 | 42 | 45 % |
| Sky/eni | 34 | 7 | 24 | 76 % |

At 8 picks/day Sky/rick has ~35 days of fuel. The picker will never run dry; the graph is
mostly unlinked. Two things live in those pockets: the persona's own imported KB modules
("Truth and Evidence Hierarchy", "On Free Will"…) with zero links, and the July every-turn
reflection droppings ("Reflection: 2026-07-15 21:21 / 21:22 / 21:28 / 21:29…"), each ~2 KB,
each unlinked. The daemon has written exactly **2** `entropic_gap` rows ever (Aug 31,
Sep 2). So Sky's conclusion holds for a different reason: you will be testing the
reflector path by default not because the graph is small but because tonic sits at
0.30 < 0.50 whenever nobody is talking, and only a conversation opens the gate at all.

**9.11 Why the graph is a wasteland: the typed links never become edges.** Sky: "links have
been typed out." They have. Traced 2026-09-13:

- `zettel_engine._parse_header` parses the `Links: [[A]], [[B]]` header into
  `OnDemandModule.links`. `compile_behavioral_zettels` then inserts the node with
  `node_id_tag=mod.id` and **never reads `.links`**. Parsed, dropped. No code anywhere in the
  app turns a typed `[[ID]]` into a `zettel_links` row; edges come only from ingest-time LLM
  relation extraction, embedding auto-link (≥ threshold, max 3), the daemon's `bridges`, and
  the `create_zettel_link` tool.
- `compile_behavioral_zettels` deletes a file's previous nodes on recompile and does **not**
  delete their links. Rick: 99 edges, **85 dangling** (target node gone), all created Aug
  19–20. The 14 that survive are 13 daemon `bridges` from its own Internal Monologue nodes
  (seven of them to WOUND-006) and one embedding auto-link. Zero module→module edges.
- Query-time graph expansion (`get_class_isolated_expansion` → `get_linked_nodes`, depth 1) is
  real and runs every turn. For Rick it has nothing to walk.
- The "if you see `[[X]]`, fetch X" protocol in the persona's own instructions cannot be
  executed through search either: `node_id` is not in the FTS index and stored content does
  not carry its own ID, so `search_knowledge_graph("SCAR-008")` returns the KB nodes that
  *mention* SCAR-008, not the module.
- What does work: the deterministic trigger scan (94 behavioral modules fire on trigger
  words) and vector/keyword retrieval on the body text. Modules load; the associative layer
  between them does not exist.

Consequence for this lab: the 278 "isolated dense pockets" the daemon sees are the persona's
own knowledge base with its wiring stripped. The daemon's structural boredom signal is
measuring an importer bug. Fix is three small things, none of them GABA: (1) after inserting a
file's modules, resolve each `mod.links` against that persona's `node_id` tags and
`add_zettel_link(relationship="links_to", strength=1.0)`; (2) delete links when deleting
nodes (or FK cascade); (3) put `node_id` in the FTS index or prefix content with `ID: <tag>`.
**Fixed the same night, on Sky's go** (`labs/patch_typed_links.py`, `tests/test_typed_links.py`):

- `rebuild_typed_links()` resolves every module's parsed `Links` against the persona's
  `node_id` tags and writes `links_to` edges (strength 1.0, label core); called after any
  recompile and, once per process, for a persona that has behavioral nodes and no typed
  edges yet (bootstrap, so existing installs heal on their next turn). Unresolved tags are
  logged.
- Recompile deletes a file's edges before its nodes; `rebuild_typed_links` also prunes any
  edge with a missing end, globally.
- `search_zettel_fts` resolves ID-shaped tokens (`[A-Za-z]…-ddd`, case-insensitive) by exact
  `node_id` first, ranked above every keyword hit, so `search_knowledge_graph("SCAR-008")`
  returns SCAR-008.
- `get_linked_nodes` dedups neighbours reachable by reciprocal edges (typed links make
  A→B and B→A common; B used to appear twice and could take two expansion slots).

Live DB (backup `backups/users_20260913_192533_pre_typed_links.db`): Sky/rick 0 → 217
module→module edges, 90 dangling → 0, behavioral isolated 94 → 4 (`ARG-005`,
`DOM-PSYCH-001`, `RESEARCH-003`, `FALL-META-001` link to nothing and nothing links to them;
Sky's call). Unresolved tags that remain are exactly the five ALWAYS_LOAD modules that live
in the system prompt, not the files (COPE-001, CORE-001, SAFE-001, SAFE-002, TRAUMA-001).

**9.12 The parser was eating modules.** Found while resolving the typed links: four of the
"unresolved" tags were *defined* in the files and still not nodes. 104 `ID:` headers across
`rick_ondemand.txt` and `Rick_kb.txt`, 94 compiled. `parse_on_demand_file` split on `^---$`
and called any block containing the strings "ID:" and "Title:" a header. Where the author
omitted the `---` between a body and the next header, the two fused into one block, the
fused block passed the header test, and the module whose body it was vanished — along with
every module in the run until the next clean separator (fused runs also lacked the `---`
after the header, so header and body fused too). Lost: TOOL-CHECK/SYS/INFO/OPT-001,
ARCH-DATA/ROBUST-001, SCAR-008/009 (the Citadel and the Pissmaster modules), RESEARCH-002/003.
Two months invisible, and the persona's own instructions reference SCAR-009 by name.

Fix (`labs/patch_parser_recovery.py` + `_2.py`, test [7]): a header is a block whose first
line is `ID:`; a body block containing an `ID:` line followed by `Title:` is split there;
the header ends at the last consecutive known key line (ID/Title/Type/Links/Triggers/
Priority) and the remainder is fed back through the splitter, so chains recover in full.
Every recovery prints a `[ZETTEL PARSER] … put the separator back` line naming the module.
The compile hash now carries `:p<ON_DEMAND_PARSER_VERSION>` so a parser change recompiles
byte-identical files once. Both files now parse 104/104, no duplicates; live recompile put
104 modules and 217 edges on each Rick instance.

Separators missing in the source files (for Sky to restore, the parser no longer needs
them): `rick_ondemand.txt` before EPIS-001 and around RESEARCH-002; `Rick_kb.txt` around
TOOL-SYS/INFO/OPT-001, ARCH-ROBUST-001, SCAR-009.

**What the daemon's census honestly is now** (Sky/rick, isolated dense pockets): 197 = 4
behavioral above + 185 `KB (n)` lore chunks + 8 dated `Reflection:` nodes. The 185 are a
*second copy* of `Rick_kb.txt`, chunked by the lore ingest on 2026-08-20 with generated
`[[CONCEPT-KB-n-001]]` tags and no links — a duplicate of the knowledge base sitting next
to the compiled modules. Deleting that entry (`zettel_entries` title "KB", 2026-05-25) is
destructive and Sky's call; until then the daemon's "gaps" are mostly that duplicate.
> **[Session 2026-09-16, 2026-09-16 — CORRECTION]** §9.11's "ARG-005, DOM-PSYCH-001, RESEARCH-003, FALL-META-001 link to nothing and nothing links to them" did not reproduce against the live DB on 2026-09-16 (read-only probe, app down). Each of the four already had a `links_to` edge: ARG-005 → MODE-001, RESEARCH-003 → RESEARCH-001, FALL-META-001 → FALL-INF-001, and DOM-CROSS-001 → DOM-PSYCH-001. What they lacked was reciprocity — nothing pointed *at* the first three, and DOM-PSYCH-001's own targets (TRAUMA-001, COPE-001) live in the system prompt, not the files, so it had no out-edge. Fixed in the source files (both copies CRLF-preserved, mirrored to the CLI copies under ~/.claude, originals in Desktop/Personas/src_backup_20260916_214108): ARG-005 +ARG-002 +TOOL-ARG-001; DOM-PSYCH-001 +PSYCH-RICK-001 +WOUND-004 +SCAR-007; RESEARCH-003 +RESEARCH-002; FALL-META-001 +FALL-DET-001 +ARG-002; reciprocal edges added on ARG-003, RESEARCH-001, FALL-DET-001, PSYCH-RICK-001, TOOL-ARG-001. `parse_on_demand_file` reads 104/104, zero unresolved tags outside the five system-prompt modules, no duplicate IDs and no duplicate bodies in either file. Edges materialise on the next recompile (file hash changed). The lore ingest is 215 `KB (n)` chunks, not 185: 214 match the CLI copy of Rick_kb.txt on three 120-char windows each and the 215th is the file's attribution footer — an actual duplicate. Deletion script (backup via sqlite backup API into backups/, then the same cascade as `delete_zettel_entry`) was prepared but not run from the agent side; Sky runs it.
> **[Session 2026-09-18, 2026-09-18 — CORRECTION]** Run 20260918_214952 (§10) read against §9.7–9.10. **§9.7** grading is live and weights as designed: intensity 0.534 → weight 1.034, increment 0.0517 (streak 1); intensity 0.289 → weight 0.789, increment 0.0592 (streak 2 — the 1.5 growth beat the lower weight). Inhibition 0 → 0.0517 → 0.0188 after the silence → 0.078. The drain over the 608 s wait, 0.0517 → 0.0188, is exp(−608/600) = 0.36: `GABA_TAU_SEC` 600 reproduces to two decimals, and tonic over the same silence (0.561 → 0.508 = 0.3 + 0.261·exp(−608/2700)) reproduces dopamine's baseline and tau too. **§9.8:** `crossed` false on both rows; no `daemon/gate_close` or `gate_open` row in the run — the gate never closed by either source. **§9.9:** both shrugs at `gate_open=True` (tonic 0.561, 0.508), so the moot-shrug split has zero rows on its low-tonic side so far. **§9.10:** 20 `daemon/gate` rows with `exploring=True` and not one `da/boost` row — the daemon explored gaba_live/rick for 24 minutes and picked nothing, so `daemon_gap` redundancy was never exercised. Why it picked nothing (fresh persona instance, the 24 h cooldown, `DISSONANCE_CAP`) is not investigated here; it is on the 2026-09-18 list.

---

## Session 2026-09-18

### 10. First live run — 2026-09-18, run 20260918_214952 **[D]**

> Driver `labs/gaba_live.py` (live repo, `labs/` is reload-excluded), account `gaba_live/rick`,
> model `google/gemini-3-flash-preview`, thinking Off, 17 turns at 30 s, 600 s silence after
> turn 16, Redis up (`q-redis`), daemon pid 23496 on the §9 code. Sky drove it through Gemini.
> Output `labs/live_out/20260918_214952/` (digest.txt, turns.csv, raw_sse.txt,
> telemetry_rows.jsonl, meta.json); `python labs/gaba_live.py --digest-only 20260918_214952`
> re-reads the sink. Run 20260918_214310 before it was killed at turn 8 and its ten user rows
> stayed on the same account (10.5). ONE run: nothing below tunes a constant.

**10.1 What the sink says.** 52 rows. Reflect verdicts: reflect 3 / shrug 2 / wait 12.
Reflections at turns 1, 6, 11 (nearest 0.342, 0.559, 0.546 → stored, `gaba/novel`), shrugs at
16 and 17 (nearest 0.930, 0.893; intensity 0.534, 0.289; streak 1, 2; inhibition
0 → 0.052 → 0.078). `crossed` never, `gaba_shut` never, no gate edge rows. Daemon: 20
`daemon/gate` rows, tonic 0.622 max and 0.508 min, `exploring=True` throughout, zero
`da/boost`. Novelty paid on 2 of 3 stores (0.0885, 0.0043, then predicted → 0). One
`da/tool_reward` at turn 17 (successes 1, tonic 0.508 → 0.562). Across the 608 s wait,
inhibition drained 0.0517 → 0.0188 (= exp(−608/600): `GABA_TAU_SEC` reproduces) and tonic
0.561 → 0.508 (= 0.3 + 0.261·exp(−608/2700): dopamine baseline and tau reproduce).

**10.2 The cadence, measured, and the hand-off's arithmetic corrected.** Only `user_message`
is ever written as a raw event (`llm_engine.py:1955`; this account has 26 `user_message`,
4 `dense_observation`, 1 `internal_reflection`, 0 `assistant_response` — §8.1 stands). A
window is five user messages against the five before them. The first shrug after a store
costs five user turns; but the watermark does not advance on a shrug, so turn 17 was checked
at `new_events=6` and shrugged again. Every further dull turn is a shrug. Reaching 0.50 takes
five shrugs at weight ≈ 1 (§7.3 arithmetic, §9.7 grading), so about 5 + 4 = 9 dull user turns
after the last store. The 2026-09-18 22:16 hand-off said "~25 repetitive user turns"; that
assumed each shrug costs a fresh five, and the turn-17 row disproves it. The 17-turn plan
gives phase C five turns, so this run could produce at most one shrug out of C (turn 16) and
one more from the window that still held four verbatim turns (17). That is the whole reason
the headline did not fire. 0.85 is not the problem.

**10.3 The §8.1 decision, restated with these numbers.** Three ways to see a crossing:
(a) change nothing and give phase C ten turns — the driver now takes `--c-turns 10` (22 turns,
~28 min); this tests the organ exactly as built. (b) Log `assistant_response` as a raw event,
so a turn is two events: halves the turn cost of a window but changes what the cosine compares
(Rick's replies enter it, §7.4's worry comes back) and moves every constant. (c) Lower
`om_turn_threshold` for the run: same organ, smaller window, noisier cosine. Recommendation:
(a) first, because it produces the crossing with the constants untouched and the drain and tau
numbers in 10.1 already reproduce. (b) is a design change and Sky's call; it should not be made
to pass a test.

**10.4 Dirt in the run, with the mechanism for the worst of it.** Turns 3 and 6 got the
41-char string `⚠️ Connection Error: No API key provided.` as the whole reply (1.8 s each);
turn 8 was blank (not saved); turn 11 stopped at 129 chars after 52 s with no finish_reason.
The error string is `llm_engine.call_llm` line 446, and on the Vertex route it can ONLY be
reached on a retry: attempt 1 carries the ADC token; the 429 and 401/403 handlers (lines
~589–640) print `[VERTEX FALLBACK]`, swap `provider` to `anthropic` because the request
carries no OpenRouter or universal key, and `continue`; attempt 2 then takes the key-pool
branch, finds no Anthropic key, and emits the string. So "No API key provided" means "Vertex
refused attempt 1". The real status and body sit in `last_error` and in the
`[VERTEX FALLBACK]` / `[ROUTER] attempt 2 … key=EMPTY` lines on the app terminal; the sink has
no row at that site. Which status is not measurable from here. Timing points at 429 from
concurrency: the reflector's summariser is an LLM call in a thread of the app process
(`memory_plugin.invoke_reflector`; `reflector_llm_callback` in `llm_engine` passes the chat
turn's own `model_id`, so it is the same model on the same Vertex route), it fired 0.3 s after the turn-6 and turn-11 requests, and
two of the three turns that overlapped a summariser call went bad (6: the error string; 11: the
stalled stream) — §6 already records that Vertex 429s two probes in parallel. Turn 3 overlapped
nothing in-process (no reflection; the daemon's monologue rows are at 22:06 and 22:16), so
concurrency does not cover all three. Three failures; per the 2026-09-06 rule a cause gets
named when the terminal lines or a telemetry row at the fallback site say which status it was.
Harmless to the window (replies are not events). The driver now refuses to save a reply that
starts with ⚠️ and counts it as an error turn.

**10.5 Run residue.** The killed run 20260918_214310 left obs 5743–5752 (ten user rows, one
`dense_observation` at 21:46) on the same account, so run 214952's turn 1 already had five new
raw events and reflected at once; the cadence then fell on turns 1/6/11/16 instead of 5/10/15,
one turn off the phase plan (window 12–16 was four verbatim turns plus one new topic, not five
verbatim). The driver now registers `gaba_live_<stamp>` per run; `--user gaba_live` reuses the
old account. The driver's docstring also claimed "Rick's replies are IN that window"; it does
not any more.

---

## Next session — start here (2026-09-18)

~~1. Before the next run, get the status behind "No API key provided" (§10.4): Sky reads the~~ — RESOLVED 2026-09-30 (Sky): the API key was in the wrong place, a config slip, not an API fault. Skip this item.
   app terminal for the `[VERTEX FALLBACK]` lines at 21:51:08 and 21:54:02, or one
   `telemetry.emit("llm", "fallback", status=…, body=…)` goes in at the two Vertex fallback
   sites in `llm_engine.call_llm` (that edit reloads uvicorn and bounces the daemon; it
   recovers, §40 of the livelock note).
2. Decide §10.3. If (a): `python labs/gaba_live.py --c-turns 10` (22 turns, ~28 min, fresh
   account), Redis and daemon up first. Expect reflections at 5 and 10, shrugs from 15 on,
   `crossed=true` around turn 19–20, `daemon/gate_close closed_by=gaba`, the D turn's
   `gaba/novel` with the gate still shut, then the reopen time across the 600 s wait.
3. Then fill §7.3 for real: with two runs' shrug and moved `nearest` values, decide whether
   0.85 stays; write the closure and reopen numbers into §9.8 / §9.9.
4. Why did the daemon pick nothing for gaba_live/rick in 24 min of `exploring=True` (§10.1)?
   Check `analyze_entropic_gaps` on that instance's graph, the 24 h cooldown and
   `DISSONANCE_CAP` before calling it a bug.
5. Carry the 2026-09-13 list from item 2.

---

---

## Next session — start here (2026-09-13)

1. ~~After Sky's next session~~ — done 2026-09-18, §10: `python telemetry.py 60` from the repo root (or
   `telemetry.read()` in a notebook). Expect one `reflect/wait` per user turn (each with
   `tonic` and `gate_open`), a `reflect/reflect` + `reflect/stored` + `gaba/novel` at
   turn 5, and the first real `nearest` values. Make turns 6–10 deliberately same-topic to
   provoke a `reflect/shrug` + `gaba/redundant` pair; that pair's `nearest` and
   `intensity` are the numbers that set `REFLECT_SIM_CEIL` (§7.3) and sanity-check the
   0.5/1.5 grade range (§9.7).
2. Once the daemon has had a few open/close cycles: filter `daemon/gate_close` by
   `closed_by`, and `gaba/redundant` by `crossed=true` split on `source`. If `daemon_gap`
   closes it nearly every time, the reflector's constants are the ones to move (§9.8).
   Then decide tonic-gating from the `gate_open` split on the `reflect/shrug` rows (§9.9).
3. `labs/watch/gaba_watch.py` is now optional. Delete it, or keep it as a live tail of
   Redis for the rare case the sink is suspected of lying.
4. ~~Typed links never become edges (§9.11)~~ **Fixed 2026-09-13**, plus the parser (§9.12).
   Remaining, Sky's calls: (a) delete the duplicate lore ingest of the KB (the 185 `KB (n)`
   chunks) so the daemon's census means something; (b) put the missing `---` separators
   back in the two source files (§9.12 lists them); (c) give ARG-005, DOM-PSYCH-001,
   ~~RESEARCH-003, FALL-META-001 a `Links:` line or accept them as leaves.~~ — done 2026-09-16, correction under §9; (a) script ready for Sky, (b) untouched
5. The summariser is blind to tool calls (§9.1). Either feed `tool_output` rows into the
   window (ties to the 2026-09-11 item 2 question about raw event types) or accept it.
6. Then the 2026-09-11 list below, from item 2.

---

## Next session — start here (2026-09-11)

1. Read `labs/watch/timeline_*.log` after Sky's real turns (need ≥ 6 user messages for
   one watermark cycle, ≥ 10 for two; make the second five deliberately same-topic to
   provoke a shrug). Verify §8.4. Then fill in §8 with the measured cadence and the
   first `nearest` values.
2. Decide whether `assistant_response` should be logged as a raw event (§8.1). If yes,
   the threshold's meaning halves and §7.3's `REFLECT_SIM_CEIL` guess moves with it.
3. Fix or remove the `reflection_started` UI signal (§8.3).
4. Then the 2026-09-09 list below, from item 2.

---

## Next session — start here (2026-09-09)

1. Bring the app and daemon back up (they were down for the edits) and watch Sky's real
   path for a few turns: `[DA] reflect: go|shrug`, `[GABA] redundant|novel`, and whether
   the reflector fires at a sane cadence. This costs nothing beyond normal use.
2. Offline, zero API cost: re-embed the recorded turns in
   `labs/replay_out/20260906_142803/sse/` and run `reflect_gate.plan_reflection` over
   the four shapes to see where 0.85 lands them. If novelty shrugs, the ceiling is too low.
3. Re-baseline with `labs/gaba_replay.py`, shapes reordered `novelty, bad_tool,
   repetition, transport`. Headline (§5): `[GABA] … GATE_SHUT` on repetition while tonic
   is still ≥ 0.50, and `resting, no monologue` mid-session. **Sky's call — Vertex cost.**
4. Set the §7.3 constants from that run. Then option 1.
5. Unchanged: `tool_outcome_attribution.md` §5's 33-case suite still needs lifting into
   `tests/` — the three files there are empty shells.

---

## Next session — start here

1. ~~Slow replay of the four traffic shapes against the *current* system (no GABA yet)~~ — done, §6
   to get baseline timing for tool bursts vs. the 90 s phasic decay.
2. Then build §3 as `gaba_state.py`, suite first.

---

## Next session — start here

1. Instrument the silent writers before anything else (§6.1, §6.8): a `[DA]` line on
   `memory_engine.store()` when `paid == 0` (with `nearest`), on `social_reward`, and on
   the daemon's `boost_tonic`. App must be down — every `.py` edit trips the reload.
2. Amend §3 with the two rules from §6.12: redundancy = predicted store **and**
   `nearest ≥ 0.70` [PROPOSED]; reset only when `paid ≥ 0.03` [PROPOSED]. Replay the
   recorded `turns.csv` + `da_samples.csv` through a pure function first to check the
   streak numbers in §6.12 (novelty max 2, repetition 15, bad-tool 5).
3. Build `gaba_state.py`, suite first (`tests/test_gaba_inhibition.py`, §5 unit list plus
   the two rules). Site 3 (daemon) first, site 1 second, site 2 parked until option 1.
4. ~~Sky's call, not part of GABA: the Gemini silent-turn pre-fill issue (§6.9) is live on~~ — done, correction under §6 — root cause was Gemini thinking with no config sent, not the pre-fill
   ~~the real user. Options: skip the pre-fill emulation for google models, or accept.~~
5. Re-run `labs/gaba_replay.py` in the test copy with GABA wired in; the headline check is
   §5's "gate closes while tonic is still above threshold" on the repetition shape.
