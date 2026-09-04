# Entropic Gap Livelock — Lab Notes

## Status: SESSIONS 1-9 COMMITTED — SESSION 9 ADDED DAEMON MODEL SELECTION, THE NOVELTY DA CHANNEL AND THE IDLE REST GATE; §47 RETRACTED; §51.2 CONFIRMED — **APP RUNNING, DAEMON HEALTHY (pid 27404), ALL FOUR SUITES GREEN, NOTHING MID-FLIGHT** (2026-09-01, 21:38)

> ✅ **SESSION 10 (2026-09-03): §23's telemetry `event_type` filter is APPLIED, one line at `main.py:859`, UNCOMMITTED as of writing** (§58). App was down at the time — no reload, no daemon recovery to check. **Then §59: the panel's third badge colour + the `/10` score lie, `App.jsx` edited and `dist` rebuilt (`index-CBrCaQPS.js`).**
>
> 🚨 **SESSION 9 HEADLINE — §47 IS RETRACTED, READ §52.** The monologue writer was
> **never** inert from an empty key pool. It fires **once per user message**, keyed on
> the `daemon:last_monologue:*` mark, and the last user message was 2026-08-29 23:02.
> "Inert since 23:13" was "nobody chatted since 23:02." §47's replay ran without `.env`,
> which is the exact trap §28 documented one session earlier: no `VERTEX_PROJECT_ID`
> → provider `google` → "no key". The live daemon inherits `.env` from `main.py`'s
> `load_dotenv()` and routes Gemini through **Vertex on ADC**; the pool was never in its
> path. Proof: the first message in three days (19:34) produced a 2,263-char monologue
> at 19:40 with zero keys in the pool. **Session 8's item 1 is withdrawn** — it was
> waiting for a key that was never needed.
>
> ✅ **§51.2 IS CONFIRMED** (§53). First live call at `max_tokens=4000`: 2,263 chars,
> above the floor. The two bodies before it were 58 and 97-char stubs.
>
> ✅ **THREE FIXES APPLIED, ALL GREEN** (§54–§56). The daemon's model is env-overridable
> per call site and `gemini-2.5-flash` is gone. A **novelty channel** is the third tonic
> input and the only one a conversation can open the explore gate with — the social
> channel tops out at 0.348 by arithmetic (`da_neuron_log.md`). And the idle-branch
> monologue was a **rumination loop**: 69 of July's 74 monologues answered "reflect on
> your current state of existence" with no gap to work on and were written into the
> graph as knowledge. Gate shut + nothing to integrate now **rests**.
>
> ⚠️ **Open (§57):** 213 of 371 lore nodes have zero links and 136 of them are one
> 215-node `KB` ingest — the auto-linker never compares siblings in the same batch.
> 75 legacy idle-branch monologue nodes are still in the corpus. Hysteresis on the
> explore gate is the one real gap left in the DA calibration.

> ✅ **SESSION 8 HEADLINE — F6 IS DONE, READ §44 AND §45.** The monologue
> idempotency mark is now written from a `finally`, independent of the write fan-out.
> Measured before the fix: **87 of 106 Reflection nodes (82%) were redundant fires**,
> thirteen of them less than 90 s apart — one refire per daemon cycle, on personas
> whose triple is frozen at the `"2000-01-01"` fallback. §25's "the uncapped writer
> behaved" was watching `Sky/rick`, the one persona of three where the guard works.
> Ten new checks in `tests/test_daemon_lock.py`; 61 total, ALL PASS.
>
> 🧭 **THE WORKSPACE REACH IS A DECISION, NOT A BUG — READ §51.1.** `/workspace/save`,
> `/create` and `/delete` build their `SafeWorkspace` root from the caller's own
> path, so the containment check cannot fail. That was "fixed" to `_APP_ROOT` in
> Session 8 and **reverted the same session**: the reach is the product. This is a
> general-purpose file manager and it is meant to be as capable as any agent CLI
> pointed at a directory. **Do not narrow it.** `tests/test_workspace_paths.py`
> section [3] fails if someone does. §34's "highest blast radius of anything open"
> framing is withdrawn.
>
> ✅ **A truncated monologue is no longer persisted** (§51.2). `MIN_MONOLOGUE_CHARS
> = 200`; anything shorter is logged with its `usage` and discarded instead of
> written to `zettel_nodes` and fed back to the gap picker. `max_tokens` 1000 →
> 4000, **unverified** — no key to replay the call with. The floor is what
> actually protects the corpus.
>
> ⚠️ **Check counts in §45, §49 and §50 are wrong; §51.3 has the measured ones.**
>
> ✅ **THE KEY POOL IS REPAIRED — READ §48 AND §49.** A single 403 used to mark an
> API key `BURNED`, and `BURNED` was **terminal**: nothing in the tree ever restored
> one and the HTTP API had no reset, so delete-and-re-add was the only way back.
> Most 403s are about the project, the region, the model or the exit IP — an
> OpenRouter moderation refusal on a persona prompt destroyed valid keys. A 403 now
> cools for 15 min; only a body that names the credential burns; an unexplained 401
> burns on the third **consecutive** strike; a burn expires after 24 h; the proxy is
> blamed too; and one request can burn at most one key. Keys burned by the old rule
> revive by themselves on first checkout. **And `checkout_key` never rotated** (§50)
> — it returned the first healthy key every time, so one credential absorbed every
> request and collected every rate limit while the spares idled. It is round-robin
> on a per-provider cursor now. 51 checks in `tests/test_key_pool.py`.
>
> ⚠️ **The monologue writer is INERT and has been since 2026-08-29 23:13** (§47).
> The API key pool is empty, so `call_llm` returns an error string and nothing is
> written. Do not read "no new Reflection nodes" as the fix working — nothing is
> being asked of it. §47 has the first thing to check once a key lands, and it is
> not good news for §30's 512 → 1000 raise.

> ✅ **SESSION 7 HEADLINE — READ §39 AND §40 FIRST.** The lock is no longer renewed
> once per cycle at a predicted TTL. It is held by a **renewer thread on a fixed 30 s
> timer**, and holder liveness is now decided by **probing the holder's pid**, not by
> reading its heartbeat. Measured across a 585.6 s active sweep: **lock present on
> 123/123 polls, 0 absent, worst-case TTL headroom 95 s** — against **73 % absent** on
> the same test eleven minutes earlier. §26 is **fixed**: a `.py` edit at 08:03 killed
> the daemon, and the replacement evicted the corpse's lock and took over **by itself**,
> with no `DEL q:daemon:lock`. That is the first source edit in this project's history
> that needed no manual recovery.
>
> ⚠️ **§35's closing instruction was half wrong and §39 says why.** Deriving `_ttl` from
> measured cycle time was right. Deriving `_lock_ttl` from it was **not**: an estimator
> can only widen *after* it has seen a long cycle, so it protects every active cycle
> except the first one — which is the only one that was ever at risk. Shipped, measured,
> and replaced within the same session; the run-1 numbers are in §39.
>
> ✅ **§36 is fixed and verified live.** A plain `DEL` of a `da:*` key now stands the
> neuron down on the very next cycle (measured twice). The recipe in §36 that says to
> `_save` the baseline instead of deleting is **no longer necessary** — though it still
> works. Redis is now actually the source of truth for `da:*`.
>
> ⚠️ **Three of this session's own claims are retracted in §42**, including a CRLF
> regression that briefly made `stream_worker.py` diff as a 943-line rewrite.

> 🚨 **SESSION 6 HEADLINE — READ §35 FIRST.** The active cycle at the 256 cap was
> **measured** at 548 s and 577 s (n=2). §33 predicted ~460 s and was low by 25%. At the
> true length the daemon is **lockless ~68%** and **past its own 300 s staleness check ~46%**
> of every cycle — so the 256 cap did not fix the TTL mismatch it was chosen for, it moved
> it. **Deriving `_ttl`/`_lock_ttl` from measured cycle time is now item 1, not cleanup.**
> Every §33 row past "idle" that is still marked as predicted is superseded by §35.
>
> ⚠️ **`DEL`ing a `da:*` key does NOT reset the dopamine neuron (§36).** An absent key falls
> through to a never-invalidated in-process cache, so a running daemon ignores the delete and
> then overwrites Redis with its stale value. To stand a context down, **write the baseline,
> do not delete the key.** Any earlier recipe that says otherwise is wrong.
>
> ⚠️ **§37 retracts two claims made in Session 6 itself** — a "wedged daemon" that was a
> cycle completing normally, and a `timeout=60` at `llm_engine.py:90` that is a pip install,
> not an HTTP call. Cite `:530` for the request timeout.
>
> ✅ **THE FIX SET IS DONE AND PROVEN IN PRODUCTION. READ §20-§26 FIRST.**
> F2 landed, ran against the live DB (118 rows deleted, all recoverable,
> `sqlite_sequence` preserved), and **both** of its call sites have now fired for
> real — the startup hook and the per-cycle daemon loop (§25). The app was
> restarted, used normally for several hours, and F1a and F1b were exercised in
> production for the first time once the dopamine gate opened: gap emission
> resumed through the repaired join, and **F1b's cap held at 8 admitted against
> 28 attempts**. `Sky/rick`'s Reflector recovered and has stayed healthy.
>
> 🚨 **SESSION 5 HEADLINE — READ §27 FIRST.** `call_nli_gate` hardcoded
> `max_tokens=10` against a **reasoning model**, so content came back empty and the gate
> returned `NEUTRAL` on 100% of calls. `semantic_contradiction`'s zero rows across the
> project's entire history are that literal, **not** evidence about the graph. Fixed to
> 256 and verified returning real verdicts (§33). Anywhere earlier in this file that
> argues from "a subsystem that has produced zero rows" — §14.5, §15 — is reasoning
> from a broken instrument.
>
> ✅ **DAEMON IS NOW PARENTED TO UVICORN** (PID replaced 2026-08-30 19:55). The
> Session-4 detached orphan is dead. `.py` edits reload normally again — but see §26:
> a replacement still silently `sys.exit(0)`s if it finds a live lock, so after any
> source edit **confirm a `stream_worker.py` process actually exists**. The minimal
> recovery is `DEL q:daemon:lock`, not a Docker bounce (§31).
>
> ⚠️ **The NLI sweep now stands down when tonic is below threshold** (§32/§33).
> Idle cycle is 60 s and makes **zero** LLM calls. If you see a quiet daemon log on an
> idle app, that is correct behaviour, not a fault. ~~An *active* cycle time at the new
> 256 cap has not been measured~~ — **MEASURED in Session 6: 548 s / 577 s, see §35.**
>
> ⚠️ **§17's "permanently" is WRONG and §14.2's numbers are WRONG.** The Reflector
> gate is `<`, not `<=`, so raw=5 against threshold 5 *passes*; and a new
> `user_message` evicts a daemon row from the 20-row window, so suppression
> self-heals with user activity. It is a stall, not a cliff. §22.
>
> ⚠️ **READ §11 BEFORE §3-§4, AND §6 BEFORE §3.** Session 3 found the gap
> detector was joining on the wrong column since the file was created, which
> invalidates §4's root-cause story. Session 2 disproved the stated *mechanisms*
> in §3a and §3b. Conclusions in §1-§10 mostly hold; several stated reasons do
> not. Corrections are inline and detailed in §6, §11, §13, §14 and §22.

Origin: follow-up to `storage_hardening.md` open item #2 (the 5078 → 468
observation discrepancy). The prune was confirmed legitimate. Investigating *why*
the table had grown to 5078 in the first place uncovered an unbounded write loop
that is currently regrowing.

Working hypothesis going in was "the system self-deletes unresolved entropic gaps
after a period." **It does not. That mechanism does not exist.**

---

### 1. Resolution of storage_hardening open item #2

The July backup is **not** holding unique data. It is safe to retire.

| | rows |
|---|---|
| deleted between 2026-07-13 and today | 4930 |
| ↳ `entropic_gap` daemon spam | 4882 |
| ↳ `user_message` | 48 |
| ↳ …of those 48, with **no** surviving identical twin | **0** |

Method: `observations.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`, so IDs are
comparable across snapshots and `sqlite_sequence` preserves the high-water mark.
Exact id-set diff, not counts:

```
JUL  5078 rows (id 245..5358)   sqlite_sequence.observations = 5358
LIVE  468 rows (id 245..5678)   sqlite_sequence.observations = 5678
  in JUL, absent from LIVE : 4930   (deleted, not never-created)
  in LIVE, absent from JUL :  320   (matches seq delta 5678-5358 exactly)
  survivors                :  148
```

The 4882 spam rows collapse into **three** content groups (2296× / 2151× / 435×).
Whole-table duplication factor was **33.6×** — 5078 rows across 151 distinct
`(username, persona, event_type, content)` tuples. The 48 `user_message`
deletions were duplicate re-submissions (`'I stabbed him'` ×7, `'Eni?'` ×5),
every one with an identical surviving row.

**Zero unique content lost. The prune was surgical.**

Also settled while in there:

- LIVE is row-identical to `users.db.migrated-20260828_185742` across all 19
  tables. The migration was faithful.
- The old-location `users.db` is a **partial** schema (only `deep_memories`,
  0 rows) — a stale worker that died during table init. Last written 18:57:49,
  nine minutes before the current process started. Unreferenced.
- `zettel_links 183 → 151` in the old notes is a **net** figure concealing
  **147 deleted / 115 added**. All 147 vanished links had *both* endpoints
  deleted too → regeneration cascade, not graph damage. Benign, but the net
  number hid a 147-row delete. Diff id-sets, not counts.

---

### 2. There is no reaper for `observations`

`memory_engine.py:457 decay_cycle()` is the only decay engine in the codebase.

1. It selects `FROM deep_memories`. Entropic gaps are written to `observations`
   (`stream_worker.py:411`, via `add_observation`). Different table — never read.
2. Even for `deep_memories` it only does `SET active=0` (`:537`). It **archives,
   never deletes.**

No TTL, no age-off, no reaper exists for `observations` anywhere. The only
deletions are three all-or-nothing per-persona wipes (`database.py:415`, `:891`,
`:1021`) — which is why a manual prune was the only available tool.

The comment at `stream_worker.py:235` — `# Consolidation mode: park the voids,
let decay do its thing.` — points at an engine that does not cover the table it
is writing to. **This comment is the source of the incorrect mental model.**

---

### 3. Why it reached 4882: three stacked failures

Any *one* of these working would have prevented the flood. All three are holed.

#### 3a. Write rate is unbounded (dominant cause)

The cooldown at `stream_worker.py:378` is keyed **per node**
(`(username, persona, node_id)`, `GAP_COOLDOWN_SECS` default 86400).

The eligible orphan pool — degree ≤ 1 and `len(content) > 150`, per
`analyze_entropic_gaps` `:231` — is:

| persona | eligible nodes |
|---|---|
| rick | **293** |
| eni | 19 |
| v | 14 |

The "curiosity rotation" (`:256-261`) picks a *different* node each cycle. With a
~5-minute cycle and a 293-node pool, it emits ~288 rows/day and will not revisit
a node for 293 days. **The 24h per-node cooldown can never bind.**

> **CORRECTED (§6.2):** the loop interval is **60s**, not 5 minutes
> (`stream_worker.py:626`, `main.py:102` spawns with no argv). The observed
> ~5–7min spacing is the dopamine `exploring` gate at `:236-241` suppressing most
> cycles, *not* the cadence. Ceiling is therefore ~1440 rows/day, not 288. The
> conclusion ("cooldown can never bind") is unaffected and understated.

Evidence — the 2026-08-27 burst, 38 rows, every one a distinct node:

```
22:31 Consciousness Concepts        23:07 Dr. Wong - The One Who Saw...
22:38 KB (144)          +7.1m      23:12 Argumentation Structures
22:44 KB (132)          +6.1m      23:17 KB (197)          +4.9m
22:51 KB (34)           +6.9m      23:22 KB (12)           +5.1m
22:57 Meta-Fallacy Awareness       23:27 KB (54)           +5.0m
23:02 KB (6)            +5.0m      23:33 KB (9) … 23:43 AI Safety Concerns
```

The rotation was added to stop the daemon "re-chewing one pocket." It fixed
repetition by **maximising throughput**. That is the regression.

#### 3b. The cooldown registry does not survive respawn

`stream_worker.py:33` — `self._gap_cooldowns = {}`. Plain dict, process memory.
`main.py:970` runs uvicorn with `reload=True`; `stream_worker` is a grandchild of
the reload worker (`6336 → 21924 → 31776`). Every source edit respawns it and the
registry returns empty, so every orphan is instantly "fresh" again.

> **CORRECTED (§6.3):** "every source edit respawns it" is wrong. The daemon holds
> a **single-instance lock** (`stream_worker.py:628-675`) and a respawn that finds
> a healthy holder calls `sys.exit(0)` (`:658-659`) — the incumbent keeps its
> populated dict. The registry resets only when the *holder itself dies*
> (three distinct vectors, §6.3). The dict is still the bug and F3 is still the
> fix; only the trigger was misidentified.

Measured: **30 of 45** same-node re-flag intervals are under the 24h cooldown,
many at 0.1h (consecutive cycles):

```
rick  The Contradiction Engine       n=17  gaps(h): 27.8, 36.5, 2.5!, 1.5!, 0.1!, 0.1!, 0.4!, 14.6!, 0.1!
eni   Reflection: 2026-07-19 15:06   n=14  gaps(h): 0.8!, 0.1!, 0.1!, 245.8, 24.0, 24.0, 14.3!, 115.2, 0.0!
v     Reflection: 2026-07-15 19:50   n=11  gaps(h): 183.4, 154.8, 62.4, 115.2, 0.1!, 336.8, 53.9, 0.1!, 0.1!
```

Secondary leak: the key is `node_id`, but zettel regeneration mints fresh
`node_id`s for re-derived nodes (196 deleted / 503 created in this window). Even a
*persisted* registry would miss those. `title` is the stable identifier — it is
also what the observation log records.

This is the same bug class as the `storage_hardening.md` incident: `reload=True`
weaponising a process-lifetime assumption. Companion to that note's rule:

> **No durable policy state in process memory.** The reloader recycles
> processes constantly; any guard held in a dict is a guard that resets on save.

#### 3c. The insert-time dedup guard excludes `entropic_gap`

`database.py:977` — `if event_type == "dense_observation":`

The guard is an allowlist of one. `FIX(turn-loss)` (`:971-976`) correctly narrowed
it — deduping *all* types was swallowing legitimate repeat user messages ("yes",
"ok") and shortening the Reflector's raw-event count. But the daemon writes
`entropic_gap` (`stream_worker.py:414`) and `semantic_contradiction` (`:437`), not
just `dense_observation`, so both fell out of the guard **originally written for
the daemon-flood case.**

The docstring at `:963-965` still describes the pre-narrowing behaviour, which is
why this stayed invisible.

---

### 4. Root cause underneath all of it

**396 of 555 zettel nodes (71%) are orphans** — zero links. By persona:
rick 328, v 42, eni 26. Of 103 `Reflection: <timestamp>` nodes (auto-generated),
**72 are orphaned (70%)** — the reflection writer creates nodes and never links
them, and the gap detector's entire job is to find unlinked nodes.

The daemon is **not malfunctioning.** It is correctly and continuously reporting
that the graph is 71% disconnected. Nothing ever links those nodes, so it reports
it ~288×/day forever. The gap observation instructs the persona to "formulate an
integrating hypothesis to link this pocket back" but **nothing verifies a link was
created** — the loop has no completion condition.

### Current cost

`get_observation_log` (`database.py:999`) reads `ORDER BY id DESC LIMIT 10`. That
window *is* the persona's WORKING_MEMORY. Present state:

```
Sky/eni           0/10 entropic_gap  [..........]
Sky/rick          6/10 entropic_gap  [GGG.G..G.G]   <-- majority noise
Sky/v             0/10 entropic_gap  [..........]
SkyTest/rick      1/10 entropic_gap  [.G........]
```

`Sky/rick`'s three newest observations are all `entropic_gap`. That persona is
being fed its own alarm spam instead of conversation history. The harm is
**functional, not disk** — 85 rows is nothing on disk.

Regrowth curve since the prune (`entropic_gap` rows/day, LIVE):

```
07-15  5   07-29  2   08-06  4   08-20   2
07-19  5   07-30  1   08-07  1   08-22  10
07-23  1   07-31  1   08-19  4   08-23   9
           08-01  2              08-27  38
```

85 rows, 37 distinct titles, dup factor already back to **2.30×**.

---

### Proposed fixes (none applied)

Descending order of value. #1 is load-bearing; without it the others are cosmetic.

1. **Cap the pool, not the node.** Per-`(username, persona)` rate limit — N gap
   flags per 24h regardless of which node is targeted. Kills the 293-node
   conveyor belt. ~5 lines in `resolve_entropic_gap`. **Do this first.**
2. **Build the reaper.** Delete `entropic_gap` / `semantic_contradiction` rows
   older than N days that were never resolved. This is the mechanism that was
   assumed to already exist. No schema change.
3. **Persist the cooldown.** Rekey `_gap_cooldowns` on `title` (stable across
   node regeneration, and it is what the log records) and rehydrate from the
   observation log in `__init__` so respawns do not reset it. Note: `__init__`
   I/O is fine — the `storage_hardening.md` rule is specifically about *import*
   scope.
4. **Invert the dedup guard** at `database.py:977` to a denylist —
   `if event_type not in RAW_EVENT_TYPES:` where `RAW_EVENT_TYPES =
   {"user_message", "assistant_message"}` — so daemon-authored types are deduped
   by default and `FIX(turn-loss)` still holds. Also fix the stale docstring.
5. **Longer term:** link the auto-generated `Reflection:` nodes at creation, or
   exclude them from gap eligibility. 70% of them being orphans is the thing
   actually generating the alarm volume.

---

### Investigation method notes

- Read every snapshot with `file:<path>?mode=ro&immutable=1`. `immutable=1`
  prevents SQLite creating `-wal`/`-shm` sidecars — which is exactly what
  littered the directory during the previous session's `mode=ro` diagnostic.
- **Diff id-sets, not row counts.** Counts hid a 147-row `zettel_links` delete
  behind a net −32, and would have hidden the 196-node regeneration entirely.
- `AUTOINCREMENT` + `sqlite_sequence` distinguishes "deleted" from
  "never created" for free. Worth preserving on `observations`.
- Correlate write timestamps against *node identity*, not just volume. The
  5-minute cadence with a distinct node each cycle is what separated
  "rotation exhausting a pool" from "one node relooping" — opposite fixes.

### Reproduce

```
python app_paths.py --status
# scratch scripts used this session (temp, not committed):
#   dbdiff.py / forensics.py / dedup_test.py / verdict.py / orphans.py / cadence.py
#   -> C:\Users\insom\AppData\Local\Temp\opencode\
```

---
---

# Session 2 — 2026-08-28, later same day

