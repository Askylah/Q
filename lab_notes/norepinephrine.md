# Norepinephrine — Lab Notes

## Status: BUILT AND WIRED (§8–§9) — RUNNING IN SHADOW

> ✅ **2026-10-11:** wired into llm_engine (per turn, reusing the zettel embedding), efferent (alert band, shadow-gated) and stream_worker (ne_hold). Verified live for zero spend: the D text fired (δ 0.928), efferent recorded band_shadow=alert, the daemon logged would_hold. Shadow stays on until ~200 real turns are recalibrated.

> ✅ **2026-10-11:** `norepinephrine_state.py` built to §3 with the §6 constants; suite 46/46 including calibration parity. Nothing calls it yet. Wiring (llm_engine call + embedding share, efferent band, daemon hold) is the next step, in shadow.

> ✅ **2026-10-11 (later):** Open 1: daemon hold approved, mechanism in §7. §6 finding 4 (tool output as user rows) is retracted; see its correction. Next: the GABA crossing run, once Sky's efferent.py rewrite has landed (each save reloads the app and bounces the daemon mid-run).

> ✅ **2026-10-11:** Sky signed off on option A (unexpected context shift, §2–§3). Open 1–2 (daemon deferral, what feeds CONV) still open. Build order unchanged: GABA crossing run first.

> Origin: mapping what the three existing pieces own (dopamine_state, gaba_state,
> efferent) for receptor #3, Sky's pick from 2026-09-30. Read from the code on
> 2026-10-10, not from memory:
>
> - `efferent.stance_band()` checks `inhibited` FIRST, before restless, spike and
>   exploring.
> - gaba_state rule 4 makes inhibition sticky on purpose: "one shiny thing does not
>   throw the gate back open."
> - dopamine's novelty channel pays only when the Reflector stores an observation. That
>   happens at most once per 5 user turns (GABA note §8).
>
> Together: nothing fast and valence-free reacts INSIDE a turn. If someone circles for
> ten minutes and then breaks the frame ("my dad's in the hospital"), the turn goes out
> with the inhibited stance ("be brief, your patience is spent"). rick.txt even says
> "Genuine crisis → all presets suspended", and the organs have no way to do it.
>
> In the brain this is the locus coeruleus: unexpected uncertainty (Yu & Dayan 2005),
> "network reset" (Bouret & Sara 2005), adaptive gain (Aston-Jones & Cohen 2005).
>
> **Build order:** this design plans on top of GABA constants that are still PROPOSED. The
> GABA crossing run (gaba note §10.3, `labs/gaba_live.py --c-turns 10`) locks §7.3
> first; NE gets built after. Same rule Sky set for dopamine before GABA.

---
### 1. Design rules **[D]**

Mirror of the dopamine and GABA rules, and as non-negotiable:

1. **NE is not phasic dopamine and not anti-GABA.** It never imports, reads or writes
   `dopamine_state` or `gaba_state`. Callers compose the three. NE changes what the
   *output* does while it is up; it never drains inhibition or moves tonic.
2. **One input: unexpected context shift.** How far the incoming user message sits from
   where the conversation has been, measured against how far this user usually moves.
   Not affect, not a crisis lexicon, not social valence (GABA rule 2's reason holds: a
   brain that reacts to how loudly someone types is approval-driven).
