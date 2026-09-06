# GABA Inhibition — Lab Notes

## Status: BASELINE REPLAYED (§6), TWO RULES NEEDED IN §3, NOTHING BUILT (2026-09-06)

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