Scope of this session: **verification and design correction only. No source file
was modified.** Everything below was established by direct read of the live tree
and read-only query of the live DB. A parallel-agent fan-out was attempted and
returned nothing (§10) — no claim here rests on it.

---

### 5. State re-verified — zero drift

All line numbers cited in §1–§4 still resolve in
`OneDrive/Desktop/Personas/PersonaApp-merged` (the live tree — the `- Copy`,
`- Copy (2)`, `garage/` and `dist_installer/` siblings are stale forks and must be
ignored; `garage/stream_worker.py:22` has no `GAP_COOLDOWN_SECS` at all, so it
predates the whole cooldown attempt).

Confirmed unchanged: `stream_worker.py` `:231` `:235` `:251` `:261` `:378` `:411`
`:414` `:437`; `database.py` `:977` `:999`; `memory_engine.py:457`; `main.py:970`.

Live DB, read with `file:…?mode=ro&immutable=1`:

```
observations total 468        sqlite_sequence.observations = 5678
  user_message          255
  dense_observation      91
  entropic_gap           85     85 rows / 37 distinct = 2.30x
  internal_reflection    37
  semantic_contradiction  0     <-- never fired, not once, ever

WORKING_MEMORY window (newest 10, database.py:999)
  Sky/eni            0/10  [..........]
  Sky/latent_space   0/10  [.]            <-- NEW persona, 1 observation
  Sky/rick           6/10  [GGG.G..G.G]   <-- unchanged, still poisoned
  Sky/v              0/10  [..........]
  SkyTest/rick       1/10  [.G........]

newest row: id 5678  Sky/rick  entropic_gap  2026-08-27 23:43:31.317019
```

The per-day regrowth curve is **byte-identical to §Current cost** — no new rows.
DB mtime 18:57 today, no `-wal`/`-shm` sidecars present, newest observation is from
23:43 the previous night. **The daemon has not run since the notes were written.**
The 2.30× is therefore a floor, not a trend line; it resumes the moment the app
starts. Two new facts worth carrying:

- `semantic_contradiction` has **0 rows in the table's entire history.** F4's and
  F2's coverage of that type is prophylactic, not remedial. Do not use it to
  justify either fix's value.
- `Sky/latent_space` is a persona that did not exist in §Current cost. New
  personas inherit a clean window and then get polluted, so the blast radius grows
  with persona count.

---

### 6. Corrections to §3 — the mechanisms were wrong

The conclusions in §3 are all sound. Three of the *explanations* are not, and each
would have sent a fix in a slightly wrong direction.

#### 6.1 Stale line-number comments (cosmetic, but they cost me time)

Two in-tree comments cite `main.py:953` for the uvicorn `reload=True` line:

| location | claims | actual |
|---|---|---|
| `app_paths.py:221` | `main.py:953` | `main.py:970` |
| `database.py:62` | `main.py:953` | `main.py:970` |

`:953` is where it still sits in `dist_installer/main.py:952`. The comments were
copied forward and never renumbered. §3b's `main.py:970` is the correct one.

#### 6.2 The cadence is 60 seconds, not 5 minutes

```
stream_worker.py:626   interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
stream_worker.py:621   time.sleep(interval_seconds)
main.py:102            subprocess.Popen([sys.executable, worker_path])   # no argv
```

`start_loop()` therefore sleeps **60s**. The ~5–7 minute spacing measured in the
08-27 burst is not the loop period — it is the loop turning 5–7 times and emitting
on only one of them, gated by:

```
stream_worker.py:236-241
    if not exploring:
        logger.info(... "(consolidation mode) — N void(s) parked.")
        return None
```

**Consequence for F1:** the theoretical ceiling is ~1440 rows/day, not ~288. More
importantly, the emission rate is a function of the *dopamine tonic level*, which
means the flood rate is coupled to an unrelated subsystem and will change on its
own. A fix that tunes N against the observed 288/day is calibrating against a
moving target. Cap hard and low.

**Consequence for §3a's framing:** the note says the rotation "fixed repetition by
maximising throughput." True, but the throughput ceiling is set by the dopamine
gate, not the rotation. The rotation only decides *which* node — it does not
control rate at all. §3a slightly over-blames the rotation.

#### 6.3 Respawn does not reset the registry — holder death does

This is the substantive correction. §3b says "Every source edit respawns it and
the registry returns empty." That is not what the code does.

`main.py:95-108` spawns the daemon as a **detached subprocess** from a
`threading.Thread(daemon=True)`. But `stream_worker.py:628-675` guards startup
with a real single-instance lock:

```
:648   if _conn_lock.set(b"q:daemon:lock", _boot_id, nx=True, ex=_ttl):  -> acquired
:652-654   else: check q:daemon:heartbeat age
:658-659   if holder healthy:  print("Healthy instance running..."); sys.exit(0)
:665   Redis down -> fallback socket lock on 127.0.0.1:18388
:613   lock renewed at the top of every cycle (_renew_lock)
```

So a reload-triggered respawn finds the incumbent's lock, **exits silently, and the
incumbent keeps its populated `_gap_cooldowns`.** The comment at `:631` — "23 hours
of dead brainstem taught us this" — is the scar tissue from getting this wrong the
other direction.

The registry resets on **holder death**, and there are three vectors, not one:

1. **Parent death.** The uvicorn reload worker is killed on source edit; its
   `Popen`'d child is orphaned or killed with it. Lock lapses at TTL
   (`max(300, interval*5)` = 300s). This is the vector §3b was groping at, but it
   is mediated by a 5-minute TTL, not instantaneous.
2. **Zombie takeover** (`:654-657`). Holder hangs past TTL → next spawn deletes
   the lock and takes over → fresh empty dict, and now the hung original may still
   be alive. **Two writers, both with empty registries.**
3. **Redis unavailable** → socket-lock fallback (`:665`) with no TTL and no
   heartbeat. Dies with the process, no zombie detection at all.

**Consequence for F3:** unchanged as a fix, but the *rationale* in §3 must be
restated. The registry is not "reset by edits" — it is reset by an unbounded set of
process-death events, one of which (vector 2) can produce **concurrent** daemons.
That is a stronger argument for durable state, not a weaker one, and it means F3
must be written to tolerate two processes rehydrating and writing at once.
A `dict` was never going to hold this.

---

### 7. New findings that change the fix design

#### 7.1 There is already a durable "run once per N hours" idiom — copy it

`database.py:46` — and the docstring is almost a letter to this session:

```
database.py:46   def backup_database(retain=10, min_interval_hours=6.0, force=False, verbose=True)
database.py:62   min_interval_hours exists because main.py:953 starts uvicorn with reload=True,
                 so startup hooks re-fire on every source edit. Without a floor, this would
                 mint a fresh multi-megabyte file every time a file is saved.
database.py:77   age_hours = (datetime.now().timestamp() - os.path.getmtime(existing[0])) / 3600.0
```

It derives last-run from **filesystem mtime of its own newest output** — durable,
no schema change, no process memory, survives every death vector in §6.3. This is
the established answer to the exact problem F2 and F3 both have, and neither fix as
drafted uses it.

- **F2 must adopt this directly.** The reaper's own snapshot file in `backups/` *is*
  its last-run timestamp. No new state anywhere.
- **F3 may not need a DB read at all.** If the cooldown registry persists as a
  small JSON sidecar next to the DB, mtime + content give durability without the
  `__init__`-queries-the-observation-log fragility (and without the brittle
  title-parse of `alert_content`, §9).

#### 7.2 Snapshot infrastructure is already there

F2 needs nothing new to write its JSONL:

```
app_paths.py:76    BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")
app_paths.py:82    ensure_dirs()          # already creates BACKUP_DIR
database.py:33     list_backups()         # newest-first, prefix/suffix filtered
database.py:29-30  _BACKUP_PREFIX / _BACKUP_SUFFIX convention
database.py:86     staging = dest + ".partial"
database.py:111    os.replace(staging, dest)     # atomic, only after verification
```

House pattern is **write `.partial` → verify → `os.replace` into place**. The reaper
must snapshot the same way: never delete rows until the snapshot has landed
atomically. Live: `C:\Users\insom\AppData\Local\PersonaApp\backups`.

#### 7.3 The reaper's connection must use the exact shared path string

```
database.py:20-27  _original_connect = sqlite3.connect
                   def _custom_connect(...):  if args[0] == DB_PATH: kwargs.setdefault('timeout', 30.0)
                   sqlite3.connect = _custom_connect
database.py:17-19  NOTE: this wrapper is keyed on STRING EQUALITY with DB_PATH ...
                   a divergence here silently disables lock protection with no error at all.
database.py:136-137  PRAGMA journal_mode=WAL;  PRAGMA synchronous=NORMAL;
```

The reaper DELETEs while the daemon may INSERT. There is **no other concurrency
protection than that injected `timeout=30.0`** — no advisory locking, no
transaction discipline around `add_observation` (`:967-993` opens, inserts,
commits, closes). So the reaper must `import` and use `app_paths.DB_PATH` verbatim;
recomputing the path silently drops it to the 5-second sqlite default and buys
intermittent `database is locked` under exactly the load that matters.

Note `backup_database` deliberately calls `_original_connect` with an **explicit**
`timeout=30.0` (`:88`, `:90`) rather than relying on the wrapper. Mirror that.

#### 7.4 Tunable convention, for F1's cap and F2's TTL

```
stream_worker.py:25   GAP_COOLDOWN_SECS = int(os.getenv("DAEMON_GAP_COOLDOWN_SECS", 86400))   # class attr
stream_worker.py:31   self.idle_threshold = int(os.getenv("DAEMON_IDLE_THRESHOLD", 300))      # instance attr
```

Class attribute + `DAEMON_`-prefixed env override, documented in a comment directly
above. New constants follow this or they will look foreign.

---

### 8. Decisions taken this session

| | decision |
|---|---|
| **Scope** | Apply **F1–F4**. **F5 deferred** (relinking `Reflection:` nodes) — it is the true root cause but touches the reflection writer, not the daemon, and is a separate design conversation. |
| **Reaper policy (F2)** | **Hard DELETE, but snapshot first.** Write the doomed rows to `backups/` as JSONL, verify, then delete. Every run recoverable. Preserve `AUTOINCREMENT`/`sqlite_sequence` so "deleted" stays distinguishable from "never created" (§Investigation method notes). |

Rejected: soft-archive via an `active` column — it mirrors `decay_cycle`'s
`SET active=0` (`memory_engine.py:537`) and is reversible, but requires a schema
change and leaves the table growing forever. The harm here is functional (the
10-row window), and archiving fixes that too — but it does not fix it any *better*
than deleting, at the cost of a migration.

---

### 9. Open risk — identified, NOT verified

**The F1 × F4 interaction.** This was flagged for adversarial review and the review
never ran (§10). It is unresolved and it is the highest-risk item in the fix set.

F1 as drafted is "count `entropic_gap` rows for this `(username, persona)` in the
last 24h; if ≥ N, return." F4 makes `add_observation` **silently dedup**
daemon-authored types — `database.py:983-985` already returns `True` for a
suppressed duplicate, so the caller cannot tell an insert happened from one that
did not:

```
database.py:983-985   if c.fetchone() is not None:
                          conn.close()
                          return True          # duplicate reflection, skip silently
```

If F1 counts **rows**, and F4 is suppressing writes, then the count
under-represents attempts and **the cap binds later than intended** — every
suppressed duplicate is a free attempt. The two fixes interfere in the direction of
*more* permissiveness. Worse, the interaction is invisible: the log shows a cap of
N being respected while the daemon made far more than N attempts.

Direction to evaluate first: **count attempts, not rows** — F1 should decide before
calling `add_observation`, from state F4 cannot mutate, and `add_observation`
should return something distinguishable (`True`/`"deduped"`, or a row id) so
suppression stops being silent. Fixing that return contract may be a prerequisite
to F1, not an optional tidy-up.

Also unverified, and needed before F3 is written:

- **Is `title` unique per `(username, persona)`?** F3 rekeys the registry on title.
  Two nodes sharing a title (plausible for `Reflection: <timestamp>` collisions)
  would silently merge their cooldowns. Not checked.
- **Is `node_id` actually regenerated on re-derivation?** §3b asserts it (196
  deleted / 503 created). The generation code was not read this session.
- **Can `title` be recovered from `alert_content` by parsing?** F3's rehydration
  depends on parsing `stream_worker.py:406`
  (`"...isolated pocket of knowledge: '{node['title']}'"`). A title containing a
  quote breaks it. §7.1's JSON sidecar avoids the parse entirely — prefer it.
- **F3 under concurrent daemons** (§6.3 vector 2). Two processes rehydrating and
  writing the same durable registry. Not designed.

---

### 10. Dead end: parallel agent fan-out

Attempted a 10-agent fan-out (5 subsystem mappers → 4 adversarial trap-hunters per
fix → 1 synthesis) to produce the integration plan and stress the F1×F4
interaction. **All 10 agents failed with `API Error: 402 — insufficient credits`.**
Zero results returned; ~291k subagent tokens billed for no output.

Everything in §5–§9 is from direct reading instead. Do not re-run that fan-out on
the same account without checking credit first — it fails *after* burning tokens,
not before.

---

### Next session — start here

1. **Answer §9 before writing any code.** Specifically the F1×F4 interaction and
   the `add_observation` return contract. That decision changes how F1 is written,
   so it is genuinely blocking.
2. **Then F1**, using the §7.4 constant convention. It remains load-bearing —
   §6.2 raises its ceiling from 288 to ~1440/day, which strengthens the case.
3. **Then F2**, built on §7.1 (mtime-as-last-run), §7.2 (`.partial` → verify →
   `os.replace`), §7.3 (`app_paths.DB_PATH` verbatim + explicit `timeout=30.0`).
4. **Then F3**, restated per §6.3 — durable because processes die three ways and
   can double up, not because edits reset a dict. Prefer the §7.1 JSON sidecar over
   `__init__` DB I/O and over parsing `alert_content`.
5. **Then F4** — and fix the stale docstring at `database.py:963-965` in the same
   edit, since that docstring is why this stayed invisible for so long.

**The single observable that proves it worked:** `Sky/rick`'s window goes from
`6/10 [GGG.G..G.G]` to `0-1/10`, and the dup factor stops climbing from 2.30×.
Disk was never the problem.

Do not repair anything in the `- Copy`, `- Copy (2)`, `garage/`, or
`dist_installer/` trees. They are stale and `garage/` predates the cooldown
entirely.

---
---

# Session 3 — 2026-08-28, later still

Scope: **§9 answered. §11-§15 were written before any edit; §16 onward record the
edits that were then applied.** One 4-agent fan-out (Opus 5, xhigh)
did run this time — 510k tokens, 0 errors — but every load-bearing claim below was
re-verified by direct read/query before being written down. Provenance is marked:
**[D]** = verified directly this session, **[A]** = agent-reported, spot-checked only.

> ⚠️ **§11 invalidates the root-cause story in §4, and §14.2 invalidates the success
> observable in "Next session — start here".** Read §11 and §14 before writing code.

---

### 11. The gap detector joins on the wrong column. It always has. **[D]**

`stream_worker.py:213` fetches links with

```
WHERE source_node_id IN (SELECT node_id FROM zettel_nodes WHERE username=? AND persona=?)
```

but `zettel_links.source_node_id` / `target_node_id` store `zettel_nodes.id` — the uuid
PK — not `node_id`, which is the bracketed slug `[[CONCEPT-KB-155-001]]`.

```
zettel_links                 151 rows, 231 distinct endpoints
  matching zettel_nodes.id      159
  matching zettel_nodes.node_id   0      <-- zero, every persona, every snapshot
  matching neither               72      (dangling; no FK on the table)
```

The predicate is in the **WHERE clause**, so `links` at `:215` is an empty list and the
degree loop at `:220-223` never executes a single iteration. `degrees` is not "all zero
after processing" — it is never touched after construction. **`if deg <= 1` at `:231` is
vacuously true for every node in the database.** Eligibility collapses to
`len(content) > 150`, exactly:

| | nodes | pool as shipped | pool if join repaired | delta |
|---|---|---|---|---|
| Sky/rick | 329 | **284** | 276 | -8 |
| SkyTest/rick | 98 | **93** | 92 | -1 |
| Sky/v | 94 | **64** | 42 | -22 |
| Sky/eni | 34 | **25** | 24 | -1 |

`git log -L 211,215:stream_worker.py` → the line was wrong in `2799982` (2026-06-11), the
commit that created the file. Never worked, not a regression. **[D]**

Three consequences:

- **§4's root-cause framing is wrong.** The daemon is not "correctly reporting that the
  graph is 71% disconnected." It is reporting 100%, because it cannot see edges at all.
- **§3a's pool table is reproducible after all, and that is the tell.** `(293, 19, 14)` is
  `deg == 0` + **PK join** + `len > 150`, personas merged across usernames. **[A, spot-checked]**
  The previous session measured the graph *correctly* and assumed the shipped code did the
  same. It doesn't. The code's real pool is `(284, 25, 64)` per `(username, persona)`.
- **The daemon writes links it cannot read.** `stream_worker.py:566` creates
  `relationship='bridges'` edges using `gap['node']['id']` — the PK — and 29 such rows
  exist. The write side was always correct; only the read side is wrong. So the completion
  signal for the livelock **exists in the data** and the detector is structurally blind to
  it. That is a stronger statement than §4's "nothing verifies a link was created." **[A]**

Scope of the repair: `stream_worker.py:213` is the **only** site in the live tree that
joins `zettel_links` on `node_id`. Every other consumer uses the PK (`database.py:417`,
`:893`, `:1073`, `:1093`, `:1237-1238`; `main.py:830`). One-line change plus rekeying
`degrees` on `n['id']` at `:219/:222-223/:228-229` and the cooldown key at `:246`. No data
repair — the stored data was always right. **[A, greps re-run]**

**Do not oversell it.** It cuts rick's conveyor belt by 3%. F1 is still load-bearing.
It is a correctness fix, not the volume fix.

---

### 12. §9 — ANSWERED

#### 12.1 The F1xF4 interaction is real, and worse than "binds later" **[A, mechanism verified D]**

F4's dedup SELECT (`database.py:978-982`) carries **no time predicate**. Suppression is
therefore permanent, so a trailing-24h *row* count is ceilinged at the number of distinct
contents ever written for that `(username, persona)`:

```
ceiling on a 24h row count under F4:  Sky/rick 31   Sky/v 3   Sky/eni 3   SkyTest/rick 1
```

With a cap of N=5, three of the four personas can **never reach it**. The failure mode is
"never binds," not "binds late."

**Decision: F1 counts attempts, never rows.** The precedent is already in the file —
`resolve_entropic_gap` records its cooldown at `:399-400` *before* calling
`add_observation`. F1 follows that: decide from state `add_observation` cannot mutate.

Corollary nobody raised: **F2 x F4 also interfere.** F4's suppression is only as durable as
the row it matches against, and F2's reaper deletes exactly those rows — a 7-day TTL frees
5 content strings to be re-written, 14-day frees 4, 30-day frees 4. The pair produces a
sawtooth. Second independent reason F1 must not read row counts. **[A]**

#### 12.2 `add_observation` return contract

It returns `True` for both a real insert and a silent skip (`database.py:983-985` vs `:994`),
and **`resolve_entropic_gap` ignores the return entirely** (`:411-417`). **[D]**

Since F1 no longer consults rows, the contract change is no longer a *prerequisite* — but
it is still required for the second unbounded writer found in §14.1, whose idempotency mark
depends on knowing whether the write landed. Make it return a distinguishable value and fix
every call site in the same edit.

#### 12.3 Is `title` unique per `(username, persona)`? YES **[D]**

```
Sky/rick 329 rows / 329 distinct titles      Sky/v  94 / 94
SkyTest/rick 98 / 98                         Sky/eni 34 / 34      collisions: 0
```

Two qualifiers:
- Titles are **not** globally unique — at least 15 appear under two different
  `(username, persona)` pairs (`The Contradiction Engine`, `Uncertainty Protocol`, ...). **[A]**
  F3's key must be the full `(username, persona, title)` triple.
- Nothing enforces it. The only index on `zettel_nodes` is the non-unique
  `idx_znodes_user_persona`. Uniqueness is a property of today's data, not a constraint. **[A]**

#### 12.4 Is `node_id` regenerated? Worse than that — it was never title-derived **[D]**

`zettel_engine.py:379-394` mints `[[{CAT}-{slug20}-{NNN}]]`, slug truncated to 20 chars,
`NNN` an ordinal from a collision counter seeded by insertion order. The live values for the
auto-generated reflection nodes are `[[CONCEPT-INTERNAL-MONOLOGUE-NNN]]` — **not derived
from the title at all**; 103 rows collapse into 4 prefix buckets separated only by ordinal
(Sky/v alone has 45 titles under one prefix). And 188 of 555 node_ids don't match the
`[[X-NNN]]` shape, so `_generate_node_id` isn't the only producer. **[A]**

§3b's "secondary leak" was right that node_id is unusable as a key, for the wrong reason.
`(username, persona, title)` is the only stable key available.

#### 12.5 Can `title` be parsed out of `alert_content`? Yes — refuted as a risk **[D]**

85/85 rows parse, 37 distinct titles, 0 failures. 20 live titles contain apostrophes and
all round-trip: the pattern is greedy and anchored on `'.\n`, so it captures to the last
quote on the line. **[A]** Prefer §7.1's JSON sidecar for durability and concurrency —
not for this.

---

### 13. Corrections to §6.2 — the cadence is the NLI scan, not dopamine

#### 13.1 The dopamine gate is a **latch**, not a duty cycle **[D]**

`boost_tonic(+0.04)` at `:264-266` fires on gap *discovery*. Decay removes only
`(T-0.30) * 0.0220` per 60s cycle. There is no fixed point below the clamp
(`0.30 + 0.04/(1-e^-(60/2700)) = 2.12`). Simulated from 0.51 it pins at saturation in ~16
cycles and stays. The gate is bistable: parked at baseline emitting nothing, or latched
open emitting every cycle. §6.2's "emitting on only one of 5-7 turns" is not a state this
system has.

And the daemon **cannot open the latch itself** — `boost_tonic` sits downstream of the very
gate it would open. Cold start is exactly 0.30. Something external (a successful tool call
in the web process, `+0.15`) must cross it first. **[A, arithmetic D]**

Live proof, right now: PID 31776 has been cycling since 19:07 with a fresh heartbeat and has
written **zero rows**, because every `da:*:tonic` key is absent from Redis and `get_state`
returns baseline. **§5's "the daemon has not run since the notes were written" is wrong** —
it has run for hours. It is not dormant, it is *armed*. **[D]**

Burst *termination* is also not the cooldown or pool exhaustion: `dopamine_state.py:84`
writes tonic with `ex=TONIC_TAU_SEC*4` = 3h. App down longer than that → key gone → latched
off. That explains why burst days look random (Jul 15, Aug 22, 23, 27). **[A]**

#### 13.2 The 5-7 minute spacing is 139 blocking LLM calls per cycle **[D]**