3. **Predicted volatility is not surprising.** A user who changes topic every message
   becomes expected-volatile and stops firing it. This is dopamine rule 2 ("predicted
   novelty is not rewarding") applied to attention.
4. **Refractory after a fire.** Biologically, the locus coeruleus inhibits itself for a
   while after a burst (α2 autoreceptors). Without a refractory window, a chaotic stretch
   pins the persona on permanent alert, which is phasic dopamine's pinned-at-1.0 failure
   (gaba note, origin) moved to a new organ.
5. **Ephemeral, like the other two.** Redis-backed floats with a timestamped exponential
   decay, plus the same in-memory fallback.
6. **Silent at rest, lesionable, recorded.** No alert means efferent returns exactly what
   it returns today. `NE_OFF` kills the organ, read per call. Every update emits telemetry
   from day one (the 2026-09-13 lesson: data recorded in an ongoing build, not
   print()). Every constant is PROPOSED until a replay sets it.

---
### 2. What raises it — the options **[D]**

| # | Source | Verdict |
|---|---|---|
| A | **Unexpected context shift**: embedding distance of the user message from a running conversation vector, minus this user's usual distance. Valence-free, fast, adapts to the person. | **Recommended.** |
| B | Arousal from emotional intensity (caps, exclamation marks, affect lexicon). | **No.** Approval-driven (GABA rule 2), anyone can pump it by yelling, and it fights global_rules §3 ("see through the packaging"). Lexicons are brittle. |
| C | Error/correction ("no, that's wrong"), unexpected failure streaks. | **Not as an organ.** Overlaps dopamine's negative RPE and needs a classifier. Possible second input to A later, once A has data. |

---
### 3. Mechanism (option A) **[PROPOSED — numbers come from §5 calibration, not argument]**

Per (username, persona), one float plus bookkeeping:

- **ALERT** (0.0..1.0): fast envelope, decays with `NE_TAU_SEC` (starting guess
  150 s, about 1–2 exchanges, between phasic dopamine's 90 s and GABA's 600 s).
- **CONV** (384 floats): running conversation vector, the EMA of unit-normalised user
  message embeddings.
- **M** (float): expected shift, the EMA of past shifts (α 0.25, the same Rescorla-Wagner
  rate dopamine uses).
- **REFRACTORY_UNTIL** (epoch).

Per user turn:

1. **Skip short messages.** Below `NE_MIN_WORDS` ("ok", "lol"), the turn changes nothing:
   no fire, no update of CONV or M. Short text embeds as noise.
2. **Long gap = new session.** If the last user message is older than `NE_SESSION_GAP_SEC`
   (starting guess 2 h), reset CONV to this message and skip firing. A user coming back
   tomorrow with a new topic is expected to have moved.
3. **Cold start.** The first `NE_WARMUP_TURNS` messages after a reset seed CONV and M and
   do not fire.
4. **Compute the shift:** `s = 1 − cos(e, CONV)`, then the surprise `δ = s − M`.
5. **Fire:** if `δ > NE_FIRE_DELTA`, raise ALERT by `NE_GAIN · δ`, multiplied by
   `NE_REFRACTORY_GAIN` (e.g. 0.3) while inside the refractory window. Clamp ALERT at 1.0.
   A fire above `STANCE_ALERT_MIN` sets `REFRACTORY_UNTIL = now + NE_REFRACTORY_SEC`.
6. **Update the bookkeeping:** M moves toward s and CONV moves toward e, every
   non-skipped turn, fired or not.

**Embedding cost: zero, with plumbing.** `zettel_engine.query_knowledge_graph` already
runs `model.encode([query_text])` (MiniLM-384) on every user message. It doesn't return
the vector, so it has to be shared (a return value or a per-turn cache). NE only encodes
on its own when retrieval didn't run. MiniLM truncates at 256 tokens, so a long message
is judged on its first ~200 words. That's acceptable: a frame break shows up early.

**What composes it, all in efferent (read-only):**

- **Stance:** a new band `alert`, checked **before** `inhibited`.
- **Sampling:** ALERT maps to a temperature delta **down** (focus/gain, starting guess up
  to −0.15). It adds to tonic's warming and is clamped by the existing
  `TEMP_FLOOR`/`TEMP_CEIL`. Dopamine warms, NE sharpens: different axes, so they compose
  instead of duplicating.
- **Tokens:** while ALERT is up, inhibition's token cut is lifted at the output only:
  `inhibition_eff = inhibition × (1 − alert)`. GABA state itself is untouched. When ALERT
  decays, the boredom is still there, unless the conversation actually changed, in which
  case the Reflector's `on_novel` resets the streak by GABA's own rules.

**Stance wording** goes in Sky's 2026-10-10 convention from day one: first person,
target behaviour, nothing named that isn't wanted. Draft, Sky's to rewrite:

> The conversation just turned. I meet the new thing head-on, with my full attention.
> **[Session 2026-10-11, 2026-10-11 — CORRECTION]** Add a step 0 to the per-turn rules: tool results arriving through the user role
> (`[EXECUTION RESULT]`, `[UNTRUSTED_TOOL_OUTPUT]`) are skipped like short messages. The
> calibration found 4 of them, and one was the biggest real δ (§6, finding 4).
>
> Open 2 is closed by §6 (finding 3): CONV = user messages only.
> **[Session 2026-10-11, later, 2026-10-11 — CORRECTION]** RETRACTED: the tool-output skip in the correction above. See the correction under §6; NE needs no tool filter.

---
### 4. Non-goals **[D]**

- **Not a crisis detector.** It detects frame breaks. Bad news delivered *on-topic*
  doesn't fire: "my dad's in the hospital" in a conversation already about hospitals is
  no shift. The persona's own judgment still has to read the room; NE only removes the
  organ-level reasons it couldn't.
- Not arousal, mood or valence (§2 B).
- Not a second exploration drive. Tonic dopamine owns explore/exploit. NE high does not
  warm anything.
- Doesn't touch memory decay, stamping, or the Reflector.

---
### 5. Verification plan **[D]**

**Calibration first (read-only, before any constant is set):** replay Sky's real chat
history from the live DB (`%LOCALAPPDATA%/PersonaApp/users.db`,
`?mode=ro&immutable=1`), user messages in order per persona, through the §3 arithmetic
with firing disabled. Report:

- the distributions of `s` and `δ` (p50/p75/p90/p99) and of message word counts;
- how often each candidate `NE_FIRE_DELTA` would fire per 100 turns;
- the turns it would fire on, as text, so Sky can say whether those were real frame
  breaks.

Sky picks the target fire rate. It should be rare: the point is the frame break, not
every new subject.

**Unit (standalone `tests/test_norepinephrine.py`, every check naming the failure it
prevents):**

- isolation: dopamine and GABA values are byte-identical before and after every NE op;
- silent at rest: no fire means efferent's output is identical to today's;
- adaptation: a constant-volatility stream stops firing after warm-up;
- refractory: a second shift inside the window gets the reduced gain;
- short-message and long-gap rules;
- Redis offline gives the in-memory fallback, same numbers;
- efferent priority: `alert` beats `inhibited`;
- GABA inhibition and streak are unchanged by an alert;
- `NE_OFF` gives the identity.

**Live (scripted against the app, fresh account, Sky's call because it costs money):**

- **Headline:** 10+ circling turns until the band reads `inhibited`, then one frame
  break. That turn is `alert`, temperature is below the inhibited turn's, and the band
  is back to `inhibited` after ALERT decays. If the band never leaves `inhibited`, NE is
  doing nothing.
- **Chaotic user:** every message is a topic jump. It fires early, then adapts and goes
  quiet. If it stays on alert, rule 3 or 4 is broken.
- **Read afterwards:** `ne/*` telemetry rows next to `gaba/*` and `da/*`, plus the
  efferent band per turn.
> **[Session 2026-10-11, later, 2026-10-11 — CORRECTION]** Observed live (gaba note §11, run 20261011_110043): the D frame break went out with band=inhibited and max_tokens 4096 → 2577, the exact case this organ exists for. The same run shows GABA itself scoring D as redundancy (window truncation), so NE's turn-level input is the only thing that sees it.

---
### Open (Sky's calls)

1. **The daemon.** Should an alert also tell the daemon to defer the next sweep for that
   persona, so attention stays on the conversation? Biologically right, but it widens
   the build (stream_worker joins the consumers).
2. **What feeds CONV.** User messages only (proposed: cheapest, and the user's frame is
   what's being tracked), or the persona's replies too (an extra encode per turn)?
3. **The numbers.** `NE_MIN_WORDS`, `NE_SESSION_GAP_SEC` and the target fire rate come
   from the §5 calibration.
4. **The `alert` stance wording.** The existing `efferent.STANCES` and `STANCE_FOOTER`
   are second person and full of "do not" ("Do not mention it, describe it, or explain
   it"). They belong in the 2026-10-10 negative-constraint rewrite, and `alert` should
   match whatever convention comes out of it.

---
### 6. Calibration — 2026-10-11, run ne_calib_20261011_094346 **[D]**

Script `labs/ne_calibrate.py`: read-only (`mode=ro&immutable=1`), MiniLM from the local cache,
no LLM calls. Runs `ne_calib_20261011_094346` and after it, in `labs/live_out/` (gitignored;
`fires.md` contains chat text).

**Data.**
- **Real:** Sky's history. rick has 103 user messages with real timestamps.
- **eni (52) and v (22): not usable for constants.** 91 of eni's 99 rows and 40 of v's 44
  share the 2026-08-24 23:50:31–32 import timestamp, so the session-gap rule can't see
  their session breaks. Every greeting at a real session start reads as a mid-conversation
  jump.
- **Labels:** the gaba_live scripted runs, where D ("Totally different…") after 10+ bread
  turns is the headline case.

**Findings:**
1. **Headline passes.** D is the #1 surprise in both labelled runs (δ +0.83 / +0.90 with
   CONV = user) and E is #2. rick's real max δ (tool-filtered) is under 0.25, so D sits
   more than 3× above anything real.
2. **The chaotic user never fires.** In the clean run, A3–A5 score δ −0.10 … +0.13. B1, a
   jump after five jumps, scores −0.18 / −0.25. Rule 3 works as designed.
3. **Open 2 is answered by data: CONV = user messages only.** Folding in the persona's
   replies ('both') shrinks D's margin: +0.64 / +0.69 against a real p99 of 0.357, versus
   +0.83 / +0.90 against 0.286 for user-only.
4. **Tool output arrives through the user role** (4 rows: rick 3, eni 1). The biggest real
   δ (+0.468) was a tool result. NE must skip `[EXECUTION RESULT]` / `[UNTRUSTED_TOOL_OUTPUT]`
   messages; see the correction under §3.
5. **keep-M rejected.** Carrying M across session gaps raises rick's evaluated turns from 35
   to 56, but fires at 0.20 go from 3 to 11. The extra turns are each session's second
   message, scored against a one-message CONV: noise. Short sessions can't build GABA
   inhibition, so NE loses nothing by sitting them out.
6. **α_c doesn't matter** across 0.2 / 0.35 / 0.5: D is #1 every time and rick's real fires
   at 0.20 are 3 every time.
7. **rick at δ > 0.20:** 3 fires in 35 evaluated turns (8.6 per 100). All three read as
   genuine frame breaks: real life interrupting tech talk twice, and one jump to a new
   artefact. At 0.25 none of them fire, so 0.25 is too high. The 300 s refractory window
   changed nothing on real data at 0.20; on the labels it merges D→E into one episode.

**Constants: still PROPOSED, because 35 real turns is thin.**

| constant | value |
|---|---|
| `NE_FIRE_DELTA` | 0.20 |
| `NE_MIN_WORDS` | 4 (6 of rick's 103 messages fall under it) |
| `NE_SESSION_GAP_SEC` | 7200 |
| warm-up | 2, with M reset per session |
| α_m | 0.25 |
| α_c | 0.35 |
| `NE_REFRACTORY_SEC` | 300 |
| CONV source | user messages only |

**Recommendation:** ship in shadow mode first (`NE_SHADOW`: compute and emit `ne/*`
telemetry, no efferent effect) until there are ≥ 200 real evaluated turns. Then rerun the
calibration and flip the switch.
> **[Session 2026-10-11, later, 2026-10-11 — CORRECTION]** Finding 4 overclaimed, and the §3 correction built on it is RETRACTED. Current code never
> stores tool output as a user row:
> - `llm_engine.py:1564` wraps tool results as `role: "tool"` inside the in-memory tool loop.
> - Only `main.py:655` (the user's own message) and `main.py:526` (the frontend's save call)
>   write conversation rows.
> - Nothing in `backend/` or `vite-project/src` emits `[EXECUTION RESULT]`.
>
> Of the 4 rows the filter matched:
> - #835 and #2348 are Sky's own messages that quote the marker in pasted code;
> - #1053 (the +0.468 "Microverse Battery" row) and #1055 (`[TEST]` output) are June test pastes.
>
> So a marker filter would hide Sky's real messages whenever they paste code or logs. NE takes
> the user's message as it arrives and needs no tool filter; a pasted log is the user's message,
> and may be a real frame break. Thresholds are unaffected: #1053 sat far above 0.20 either way.

---

### 7. Daemon hold — agreed 2026-10-11 **[D]**

**Decided (Sky, 2026-10-11):** an NE alert holds the daemon's next sweep for that persona.
CONV is user messages only (§6, finding 3).

**Mechanism: the consumer pulls, the organ doesn't push** [PROPOSED].
- `stream_worker` calls `ne.is_alert(username, persona)` at the top of its per-persona sweep,
  before the gap scan and the NLI check. That's the same pattern it already uses with
  `gs.is_inhibited()`. If the alert is up, it skips the cycle and emits
  `daemon/sweep_held`.
- The alternative on the table: the organ writes a `daemon:hold_sweep:{user}:{persona}` key
  with a 300 s TTL. Rejected, because it makes NE know its consumers (§1 rule 1, the
  pattern both other organs keep), and the TTL can drift from NE's own decay. With the pull,
  the hold lasts exactly as long as the alert.
- **Shadow mode covers the hold.** With `NE_SHADOW` on, the worker emits `daemon/would_hold`
  and sweeps anyway. A shadow that changes daemon behaviour isn't a shadow.
- **Limitation:** the hold stops the NEXT sweep. It doesn't interrupt one that's already
  running, and the NLI sweep was measured at 548–577 s (COGNITION.md).

**Why the hold earns its place.** The daemon's real cost is model calls, not SQLite: its graph
writes are capped, and it reads the existing graph, not the turn in flight. If the daemon and
the chat share a provider quota, a background call can win a rate-limit race against the
foreground turn. Vertex returned 429s on parallel calls when the daemon ran on it (daemon-work
notes). The current route hasn't been rechecked since `backend/llm_engine.py` dropped its
Vertex path; check `stream_worker`'s model route at build time.

---

### 8. Organ built — 2026-10-11 **[D]**

**Built (uncommitted, NOT wired):** `backend/norepinephrine_state.py` (LF), mirroring
`gaba_state.py`'s storage pattern.
- One JSON entry per (user, persona), `ne:{user}:{persona}:state`, holding alert, alert_ts,
  CONV, M, seen, last_ts and refractory_until, so the bookkeeping can never be half-updated.
- Redis with a TTL of max(2 × session gap, 6 × tau), plus the in-memory fallback and the
  absent-beats-cache rule.
- Values are rounded once and stored the same on both paths.

**API:**
- `on_user_message(user, persona, text, embedding=None, now=None)`: one turn through the §3
  arithmetic, in `ne_calibrate.replay()`'s exact order. It never raises on the chat path. It
  takes the zettel query embedding when given, otherwise it encodes on the shared MiniLM.
- `get_state(...)`: decayed alert, M, seen, refractory flag.
- `is_alert(alert)`: a pure predicate like `gaba_state.is_inhibited`.
- `alert_active(user, persona)`: the daemon's convenience call. §7 wrote this as
  `ne.is_alert(user, persona)`; the split keeps `is_alert` pure.
- `shadow()`: `NE_SHADOW` defaults ON.
- `acting()`: False when lesioned or in shadow; consumers check it before acting.
- `half_life_sec()`.
- Telemetry: one `ne/turn` row per user message.

**Constants [PROPOSED].** From §6: `NE_FIRE_DELTA` 0.20, `NE_MIN_WORDS` 4,
`NE_SESSION_GAP_SEC` 7200, `NE_WARMUP` 2 (M reset per session), α_m 0.25, α_c 0.35,
`NE_REFRACTORY_SEC` 300. New here, from §3's starting guesses:

| constant | value | why |
|---|---|---|
| `NE_TAU_SEC` | 150 | |
| `NE_GAIN` | 3.0 | a fire at the threshold adds +0.60; δ ≥ 0.34 saturates |
| `NE_ALERT_MIN` | 0.30 | a threshold fire stays at or above it ~104 s (about one exchange); a D-sized break ~181 s |
| `NE_REFRACTORY_GAIN` | 0.3 | |

A fire inside the refractory window does not extend it. That is the same rule as the
calibration's `episodes()`.

**Verified:** `tests/test_norepinephrine.py` (standalone; fake unit vectors, explicit
clocks, no model): **46 PASS, 0 FAIL, 0 SKIP**, and it leaves no Redis keys behind. Coverage:
- rule 1, by import AST and by DA/GABA state being unchanged;
- silent at rest;
- seed, warm-up, evaluate;
- short-message skip that still counts as presence;
- the session gap;
- the frame break firing and crossing `NE_ALERT_MIN`;
- a sub-threshold drift (δ ≈ 0.1) staying quiet;
- 16 topic jumps in a row giving **zero** fires (rule 3);
- refractory gain measured from telemetry;
- decay at tau·ln2;
- `NE_OFF` and `NE_SHADOW`;
- the Redis-vs-memory paths agreeing;
- no-model, garbage and wrong-size embeddings;
- the telemetry fields;
- **calibration parity: the organ reproduces `ne_calibrate.replay()`'s statuses, s and δ
  to 1e-5**, so §6's numbers apply to this code.

**Not wired yet, nothing calls it:**
- `llm_engine`: the per-turn `on_user_message` call, plus the zettel embedding share.
- `efferent`: the `alert` band (Sky's file).
- `stream_worker`: the §7 hold.

Shadow mode only starts recording once the llm_engine call exists.

---

### 9. Wired and running in shadow — 2026-10-11 **[D]**

**Wired, uncommitted. Sky: "execute all 3".**
- **`backend/llm_engine.py`:** imports `norepinephrine_state`. Once per user turn, AFTER the
  zettel query and BEFORE `efferent.for_turn`, it calls
  `on_user_message(user, persona, text_only_message, embedding=last_query_embedding(...))`,
  so a frame break shapes the very reply it arrives in. Group turns are skipped (they are
  other characters' lines). It never raises.
- **`backend/zettel_engine.py`** (CRLF, byte-patched): `_remember_query_vec` stashes the
  query embedding per (user, persona). `last_query_embedding(user, persona, text)` returns
  it only for exactly the same text, so NE encodes for itself otherwise.
- **`backend/efferent.py`** (Sky's file, on Sky's go):
  - Band `alert` comes FIRST, ahead of `inhibited`.
  - Temperature moves by `TEMP_ALERT_DELTA` (−0.15, PROPOSED) × alert.
  - The token cut uses `inhibition × (1 − alert)`.
  - All three apply ONLY when `ne.acting()`; GABA state is never touched. In shadow the
    request is unchanged and `band_shadow` records the band it would have used.
  - `efferent/turn` telemetry gains `alert`, `ne_acting` and `band_shadow`.
  - Stance text, written in Sky's efferent register (affirmative, no numbers): "The exchange
    just broke sharply onto new ground. Meet the new direction head-on with full attention
    and full depth."
- **`backend/stream_worker.py`** (CRLF, byte-patched): a module-level `ne_hold(user,
  persona)` returns `hold`, `would_hold` or `""` and emits `daemon/sweep_held` or
  `daemon/would_hold`. The per-persona loop `continue`s on `hold`; the AST confirms the
  `continue` targets `for ctx in contexts`. It never raises.

**Tests:**
- NE 54/54 (8 new daemon-hold checks).
- Efferent 76/76 (Sky's 59 unchanged, plus 17 new: shadow is a no-op, alert outranks
  inhibited, cooling, token cut lifted, the graded lean under `NE_ALERT_MIN`, silent at rest,
  no digits in the stance, `NE_OFF` and `EFFERENT_STANCE_OFF` still work, `read_organs`
  reads live NE, GABA untouched).
- GABA 84/84, zettel retrieval 12, persona tail 23, prefill 8, gemini thinking 10, daemon
  lock 97, lore 71, typed links 36, triggers 80, novelty 34: 0 FAIL anywhere.
- `test_context_stream` fails identically on the PRE-wiring llm_engine ("no such table:
  users"): a stale fixture, not this change.

**Deployed:** the app reloaded on the edits. The daemon was stopped (PID 12376) and respawned
by a forced reload as PID 31896, which holds `q:daemon:lock`.

**Live check:** `labs/ne_wiring_check.py`, a fresh keyless account sending five bread repeats
and then the D text, so every model call fails and nothing is billed:

| | turns 1–5 | turn 6 (D) |
|---|---|---|
| `ne/turn` | seed, warmup, eval δ ≈ 0.00 | s 0.932, **δ 0.928, fired, alert 1.0**, shadow |
| embedding | `given` (the zettel share works) | `given` |
| `efferent/turn` | band '', temp 0.9→0.9 | **`band_shadow='alert'`**, temp 0.9→0.9 (unchanged, as shadow must) |
| daemon | — | **`would_hold`** (alert 0.914 after decay); the sweep ran |

**Status: running in SHADOW in the live app.** Every real user turn now writes an `ne/turn`
row. Next: collect ≥ 200 real evaluated turns, recalibrate (§6 method or the `ne/*` rows),
then `NE_SHADOW=0`.

---

## Next session — start here (2026-10-11)

1. ~~**Sky:** Open 1. Should an alert also defer the daemon's next sweep for that persona?~~ — approved 2026-10-11, §7; (Open 2
   is answered by §6.)
2. ~~**Before building:** run the GABA crossing run and lock gaba §7.3~~ — done, gaba §11-§12; (gaba note §10.3).
3. **Build:**
   - ~~`norepinephrine_state.py` with the §3 rules~~ — built, §8; ~~plus the tool-output skip (§3 correction)~~ — retracted, §6 correction;
   - ~~the `alert` band in efferent, after Sky's 2026-10-11 rewrite of `efferent.py` lands;~~ — wired, §9;
   - ~~`tests/test_norepinephrine.py`;~~ — 46 PASS, §8;
   - ~~the zettel query-embedding share.~~ — wired, §9

   Ship with `NE_SHADOW` on (§6).
4. **Recalibrate:** once there are ≥ 200 real evaluated turns with real timestamps, rerun
   `labs/ne_calibrate.py` (or read the `ne/*` telemetry), set the constants, then turn
   `NE_SHADOW` off.

---

## Next session — start here (2026-10-10)

1. **Sky:** ~~sign off on option A, or pick another (§2).~~ — agreed 2026-10-11; Answer Open 1–2.
2. **Before building:** run the GABA crossing run and lock gaba §7.3 (gaba note §10.3).
3. ~~**Calibration script**~~ — done, §6 `labs/ne_calibrate.py` (read-only against the live DB, no LLM
   calls, free): §5 calibration. Write the result up as §6 here.
4. **Then build:** `norepinephrine_state.py`, the efferent band and composition, the
   suite, and the zettel embedding share. Everything stays PROPOSED until the live run.