`analyze_semantic_conflicts` is called at `:141` **ungated by `exploring`** — only the gap
picker is gated. It is an O(n^2) scan that returns early only on `CONTRADICT`, which has
never happened in the table's history, so it runs to completion every cycle.

Replaying the Jaccard predicate at `:317` against the live graph:

```
Sky/rick     137 qualifying pairs of 53,956 scanned
SkyTest/rick   2 qualifying pairs of  4,753 scanned
             -------------------------------------
             139 blocking round-trips per cycle
```

**139, not 261.** `run_cycle` builds **one context per user** (`:66-115`), active persona
only, and both users' active persona is `rick` — `Sky/v` and `Sky/eni` are never scanned.
The 261 figure sums all four `(username, persona)` pairs, which is not a state the runtime
has. **[D]**

At the measured ~1.9-2.0s per Vertex round trip that is 264-279s of work, against a 60s
sleep — **so the loop period is ~300-340s and §6.2's 60s cadence claim is moot**. The
notes' own observed 288 rows/day is `86400/300`: that is the cycle period showing through.
The theoretical ceiling is ~262-286 rows/day, **not 1440**. **[A, arithmetic D]**

Dopamine controls **whether** it emits. The NLI scan controls **how fast**. §6.2 has these
swapped.

#### 13.3 The picker walk, pinned exactly **[A]**

`epoch_slot = int(now // 86400)` is days-since-epoch — **constant for a whole UTC day**
(20693 on 08-27). It contributes no rotation. The apparent rotation is entirely `len(fresh)`
shrinking under a constant numerator: each removal advances the index by `20693 // len`.
Against a length-descending sort that yields descending runs of 3-4 then a jump — and the
observed burst's content lengths `[1339, 977, 694, 523 | 1314, 921, 546 | ...]` match the
simulated signature. Useful forensically: **content-length order fingerprints which cycle
wrote which row, and whether the registry reset between them.**

Which is how the Aug 27 registry wipe was caught: five nodes repeat at an exact +35min
offset (`On Ethics` 20:55->21:30, `KB (168)` 21:00->21:35, ...), impossible under a 24h
cooldown unless the in-memory dict was cleared and the walk restarted. Empirical
confirmation of §6.3's holder-death vectors, with a timestamp. **[A]**

Also: `GAP_COOLDOWN_SECS` is **both** the cooldown length (`:247`) and the rotation modulus
(`:260`). Tuning it silently retunes the picker. Split the constant before touching it.

#### 13.4 The daemon has pinned itself to one persona **[D]**

`user_settings.active_persona_key` is empty for both users, so `run_cycle` resolves the
active persona from the **observations fallback** at `:78-84` (`ORDER BY id DESC LIMIT 1`).
The daemon's own `entropic_gap` rows are therefore what select which persona it scans next.
Sky/rick is newest → rick gets scanned → writes another rick row → stays newest. It cannot
rotate to `v` or `eni` unless the user chats as them. Self-reinforcing, and not previously
noted.

---

### 14. Findings that change the fix set

#### 14.1 `generate_idle_monologue` is a second unbounded writer — BLOCKING **[A, corroborated D]**

> ⚠️ **CORRECTED IN §46 (Session 8) — and FIXED in §45.** The re-arm is real and
> was measured at **87 redundant fires out of 106 Reflection nodes (82%)**, clustering
> at one refire per 60 s cycle on personas whose idempotency triple is frozen (§44).
> But the second paragraph below is no longer evidence of anything: `internal_reflection`
> is reaped by F2 **by design** (`database.py:183`), so the node-to-observation ratio is
> now 106:1 and cannot be read. Its stated mechanism was wrong too — `add_observation`
> cannot raise, it swallows and returns `False`. The only statement in the fan-out that
> can propagate is `bulk_append_to_zettel_cache`.

Its idempotency mark (`:580`) *and* the Redis mirror (`:581-586`) are the last two statements
of the `try` opened at `:494`, which also wraps the LLM call and the whole write fan-out.
Anything raising between `:495` and `:580` skips **both** durability layers and re-arms the
monologue next cycle.

Measured: 22 of 37 `internal_reflection` rows (59%) fired against an already-processed
`(username, persona, last_user_timestamp)` triple; six against the single timestamp
`2026-08-19 21:53:33` across three days. And 103 `Reflection:` zettel_nodes exist against
only 37 observations — 66 nodes (64%) whose path died after `add_zettel_node` (`:541`) and
before `add_observation` (`:569`).

Every duplicate fire costs an uncounted LLM call, 3 permanent zettel rows, and **one more
gap-eligible orphan**. The monologue writer is feeding the pool the gap detector scans.

#### 14.2 The success observable in "Next session — start here" measures a window nothing reads — BLOCKING **[D]**

`database.py:999`'s `limit=10` is a **default used by nobody on the prompt path**. The real
consumer is `llm_engine.py:1677-1679`: `limit=15`, then non-dense `[-5:]`.

```
                     REFLECTOR GATE             REAL WORKING_MEMORY (5 slots)
                     (newest 20, need >=5 raw)  (llm_engine.py:1677-1679)
  Sky/rick           raw=5  daemon=11  0 HEADROOM   3/5 daemon  [RGG..]  1819 ch
  SkyTest/rick       raw=9  daemon= 2  4 headroom   0/5
  Sky/v              raw=13 daemon= 0  8 headroom   0/5
  Sky/eni            raw=20 daemon= 0 15 headroom   0/5
```

Note `[-5:]` on an `ORDER BY id DESC` list takes the **oldest** five of the newest fifteen —
a separate latent bug worth its own line.

**Re-anchor the acceptance test on `llm_engine.py:1677-1679`.** A fix can drive the
`database.py:999` metric to `0/10` while the actual injected window stays poisoned.

#### 14.3 Sky/rick is ONE ROW from permanent Reflector death — BLOCKING **[D]**

`plugins/memory_plugin.py:43-46` reads the newest 20 and returns early if fewer than 5 are
in `['user_message','assistant_response','tool_output']`. Sky/rick currently has **exactly
5**. Threshold is 5.

One more `entropic_gap` row → 4 → `reflect()` returns before the LLM call → `dense_observation`
stops being written for that persona **permanently**, since nothing removes daemon rows from
the window. A hard cliff, invisible in the table (the absence of a row).

Note two of the three types in that filter — `assistant_response`, `tool_output` — **have
never been written**. Only four event types have ever existed. So the gate is effectively
counting user messages alone. **[A]**

This inverts F2's risk profile: **deleting old `entropic_gap` rows restores the Reflector.**

#### 14.4 F5 as drafted is a no-op — BLOCKING **[A]**

Eligibility is `deg <= 1`. A node needs **two** links to escape the pool, and the daemon's
own repair writes exactly **one** (`:566`). All 29 `bridges` source nodes sit at degree
exactly 1; 13 of them are still gap-eligible. Across the 103 `Reflection:` nodes the degree
histogram is `{0:72, 1:29, 4:1, 7:1}` — 98% satisfy `deg <= 1`.

"Link the Reflection: nodes at creation" removes **zero** nodes from the pool. Either the
predicate becomes `deg == 0`, or reflection nodes are excluded by `node_class`/title, or F5
writes >=2 links. As drafted it will read as a fix and change nothing.

#### 14.5 `resolve_semantic_conflict` has no guard of any kind — HIGH **[D]**

`:419-440` goes straight to `add_observation`. No cooldown registry, no dedup, no rate limit
— while `resolve_entropic_gap` at least has `_gap_cooldowns`. And `analyze_semantic_conflicts`
returns the **first** contradicting pair in a deterministic scan order, so one genuine
`CONTRADICT` reproduces identical content every cycle forever, with **both** nodes' full
content embedded (`:429-430`) — roughly twice the row size of an `entropic_gap`.

§5 downgrades this type to "prophylactic, not remedial." That judgement rests on it never
having fired, not on it being guarded. **It is guarded strictly less than the path that
already flooded.** F1 must cover it.

#### 14.6 F2's snapshot-then-delete is unsafe as drafted — HIGH **[A]**

Python's sqlite3 is in legacy transaction control here (no `autocommit=`/`isolation_level=`
anywhere in the tree). A bare `SELECT` opens no transaction; only the first DML does. So
`SELECT ... WHERE timestamp < cutoff` (writes the JSONL) followed by `DELETE ... WHERE
timestamp < cutoff` **re-evaluates the predicate against newer state** — any row the daemon
commits in between is deleted without ever appearing in the snapshot.

§7.2's `.partial` -> verify -> `os.replace` protects the *file*, not the *row set*. Fix:
capture explicit ids and `DELETE ... WHERE id IN (...)`, or `BEGIN IMMEDIATE` before the
SELECT. No deadlock risk found (WAL, single writer).

Related, and §7.3 is incomplete in the direction it warns about: the `timeout=30.0`
monkeypatch lives in **`database.py`'s module body**. A reaper that does
`from app_paths import DB_PATH; import sqlite3` — precisely what §7.3 prescribes — never
executes the patch and gets the 5s default. The equality is also against the bare path, so
any `file:...?mode=ro` URI silently misses. **Real rule: `import database`, or pass
`timeout=30.0` explicitly, as `backup_database` does at `:88`/`:90`. Never rely on the
wrapper.**

#### 14.7 The lock TTL is shorter than the measured cycle — HIGH **[A]**

Boot computes `_ttl = max(300, interval*5)` = 300s for the staleness check, but hands the
worker `_lock_ttl = max(180, interval*3)` = **180s**, which is what `_renew_lock` actually
SETs — once per ~300s cycle. So the key is absent ~40% of wall clock and any spawn's
`SET NX` succeeds outright; the zombie branch never even runs. On the two measured 417s/428s
cycles a *healthy* incumbent's heartbeat is >300s stale, so a spawn deletes the lock, the
incumbent reads a foreign boot_id at `:601` and shuts down — taking its populated
`_gap_cooldowns` with it.

This is §6.3 vector 2 as a **routine occurrence, not an edge case**, and it gets worse as
the graph grows (NLI cost drives cycle time). Any F3 design must assume handoff every few
cycles. Derive TTLs from measured cycle time, not from `interval`, which is only the sleep.

#### 14.8 Smaller, worth folding into whichever edit is already open **[A]**

- `dopamine_state._MEM_FALLBACK` (`:39`, `:78`, `:68-69`) has no expiry, so inside a live
  process the 3h Redis TTL never returns tonic to baseline — only process death does. With
  Redis down, daemon and API keep two independent tonic values. Third process-lifetime
  assumption on this path, and the only one hiding in another module.
- `generate_idle_monologue` loads **every embedding BLOB** for the persona (505 KB for
  Sky/rick) to build a set of `node_id` strings (`:534-535`). `include_embeddings=False`
  exists and `database.py:1152-1156` documents it for exactly this.
- `bulk_append_to_zettel_cache` is a cross-process read-modify-write on one Redis key
  guarded only by a process-local `threading.Lock` — the daemon can silently drop nodes the
  API just indexed.
- 103 `zettel_entries` rows sit at `processed=0`; the daemon writes the entry then bypasses
  the pipeline and never marks it. If anything ever processes them it mints a fresh node set
  and multiplies the pool in one batch.
- `q:daemon:heartbeat` (`:49`) and `daemon:last_monologue:*` (`:584`) are written with **no
  TTL**, while `q:daemon:lock` has 180s. `main.py:811-814` returns the heartbeat with no
  staleness check, so a dead daemon reports live forever.
- Daemon LLM spend is unattributed: `stream_worker.py` never calls `increment_usage`, and
  because `.env` sets `VERTEX_PROJECT_ID` every call routes to `google_vertex`, which
  `llm_engine.py:387-391` excludes from the key-pool accounting. `call_nli_gate` swallows
  every exception and returns `NEUTRAL` (`:374-376`) with no log line, so the subsystem's
  cost varies ~250x (8ms vs 2.0s per call) with no observable difference in the logs.
  **Removing `VERTEX_PROJECT_ID` from `.env` is a zero-code kill switch for all daemon LLM
  calls** — worth knowing before measuring anything.
- Two DB connections open without `try/finally` (`:150-158`, `:446-453`). In WAL a pinned
  read connection is what makes a DELETE-heavy reaper appear to reclaim no space.

---

### 15. Revised plan

Ordering changed. §11 goes first because it is one line and everything downstream is
measured against a pool it defines; §14.3 goes first because the system is one row from an
irreversible state.

| # | fix | why here |
|---|---|---|
| **F0** | **Stop the bleeding.** Sky/rick has 0 Reflector headroom (§14.3). Either prune its `entropic_gap` rows or leave the app down until F1 lands. Not a code change — an operational decision to make *now*. | one row from permanent `dense_observation` death |
| **F1a** | Repair `stream_worker.py:213` -> `SELECT id`, rekey `degrees` on `n['id']` (`:219`, `:222-223`, `:228-229`) and the cooldown key at `:246`. | one line, no data repair, sole offender. -3% pool. Correctness, not volume. |
| **F1b** | Per-`(username, persona)` cap, **counting attempts not rows**, decided **before `:264`** — not in `resolve_entropic_gap`. | F1 as drafted caps the write but leaves the arousal: every capped attempt still boosts tonic and keeps the latch open (§13.1). Must gate upstream, and make the boost conditional on the write being admitted. Must also cover `semantic_contradiction` (§14.5). |
| **F2** | Reaper, built on §7.1/§7.2/§7.3 **plus** §14.6: delete by explicit id list, and `import database` for the connection. | also restores the Reflector (§14.3) |
| **F3** | Durable registry keyed on `(username, persona, title)`. | **has no throttling effect** (see note below). Do it for correctness of the picker walk, not for rate. |
| **F4** | Keep an explicit **allowlist** of daemon types to dedup — `{entropic_gap, semantic_contradiction, dense_observation}` — do **not** invert to a denylist. | a denylist means every future type is deduped by default; the assistant logger the Reflector already expects arrives as `assistant_response` (§14.3), falls outside `RAW_EVENT_TYPES`, and silently vanishes — reintroducing the exact `FIX(turn-loss)` regression. Also add an index for `:978-982` or it is a growing linear scan. Fix the `:963-965` docstring in the same edit. |
| **F5** | Deferred, and **redesigned** — as drafted it is a no-op (§14.4). | |
| **F6** | New: `generate_idle_monologue`'s idempotency mark must be written independently of the write fan-out (§14.1). | second unbounded writer, feeds the gap pool |

**On F3, stated plainly:** cooldowns are per-node and dated from each node's own flag time,
so a durable registry frees nodes *staggered*, never simultaneously — there is no thundering
herd. But Sky/rick's 276-node pool at ~302s/cycle drains in 23.15h against a 24.00h cooldown,
so F3 converts the system into a clean 1-emission-per-cycle limit cycle at ~276 rows/day.
The Aug 27 burst already ran at that ceiling (38 rows / 3h17m = one per cycle). **F3 changes
which node is flagged, not how many** — and over a long enough session it would *raise*
Sky/rick's daily total from 38 to 276. It will produce a clean-looking log while the write
rate is unchanged. Only F1b throttles.

**Not in F1-F6, and larger than all of them:** `analyze_semantic_conflicts` costs 139
blocking LLM calls per cycle (~112k input tokens per sweep, ~1.3M/hr) for a subsystem that
has produced **zero rows in its entire history**, is ungated by `exploring`, has no
memoisation, no early exit and no budget, and scales as O(n^2) in graph size. You can hit
every acceptance criterion below and still burn that forever. It deserves its own note.

### Acceptance criteria — replaces the one in "Next session"

1. `llm_engine.py:1677-1679` window for Sky/rick: `3/5` daemon-authored -> `0-1/5`.
   *(Not `database.py:999`'s `6/10`. That window is read by nobody.)*
2. `memory_plugin.py:43-46` raw-event count for Sky/rick: `5` (0 headroom) -> `>=8`.
3. `entropic_gap` dup factor stops climbing from 2.30x.
4. `stream_worker.py:213` returns a non-zero row count for Sky/rick (currently 0).
5. Daemon LLM calls per cycle: 139 -> whatever you decide, but **measured**, not assumed.

Disk was never the problem. Neither, it turns out, was the cooldown.

---

### 16. APPLIED — F1a and F1b are in the working tree (uncommitted)

**State as of the end of this session:**

| | |
|---|---|
| app | **STOPPED.** `taskkill /PID 6336 /T /F` — 12 processes, incl. daemon PID 31776. |
| live DB | untouched. `sqlite_sequence.observations` still 5678, newest row still id 5678 / 2026-08-27 23:43:31, no `-wal`/`-shm`. |
| `stream_worker.py` | **MODIFIED** (was clean vs HEAD). +133/−5. |
| every other file | unchanged by this session. |
| rollback | pre-fix copies + `SHA256SUMS.txt` in **`%LOCALAPPDATA%\PersonaApp\rollback_20260829_prefix\`** — `stream_worker.py` (sha256 f538a7bf…, the only file actually edited), plus `database.py`, `app_paths.py`, `dopamine_state.py`, `zettel_engine.py`, and `entropic_gap_livelock.md.pre-session3`. Durable, outside the repo, and outside `backups/` so the F2 reaper will not walk it. |
ollback_20260829_prefix\`** — `stream_worker.py` (sha256 f538a7bf…, the only file actually edited), plus `database.py`, `app_paths.py`, `dopamine_state.py`, `zettel_engine.py`, and `entropic_gap_livelock.md.pre-session3`. Durable, outside the repo, and outside `backups/` so the F2 reaper will not walk it. |

Nothing was committed and nothing was pushed, by explicit instruction.

#### F1a — the join key (§11). APPLIED.

Three edits in `analyze_entropic_gaps`, plus a `FIX(join-key)` comment block:

```
:213   SELECT node_id FROM zettel_nodes   ->   SELECT id FROM zettel_nodes
:219   degrees = {n["node_id"]: 0 ...}    ->   degrees = {n["id"]: 0 ...}
:228   nid = n["node_id"]                 ->   nid = n["id"]
```

Verified by lifting the SQL back out of the patched file and replaying it read-only:

```
context          nodes   :213 rows   deg>0    POOL    (was)
  Sky/rick         329          97      96     276    (284)
  Sky/v             94          40      52      42     (64)
  Sky/eni           34           7       8      24     (25)
  SkyTest/rick      98           2       3      92     (93)
```

**Acceptance criterion #4 met** (`:213` non-zero for Sky/rick).

Deliberately **not** done: the `source_node_id`-only asymmetry at `:213`. Measured
bidirectional — identical 434-node total pool, because the 24 target-only nodes are all
reached via same-persona sources. Inert here. Left minimal on purpose.

#### F1b — the cap (§12.1, §13.1, §14.5). APPLIED.

New class constants at the top of `ConsciousnessWorker`, following the `:25` convention:

```python
DISSONANCE_CAP         = int(os.getenv("DAEMON_DISSONANCE_CAP", 8))
DISSONANCE_WINDOW_SECS = int(os.getenv("DAEMON_DISSONANCE_WINDOW_SECS", 86400))
```

Deliberately **not** reusing `GAP_COOLDOWN_SECS` — §13.3/§14.8: it doubles as the rotation
modulus at `:260`, so reusing it as a generic "one day" would silently reshuffle which nodes
get picked. A warning comment to that effect was added above it.

New method `_claim_dissonance_slot(username, persona, kind, claim=True) -> (allowed, used)`,
placed after `get_db_connection`. Atomic Redis `INCR` + `EXPIRE`, keyed
`q:daemon:dissonance:{kind}:{username}:{persona}:{slot}`. Process-local dict fallback when
Redis is down. `self._dissonance_fallback = {}` added to `__init__`.

Counts **attempts, never rows** — the §12.1 decision. Durable in Redis specifically because
of §14.7 (lock TTL 180s < measured cycle ~300s ⇒ two writers is routine); `INCR` is atomic so
concurrent daemons share one budget.

Three call sites:

| where | what | why |
|---|---|---|
| `analyze_entropic_gaps`, after the `if not fresh` guard | authoritative claim | **before `boost_tonic` at `:264`** — §13.1. Capping the write while still boosting would hold the explore latch open and spin the daemon at full rate emitting nothing. |
| `analyze_semantic_conflicts`, top of method | advisory, `claim=False` | skips the sweep when the budget is spent — **139 blocking LLM calls per cycle** not made |
| `resolve_semantic_conflict`, before the write | authoritative claim | §14.5 — that path had no guard of any kind |

Budgets are **per-kind**, so gap spam cannot starve contradictions.

Verified — six unit behaviours against live Redis (binds at attempt 9 with cap 8; advisory
does not consume; per-kind isolation; per-persona isolation; `cap<=0` disables; degraded
fallback binds), then end-to-end via the real `analyze_entropic_gaps` against a disposable
copy of the DB with `boost_tonic` stubbed to a counter:

```
emissions: 8   boost_tonic calls: 8
PASS: emissions capped at 8
PASS: boost fired only on admitted emissions (latch stays shut once capped)
```

All test keys deleted from Redis; probe DB removed; live DB never opened for write.

---

### 17. ⚠️ DO NOT RESTART YET — F1b does not save the Reflector

F1b drops the ceiling from ~276 rows/day to 8. **Two is all it takes.**

```
Sky/rick newest-20 window (newest first):
  G G G R G D U G R G G D U D U D U G U G
  (U=user_message  G=entropic_gap  R=internal_reflection  D=dense_observation)

  +0 new gap rows -> raw=5  ok
  +1 new gap rows -> raw=5  ok        <-- evicts a G, survives
  +2 new gap rows -> raw=4  REFLECTOR DEAD, permanently
```

The oldest row in the window happens to be an `entropic_gap`, so the first emission is free.
The second evicts a `user_message`, `raw` drops below `turn_threshold`, and
`dense_observation` stops being written for Sky/rick forever — nothing removes daemon rows
from that window.

**F2 is therefore the restart gate, not a nice-to-have.** Build it per §15 (snapshot to
`backups/` as JSONL, `.partial` → verify → `os.replace`, delete by explicit id list not by
re-running the predicate, `import database` so the connection actually gets `timeout=30.0`),
then clear Sky/rick's backlog, then restart.

---

### 18. The observer only watches one side — and that is the other half of §14.3

`run_observers` has exactly **one** call site in the tree:

```
llm_engine.py:1828-1830   threading.Thread(target=manager.run_observers,
                                            args=("user_message", text_only_message), ...)
```

One dispatch, one event type, fired on the inbound message. Nothing anywhere emits
`assistant_response` or `tool_output` — yet the Reflector gate accepts all three:

```
plugins/memory_plugin.py:44   raw_events = [l for l in logs
                                  if l['type'] in ['user_message','assistant_response','tool_output']]
```

Two of the three are dead letters, so `turn_threshold` counts **user messages, not turns**.

**This reframes §14.3.** The Reflector fragility is not only "too many daemon rows," it is
equally "too few raw rows." The flood met the cliff halfway. If the assistant side were
logged, a 20-row window would carry roughly double the raw events and 11 daemon rows could
not crowd it to threshold.

Also `memory_plugin.py:48`: the gate filters on `raw_events`, then `log_text` feeds the LLM
**all 20 rows** — daemon alarm text included. ~45% of Sky/rick's reflection input is the
daemon's own panic being summarised as "facts learned about the user."

Two consequences:

- **Keep F4 as an allowlist** (§15). If the assistant logger is ever wired and F4 had been
  inverted to a denylist, `assistant_response` would fall outside `RAW_EVENT_TYPES` and be
  silently deduped — the exact `FIX(turn-loss)` regression. An allowlist fails open.
- Filtering `log_text` at `:48` to exclude daemon types is small and contained. Fold it into
  F4's edit. **Not yet done.**

Wiring the assistant logger itself is a **memory-system design change, not a flood fix** —
it doubles observation write volume and changes what `WORKING_MEMORY` and the Reflector see.
Deliberately not done.

---

### 19. Design thread (opened, NOT built): salience-gated observation

Owner's framing, recorded because it changes the requirement: `turn_threshold=5` is a
**deliberate compute dial**, not an oversight — settable to 1 or 1000 on purpose. The ask is
not "tune it," it is *"always watching, but not writing everything down"* — write on
**relevance**, dynamically, instead of every N turns.

Primitives already present, all local, no API cost:

```
observations.reflection_score FLOAT DEFAULT 0.0   a salience column, currently a constant per type
                                                  (user_message 0.0, gap 0.7, reflection 0.8, dense 1.0)
zettel_nodes embeddings        555/555 present    complete free novelty reference
embedding_model.py             all-MiniLM-L6-v2   shared thread-safe singleton, already resident
dopamine_state phasic          bidirectional      :120 max(-0.3, rpe), decays in minutes
```

Proposed shape — **the counter stops being the trigger and becomes the guardrails**:

```
salience = w1*novelty      (1 - max cosine(turn, zettel graph))   local, ~ms
         + w2*phasic       (already computed per turn)
         + w3*unreflected  (turns/chars since last dense_observation)

reflect when  salience >= threshold AND turns_since >= MIN   (floor: no thrash)
         or   turns_since >= MAX                             (ceiling: never goes blind)
```

`turn_threshold` survives as `MAX` — still the compute dial, now a ceiling rather than the
trigger, so 1000 means "only when it matters" instead of "never."

Novelty against the zettel graph is self-limiting in the right direction: reflections write
nodes, nodes lower future novelty for that topic, so it stops re-recording what it knows.

**HARD CONSTRAINT — gate on phasic, never tonic.** `dopamine_state.py:120-122`: phasic is
bidirectional and decays in minutes; tonic only ever rises except by time decay. Tonic is
precisely what latched the daemon at saturation (§13.1). Wiring memory formation to tonic
reproduces the same bistable failure inside the memory system: silent for days, then writing
every turn.

Cheaper than every-5-turns: MiniLM on one window is milliseconds of CPU and no API call; the
expensive reflection LLM only fires when the gate opens. Local compute traded for remote.

Open before building:
1. What `novelty` compares against — the zettel graph, the last N `dense_observation`s, or both.
2. Whether a **negative** phasic dip should also trigger a write. A violated expectation is
   arguably the most memorable thing in a turn and currently scores zero.

---

### Next session — start here (supersedes the Session 2 list)

1. **F2, immediately.** It is the restart gate (§17), not an optimisation. App stays down
   until it lands and Sky/rick's backlog is cleared.
2. **F4** — allowlist, not denylist (§15, §18). Fix the `database.py:963-965` docstring and
   filter `memory_plugin.py:48` in the same edit.
3. **F6** — `generate_idle_monologue` idempotency mark written independently of the write
   fan-out (§14.1).
4. **F3** — durable registry on `(username, persona, title)`. Remember it does not throttle
   (§15); it is a correctness fix for the picker walk.
5. Then §19, as its own build.

F1a and F1b are done and verified (§16). Do not redo them. Do not restart before F2.

---
---

# Session 4 — 2026-08-29, evening

Scope: **F2 built, tested, and RUN against the live database.** One 6-agent fan-out
(3 recon + 3 adversarial refuters, 491k tokens, 0 errors — the §10 billing failure did
not recur). Provenance: **[D]** verified directly, **[A]** agent-reported and spot-checked.

Two of the three claims this session took into adversarial review came back **refuted**,
and both refutations were correct. They are recorded in §22 rather than buried, because
in both cases the wrong version was mine.

---

### 20. F2 APPLIED — and run. **[D]**

`database.py:reap_observations()`, `list_reap_snapshots()`, `_touch_reap_stamp()`, plus
two call sites. +352/−1 across exactly three files, verified against the pre-F2 rollback
copies rather than against `git`:

| file | delta |
|---|---|
| `database.py` | +312/−1 (the −1 is `from datetime import ... timedelta`) |
| `main.py` | +20 — `@app.on_event("startup") reap_observations_on_start` |
| `stream_worker.py` | +20 — per-cycle call at the tail of `start_loop` |

Rollback: `%LOCALAPPDATA%\PersonaApp\rollback_20260829_pref2\` + `SHA256SUMS.txt`.
**Note this is a different directory from Session 3's `rollback_20260829_prefix`,** which
predates F1a/F1b — restoring *that* one silently reverts Session 3's work as well.

**Policy.** A reapable row dies if it is older than `ttl_days` **OR** is not among the
newest `keep_per_context` reapable rows for its `(username, persona)`. The second clause
is the load-bearing one; see §22.3 for why age alone cannot do this job.

`keep_per_context` is a **combined** budget across all reapable types per context, not
per type — the injected window does not care which daemon wrote a row.

| constant | default | env |
|---|---|---|
| reapable types | `entropic_gap, semantic_contradiction, internal_reflection` | `PERSONAAPP_REAP_TYPES` |
| `ttl_days` | 14 | `PERSONAAPP_REAP_TTL_DAYS` |
| `keep_per_context` | **1** (floored at 1, never 0) | `PERSONAAPP_REAP_KEEP` |
| `min_interval_hours` | 1.0 | `PERSONAAPP_REAP_INTERVAL_HOURS` |

`internal_reflection` is in the default set **deliberately**, and this is the one policy
call worth re-examining later. It is there because it is the only writer on this path with
**no rate limit at all** — F1b's `_claim_dissonance_slot` covers `entropic_gap` and
`semantic_contradiction` only; `generate_idle_monologue` has no such call (`grep` over
`:610-720` returns nothing), and its idempotency mark is still the tail of the try block
§14.1 describes. Until F6 lands, **the reaper is the only bound on that writer.** [D]

`keep_per_context=1` is not arbitrary either: it is the only value at which *every* live
context lands at ≤1 daemon row in the 5-slot injected window. 2 leaves `SkyTest/rick` at
2/5; 3 leaves `Sky/rick` at 3/5. See §22.2 for the large caveat on what that buys.

**Durability idiom (§7.1), adapted.** `backup_database` derives last-run from the mtime of
its own newest output. A reaper cannot: a run that finds nothing to reap produces no file,
so keying off the newest snapshot means such a run never records that it happened, the
floor never binds, and it re-enters every cycle forever. F2 therefore writes a separate
`backups/reaped_last_run.stamp` on **every** completed run, no-ops included.

**Live run, 2026-08-29 21:09** (app stopped, no concurrency): [D]

```
forced full backup first : users_20260829_210844.db (7.2 MB) quick_check ok
dry run                  : would delete 118 of 122 — DB unchanged, 468 rows, seq 5678
real run                 : deleted 118 -> reaped_20260829_210902_425591.jsonl (206 KB)
                           {Sky/eni 26, Sky/rick 70, Sky/v 16, SkyTest/rick 6}
rows 468 -> 350          sqlite_sequence 5678 PRESERVED
snapshot rows 118 == deleted 118 | snapshot ids still live: 0 | 350+118 == 468
PRAGMA quick_check / integrity_check / foreign_key_check : ok / ok / clean
no -wal/-shm sidecars left behind; 20 tables intact
```

**Acceptance criteria (§15), measured on the live DB by replaying the real consumer code:**

```
context            daemon/5 (#1)   raw/20 (#2)   absorb        window
Sky/eni            0 -> 0          20 -> 20      15 -> 15      [UUUUU]
Sky/latent_space   0 -> 0           1 ->  1      n/a           [U]
Sky/rick           5 -> 1           5 -> 13       1 -> 10      [UUUUG]
Sky/v              0 -> 0          13 -> 13      12 -> 12      [UUUUU]
SkyTest/rick       2 -> 1           9 -> 10       8 ->  9      [UUUUR]

#1 llm_engine.py:1677-1679 window <=1 daemon, every context : PASS
#2 memory_plugin.py:43-46 raw/20 for Sky/rick >= 8          : 5 -> 13   PASS
#3 entropic_gap dup factor                                   : 2.30x -> 1.00x
#4 stream_worker.py:213 non-zero for Sky/rick                : already met by F1a (§16)
"absorb" = new daemon rows the context can take before raw < 5. NOT in §15; added because
it is the only number that says whether the fix HOLDS rather than whether it LANDED.
```

---

### 21. Two bugs in F2, both found by testing and neither by reading. **[D]**

Recording these because both survived careful authorship *and* a line-by-line read, and
were caught only by adversarial execution. The recon agent independently found the first
one by reading, which is the one data point in the other direction.

**21.1 The `sqlite_sequence` guard aborted on innocent concurrent writes.**
`seq_before` was read *before* `BEGIN IMMEDIATE`. In legacy transaction control a bare
SELECT opens no transaction (§14.6), so any `add_observation` committing in that gap moves
the sequence and the guard fires. It is meant to assert *"the DELETE did not touch
sqlite_sequence"*; as written it asserted the far stronger *"no INSERT happened anywhere
during the run"* — which a running daemon falsifies routinely.

Measured under two synthetic writers: **23 of 40 reaps aborted.** It failed safe (rollback,
nothing deleted) but under §14.7's routine two-daemon condition the reaper would rarely
have succeeded, while logging `[REAP] FAILED` in a way that reads like corruption.
Fix: read the sequence *inside* the transaction, where no other writer can commit.

**21.2 Snapshot filenames collided and silently overwrote each other — the one that
destroys recoverability.** `%Y%m%d_%H%M%S` is second-granularity. `backup_database` gets
away with it behind a 6-hour floor; a reaper does not, because `force=True` exists for
operators and two daemon instances are routine. Two reaps in the same second compute the
same name and the second `os.replace` overwrites the first — **with the first run's DELETE
already committed.** Measured: 40 runs inside one second left one snapshot holding **6 of
234 deleted rows.** Fix: microseconds + a collision loop for the destination, and the pid
in the staging name so two processes can never share a half-written file.

**Also hardened, from the same test pass:**

- The snapshot lands before the DELETE, so a failure *between* `os.replace` and `COMMIT`
  left a snapshot describing rows that are still in the table. Not a loss — a **lie**;
  restoring from it would duplicate live rows. The except path now retracts it.
- Orphan `.partial` accrual. Nothing in the tree janitors `BACKUP_DIR` of anything but
  `users_*.db` [A, greps re-run], so a hard kill leaks a staging file forever. Swept at the
  top of each run, age-gated to 1h so a concurrent instance's in-flight file is never hit.

**Test matrix, all passing** — 3 concurrent writers × 400 inserts against a reaper looping
60×: 0 errors, 0 `database is locked`, every deleted row present in exactly one snapshot,
exact row conservation. Fault injection at each realistic failure point (snapshot write,
DELETE statement, sequence assertion): nothing deleted, snapshot retracted, clean recovery.

---

### 22. Corrections. Two of them are to this session's own claims.

**22.1 §17's "permanently" is wrong. The Reflector stall is self-healing. [D, agent-found]**

`memory_plugin.py:45` is `if len(raw_events) < turn_threshold: return`. **Strict `<`.**
With `raw=5` against threshold 5, `5 < 5` is False — the Reflector *fires*. "Exactly 5,
zero headroom" was a PASS state, not a blocked one.

More importantly, §17's stated mechanism — *"nothing removes daemon rows from that window"*
— is false. The window is the newest 20 rows; every new `user_message` shifts it and
evicts the oldest row, which is frequently a daemon row. And `memory_plugin.py:120` logs
the incoming user message **before** spawning the reflector thread, so the next real turn
evaluates at raw+1. Replaying the gate historically across all 69 `Sky/rick` user turns:
it blocked on exactly **4** turns (ids 5651–5656, 2026-08-27 22:51–23:02) and self-cleared
within ~11 minutes. [A, mechanism re-read D]

**This does not retire F2.** The flood outruns user activity by two orders of magnitude
(38 rows in 3h17m against a handful of turns), and while stalled, no `dense_observation` is
written. But the correct framing is **a stall proportional to daemon volume**, not an
irreversible cliff, and F2 is a throughput fix rather than a rescue from a trap door.

**22.2 Criterion #1 is reachable by the reaper — my "structurally unreachable" was wrong,
and it is still not *durable*. [D]**

I argued #1 could never be met by retention and belonged to a filter at
`llm_engine.py:1679`. The refuter was right that this is false: because the shipped default
reapable set includes `internal_reflection` and `keep_per_context` is a *combined*
per-context budget, `keep=1` puts every live context at ≤1/5. No filter needed. That is why
the default is 1 and not the 3 originally chosen.

What survives of the original argument, and what the refutation did not test: **#1 is a
property of the instant after a reap, not a property the system holds.** Simulated forward
from the post-reap live state, `Sky/rick` goes 1/5 → 2/5 on the **first** new daemon row and
3/5 on the second. Retention cannot hold #1 at any `keep` value; only excluding daemon
types from `recent_events` at `llm_engine.py:1679` can. Both statements are true and the
useful one is the second. Treat #1 as a *green-light-at-reap-time* check, not an invariant.

**22.3 A pure age TTL is not "too weak" — it is a cliff. [D, agent-reframed]**

I claimed a TTL could not restore the window. Sharper, and correct: `Sky/rick`'s entire
52-row gap population lives in a 9-day span whose newest **38 rows landed inside one
4-hour window on 08-27**. The same TTL constant therefore deletes 0, 14, 22 or 52 rows
depending only on **what hour it happens to run**, and flips across the whole burst within
a ~2-hour band. The argument for `keep`-per-context is that it is *stable against that
cliff*, not that age "cannot" move the window. Same fix, better reason.

**22.4 §14.2's `[-5:]` claim is wrong, and its table under-reports the damage. [D]**

Held up under adversarial attack (the one claim of three that was not refuted).
`get_observation_log` ends in `return [... for r in rows[::-1]]` — it reverses the
`ORDER BY id DESC` result, so the list is **ascending** and `[-5:]` takes the **newest**
five non-dense rows. §14.2's "takes the oldest five — a separate latent bug" omits the
reversal and should be struck.

Consequently §14.2's table under-reports the poisoning it was written to expose:

```
                    §14.2 says      actually was
  Sky/rick          3/5 [RGG..]     5/5 [GRGGG]   <- 100% of injected working memory
  SkyTest/rick      0/5             2/5
```

Verified per context, ids carried through the real query: returned ids ascending in all 5
groups; for `Sky/rick`, `[-5:]` selects `5674-5678`, not `5662-5670`.

---

### 23. New consumer, not in any prior session's map: the telemetry panel. **[A, spot-checked D]**

`main.py:797` `@app.get("/settings/{username}/telemetry")` → `main.py:833-838`:

```sql
SELECT id, persona, event_type, content, reflection_score, timestamp
FROM observations WHERE username=? ORDER BY id DESC LIMIT 10
```

**No persona filter and no event_type filter.** Returned as `recent_dissonances`, rendered
by `vite-project/src/App.jsx:785-849` as *"Consciousness Observations Log (Internal
Dissonance)"* under Settings → Ecosystem Health — and present in the shipped `dist` bundle,
so it is live, not just source.

Consequence of F2: 6 of the 10 rows it showed for `Sky` were reapable, so the panel now
backfills with `user_message` / `dense_observation` rows, which `App.jsx:797-802` styles
contradiction-red and mislabels. **Cosmetic, and pre-existing** — the panel was already
showing 4 non-dissonance rows out of 10 before the reap. F2 makes an existing display bug
more visible; it does not create one. One-line fix if wanted: add
`AND event_type IN ('entropic_gap','semantic_contradiction','internal_reflection')` at
`main.py:833`. **Not applied** — it changes what a UI panel displays, which is a product
decision, not a flood fix.
> **[Session 10, 2026-09-03 — CORRECTION: APPLIED.]** Owner made the product call. Route had drifted to `main.py:817`, SQL to `:856`. Measured before/after in **§58**.

The most plausible feared coupling was checked and **does not exist**: F1b's
`_claim_dissonance_slot` counts *attempts* in Redis, never rows, so **the reaper cannot
reset the dissonance budget or re-open the livelock.** [A, docstring + code confirmed D]

Also confirmed clean: nothing joins `observations` to the zettel tables; `reflection_score`
is display-only (`main.py:835` → `App.jsx:841-845`), never aggregated or thresholded; the
dedup guard at `database.py:977-985` is inert for the reapable set — but **becomes live if
`dense_observation` is ever added to `PERSONAAPP_REAP_TYPES`**, because reaping a deduped
row un-suppresses re-insertion of content the guard was permanently blocking.

---

### 24. Smaller findings, none blocking

- **`database.py`'s connect monkeypatch is not reload-safe. [D]** The module body does
  `_original_connect = sqlite3.connect`. On a second execution of that body — `importlib.reload`,
  or a double-import under two names — it captures the *already-patched* `_custom_connect`,
  which then calls itself: `RecursionError` on the next connect, ~1000 frames deep. Hit
  while writing this session's test harness. Not reachable in production (uvicorn's
  `reload=True` spawns a fresh process, so the body runs once per process) but it is a live
  landmine for any future in-process reload, and the fourth process-lifetime assumption
  catalogued on this path. One-line guard: `if not getattr(sqlite3.connect, "_pa_patched", False):`
- **The monkeypatch misses more argument forms than §7.3 says. [A, probe run]** `DB_PATH` is
  backslashed, and the patch is string equality, so `sqlite3.connect(DB_PATH.replace("\\","/"))`,
  a `pathlib.Path`, and every `file:...?mode=ro` URI all silently get the 5s default. The
  forward-slash form appears in this file's own investigation recipes. F2 sidesteps it by
  calling `_original_connect(DB_PATH, timeout=30.0)` explicitly, as `backup_database` does.
- **Reading the live DB with `immutable=1` reports `journal_mode=delete`. [A]** It is WAL —
  header bytes 18/19 are `2/2`. Any diagnostic that reads that PRAGMA through an immutable
  URI will lie to you. Worth knowing given how much of this file rests on read-only probes.
- **The write-lock hold is ~2.5 ms** for the full 171 KB / 122-row snapshot including fsync
  and read-back verification, against `add_observation`'s 30 s timeout. [A] Four orders of
  magnitude of headroom; the design's blocking concern is a non-issue at this scale.
- **`main.py:748-753` `@app.delete("/workspace/delete")` derives its jail root from the path
  being deleted**, so `SafeWorkspace`'s containment check is vacuously true for any absolute
  path — an auth-gated arbitrary-path delete reaching `shutil.rmtree`. [A] Unrelated to F2
  and out of scope here, but it means "nothing can delete my snapshots" is not true, and it
  wants its own note.

---

### Next session — start here (supersedes the Session 3 list)

1. ~~**Restart the app.**~~ — **DONE, and F1a/F1b/F2 are all verified under live traffic
   (§25).** What is left of this item: re-measure the §20 acceptance table after a *full
   day* of real use. Everything recorded so far covers roughly four hours, and the one
   number still unobserved over a long session is where `raw` settles for `Sky/rick` —
   modelled at 9-10, and holding at 9 as of 2026-08-30 00:40.
   State at hand-off: 362 rows, `sqlite_sequence` 5701, all four contexts at
   `daemon_rows=1`, raw 20 / 9 / 13 / 10 for eni / rick / v / SkyTest-rick.
   **Check the daemon is alive before trusting any of it** — see the header, and §26 for
   why it may silently not be.
2. **F4** — allowlist, not denylist (§15, §18). Fix the `database.py:963-965` docstring and
   filter `memory_plugin.py:48` in the same edit. Note `:48` matters more than previously
   stated: it feeds all 20 rows to the LLM, so daemon text is verbatim prompt body for every
   `dense_observation` ever written, gate or no gate.
3. **F6** — `generate_idle_monologue`'s idempotency mark written independently of the write
   fan-out (§14.1). **Promoted in priority:** it is the only unbounded writer left, and F2's
   default reapable set currently exists to compensate for it. Land F6 and reconsider whether
   `internal_reflection` still belongs in `PERSONAAPP_REAP_TYPES`.
4. **The `llm_engine.py:1679` filter**, if criterion #1 is wanted as an invariant rather than
   a reap-time check (§22.2). Small, contained, and the only thing that actually holds it.
5. **F3** — durable registry on `(username, persona, title)`. Still does not throttle (§15).
6. Then §19, as its own build.

**Not in F1–F6 and still larger than all of them:** `analyze_semantic_conflicts` costs 139
blocking LLM calls per cycle for a subsystem with zero rows in its entire history (§15).
F1b's advisory claim now skips that sweep once the budget is spent, which caps it — it does
not fix it.

---

### 25. F2 verified under live traffic. **[D]**

The app was restarted at 21:12 and used normally. Everything below is observed, not simulated.

**The Reflector is alive.** Two user turns, two `dense_observation` rows, 8–9s after each:

```
5679 user_message        21:36:49
5680 dense_observation   21:36:57   <- reflect() fired
5681 internal_reflection 21:43:31   <- idle monologue, ~7min after the turn (idle_threshold 300s)
5682 user_message        21:54:04
5683 dense_observation   21:54:13   <- reflect() fired
5684 internal_reflection 21:59:37
```

This is the capability §14.3/§17 said would stop permanently. It did not, and §22.1 explains
why the framing was wrong in the first place.

**The uncapped writer behaved.** `generate_idle_monologue` fired exactly once per user turn
(5681, 5684) and wrote its mark each time. §14.1's re-arm loop did not trigger. Two reapable
rows in 23 minutes of use, not the ~12/hr the failure path would produce. F6 is still worth
doing — this run is one sample, not a proof of correctness.

**The reaper fired for real at 22:34:30**, on the restart, past its 1h floor:

```
stamp     21:09:02 -> 22:34:30
snapshot  reaped_20260829_223430_450611.jsonl  (1729 B)
rows      356 -> 354
deleted   5678 (entropic_gap) + 5681 (internal_reflection); kept 5684 as newest-1 for Sky/rick
```

Exactly the `keep=1` policy, on live data, with the snapshot landing first. That one was the
`main.py` startup hook — it ran at 22:34:30 and the daemon spawned at 22:34:31, one second
later, so it cannot have been the loop.

**The per-cycle call then proved itself independently at 23:36:37**, an hour after the startup
hook with no restart in between, so only `start_loop` can have fired it:

```
reaped_20260829_233637_637200.jsonl  (5970 B)   stamp -> 2026-08-29 23:36:37.654228
every context left at exactly daemon_rows=1:
  Sky/eni      raw 20/20  injected 0/5
  Sky/rick     raw  9/20  injected 1/5   [DUDUDUDUDUDUDUDUDUDG]
  Sky/v        raw 13/20  injected 0/5
  SkyTest/rick raw 10/20  injected 1/5
```

**Both call sites are now verified in production.** The per-cycle one is the load-bearing
half: the startup hook only fires on restart, and this process is expected to stay up for
days.

It also resolved a live WARN without intervention, which is the first end-to-end proof the
fix *holds* rather than merely lands. At 23:14 `Sky/rick` had drifted to `raw=7`,
`injected=4/5`, with absorption of 6 more daemon rows against ~3 expected before the reaper
was due. The reaper won with margin: `raw 7 -> 9`, `injected 4/5 -> 1/5`.

**Operational note on the F1b cap.** The budget is 8/day, but at one emission per ~340s cycle
it burns in **~45 minutes** of latched-open operation — inside the reaper's 1h floor. Measured
inter-gap spacing was 334s and 360s. That only bites in one order: sustained conversation
drives `raw` to its ~9 equilibrium (absorption ~6), *then* the user leaves with a full 8-row
budget and the latch open. Post-reap absorption is ~10, which beats 8, so the fix holds in
every state observed — but `PERSONAAPP_REAP_INTERVAL_HOURS=0.5` closes the window if it ever
does bite.

**§22.2 demonstrated across a full cycle.** Criterion #1 for `Sky/rick`, measured live:

```
1/5  [UUUUG]  immediately after the 21:09 reap
2/5  [UGURU]  after 5681 landed — degraded on the FIRST new daemon row, within 20 minutes
1/5  [UUUUR]  after the 22:34 reap
```

It oscillates. Retention restores it every reap and cannot hold it between them. Anyone
re-testing criterion #1 must record *when* they measured relative to the last reap, or the
number is meaningless.

**The emit latch — shut for hours, then opened, and F1a/F1b finally ran. [D]**

For the first ~90 minutes `da:Sky:rick:tonic` sat at **0.3077** against `EXPLORE_THRESHOLD`
0.5: zero `entropic_gap` rows, no F1b budget key. The daemon was **armed, not dormant** —
running its full NLI sweep every cycle and emitting nothing. Conversation alone cannot open
it; only `social_reward` from the Reflector was lifting tonic, at `_SOCIAL_TONIC_GAIN` 0.04,
about +0.008 per turn. Turns *without* tool calls contribute literally nothing —
`tool_reward` returns early when `total <= 0`.

Quantifying what §13.1 left qualitative: from baseline, **four back-to-back turns of clean
tool use** reach 0.505, `_TOOL_TONIC_GAIN` 0.15 against a Rescorla-Wagner RPE that shrinks as
expectation catches up (0.375 → 0.431 → 0.473 → 0.505). The live walk matched the prediction
to three decimals — `0.3077 → 0.3827 → 0.4536 → 0.6397`, each step within 0.001 of model.

Once it crossed, **both session-3 fixes ran in production for the first time:**

```
entropic_gap 5691, 5694, 5696 — first gaps ever emitted through the REPAIRED join.
  Every gap row written before tonight came from the broken node_id predicate (S11),
  which saw degree 0 for every node in the database. All of them were false alarms.
inter-gap spacing 334s / 360s — one emission per cycle, confirming S13.2 (the NLI sweep
  sets the pace, not the 60s sleep) and S15's predicted 1-per-cycle limit cycle.
q:daemon:dissonance:entropic_gap:Sky:rick:20695 — reached 28 by 00:40.
  F1b counts ATTEMPTS, so that is 8 admitted and 20 REFUSED. The cap holds under load.
```

**F1b's cap is therefore verified, not merely deployed.** Note the shape of it: 20 refusals
means the daemon spent most of the night *trying* to emit and being told no — which is
exactly the intended behaviour, and also why the §15 note about the 8/day budget burning in
~45 minutes matters. It is a burst limiter, not a rate spreader.

---

### 26. A source edit silently and permanently kills the daemon. **[D]**

Discovered by accident: editing `database.py` at 22:03:16 triggered uvicorn's reloader, and
the daemon never came back. Not a crash — a *decision*.

```
22:00:41  daemon 34276 writes heartbeat, renews lock (_lock_ttl 180s -> expires 22:03:41)
22:03:16  .py edit -> reload -> uvicorn worker killed -> daemon killed with it (§6.3 vector 1)
22:03:23  replacement spawns. SET NX FAILS — the corpse's lock still has 18s left
          zombie check (:800-807): heartbeat age 162s vs _ttl = max(300, interval*5) = 300
          162 < 300  ->  "Healthy instance running. Exiting silently."  ->  sys.exit(0)
```

Both guards vouched for a process that had been dead for seven seconds. `sys.exit(0)` fires
on the first of the two `range(2)` attempts, so the retry never runs, and nothing else ever
tries again. The daemon stays dead until the next reload happens to land in the other window.

**This is not a narrow race.** The lock lives 180s from each renewal against a measured
~300s cycle, so the daemon sits in "lock present, heartbeat fresh" roughly **60% of wall
clock**. A `.py` edit during any of it kills the daemon permanently. This is almost certainly
the "23 hours of dead brainstem" the comment at `:775` is scar tissue from — the same failure
with a different lock implementation.

**It is new.** §6.3 established that the registry resets on holder death. §14.7 predicted the
*opposite* of what happened — "any spawn's `SET NX` succeeds outright; the zombie branch
never even runs" — which holds only in the 40% window. Neither noticed that in the other 60%
the replacement quietly declines to start.

> ⚠️ **CORRECTED IN SESSION 7 (§40): THIS IS NOW FIXED.** Not because the cost changed,
> but because §39's lock fix made the TTLs long enough that this failure would have gone
> from ~33 % of wall clock to ~100 % — a permanent daemon death on *every* source edit.
> The fix is exactly the one this section prescribes below: the lock value carries
> `<pid>:<create_time>:<boot_id>` and a spawn asks the OS whether the holder is alive.
> Verified live at 08:03:52. The paragraph below records the pre-fix decision.

**Owner's call: not fixing it.** No data is lost and a restart is cheap. Recorded because the
failure is *silent* — nothing logs at app level, the app serves normally, and the only symptom
is that nothing is ever written again. It was caught tonight only because a watchdog was
watching the heartbeat.

If it is ever fixed, the minimal correct fix is to make the zombie check test **liveness of
the holder's PID** rather than trust a heartbeat that outlives its process by up to 300s:
store `f"{os.getpid()}:{boot_id}"` as the lock value and force takeover when that pid is gone.
Shortening `_ttl` is the wrong lever — it exists to stop a slow-but-healthy sweep being
murdered mid-cycle.

**Operational rule while this stands, verified against uvicorn's own `FileFilter`:**

```
reload includes = ['*.py']   ONLY
  database.py / stream_worker.py / main.py   -> RELOAD (≈60% chance the daemon dies silently)
  lab_notes/*.md, *.txt, vite-project/**/*.jsx -> ignored, safe to edit
```

So every F4/F6 edit needs the daemon checked afterwards, and notes/frontend edits are free.


---
---

# Session 5 — 2026-08-30, afternoon into evening

Scope: audit of all four lab notes against live state, then **three defects found and
fixed in `stream_worker.py`** — two hardcoded token budgets and an ungated sweep.
Provenance: **[D]** verified directly, **[A]** inferred and later corrected.

The headline is that the contradiction subsystem has **never once produced a verdict in
the entire history of this project**, and the cause is a three-character literal. §27.

Two of this session's own claims were wrong and are recorded in §29 rather than buried.
Both were mine, and both came from measuring an instrument I had not checked.

---

### 27. `call_nli_gate` cannot return anything but NEUTRAL. It never could. **[D]**

`stream_worker.py` hardcodes `max_tokens=10` on the NLI call. `gemini-3-flash-preview` is
a **reasoning model**, and `max_tokens` caps reasoning *plus* content. Measured against
the live graph, same pair, same prompt, only the budget varying:

```
max_tokens=10   content=''            finish='length'  reasoning_tokens=7
max_tokens=64   content='CONTRADI'    finish='length'  reasoning_tokens=57
max_tokens=256  content='CONTRADICT'  finish='stop'    reasoning_tokens=215  completion=4
```

At 10 every token goes to reasoning and content comes back empty. `call_nli_gate` then
runs `if "CONTRADICT" in content` against `""`, falls through, and returns `NEUTRAL`.

Note 64 is *also* broken and looks fine: `"CONTRADICT" in "CONTRADI"` is **False**. Any
fix that raises this to a merely-larger-but-still-small number reproduces the bug while
appearing to address it. 256 is the first value where the model reaches `finish='stop'`.

**This retires the standing explanation for `semantic_contradiction`'s zero rows.** §14.5
and §15 both reason from "a subsystem that has produced zero rows in its entire history"
as though that were evidence about the *data*. It was evidence about the *literal*. The
correct statement is: the gate has been answering `NEUTRAL` unconditionally since the file
was written, and nothing is known about how many contradictions the graph actually holds.

`generate_idle_monologue`'s sibling literal (`max_tokens=512`) is the same class of hazard
and demonstrably clears the reasoning overhead — which is why `internal_reflection` rows
exist and `semantic_contradiction` rows do not. See §30 for what 512 costs anyway.

---

### 28. A daemon started without `.env` silently loses its entire LLM capability. **[D]**

`llm_engine.call_llm` returns a **string** on failure — `"⚠️ Connection Error: No
available API key for the requested provider (google)."` — and never raises.
`call_nli_gate` guarded with `if isinstance(res, dict)`, so that string was dropped on the
floor and the function returned `NEUTRAL`. **No exception, no log line, no network call,
0.4 ms.** A failure to obtain a verdict was indistinguishable from a verdict.

The dependency chain, verified by running the same call twice in one process with the only
difference being `load_dotenv()`:

```
no .env   ->  VERTEX_PROJECT_ID unset  ->  use_vertex False  ->  provider "google"
          ->  key_pool empty, GOOGLE_API_KEY empty  ->  error STRING  ->  1.4 ms  ->  NEUTRAL
with .env ->  use_vertex True  ->  provider "google_vertex"  ->  OAuth via ADC
          ->  real call to aiplatform.googleapis.com  ->  2828 ms  ->  dict
```

The detached daemon from §26 was spawned from a bare shell and sat in the first state for
**15.9 hours**, running full sweeps that were structurally incapable of a result, at 61.4 s
per cycle (measured, 8 intervals, ±0.15 s), logging nothing at any level.

This is the **fourth** silent-degradation-on-missing-dependency on this path: Redis for the
lock, ADC for Vertex, `.env` for providers, and now the `isinstance` coercion that converts
all three into a plausible answer. `generate_idle_monologue` at the equivalent point already
had the check this path lacked (`elif isinstance(res, str) and not res.startswith(...)`).

**Rule this earns: a subsystem that cannot distinguish "no answer" from "an answer" will
eventually report the wrong one, and will never tell you.**

---

### 29. Corrections — both to this session's own claims. **[D]**

**29.1 "The NLI subsystem makes no LLM calls and costs nothing" — WRONG.**
I measured 61.4 s cycles on the orphan, ran a probe that returned an error string in
0.4 ms, and concluded the sweep was structurally dead and §15's cost estimate was fiction.
Both measurements were real; the generalisation was not. **My probe process had not loaded
`.env`** (§28) — I was reproducing the orphan's crippled state and calling it the system's
normal state. With `.env` the calls are real at 2.4–2.8 s each.

**29.2 "§13.2 and §25's 334–360 s cycle time is wrong" — WRONG, they were right.**
Measured on the healthy app-parented daemon: **321.5 s and 321.0 s**. Squarely in the band
the earlier sessions reported. The 61.4 s figure was a corpse, not a counterexample.

The general lesson is the one §21 already records: *verify the instrument before believing
the reading.* An environment difference between the process under test and the process in
production invalidated three conclusions at once.

---

### 30. The monologue is guillotined mid-sentence 83% of the time. **[D]**

`max_tokens=512` against the same reasoning model. Measured across every `Reflection:`
node in `zettel_nodes`, split by length so short structural nodes do not contaminate it:

```
DAEMON monologue (max_tokens=512), prose bodies >=800 chars, n=48
  min 1682   p50 1987   p90 2068   max 2140      458 chars of total spread
  ten longest: 2047 2050 2051 2052 2068 2088 2109 2136 2140 2140   <- within 93 chars
  ends mid-sentence: 83%

APP Reflector (request-supplied parameters), same window, n=81   <- CONTROL GROUP
  min  808   p50 1245   p90 2662   max 3984      3176 chars of spread
  ends mid-sentence: 2%
```

Forty-eight independent generations over weeks, ten longest within 93 characters of each
other. The full-corpus histogram is the confession — **nothing exists between 250 and 1500
chars**: 58 short structural nodes, then a wall at ~2000–2140. 2140 / 512 = 4.18 chars per
token, textbook English prose, i.e. content consumes essentially the whole budget.

The Reflector is the control: same DB, same era, same model family, 41× less truncation,
because it runs on request parameters instead of a literal.

**Why this is not cosmetic.** The fragment is written **permanently** to `zettel_nodes`,
and those nodes are (a) the orphan pool the gap picker scans and (b) the exact text the NLI
sweep Jaccard-compares. The contradiction engine has been reading mutilated input and
answering with a hardcoded shrug.

Checked and **refuted**: newline density is not the cause. Daemon nodes carry p50 10
newlines / 5 blank lines against the Reflector's 0, so the daemon genuinely writes
multi-paragraph prose — but that is ~15 tokens of 512, **3%**. Stripping newlines buys
nothing. The cap is the cause.

---

### 31. What the Docker restart actually did — and the one-command version of it. **[D]**

Owner restarted Docker to escape §26 after a replacement daemon "detected as running and
then silently exited". It worked, and the mechanism is worth having:

```
q-redis: redis:alpine, Mounts=0, RestartCount=0
  -> RDB lives in the container's WRITABLE LAYER, no volume

SURVIVED the restart (long TTL, absolute expiry restored from RDB):
  q:daemon:dissonance:entropic_gap:Sky:rick:20695 = 28   ttl 118270 -> 116556 across 29 min
  daemon:last_monologue:*  (no TTL)      q:provider:health:*  (long TTL)
DIED (180 s TTL, expired inside the downtime window):
  q:daemon:lock            <- the ONLY thing blocking the replacement
```

**So the minimal §26 recovery is `DEL q:daemon:lock`.** Same effect, one second, and it does
not disturb the dissonance budget, the monologue idempotency marks, or provider health.

**And the risk the zero mounts create:** a `docker compose down`, an image update, or any
container *recreation* (as opposed to restart) permanently wipes the F1b budget, the
monologue idempotency marks, and provider health. This is the next layer of the rule
`storage_hardening.md` already states twice — *no durable policy state in process memory*
becomes *no durable policy state in a container writable layer*.

---

### 32. The NLI sweep ran at full cost on a completely idle app. **[D]**

`analyze_semantic_conflicts` was gated only on the dissonance budget, never on `exploring`,
while the gap picker stands down at `:320`. da_neuron open issue #4 flagged this; the cost
was never measured. Measured now, with the app idle from 15:39 onward — no user turns,
tonic at baseline (`da:Sky:rick:tonic` absent), `observations` frozen at 362 rows / seq
5701, `zettel_nodes` unchanged at Sky/rick 332 and SkyTest/rick 98:

```
~5 sweeps/hour x 153 blocking Vertex calls, re-asking the identical 153 questions
about the identical 153 pairs, and writing nothing -- nothing CAN be written, because
resolve_semantic_conflict sits downstream of the same gate that was shut.
```

Also corrected while in here: the sweep is **153 calls per cycle, not 139** (graph growth),
and it covers **2 contexts, not 4** — `run_cycle` takes one active persona per user and
both users currently resolve to `rick`.

---

### 33. APPLIED — three fixes, all verified live. **[D]**

Rollbacks: `%LOCALAPPDATA%\PersonaApp\rollback_20260830_pretokens\` (pre-token) and
`rollback_20260830_pregating\` (pre-gating), each with `SHA256SUMS.txt`.

| line | change | verification |
|---|---|---|
| `:512` | NLI `max_tokens` 10 → **256** | 6 real graph pairs → `{ENTAIL: 2, NEUTRAL: 4}`. Pre-fix: 100% NEUTRAL by construction. |
| `:512` | non-dict **and** empty-content both logged as FAILURE, not verdict (§28) | — |
| `:722` | monologue `max_tokens` 512 → **1000** | covers the Reflector's full observed range (max 3984 chars) |
| new | `_trim_to_sentence()` + trim on `finish_reason == 'length'` | **40/40** truncated nodes repaired, **0/8** clean nodes altered, p50 40 chars discarded (2.0%), max 145 (7.5%) |
| `:398` | sweep stands down on `exploring=False`, mirroring `:320` | **idle cycle 1009 s → 60.0 s**, zero LLM calls |
| `:218` | call site passes `exploring=_exploring` | — |

**Cycle time, every state measured this session:**

```
  61.4 s   orphan, no .env       -- all calls fail in 0.4 ms, silently        (S28)
 321.0 s   healthy, cap 10       -- 153 real calls, all returning NEUTRAL     (S29.2)
1008.8 s   healthy, cap 1000     -- 153 real calls at ~6.5 s, real verdicts   (2 intervals, spread 4.3 s)
  60.0 s   healthy, cap 256, GATED, idle -- zero calls, deliberate stand-down
```

**Why the cap went to 256 and not 1000.** 1000 tripled cycle time to 1008.8 s and bought no
correctness (`finish='stop'` at 219 tokens either way). At that cycle length the daemon sat
past its own 300 s staleness threshold for **70%** of every cycle and held no lock for
**82%** — turning §14.7 vector 2 from "routine occurrence" into the dominant behaviour, and
inverting §26 so that any spawn seizes the lock and aborts a healthy incumbent mid-sweep.
The counterintuitive part: total token burn actually *fell* (~440k → ~406k/hour) because
cycles became rarer. **Cost was never the argument. The TTL mismatch was.**

---

### 34. Smaller findings

- **The workspace jail is vacuous on THREE endpoints, not one. [D]** §24 flagged
  `/workspace/delete`. `main.py:747` `/workspace/save` and `:760` `/workspace/create` carry
  the identical defect: `root = os.path.dirname(os.path.abspath(path))`, so `_resolve`
  compares the path against a root *derived from that path* and the containment check can
  never fail. `SafeWorkspace._resolve` is genuinely careful — it even carries a
  `FIX(prefix-escape)` — and is being handed a jail built around the prisoner. Worse:
  `SafeWorkspace.__init__` runs `os.makedirs(self.root, exist_ok=True)`, so all three
  endpoints **create directories at caller-controlled paths before any validation runs**,
  including on requests that are subsequently rejected.
- **`reflector_watchdog.py:139` hardcodes `age > 900`. [D]** Fine at 321 s (2.8× headroom),
  fires every cycle at 1008.8 s, fine again at 60 s / ~460 s. Deliberately **not** changed —
  re-tuning a constant against a workload that moved twice in one session is the antipattern
  this file keeps warning about. Re-evaluate once an *active* cycle is measured at the 256
  cap; ~460 s is predicted from 2.6 s/call × 153, not measured. Also: it lives in a
  `Temp\claude\...` scratchpad and will not survive a temp clean.
- **The F1b budget window is 86400 s and the slot is `int(time.time() // window)`. [D]**
  `...:20695` rolls at **19:00 local** (UTC−5). At 28 attempts against a cap of 8 the budget
  was spent, so a quiet log before the roll is the cap working, not a fault.
- **`call_nli_gate` does log its exceptions** (contra §14.8's "with no log line"). The
  silence is real but comes from two other places: the non-exception `isinstance` drop
  (§28), and a daemon whose stdout goes nowhere because it was spawned detached.

---

### Next session — start here (supersedes the Session 4 list)

1. **Measure an *active* cycle** (tonic > 0.50, `exploring=True`) at the 256 cap. Everything
   in §33 past the idle row is predicted. This one number decides both the watchdog
   threshold and whether §14.7 still bites under load.
2. **F6** — `generate_idle_monologue`'s idempotency mark written independently of the write
   fan-out (§14.1). Still the last unbounded writer.
3. **The workspace jail** (§34). Highest blast radius of anything open: reaches
   `shutil.rmtree` and can delete `users.db` and the reap snapshots.
4. **Derive `_ttl` and `_lock_ttl` from measured cycle time**, not from `interval` (§14.7,
   confirmed again in §33). Kills the class instead of re-tuning constants.
5. **F4** — allowlist, the lying `add_observation` docstring, `memory_plugin.py:48`, index.
6. Per-pair NLI watermark (da_neuron #4) — the structural version of §33's gate.
7. Then §19, as its own build.

**Mechanical batch, still untouched:** persist the 33-case attribution suite into `tests/`
(the directory exists); `mcp_client.py:83` colon; `database.py:20` monkeypatch reload guard;
`q:daemon:heartbeat` TTL + `main.py:832` staleness check; telemetry `event_type` filter;
`include_embeddings=False`; `try/finally` on the two bare connections; delete the stale
`users.db` stub and the July backup (~15.4 MB).

---

# Session 6 — 2026-08-30 night into 2026-08-31 morning

Single objective: Session 5's next-list item 1, *measure an active cycle at the 256 cap*.
Done, n=2. The predicted number was wrong, and the correction promotes item 4 from cleanup
to the actual fix. Two further findings arrived by accident, one of them from a cleanup of
mine that silently failed. Two claims made this session are retracted in §37.

### 35. The active cycle is 548–577 s, not the predicted ~460 s. **[D]**

§33 closed with every non-idle number predicted rather than measured, from 2.6 s/call × 153.
Measured against the live daemon (PID 31628, parented to `main.py` 11540, untouched — no
`.py` edited, no restart):

```
cycle 2  22:22:29   60.0s   lock_ttl=180   gated idle, control
cycle 3  22:23:29   60.0s   lock_ttl=180   gated idle, control
    [tonic seeded 22:23:42 -> 0.90 both contexts, exploring=True]
cycle 4  22:24:29   60.0s   lock_ttl=180   straddle: sleep only, sweep not yet armed
cycle 5  22:34:06  577.2s   lock_ttl=178   ACTIVE
    [second active cycle, unplanned — see §36]
         23:03:27  548.0s                  ACTIVE
```

**548 s and 577 s**, agreeing to within 5%. The sweep alone is 577.2 − 60 s sleep = **517 s**;
at 153 calls that is **3.38 s/call**, not 2.6. The prediction was low by 25%.

Method: `q:daemon:heartbeat` polled every 2 s. It is written once per cycle at
`stream_worker.py:126`, so consecutive deltas *are* cycle time. The gate was forced by
seeding `da:{user}:rick:tonic` to 0.90 for both active contexts (`run_cycle` takes one
persona per user and both resolve to `rick`, §32) — conversation cannot open the gate fast
enough to schedule a measurement around it.

**§14.7 at the measured length.** `_lock_ttl` = 180 s is what `_renew_lock` SETs, once per
cycle; `_ttl` = 300 s is the boot staleness check. Both derive from `interval`, which is only
the sleep:

```
                       lock key ABSENT    heartbeat past the 300 s staleness check
 321 s (cap 10)            43.9%                    6.5%
 548-577 s (cap 256)       67-69%                  45-48%     <- measured
1008 s (cap 1000)          82.1%                   70.2%
```

**Observed directly, not computed:** at 22:42:42 the daemon was healthy and mid-sweep with
`heartbeat age 516.1 s` and `q:daemon:lock` returning `ttl -2`. That is §14.7 vector 2 caught
in the act — any spawn in that window takes the lock and aborts a healthy incumbent.

**This is the finding that matters.** §33 chose 256 over 1000 *because* 1000's TTL mismatch
was intolerable — 70% stale, 82% lockless — and justified that choice against a predicted
460 s. At the true 548–577 s the 256 cap does **not** restore the cap-10 regime; it lands
nearer the cap-1000 one it was chosen to avoid. The cap was still the right call — cost was
never the argument — but it did not solve the TTL problem, it moved it. **Deriving `_ttl` and
`_lock_ttl` from measured cycle time (Session 5 list item 4) is no longer a cleanup item; it
is the fix this measurement was ordered to justify.**

`reflector_watchdog.py:139`'s hardcoded `age > 900` does not fire at 577 s, but the headroom
is 1.56×, not the comfortable margin 460 s implied. Still not re-tuned — same reasoning as
§34: the workload has moved twice, do not chase it with constants.

> ⚠️ **CORRECTED IN SESSION 7 (§39).** The instruction above — "deriving `_ttl` and
> `_lock_ttl` from measured cycle time … is the fix this measurement was ordered to
> justify" — is **half right**. `_ttl` (the heartbeat staleness threshold) genuinely does
> belong on measured cycle time, and is now derived from it. `_lock_ttl` does **not**: it
> was implemented that way, shipped, and measured failing inside the same session. An
> estimator has no measurement to use on the idle→active transition, so the first active
> sweep still ran under the 180 s floor and sat lockless for 73 % of its 652.8 s. Lock
> presence is now a renewer thread on a fixed timer, which predicts nothing. §39.
>
> Also: the 548–577 s range here is n=2. Session 7 added four more measurements —
> **652.8, 569.2, 585.6, 580.1 s** — so the real range is **548–653 s**, n=6.

### 36. Deleting a `da:*` key does not reset the neuron. It resurrects a stale one. **[D]**

Found by the cleanup failing. After the measurement the seeded keys were `DEL`ed, and the
clearing process read back `0.3000 / exploring=False`. **The daemon kept exploring anyway**,
for another ~30 minutes and four more cycles.

`dopamine_state._load()`:

```python
raw = None
if _RCONN is not None:
    try:    raw = _RCONN.get(k)
    except Exception: pass
if raw is None:
    return _MEM_FALLBACK.get(k)     # <-- absent key falls through to stale in-process copy
```

`_MEM_FALLBACK` is a write-through cache that is **never invalidated**, and an absent key is
indistinguishable from an unreachable Redis. The daemon fell back to its own in-memory copy —
still holding the seeded 0.9 — decayed from there, was re-boosted +0.04 per gap discovery
(`:363`), and **wrote itself back to Redis** at 22:53:19:
`{"v": 0.7303, "ts": 1788148399.6069694}`, ts identical to that cycle's heartbeat.

Two consequences, both exercised live:

1. **The DA neuron cannot be reset from outside its process.** `DEL`, `FLUSHDB`, a Redis
   restart — all silently ignored by a running daemon, then overwritten by it on its next
   save. Redis looks like the source of truth for `da:*` and is not.
2. **A wiped Redis restores stale posture rather than baseline.** The absent-key path was
   presumably written for "Redis down, degrade gracefully"; it also fires for "Redis up, key
   legitimately gone", and those want opposite behaviour.

**Correct intervention**, used to actually stand it down at 23:03:40: `_save` the baseline
value rather than deleting it, so `raw` is non-None and the fallback is never consulted. Note
`_save` sets a TTL of ~3 h, so the key expires on its own and the trapdoor re-arms.

**Fix, NOT applied** (needs a `.py` edit, §26): have `_load` distinguish a live-but-empty
Redis from an unreachable one — consult `_MEM_FALLBACK` only when the connection itself
failed, not when the key is simply absent.

> ✅ **APPLIED AND VERIFIED IN SESSION 7 (§41).** Exactly as prescribed, plus one thing the
> prescription missed: on a live-but-absent key `_load` also **evicts** the stale
> `_MEM_FALLBACK` entry, so the old value cannot resurrect later if Redis subsequently goes
> down. Measured twice: a plain `DEL` now stands the neuron down on the very next cycle
> that reads it (60.0 s gated-idle cycles resumed immediately), against §36's observed
> "~30 minutes and four more cycles" pre-fix. **The `_save`-the-baseline workaround above
> is no longer required.**

### 37. Corrections — both to this session's own claims. **[D]**

**37.1 "The daemon is wedged" was wrong. It was finishing a cycle.**

The PC slept ~23:05 and woke 05:53:47. At 05:55 the heartbeat was 6.9 h stale, CPU was
essentially flat (0.08 s per 40 s), and one HTTPS socket to a Google IP had been open since
wake. I called it wedged and hypothesised the Vertex path enforced no timeout. **All three
observations are equally consistent with a blocking LLM call in a cycle that is completing
normally** — which is what it was. It recovered unaided at 05:59:55, ~6 min after wake, and
returned to 60 s idle cycles. This is the §27 error in miniature: reading an instrument that
cannot separate two states, and reporting the alarming one.

**37.2 `llm_engine.py:90`'s `timeout=60` is a pip install, not an HTTP call.** It was cited
as evidence the LLM path was time-bounded. The real request timeout is `:530`
`OR_SESSION.post(..., timeout=60)`. Do not cite `:90`.

**Still open, and NOT investigated:** whether a sleep landing mid-sweep can ever fail to
recover — whether `:530`'s 60 s is per-socket-op rather than total, and whether `OR_SESSION`
carries urllib3 retries that multiply it. It recovered once. That is one sample and says
nothing about the worst case.

**The one real cost of the intervention.** Forcing `exploring=True` meant the sleep landed
mid-sweep rather than inside a 60 s `time.sleep()`. A gated-idle daemon resumes instantly;
this one needed ~6 min and left 7 h of stale heartbeat, which is what tripped the external
watchdog at 05:55. No data lost, nothing rolled back — but **an intervention that widens the
blast radius of an ordinary laptop sleep should be stood down before the machine is.**

### 38. Smaller findings

- **F2 verified a third time, unprompted, on rows this session created. [D]** The four forced
  `entropic_gap` rows drove `Sky/rick` to `5/5 [GGGGG]`. The reaper fired on its own 1 h floor
  at 23:02:27 with no intervention: `Sky/rick 5/5 -> 1/5 [UUUUG]` (raw 15 -> 19),
  `SkyTest/rick 4/5 -> 1/5` (raw 16 -> 19), every context left at exactly 1 reapable row. The
  external watchdog independently logged WARN then OK across the same interval.
- **The Reflector was never blocked, again. [D]** At its worst the window was 100% poisoned
  (`injected 5/5`) while `raw` stood at 15/20. The gate is `raw < 5`. §22.1's correction
  holds: poisoning and stalling are different failures, and only the first one occurred.
- **`semantic_contradiction` rows: still 0.** These were the first sweeps in the project's
  history where the NLI gate was both open *and* functional (pre-§27 it returned NEUTRAL by
  construction). Two full active cycles ran and wrote nothing. This is **not** yet evidence
  that the graph holds no contradictions — the sweep's own Jaccard pre-filter chooses the
  pairs, and n=2 cycles — but it is the first sample that could ever have said otherwise.
  Per-pair NLI watermarking (da_neuron #4) is the way to turn this into a real answer.
- **F1b never came under pressure. [D]** 2 attempts per context against a cap of 8; 4 rows
  admitted, 0 refused, slot `20696`. Contrast §25's 28-attempts-for-8-admitted night.
- **`labnote` now exists** (`~/.local/bin/labnote.py`, on PATH). Pulls one section out of this
  file instead of reading all ~26k tokens of it: `labnote status`, `next`, `toc`, `show <n>`,
  `grep <re>`. `show` refuses >2500 tokens without `--force`. Use it.

---

### Next session — start here (supersedes the Session 5 list)

1. **Derive `_ttl` and `_lock_ttl` from measured cycle time, not from `interval`.** Promoted
   to the top by §35 — at 548–577 s the daemon is lockless ~68% and stale ~46% of every cycle,
   which makes §26's silent-suicide window and §14.7's mid-sweep abort routine rather than
   occasional. This is the fix the measurement was ordered to justify, and it kills the class
   instead of re-tuning a constant.
2. **§36's `_load` fallback.** Small, self-contained, and it makes every future intervention
   on `da:*` actually work. Do it in the same edit pass as item 1 to pay §26's daemon-death
   coin flip once instead of twice.
3. **F6** — `generate_idle_monologue`'s idempotency mark written independently of the write
   fan-out (§14.1). Still the last unbounded writer.
4. **The workspace jail** (§34). Highest blast radius of anything open: reaches
   `shutil.rmtree` and can delete `users.db` and the reap snapshots.
5. **F4** — allowlist, the lying `add_observation` docstring, `memory_plugin.py:48`, index.
6. Per-pair NLI watermark (da_neuron #4) — now also the only way to make §38's "still zero"
   mean anything.
7. Then §19, as its own build.

**After any `.py` edit, confirm a `stream_worker.py` process still exists** (§26). Minimal
recovery is `DEL q:daemon:lock` (§31).

**Mechanical batch, still untouched:** persist the 33-case attribution suite into `tests/`
(the directory exists); `mcp_client.py:83` colon; `database.py:20` monkeypatch reload guard;
`q:daemon:heartbeat` TTL + `main.py:832` staleness check; telemetry `event_type` filter;
`include_embeddings=False`; `try/finally` on the two bare connections; delete the stale
`users.db` stub and the July backup (~15.4 MB).
---

# Session 7 — 2026-08-31, morning

Objective: Session 6's next-list items 1 and 2 — derive the TTLs from measured cycle time,
and fix `_load`'s fallback — in one edit pass, to pay §26's daemon-death coin flip once
instead of twice. Item 1 as specified turned out to be half wrong, which the measurement
caught inside the same session; the corrected version required §26 to be fixed first, so
that came along too. All four changes are applied and verified live. Three claims made this
session are retracted in §42.

### 39. The lock TTL cannot be predicted. The first active cycle after idle is always unprotected. **[D]**

§35 ordered `_lock_ttl` to be derived from measured cycle time. It was, it shipped, and it
was then measured failing — on the first active sweep it saw.

The estimator: each cycle measures top-of-loop to top-of-loop (the same quantity consecutive
heartbeat deltas measure), keeps `max(elapsed, prior × 0.9)` so a slow cycle widens the TTL
instantly while idle narrows it only 10 % per cycle, and publishes to `q:daemon:cycle_secs`
(24 h TTL) so the next boot inherits the measurement instead of restarting at the floors.
All of that works. It is also beside the point.

Run 1, live, gate forced by seeding `da:{Sky,SkyTest}:rick:tonic` to 0.90 (§35's method):

```
07:38:03  cycle idle      60.0s   lock_ttl=180   published_cycle=60.0
07:41:03  lock key EXPIRES  <- 180s after the cycle top; the sweep is 3 minutes in
   ...    lock ABSENT for 95 consecutive 5s polls
07:48:59  cycle ACTIVE   652.8s   lock_ttl=1629  published_cycle=652.8
```

**~475 s absent out of 652.8 s — the lock was gone for 73 % of the cycle**, worse than the
67–69 % §35 measured with no estimator at all. The reason is structural, not a tuning miss:

- The TTL is set at the **top** of a cycle, from cycles that have **already finished**.
- On the idle→active transition every finished cycle is 60 s, so the TTL is the 180 s floor.
- The very next cycle is 650 s.
- Afterwards the estimate is correct — 1629 s, visible above — but the cycle that needed it
  has already run.
- And it decays back: ~20 idle cycles return it to the floor, so **every** idle→active
  transition re-opens the hole. This app is idle most of the time and the dopamine gate is a
  latch (§13.1). The unprotected case is the common one.

There is no estimator that fixes this. The first long cycle is unmeasured by definition.

**The fix is to stop predicting.** A daemon thread renews the lock every `LOCK_RENEW_SECS`
= 30 s at a fixed `LOCK_TTL_SECS` = 120 s, for as long as the process is alive. The main
loop is blocked inside `run_cycle` for the whole sweep, so renewal cannot live there. Lock
presence stops being a forecast about the next cycle and becomes a fact about a running
thread.

Run 2, same method, eleven minutes later:

```
08:09:03  cycle idle      60.0s
08:18:49  cycle ACTIVE   585.6s
LOCK PRESENCE: 123/123 polls present, 0 absent (0.0%)   min ttl seen: 95s
```

**Zero absences across the whole run**, post-clear phase included, with 95 s of headroom at
its worst. §14.7 vector 1 — "the key is absent ~40 % of wall clock and any spawn's `SET NX`
succeeds outright" — is closed by construction rather than by a bigger constant.

The heartbeat staleness threshold `_ttl` is a different animal and the estimator **is** right
for it: the heartbeat is deliberately written once per cycle, because that is what makes
consecutive deltas measure cycle time, so on a healthy daemon it is legitimately one full
sweep old. `_derive_stale_ttl` keeps `max(300, interval×5)` as a floor and widens to 4×
observed (2611 s at the measured 652.8 s). Prediction is safe there because it is now the
**third** line of defence, behind the pid probe and the lock's own TTL, consulted only for a
holder that cannot be identified at all.

### 40. Holder liveness is a question for the OS. §26 is fixed — and it had to be. **[D]**

§39's lock fix could not ship alone. §26's silent suicide fires when a replacement finds
*lock present + heartbeat fresh*; pre-fix the lock was present ~33 % of a 548 s cycle, so
that was the death window. Holding the lock continuously would have made it **~100 %** — a
permanent, silent daemon death on **every** `.py` edit. The §26 fix is a precondition of the
§39 fix, not a separate cleanup.

Implemented exactly as §26 prescribed. The lock value is now
`"<pid>:<create_time>:<boot_id>"`, and a spawn that finds the lock held asks the OS whether
that pid is still running before it does anything else. Heartbeat age is only consulted for
a legacy bare-boot_id value that carries no pid.

Three details that are not optional:

1. **Never `os.kill(pid, 0)` on Windows.** CPython implements `os.kill` there via
   `TerminateProcess`, so the POSIX "does this pid exist" idiom would kill the process it is
   asking about — it would murder the healthy daemon it was checking on. The probe uses
   `OpenProcess(SYNCHRONIZE|PROCESS_QUERY_LIMITED_INFORMATION)` + `WaitForSingleObject(h, 0)`
   via `ctypes`; `WAIT_TIMEOUT` means running, signalled means exited. `psutil` is not
   installed in this environment and was not added for this.
2. **Create time, not just pid.** Windows recycles pids briskly, and "pid 29220 exists" is
   not "pid 29220 is still the daemon that took this lock". `GetProcessTimes` supplies a
   creation FILETIME that is stored in the lock and compared on takeover.
3. **Unknown reads as ALIVE.** `OpenProcess` failing with anything except
   `ERROR_INVALID_PARAMETER` (87) — access denied, most importantly — means the process
   exists but is not ours to inspect. Evicting a healthy incumbent mid-sweep costs its
   populated `_gap_cooldowns` (§14.7 vector 2), so the bias is toward leaving it alone. Only
   a pid that is *demonstrably* gone gets evicted.

Also fixed: the `range(2)` retry §26 noted "could never run" because `sys.exit(0)` fired on
attempt 0. A holder that still looks alive on the first attempt now costs a 3 s sleep and a
re-probe before the daemon gives up, which covers the reload race where uvicorn spawns the
replacement before the previous worker has finished dying.

**Verified live, unplanned, by the session's own edit.** Applying patch 2 at 08:03:41
triggered a reload that killed daemon 27796. Its lock still had **850 s of TTL left** and its
heartbeat was **16.8 s old** — textbook §26, and under the old code a guaranteed permanent
death. Instead:

```
08:03:41  reload; daemon 27796 dies. lock=27796:1343265503...  ttl 850  hb_age 16.8s
08:03:52  replacement 11800 spawns, probes pid 27796 -> gone, deletes the lock, SET NX wins
08:04:00  lock=11800:134326550325192828:61d2ae80dcfa  ttl 118
```

No `DEL q:daemon:lock`, no restart, no intervention. **This is the first source edit in this
project's history that did not require manual daemon recovery.**

It then did it twice more, unprompted, from the two edits that installed `tests/test_daemon_lock.py`:
`11800 -> 30336` at 08:36:08 and `30336 -> 15384` at 08:37:05. **Three source edits, three
self-recoveries, zero interventions.** The daemon still dies on a reload about as often as it
ever did — that half of §26 is unchanged and was never the problem. What changed is that the
replacement now starts.

§31's one-command recovery is still correct and still worth keeping for a legacy bare-boot_id
lock, which is the one format that still falls through to the heartbeat rule.

### 41. APPLIED — four changes, all verified live. **[D]**

Uncommitted, as everything else here is. `stream_worker.py` 943 → 1239 lines,
`dopamine_state.py` 429 → 441.

| # | Change | File | Verified by |
|---|--------|------|-------------|
| 1 | Lock renewer thread; `LOCK_RENEW_SECS=30`, `LOCK_TTL_SECS=120` | `stream_worker.py` | 123/123 polls present across a 585.6 s sweep (§39) |
| 2 | Lock value `<pid>:<ctime>:<boot_id>`; pid-probe takeover; 3 s re-probe retry | `stream_worker.py` | live takeover at 08:03:52 (§40) |
| 3 | `_derive_stale_ttl` from measured cycle; `_observe_cycle` publishes `q:daemon:cycle_secs` | `stream_worker.py` | 652.8 s → 2611 s; key persisted, ttl 86391 |
| 4 | `_load` distinguishes absent-key from unreachable-Redis, and evicts the stale fallback | `dopamine_state.py` | plain `DEL` stood the neuron down next cycle, twice (§36) |

51-check suite, all passing, now persisted at **`tests/test_daemon_lock.py`**
(standalone, no pytest, matching the rest of that directory: `python tests/test_daemon_lock.py`,
non-zero exit on failure). It covers the renewer thread, the pid probe — including a real
corpse and a forged recycled pid — the staleness estimator, and all four `_load` branches. It
touches only `q:test:*` in Redis, never `q:daemon:*` or `da:*`, so it is safe to run against
the live app.

One check had to be rewritten on install, and the reason is worth keeping. It originally
probed the pid out of `q:daemon:lock` to prove that probing does not kill. Writing the file
into `tests/` triggered a reload, which killed that very daemon, and the check failed —
**correctly**: it was reporting a real corpse. A test in this repo cannot assume the daemon it
saw a moment ago still exists. It now spawns its own child to probe instead.

The `_load` fix went slightly beyond §36's prescription: on a live-but-absent key it also
**evicts** the `_MEM_FALLBACK` entry, not just declines to read it. Without that, the stale
value survives in the cache and resurrects the moment Redis later becomes unreachable.

**Pre-patch originals**, hashed, at `~/.claude/backups/personaapp-s7-pre-patch/`
(`stream_worker.py`, `dopamine_state.py`, this note, plus both monitor logs and a README).
Restoring either `.py` reverts all four fixes at once. Note the revert trap: a reverted build
writes a **bare boot_id** lock, which falls back to the heartbeat rule, so the self-recovery
in §40 stops applying and `DEL q:daemon:lock` (§31) comes back into play.

### 42. Corrections — three of them to this session's own work. **[D]**

1. **`_lock_ttl` from measured cycle time was wrong, and §35 told me to do it.** Shipped in
   patch 1, measured failing (73 % lockless) in run 1, replaced by the renewer thread in
   patch 2 — all within about 25 minutes. §39. The measurement is the only reason this was
   caught rather than shipped as a fix; the unit tests for the estimator all passed, because
   the estimator was not the broken part.
2. **A CRLF regression, mine, caught by diffing rather than by testing.** Patch 1 used
   `pathlib.write_text()`, whose default `newline=None` translates every `\n` to `os.linesep`
   on Windows. `stream_worker.py` was pure LF; a five-anchor patch silently rewrote all 943
   line endings, so the file diffed as a total rewrite (891 removed / 1111 added, against 264
   lines of real change). Patch 2 writes bytes and preserves LF; the file is back to CRLF=0.
   Nothing functional was affected. **`dopamine_state.py` was already CRLF and was left
   alone** — the repo is not uniform, so normalise per file, never wholesale.
3. **Run 1's "DID NOT STAND DOWN" verdict was a bug in my test, not a finding.** The
   predicate counted the cycle that was *already in flight* when the `DEL` landed — it had
   read tonic before the delete and could not possibly have seen it. Post-clear deltas were
   `[569.2, 60.0, 60.0]`: the daemon stood down on the very first cycle that actually re-read
   the key. Run 2 excludes the in-flight cycle and reports PASS on `[60.0, 60.0]`. §36's fix
   works; my assertion did not.

### 43. Smaller findings

- **The active cycle is 548–653 s, n=6.** §35's 548/577 (n=2) plus this session's 652.8,
  569.2, 585.6, 580.1. Mean ≈ 586 s. The 652.8 s outlier is 13 % above §35's high mark, so
  the workload is at least as heavy as §35 thought and possibly heavier. `reflector_watchdog.py:139`'s
  hardcoded `age > 900` now has 1.38× headroom against the worst measurement, not §35's 1.56×.
  Still not re-tuned — same reasoning as §34 and §35.
- **`q:daemon:cycle_secs` is a new key.** 24 h TTL, written once per cycle, inherited at boot.
  It survives restarts, which is the point — a fresh daemon should not have to re-learn that
  its sweeps take ten minutes. It is also a free cycle-time telemetry feed for anything that
  wants one; `main.py:832`'s staleness check is the obvious first consumer when the mechanical
  batch reaches it.
- **The daemon ran 30 minutes across two forced sweeps with no restart** (pid 11800 from
  08:03:52), app serving HTTP 200 throughout. It was then cycled twice more by the test-suite
  installs and self-recovered both times (§40); the live daemon at hand-off is **15384**.
  `da:*` is empty, `q:test:*` is empty, and tonic is back at the 0.30 baseline — the app is in
  the same posture it was found in.
- **`semantic_contradiction` rows: still 0.** Two more full active sweeps with the NLI gate
  open and functional, nothing written. That is now n=4 (§38's two plus these two). Still not
  evidence about the graph — the Jaccard pre-filter chooses the pairs — and still waiting on
  per-pair NLI watermarking to mean anything.
- **The three `- Copy` / `garage` / `dist_installer` forks were not touched.** Neither was
  `main.py.bak`. Same standing rule.
- **SOAK: 8.5 hours, one process, zero restarts. [D]** Daemon `15384` was checked again at
  17:09 having come up at 08:37:05 — **8 h 32 m of continuous uptime** across a full working
  day, holding `q:daemon:lock` the whole time (ttl 110 of 120, heartbeat 29.5 s old), app
  still serving HTTP 200. `q:daemon:cycle_secs` had decayed to the 60.0 s idle floor, which
  is the estimator behaving correctly on an idle app. `tests/test_daemon_lock.py` re-run
  against that live daemon: ALL PASS. The renewer thread does not leak, stall, or lose the
  lock over a long idle run — which is the failure mode a 20-minute test could not have
  found.

---

### Next session — start here (supersedes the Session 6 list)

1. **F6** — `generate_idle_monologue`'s idempotency mark written independently of the write
   fan-out (§14.1). Now the last unbounded writer, and the highest-value item left.
2. **The workspace jail** (§34). Highest blast radius of anything open: reaches
   `shutil.rmtree` and can delete `users.db` and the reap snapshots.
3. **F4** — allowlist, the lying `add_observation` docstring, `memory_plugin.py:48`, index.
4. Per-pair NLI watermark (da_neuron #4) — still the only way to make §43's "still zero"
   mean anything, now at n=4.
5. Then §19, as its own build.

**Done this session, not carried forward:** the 51-check suite is persisted at
`tests/test_daemon_lock.py` (§41). The 33-case attribution suite is still unpersisted and
stays in the mechanical batch below.

**The `.py`-edit rule is relaxed but not retired** (§40). A source edit still kills the
daemon roughly as often as before — that part is unchanged — but the replacement now
recovers itself. Confirm a `stream_worker.py` process exists afterwards anyway; if one is
missing, check whether the lock is a legacy bare-boot_id value before reaching for
`DEL q:daemon:lock` (§31), because only that format still falls through to the heartbeat rule.

**Mechanical batch, still untouched:** persist the 33-case attribution suite into `tests/`
(the directory exists); `mcp_client.py:83` colon; `database.py:20` monkeypatch reload guard;
`q:daemon:heartbeat` TTL + `main.py:832` staleness check; telemetry `event_type` filter;
`include_embeddings=False`; `try/finally` on the two bare connections; delete the stale
`users.db` stub and the July backup (~15.4 MB).

---

## Session 8 — 2026-08-31, evening

F6 only. One fix, applied and verified; three corrections, two of them to §14.1;
and one thing the next session has to re-check the moment an API key exists.

### 44. The monologue mark: 82% of the Reflection corpus is redundant fires — and §25 watched the one persona where the guard works **[D]**

§14.1 called this out from a 37-row sample and F6 has been carried forward for
four sessions. Measured against the live DB now, reconstructing the daemon's own
idempotency triple for every node it ever wrote:

```
Reflection: zettel_nodes                        106
distinct (user, persona, last_user_ts) triples   19
triples that fired more than once                 7
redundant fires                                  87  = 82% of the corpus
```

Reconstruction is the daemon's own query — the newest `conversations` row with
`role='user'` for that (username, persona) strictly before the node's
`created_at`, which is what `stream_worker.py:400-413` reads at the top of each
cycle. It is not a proxy.

**The refires are not spread evenly, and the shape names the mechanism.**
Inter-node gaps, per persona:

| persona | nodes | <90 s | 90 s–10 min | 10 min–2 h | >2 h |
|---|---|---|---|---|---|
| `Sky/v`    | 45 | 13 | 27 | 0  | 4  |
| `Sky/eni`  | 34 | 10 | 13 | 3  | 7  |
| `Sky/rick` | 23 | 0  | 1  | 10 | 11 |

The daemon's idle cycle is 60 s. Forty of `Sky/v`'s 44 gaps are under ten
minutes and thirteen are under ninety seconds — that is one refire per cycle,
not one per session or one per restart. Nothing else in this system operates on
a 60 s cadence, and the Redis mark has **no TTL** (verified: `ttl = -1` on all
six live `daemon:last_monologue:*` keys), so expiry cannot explain it.

**Why `v` and `eni` and not `rick`.** `Sky/v`'s earliest `conversations` row with
`role='user'` is `2026-08-24 23:50:32` — *after* its last monologue at
`2026-08-22 14:26:07`. All 45 of its fires happened while that persona had zero
user rows, so `last_user_time_str` was the `"2000-01-01 00:00:00"` fallback at
`:412` on every single cycle. A frozen triple turns the guard into the only thing
standing between the daemon and an unbounded write loop, and the guard leaks.
`Sky/rick` has real traffic, so its triple moves on its own and the leak is
mostly hidden — which is exactly the persona §25 was watching when it recorded
"the uncapped writer behaved… one sample, not a proof of correctness." It was
right to hedge. The sample was the best case.

`rick` still shows four repeated triples (6x, 3x, 2x, 2x) at multi-hour spacing.
Those are ambiguous between a lost mark and a Redis flush, and I am not going to
claim either.

**What actually raises — and it is not what §14.1 says.** Walking the fan-out
against the code on disk rather than the function names:

| step | can it propagate? |
|---|---|
| `add_zettel_entry` (`database.py:1428`) | no — `try/except`, returns `False` |
| `shared_model.encode` | yes, but 106/106 nodes exist, so it never did |
| `add_zettel_node` (`:1502`) | no — swallows |
| `bulk_append_to_zettel_cache` (`zettel_engine.py:136`) | **yes** — `np.vstack` at `:163` raises on a shape mismatch against the cached matrix, and nothing catches it |
| `add_zettel_link` (`:1532`) | no — swallows |
| `add_observation` (`:1358`) | no — swallows, `print`s, returns `False` |

Every `db_manager` writer eats its own exceptions. The cache append is the only
statement between the node insert and the mark that can reach the outer handler,
and when it does it takes the link, the observation *and* both durability layers
with it. `zettel_entries` = `zettel_nodes` = 106 confirms the fan-out always gets
at least as far as the node.

Each redundant fire costs an uncounted LLM call, three permanent `zettel_nodes`
rows and one more gap-eligible orphan in the pool this same daemon then scans.

### 45. APPLIED — F6, and the fan-out isolation it needs to be worth anything **[D]**

`stream_worker.py`, three localized hunks, LF preserved (1239 → 1284 bare LF, 0
CRLF — checked, per the rule that bit Session 7):

1. **`_mark_monologue_processed(username, persona, last_user_time_str)`** — new
   method, both durability layers in one place. Redis failures are now logged at
   warning instead of `pass`, because the in-process mark is the fallback and
   silently losing the mirror is how this stayed invisible.
2. **The mark moved into a `finally`** on the outer `try` of
   `generate_idle_monologue`. Success, an empty completion and an exception all
   arm the guard exactly once. Losing one monologue to a transient LLM failure is
   cheap and self-corrects the moment the user speaks (a new message changes the
   triple). Re-writing the corpus every 70 s is neither.
3. **`bulk_append_to_zettel_cache` wrapped in its own `try/except`**, logged. The
   node is already committed at that point; a cache-shape error must not cost the
   link and the observation.

**Verified.** Ten new checks appended to `tests/test_daemon_lock.py` as section
`[6]` — 61 checks total, `ALL PASS`. Four are AST assertions against
`stream_worker.py` itself (one top-level `try`; the mark is in `finalbody`; no
`last_monologue_time` assignment survives in the `try` body; the cache append is
guarded) so a future refactor that slides the mark back inside fails the suite
rather than quietly regressing. The behavioural checks fake `redis_client`'s
`is_active`/`set_val` and restore them in a `finally`, so the suite still touches
no live `daemon:*` key.

The in-process and Redis layers were also exercised once against real Redis
before the suite was written; both probe keys were deleted afterwards
(`daemon:last_monologue:F6Probe:*`, confirmed `None`).

**Daemon survived both edits unaided.** `stream_worker.py` → replacement pid
34304 at 17:24:10 with the lock value's pid prefix matching; `tests/*.py` →
replacement pid 31920 at 17:27:09, lock ttl 109 s, heartbeat 41 s old. No
`DEL q:daemon:lock` needed either time. §40 now stands at five unaided
recoveries. **New:** editing `tests/*.py` also triggers the reload — the free
list is `lab_notes/*.md` and `*.jsx`, and `tests/` is not on it.

### 46. Corrections **[D]**

**§14.1's second paragraph no longer measures anything, and its mechanism was
wrong even when it did.** It reads "103 `Reflection:` zettel_nodes exist against
only 37 observations — 66 nodes (64%) whose path died after `add_zettel_node` and
before `add_observation`."

- The ratio is now void: `internal_reflection` is in `_DEFAULT_REAP_TYPES`
  (`database.py:183`) **by design**, so F2 reaps it. Live count is 106 nodes
  against **1** observation. That comparison can never be evidence about the
  fan-out again.
- The mechanism was wrong regardless. `add_observation` cannot raise — it wraps
  everything and returns `False` (`database.py:1393`). The path did not "die"
  there. Of the 66, at most 14 are explained by the old all-types dedup guard
  (106 nodes carry only 92 distinct bodies; the guard returned `True` and wrote
  nothing). The rest were `add_observation` failing silently, which is a
  different bug from the one §14.1 described.

**§25's "the uncapped writer behaved" holds but is not evidence of correctness.**
It observed `Sky/rick`, the only persona of the three whose triple moves on its
own. §44 has the split.

**My own first reading of §44's data was wrong.** I read `Sky/v`'s 45 fires as
"no user rows exist for this persona" and nearly recorded it as a join bug in my
own query. The rows exist; they are all *later* than every one of that persona's
monologues. The reconstruction was correct and the data was strange.

### 47. Smaller findings

**The monologue body distribution is bimodal and §30 only measured half of it.**
Across all 106 Reflection nodes: 48 bodies ≥ 1000 chars (1682–2140), 58 bodies
< 200 chars (56–97), and **nothing in between**. §30's "guillotined mid-sentence
83% of the time" was measured over the 48 prose bodies — the same 48 — so it
never accounted for the 58 stubs, which are 55% of the corpus and are cut
**mid-word**, not mid-sentence: `'...eyes tracking a spider crawling across a
blueprint for'`. Sixty to a hundred characters is fifteen to twenty-five tokens.
That is the §27 signature — a reasoning model whose thinking shares the
`max_tokens` budget — not a prose-length distribution, and `_trim_to_sentence`
cannot help because it only runs on `finish_reason == "length"` and there is no
sentence boundary in a twenty-token stub. One node ends literally with
`</thinking>`.

I could **not** test whether §30's 512 → 1000 raise helps or hurts here, because:

**The API key pool is empty, and the monologue writer has been inert since
2026-08-29 23:13.** `q:pool:key:*` returns zero keys, `.env` carries no provider
keys, and replaying the daemon's own call — same model expression, same
arguments — returns the string `"⚠️ Connection Error: No available API key for
the requested provider (google)"`. That is not a dict, so `monologue_text` is
`""`, nothing is written, and (now) the mark is still armed. Zero Reflection
nodes have been written since the §30 fix landed, so **every number in this
section describes the 512-token era.**

> **Next session, first thing after a key lands:** watch the next monologue body.
> If it comes back at 60–100 chars, `max_tokens` is being eaten by reasoning and
> the fix is the §27 one (raise it, and read `usage.completion_tokens_details`),
> not another prose-length argument. If it comes back near 4000, §30 worked.

**Do not create a scratch `.py` anywhere in the repo tree.** Writing a throwaway
probe script to the repo root tripped the uvicorn reload and killed the daemon
just as reliably as editing a tracked file. Scratch scripts belong outside the
tree and should `sys.path.insert` back in. Importing the app package from such a
script also boots the plugin system and spawns the MCP servers, which is loud but
harmless.

**Left alone deliberately:** `generate_idle_monologue` still has a now-unused
function-local `import redis_client` at `:830`. Removing it is one more reload for
zero behaviour, so it can ride along with the next edit to that function.

### 48. Why the key pool was abandoned: one 403 destroyed a key permanently, and there was no way back **[D]**

Not from the backlog — the user surfaced it. The pool exists to spread work across
several providers' free tiers and swap when one runs dry. It was abandoned because
keys kept coming up dead when they were not, and the only repair was manual.

The pool is empty right now (`q:pool:key:*` and `q:pool:proxy:*` both zero), so all
of this is read off the code rather than off live data. `redis_pool.py` was
**unmodified since its last commit**, so none of it is recent damage.

**The defect, four parts:**

1. **One strike, permanent.** `llm_engine.py:594` and `:843`, both auth branches:

   ```python
   elif response.status_code in [401, 403]:
       if is_pooled:
           key_pool.release_key(provider, key_id, "BURNED")
   ```

   No cooldown, no strike count, no body inspection.

2. **`BURNED` was terminal.** `checkout_key` (`redis_pool.py:73-76`) revived
   `COOLDOWN` only. Nothing anywhere in the tree ever set a burned key back to
   `HEALTHY` — grepped. The HTTP API had add / delete / list (`main.py:886`, `:897`)
   and **no reset**. Delete-and-re-add was the only recovery path that existed.

3. **A 403 is usually not about the credential.** API-not-enabled on the project,
   a referrer/IP restriction on the key, a region block, a model the project cannot
   reach, and — the one that matters for this app — an OpenRouter moderation refusal
   on the prompt. This app sends uncensored persona prompts. A flagged prompt
   permanently destroyed a valid key.

4. **The proxy was never blamed.** On 429 the code cools the proxy
   (`llm_engine.py:826`). On 401/403 it did not touch it. A region-blocked or
   flagged exit IP returns 403 from *every* provider, so the pool would burn keys
   one after another while the actual culprit — the tunnel — stayed `HEALTHY`.

**And the blast radius was three keys per request, not one.** `max_retries = 3`
(`llm_engine.py:385`) and each attempt calls `checkout_key`, which hands back the
next `HEALTHY` key. One bad call could burn three.

Keys had no `failures` counter at all. Proxies did (`release_proxy`, `:222-225`) —
the pattern existed in the same file and was never applied to keys.

### 49. APPLIED — the burn is now a hypothesis with an expiry **[D]**

Five files, ten hunks, each file's own line endings preserved (`redis_pool.py`,
`llm_engine.py`, `App.jsx`, `api.js` are LF; `main.py` is CRLF — checked
individually, not assumed).

**`redis_pool.py`** — new `classify_auth_failure(status_code, body)`, pure and
therefore testable without Redis. It returns:

| verdict | when | what happens |
|---|---|---|
| `BURN` | the body matches `HARD_KEY_ERRORS` — the provider named the credential (`api key not valid`, `invalid_api_key`, `invalid x-api-key`, `API_KEY_INVALID`, …) | burned, with `burned_at` recorded |
| `STRIKE` | an unexplained 401 | 15 min cooldown; burns only on the 3rd **consecutive** strike |
| `COOL` | a 403 that names anything else, or any unexpected code | 15 min cooldown, never a burn |

Plus: `note_auth_failure()` as the single entry point; `unburn_key()`; a success
(`release_key(..., "HEALTHY")`) now clears `failures`, so only *consecutive*
failures count; `checkout_key` re-probes a burned key after `BURN_RETRY_SECS`
(24 h); and `get_pool_status` reports `failures`, `burned_at` and `last_error` so
the UI can say *why* a key is down.

**Keys burned by the old rule come back on their own.** A pre-existing `BURNED`
key has no `burned_at`, reads 0, and revives on the first checkout after this
change, with a log line saying so. That is intended: they were burned by a
one-strike rule and were probably never bad.

**`llm_engine.py`** — both auth branches route through `note_auth_failure` instead
of burning; both now also cool the proxy for 300 s; a new `_auth_burns` counter
caps one request at burning a single key (further hard verdicts in the same call
downgrade to a cooldown); and `DummyPool` — the fallback used when `redis_pool`
fails to import — gained the new methods, or an import failure would have turned
into an `AttributeError` at the worst possible moment.

**`main.py`** — `POST /settings/{username}/telemetry/keys/{provider}/{key_id}/reset`.
Verified live against the running app: it is in `/openapi.json`.

**`api.js` / `App.jsx`** — `resetPoolKey`, `handleResetKey`, and a restore button
that appears on any key that is not `HEALTHY`. The status chip now shows the strike
count and carries `last_error` as its tooltip. Both files parse clean under
`@babel/parser` with the JSX plugin.

**Verified.** New `tests/test_key_pool.py` — standalone, no pytest, 40 checks,
`ALL PASS`. It runs entirely against an in-memory `FakeRedis`, so it never touches
a real `q:pool:*` key. Section [1] pins the whole classification table case by case
(region block, moderation refusal, API-not-enabled, referrer restriction — each with
the reason it exists); [12] is a source check that the two call sites cannot regress
to an unconditional burn and that `DummyPool` still answers.

Also exercised end to end against **real** Redis on a throwaway provider name
(`burnfix_probe`): add → 403 leaves it `COOLDOWN` → 3×401 burns → checkout refuses
→ restore → checkout succeeds → an expired burn re-probes to `HEALTHY`. Probe key
deleted afterwards; real pool still at zero keys.

App and daemon both survived: uvicorn reloaded, app answered `HTTP 200`, daemon
replaced itself unaided at pid 17624. Six unaided recoveries.

**~~What is deliberately NOT fixed here.~~ FIXED IN §50 in the same session.**
`checkout_key` returned the *first* `HEALTHY` key from `redis.keys(...)` — arbitrary
order, no round-robin. This paragraph called it "a behaviour change rather than a bug
fix… not what was breaking things", which was wrong on the second count: with no
rotation the busiest key is also the one that collects every failure, so it made
§48's one-strike burn far more destructive than it looked. §50.

### 50. APPLIED — `checkout_key` never rotated; one key absorbed everything **[D]**

Raised by the user immediately after §49, and it is the other half of why the pool
was abandoned. §49's closing paragraph called this a behaviour change rather than a
bug and left it alone. That framing was too generous — with no rotation, the pool
is not a pool, it is one key with spares that never get used.

**What it did.** `checkout_key` walked `redis.keys(f"q:pool:key:{provider}:*")` and
returned the **first** `HEALTHY` entry it found. Same key, every call, forever. The
other keys were only ever reached once the first one went `COOLDOWN` or `BURNED`.

That compounds with §48 in a nasty way: the single most-used key is the one that
collects every rate limit and every auth failure, so under the old one-strike rule
the key doing all the work was also the one most likely to be destroyed — and only
then would the pool "discover" key two. Free-tier spreading, which is the entire
reason this subsystem exists, never happened at all.

The proxy half of the same file has done round-robin since it was written
(`checkout_proxy`, `q:pool:proxy_index`). Keys never did.

**Applied**, one hunk in `redis_pool.py`:

- `checkout_key` now walks a **sorted** candidate list from a per-provider cursor
  (`q:pool:key_cursor:{provider}`), takes the first healthy entry from that
  position, and advances the cursor past it — so consecutive requests get
  `k1, k2, k3, k1, …` and an unhealthy key is skipped without disturbing the cycle.
- The per-key revival logic (cooldown expiry, burn re-probe from §49) moved into
  `_effective_status`, so the selection loop stays readable.
- `float(fields.get("cooldown_until", 0))` became `_as_float(...)`. A malformed
  value used to raise `ValueError` and take the entire checkout down — every
  provider, not just that key.

Two deliberate differences from `checkout_proxy`, which this does **not** copy
verbatim:

1. The candidate list is **sorted**. `checkout_proxy` indexes into whatever order
   `redis.keys()` returned, which Redis does not define.
2. The cursor indexes **all** keys for the provider, not just the healthy ones.
   `checkout_proxy` indexes into a filtered `healthy_proxies` list whose length
   changes as proxies cool, so its cursor silently reshuffles everyone's position
   whenever the healthy set changes. That is a real (if minor) defect in
   `checkout_proxy` — noted, not fixed, because nothing is currently proxied.

**Verified.** `tests/test_key_pool.py` grew sections [13]-[16]; 51 checks total,
`ALL PASS`. The interesting ones: a cooling key is skipped while the rest keep
rotating; the cycle is identical when the fake backend enumerates keys in reverse
(the property `checkout_proxy` lacks); and the cursor survives being read by a
fresh `RedisKeyPool`, because it lives in Redis rather than in the process.

Also run end to end against **real** Redis on a throwaway provider
(`rotation_probe`), three keys:

```
six checkouts    alpha bravo charlie alpha bravo charlie
bravo cooling    alpha charlie alpha charlie
bravo restored   alpha bravo charlie alpha bravo charlie
cursor           b'3'   (in redis, per provider)
```

Probe keys deleted; the real pool is still empty.

> ⚠️ **One test-harness bug worth recording, because it cost a cycle.** The
> `FakeRedis` in `tests/test_key_pool.py` used `self.store = store or {}`, which
> silently un-shares an **empty** dict — exactly what the cursor-persistence checks
> pass in. Three checks failed against correct production code. It is
> `{} if store is None else store` now. `x or default` is wrong for any container
> whose empty value is legitimate.

### 51. The workspace reach (decided, not fixed) and the monologue stub floor **[D]**

Two items from the top of the list. One turned out not to be a defect at all —
the reach is the product — and the record of *why* is more useful than the patch
was. The other shipped.

#### 51.1 The workspace reach is deliberate — applied, then REVERTED the same session

**Outcome: no code change. `main.py` is back to what it was.** This subsection
originally reported a fix. The fix was wrong about what the code is for, and this
is the record of that, because the next session will otherwise find the same shape
and "fix" it again.

**What is true.** `/workspace/save` (`:747`), `/workspace/create` (`:761`) and
`/workspace/delete` (`:770`) each build their `SafeWorkspace` root out of the
requested path:

```python
root = os.path.dirname(os.path.abspath(<the caller's own path>))
ws = SafeWorkspace(root)
```

so `_resolve` compares a path against a root derived from that path, the
containment check can never fail, and the `except SecurityViolation` branch on all
three endpoints is unreachable. `delete_item` reaches `shutil.rmtree`. §24 and §34
recorded this accurately.

**What was wrong was calling it a defect.** All three were changed to `_APP_ROOT`,
tested, and reverted within the hour on the author's direction:

> *"We don't want to narrow behavior. It should be just as functional as Claude
> Code, not less."*

That is the correct call and it settles the question. This workspace is a
general-purpose file manager for a single-user local application, held to the same
reach as any agent CLI pointed at a directory. Jailing writes to the application
directory made the tool less capable than the thing it exists to be, in exchange
for a boundary that protects nothing: an attacker who can call these endpoints has
already authenticated as the user, on the user's machine, where they can open a
terminal. The read endpoints say so in their own docstrings — *"Root jail is the
requested path itself"*, *"the user can browse everything inside it"* — and they
were never in question.

**So the standing decisions are:**

1. The reach is intended. Do not narrow `save`/`create`/`delete`, and do not
   narrow `tree`/`file`. If a future session wants a jail, it belongs behind an
   explicit user-chosen root — a working directory the operator picks — not a
   root derived per request, and not a constant.
2. **Stop calling it a jail.** `SafeWorkspace` is not a security boundary here and
   the note should not have described it as "the highest blast radius of anything
   open" for four sessions. It is path plumbing with a containment check that is
   only meaningful once someone chooses a root.
3. `SafeWorkspace._resolve` itself is genuinely careful and **is** worth
   protecting. Given a real root it refuses traversal, deep traversal,
   absolute-outside and the sibling-prefix case its own `FIX(prefix-escape)` was
   written for. That contract is now covered.

**Two consequences that are real and are being kept, not fixed:**

- The `except SecurityViolation` handlers on those three endpoints are unreachable
  as written. Harmless, but they read as protection that is not there.
- `SafeWorkspace.__init__` runs `os.makedirs(self.root, exist_ok=True)`, so with a
  per-request root a `save`/`create`/`delete` for a path that does not exist
  creates its parent chain first, even when the request then fails. A delete
  request that makes a directory is silly, and it is a one-line guard whenever
  anyone cares. It is not a reason to narrow anything.

**What survives.** `tests/test_workspace_paths.py` — standalone, no pytest, 22
checks, `ALL PASS`. Section [1] pins the `_resolve` contract against an explicitly
chosen temp root (the part that is actually a security property). Section [3] is a
**decision lock**: it asserts the three endpoints *still* derive their root from
the request, and fails with a message pointing here if someone narrows them again.
Section [4] documents the `makedirs` behaviour in a temp directory it removes
itself. The file was named `test_workspace_jail.py` for about twenty minutes; the
name was part of the mistake.

#### 51.2 A truncated monologue is no longer written

Per §47's box, minus the part that needed an API key.

- **`max_tokens` 1000 → 4000.** §30 raised it 512 → 1000 on a prose-length
  argument measured over the 48 long bodies, which never accounted for the 58
  stubs. This model shares the budget between reasoning and content (§27, the same
  defect at the NLI gate), and at 1000 the content came back at ~20 tokens. 4000
  leaves room for both. **This number is UNVERIFIED** — the key pool was empty, so
  the call could not be replayed. It is a hypothesis with a comment saying so.
- **`MIN_MONOLOGUE_CHARS = 200`, and a body under it is discarded.** This is the
  part that does not depend on the token question being right. The floor sits 8×
  below the smallest real body (1682) and 2× above the largest stub (97), between
  two measured populations with nothing in between — not tuned to either edge. A
  rejected body is logged at ERROR with its length, `finish_reason`, the provider's
  `usage`, and the text itself.
- **`usage` is captured and logged** on both the reject and the write path, so the
  next session can confirm or kill the reasoning-budget hypothesis with numbers
  instead of inference.

Dropping a stub costs at most one monologue for that triple, and **cannot** become
a refire loop, because §45 put the idempotency mark in a `finally` — it is armed
whether or not anything was written. The two fixes only compose safely in that
order; doing this one before F6 would have produced exactly the 70 s write storm
§44 measured.

**Verified.** `tests/test_daemon_lock.py` gained section [7] (8 checks; 73 total),
`ALL PASS`. It asserts the floor separates the two measured populations, that
exactly one guard exists, that it discards rather than writes, that it runs
**before** the first durable write, that `max_tokens=1000` is gone, and that
`usage` reaches the rejection log.

All three suites green, counted from the output rather than from arithmetic:
`test_daemon_lock` 73, `test_key_pool` 56, `test_workspace_jail` 22. App reloaded
to `HTTP 200` with all eight `/workspace/*` routes registered; daemon replaced
itself unaided at pid 30724.

#### 51.3 Corrections

**The workspace fix was reverted (§51.1)** and §34's framing of it as "the highest
blast radius of anything open" is withdrawn. It was ranked as the top open item for
four sessions on the strength of a security reading nobody had checked against what
the feature is for. The finding was right; the priority was not.

**Every check count I reported this session was wrong.** Not the code, the
bookkeeping. §45, §49 and §50 each reported a suite size I
arrived at by adding the number of `check(...)` **call sites** I had written to a
remembered baseline. Several sections iterate a table, so one call site emits many
checks. Measured by counting `PASS`/`FAIL` lines in the actual output:

| claimed | where | actual |
|---|---|---|
| "61 checks total" | §45 | **65** at that point (73 now, with §51.2's section [7]) |
| "40 checks" | §49 | **46** |
| "51 checks total" | §50 | **56** |

The direction is consistent and unflattering in the honest way: I undercounted
every time, because a `for` loop over a case table reports more than it looks
like. The suites always passed and no result changes; the numbers in those three
sections do. This is the same failure this file keeps recording — a figure
asserted from reasoning rather than read off the run.

### Next session — start here (supersedes the Session 7 list) — SUPERSEDED BY THE SESSION 9 LIST

1. **Confirm the monologue token hypothesis** (§51.2) as soon as a key is in the
   pool. The floor now stops the corpus damage either way, but `max_tokens=4000`
   is a guess: watch the first `usage=` in a `[CONSCIOUSNESS_DAEMON] Writing
   monologue` line and see where the tokens actually went. If bodies still come
   back under 200 chars the daemon will now say so at ERROR, loudly, every time.
2. **F4** — allowlist, the lying `add_observation` docstring, `memory_plugin.py:48`,
   index. Note §46 raises a second `add_observation` problem: it swallows and
   returns `False`, and no caller checks the return.
3. Per-pair NLI watermark (da_neuron #4) — still the only way to make §43's
   "still zero" mean anything, now at n=4.
4. Then §19, as its own build.
5. **`checkout_proxy`'s cursor** (§50, end) — it indexes into the *filtered*
   healthy list, so its rotation reshuffles whenever a proxy cools. Same fix as
   §50. Low priority: nothing is currently proxied.

**F6 is done** (§45) and **the monologue can no longer poison its own corpus**
(§51.2). **The workspace reach is settled and off this list for good** (§51.1) —
it is intended behaviour, not an open item. There is no unbounded writer left that this
note has identified. **The key pool is repaired** (§48/§49/§50); the next time
keys are added, watch for a `COOLDOWN` where the old code would have said `BURNED`,
and check that consecutive requests actually cycle through the key ids.

**Mechanical batch, still untouched:** persist the 33-case attribution suite into
`tests/` (the directory exists); `mcp_client.py:83` colon; `database.py:20`
monkeypatch reload guard; `q:daemon:heartbeat` TTL + `main.py:832` staleness
check; telemetry `event_type` filter; `include_embeddings=False`; `try/finally`
on the two bare connections; delete the stale `users.db` stub and the July backup
(~15.4 MB).

---

## Session 9 — 2026-09-01, evening

Started from the Session 8 list and threw its first item away inside the hour.
Three builds, one retraction, one confirmation, and the daemon replaced itself
unaided on every one of four source edits (pids 28476 → 32968 → 33452 → 27404,
no `DEL`). §40 holds.

### 52. §47 retracted: the monologue writer is gated on user messages, not on keys **[D]**

The idle coordinator (`stream_worker.py`, `run_cycle` step 3) reads the timestamp of
the last `role='user'` row per persona and compares it with
`daemon:last_monologue:{user}:{persona}`. Equal means already processed; nothing
happens. **One monologue per user message, ever.** The `Sky:rick` mark was
`2026-08-29 23:02:56`, the last Reflection node landed 23:13 the same night, and no
user message arrived until 2026-09-01 19:34. That is the whole mechanism.

§47's replay returned `"No available API key for the requested provider (google)"`.
Provider `google` is the **non-Vertex** branch, which `llm_engine.call_llm` only takes
when `VERTEX_PROJECT_ID` is unset (`use_vertex = bool(os.getenv(...))`, line 270). The
replay was a scratch script that did not `load_dotenv()`. §28 documented that exact
chain — "no .env → use_vertex False → provider google → error string" — one session
earlier, and §47 walked into it and blamed the pool.

Verified on the live system: `main.py` calls `load_dotenv()` at import and spawns
`stream_worker.py` with `Popen`, so the daemon inherits the project id; `.env` carries
`VERTEX_PROJECT_ID` and `VERTEX_LOCATION` and nothing else; an ADC file exists and
`gcloud auth application-default print-access-token` mints a token; `q:pool:key:*` is
empty; and the 19:40 monologue was written anyway. Every Reflection node in the corpus
came through Vertex. The key pool has never been in the daemon's path.

Consequences: the Status banner's "monologue writer is INERT" paragraph, §47's box, and
Session 8's next-list item 1 are all withdrawn. The key-pool repairs (§48–§50) stand on
their own merits; they just never explained this.

### 53. §51.2 confirmed on the first live call **[D]**

| when | chars | note |
|---|---|---|
| 2026-08-29 21:59 | 97 | stub, `max_tokens=1000` era |
| 2026-08-29 23:13 | 58 | stub, same |
| 2026-09-01 19:40 | 2,263 | first call at `max_tokens=4000` |

Above the 200-char floor, prose to the end. The reasoning-budget hypothesis holds on
n=1; the `usage=` line is in the app's stdout, not in any file this note can cite.

### 54. APPLIED — daemon model selection **[D]**

Both daemon call sites hardcoded `"google/gemini-2.5-flash" if openrouter_key else
"google/gemini-3-flash-preview"`. Replaced with a module-level table and resolver:

| call | env override | OpenRouter default | Vertex default |
|---|---|---|---|
| NLI gate | `DAEMON_NLI_MODEL` | `google/gemini-3.5-flash` | `google/gemini-3-flash-preview` |
| monologue | `DAEMON_MONOLOGUE_MODEL` | `google/gemini-3.7-flash` | `google/gemini-3-flash-preview` |

The gate gets the smartest flash, not the cheapest: a wrong verdict is a
`semantic_contradiction` row the resolver acts on. Owner's call, and the right one.
Prices from the raw OpenRouter catalog (419 models, 2026-09-01), per M tokens: 3.5-flash
$1.50/$9.00, 3.6 and 3.7-flash $0.75/$3.75, `z-ai/glm-5.3-flash` $0.075/$0.25. A
summarised fetch of that catalog **omitted `gemini-3.5-flash` entirely**; the owner
caught it. Grep the raw JSON, never a paraphrase.

An override wins on either route, whitespace stripped; blank is not an override. The
Vertex id did not move. `tests/test_daemon_lock.py` section [8], 11 checks.

Two facts this made explicit. **UI keys never reach the daemon**: the UI keeps them in
`localStorage`, ships them per request as `active_api_keys`, and the chat endpoint
merges them over `.env` for that request only; `user_settings` has no key column; the
daemon reads `os.getenv` and nothing else. By design. And the moment
`OPENROUTER_API_KEY` lands in `.env`, `has_openrouter_key` disables the Vertex route for
the daemon and the OpenRouter defaults above take over.

### 55. APPLIED — the novelty channel **[D]**

The DA calibration protocol the owner brought in (four dials: saturation, reuptake,
hysteresis, RPE asymmetry) was checked against the code. Three of four were already
measured and behaving; the write-up is in `da_neuron_log.md`. The finding that mattered:
**conversation alone cannot open the explore gate, by arithmetic.** Every tonic input is
measured against a Rescorla-Wagner expectation that ratchets at 0.25, so a channel's
lifetime lift from neutral is `gain × 2.0`: tool success +0.30 (asymptote 0.60), social
valence +0.048 (asymptote 0.348, never reaches 0.50). The daemon's own +0.04 sits
downstream of the gate. Cold start is exactly baseline.

The owner's brief is a genius-level ADHD brain modelled on the persona. That brain is
interest-driven, not approval-driven, so raising the social gain was the wrong lever. Built
instead: `dopamine_state.novelty_reward`, fired from `DeepMemory.store()` with the new
memory's max cosine similarity to the persona's existing memories, computed there because
the vector is already in hand (`_auto_associate` scores on keywords, not vectors).
Bounds measured on the live corpus (217 memories): nearest-neighbour p10=0.59, p25=0.80,
p50=0.89. Floor 0.45, ceiling 0.75, gain 0.12, phasic 0.30, leaky bucket 0.30 per tau —
the same +0.30 ceiling the tool channel has. Three genuinely new observations open the
gate from cold; ten ordinary Reflector rehashes pay nothing. `tests/test_novelty_reward.py`,
34 checks, including the asymptote arithmetic.

Note the actual birth path: **conversation does not create zettel nodes.** Every recent
`zettel_entries` row is a daemon monologue or a UI lore paste. Conversation creates
`deep_memories` via the Reflector every five turns. That is where the hook lives.

### 56. APPLIED — the idle branch was a rumination loop **[D]**

`generate_idle_monologue` has three prompts. Conflict, gap, and — when it has neither —
`"Reflect on your current state of existence, your beliefs, and what you want next."`
Asked with the full persona prompt loaded, at tonic 0.30 with the gate shut, and no
problem to work on. The 19:40 monologue (§53) was that branch: no link, no gap, an
existential inventory, written into `zettel_nodes` as knowledge.

| month | gap/conflict-driven (linked) | idle branch (orphan) |
|---|---|---|
| 2026-07 | 5 | 69 |
| 2026-08 | 27 | 5 |
| 2026-09 | 0 | 1 |

July is F1a's missing month: the gap detector joined on the wrong column (§11), never
found a gap, and the daemon answered the intake form 69 times. Each one was stored and
read back later as the persona's own memory. And the coupling is backwards: low tonic
shuts the gap picker, which guarantees the idle branch, which produces the rumination.
The less drive, the more self-interrogation. Low tonic in a brain is rest.

Applied, four anchors in `stream_worker.py`: the cycle passes `exploring=_exploring`;
**gate shut + nothing to integrate returns before any DB or LLM work**, with the
idempotency mark still armed (§45 — the early return is not a refire); the idle prompt
is forward-facing ("what are you curious about at this moment, pick one thread"); and
idle output goes to the observation log only, never to the graph. Gap and conflict
monologues are untouched. `tests/test_daemon_lock.py` section [9], 14 checks, including
a functional one with the LLM and DB stubbed to raise: the rest path returns clean, marks
once; a gap with the gate shut still proceeds.

### 57. Smaller findings

- **The Zettel is half doing its job.** 213 of 371 lore nodes have zero links (behavioral
  nodes are orphans by design; they are trigger-matched). 136 of the 213 come from one
  215-node `KB` ingest, 2 more from the other KB batch. `process_entry` cross-links a new
  node only against nodes that **already existed** (`AUTO_LINK_SIMILARITY_THRESHOLD =
  0.75`), never against its siblings in the same batch, so 59 orphans sit within 0.80 of
  a neighbour and some are exact duplicates at 1.00. Monologue-born nodes from gaps and
  conflicts are all linked. One loop in `process_entry` fixes it. Not built.
- **75 legacy idle-branch monologue nodes** (July 69, August 5, September 1) are still in
  the corpus and retrievable. Identifiable as monologue-sourced nodes with zero links.
  Removing them is a live-graph data operation — snapshot-then-delete like F2 — and is
  the owner's call.
- `q:daemon:cycle_secs` survived the outage (24 h TTL) and read 60 s at boot, so the boot
  line derived `stale_ttl 300s`: the floor, correct for an idle last cycle.
- `tests/test_workspace_paths.py` prints **20** PASS lines. §51.3's "test_workspace_jail 22"
  matches neither the file name nor the count. All counts here are from output.
- `uvicorn.log` in the project root was last written 2026-06-18. It is not the app's log.
- Line endings, measured: `stream_worker.py` is LF; `dopamine_state.py`,
  `memory_engine.py` and `tests/*.py` are CRLF. A Git Bash `grep -c $'\r$'` reported 1323
  CRLF lines on the LF file. Count bytes in Python; choose the EOL per file.
- `da_neuron_log.md` still said the active cycle "has not yet been measured." It has:
  548/577 s (§35) and 652.8 s (§39). Corrected there.

### 58. APPLIED — §23's telemetry `event_type` filter **[D]**

Session 10, 2026-09-03. Owner's call; §23 had left it as a product decision. **App was not
running** (no python process, `q:daemon:lock` gone, `q:daemon:heartbeat` a stale 09-02 stamp),
so no uvicorn reload and nothing to recover.

Reality vs §23's line numbers: the route is now `main.py:817`, the query `main.py:856-860`
(Session 9's edits pushed it 20 lines). The SQL text itself was unchanged from §23.

**Live DB, `observations.event_type` across all users, read-only `immutable=1`:**
`user_message` 268, `dense_observation` 104, `entropic_gap` 3. **That is the whole set.**
`semantic_contradiction` and `internal_reflection` have zero rows but are live writers
(`stream_worker.py:792/:831` and `:1067/:1146`), so §23's three-type `IN` list covers every
dissonance writer in the tree and nothing else.

**Replay of the exact SQL lifted back off disk, `username='Sky'`:**

| | rows | non-dissonance rows |
|---|---|---|
| before | 10 | 8 (`user_message`/`dense_observation` backfill, all `Sky/rick`, 09-02 18:23–19:03) |
| after | **2** | **0** — id 5733 `Sky/rick` 09-02 19:15, id 5464 `Sky/v` 08-22 14:38 |

The panel will look nearly empty. That is the honest state: three gaps ever detected, two
of them Sky's. The `App.jsx:826-827` empty-state copy ("complete equilibrium") never fires
for Sky while those two rows exist.

Mechanics: byte-level edit, CRLF 1011 → 1012 lines, bare-LF 0 → 0, `py_compile` OK,
`git diff --stat` = `1 file changed, 1 insertion(+)`, one hunk. Pre-edit copy in the session
scratchpad. **Not committed** — `main.py` was clean vs `9fa8afc` before this, so it is one
`git checkout -- main.py` from clean if the owner wants it gone.

**Still open, deliberately not touched:** `App.jsx:834-839` only distinguishes `entropic_gap`
from *everything else* — an `internal_reflection` row (the daemon's own monologue,
`reflection_score=0.8`) would still render in the red "contradiction" style. Cosmetic, JSX-only,
needs a rebuild of `dist` to ship. Worth a third badge colour if the monologue ever lands there.
> **[Same session — done, §59.]**

---

### 59. APPLIED — third badge colour for `internal_reflection`, and the score was never out of 10 **[D]**

Session 10, same sitting as §58. Owner's call ("an interesting brain, not a fucked up one").

`vite-project/src/App.jsx:833-848` (post-edit numbering). The badge was a two-way ternary:
`entropic_gap` → amber, *anything else* → red. `internal_reflection` — the daemon's own
monologue, `reflection_score=0.8`, written at `stream_worker.py:1067/:1146` — would have
rendered as a contradiction. Now three-way, using colours the file already owns: gap stays
amber/magenta (`#f0b232` / `#ff007f` void), **reflection is green** (`#23a55a` / `#00cc66`
void — 14 prior uses of the void green, it is this UI's "healthy" colour), contradiction
stays red/violet. Background tint follows.

**Second thing, spotted while in the block:** `App.jsx:888` printed
`Dissonance Reflection Score: {reflection_score}/10`. The column is a **0–1 fraction**
(0.7 gap, 0.8 monologue, 1.0 dense — §23, `main.py:835`). "0.8/10" was a display lie on every
row. Now `Reflection Score: 80%`. Label dropped the word "Dissonance" because a reflection row
is not one.

Mechanics: `App.jsx` is pure LF (4251 → 4258 lines, 0 CRLF before and after — note this file
is the *opposite* of `main.py`). `git diff --stat`: 1 file, 14 insertions, 6 deletions, two
hunks. `npm run build` (vite 8.0.0-beta.16, node 24.13): 30 modules, 208 ms.
`dist/assets/index-Bak0jk3w.js` (Jul 18, 326,259 B) → `index-CBrCaQPS.js` (327,253 B); CSS
hash `BckMtsgU` **unchanged**, so no style drift. Bundle grep: `internal_reflection` 1,
`#00cc66` 3, `rgba(0,204,102,0.12)` 1, `Reflection Score:` 1, `/10` **0**,
`Dissonance Reflection Score` **0**. `dist` is gitignored (`vite-project/.gitignore:11`);
the pre-build copy is in the session scratchpad as `dist.pre-s10`.

**Not committed**, same as §58. `git` warns `LF will be replaced by CRLF` on `App.jsx` —
that warning fires on the pristine file too; it is `core.autocrlf` meeting a tree that is
CRLF/LF *per file*. Nothing here changed that.

**The rebuild also shipped something older:** the Jul 18 bundle predates `9fa8afc`, which added
`api.resetPoolKey` and the *Restore this key to HEALTHY* button to the Ecosystem Health panel
(`App.jsx` +39, `api.js` +6). Committed 09-01, never built until now. First time it is live.

Not verified in a browser: the app was down for the whole session. The bundle is on disk;
the next `main.py` boot serves it from `FRONTEND_DIST` (`main.py:996`).

---

### Next session — start here (supersedes the Session 8 list)

1. **Watch the two new log lines.** A ≥5-turn conversation that teaches `Sky/rick`
   something it does not know should print `[DA] novelty: nearest=… paid=+… tonic=…` from
   the Reflector's store; an idle persona with the gate shut should print
   `resting, no monologue` once per user message. If novelty never pays across a session
   that clearly covered new ground, the 0.75 ceiling is too low for this embedding model.
2. **Hysteresis on `should_explore`** — open at 0.50, close at 0.45, one stored posture
   key per persona. Each boundary flap costs a full active cycle (153 NLI calls at
   3.5-flash prices). The one real gap left in the DA calibration (`da_neuron_log.md`).
3. **Decide on the 75 legacy rumination nodes** (§57). F2-style snapshot first if removed.
4. **Sibling auto-linking in `process_entry`** (§57) — then the gap picker's queue drops
   by ~136 without a single LLM call.
5. Carried from Session 8, unchanged: **F4** (allowlist, the lying `add_observation`
   docstring, `memory_plugin.py:48`, index; §46's swallowed `False`); per-pair NLI
   watermark; §19 as its own build; `checkout_proxy`'s cursor (§50, end).

**Mechanical batch, still untouched:** persist the 33-case attribution suite into
`tests/`; `mcp_client.py:83` colon; `database.py:20` monkeypatch reload guard;
`q:daemon:heartbeat` TTL + `main.py:832` staleness check; ~~telemetry `event_type` filter~~ (done, §58);
`include_embeddings=False`; `try/finally` on the two bare connections; delete the stale
`users.db` stub and the July backup (~15.4 MB); the now-unused `import redis_client` at
the top of `generate_idle_monologue`.
