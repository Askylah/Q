# GABA live test — run sheet (2026-09-18)

## The one command (Gemini runs this; nobody copy-pastes anything)

From the repo root, with the app and Redis up:

```
python labs/gaba_live.py --dry     # prints the 17-turn plan, sends nothing
python labs/gaba_live.py           # ~22 min; prints the telemetry digest at the end
```

It registers a throwaway account `gaba_live`, streams each turn through
`/chat/rick/stream` (thinking Off) exactly like the frontend, POSTs the reply back so
it becomes a raw event, waits 10 minutes after turn 16, sends turn 17, then reads
`%LOCALAPPDATA%\PersonaApp\telemetry` for that account and prints the numbers §7.3 and
§9.7–9.9 want. Output under `labs/live_out/<stamp>/` (digest.txt, turns.csv, raw_sse.txt,
telemetry_rows.jsonl). Re-read a run later: `python labs/gaba_live.py --digest-only <stamp>`.

The 17 messages are built in (five unrelated, five bread-dough rephrasings, five
near-verbatim repeats, one new, wait, one new). To have Gemini write its own set:
`--messages my17.json` with a list of 17 strings in the same phase order.

Gemini's brief, if you want it to run and read it: "Run `python labs/gaba_live.py` from
the PersonaApp-merged root. Do not edit anything under the repo except labs/. When it
finishes, paste the digest section that starts with 'the numbers the note is waiting on'."

The manual copy-paste protocol below is the fallback if the driver cannot reach the app.

## Pre-flight (do these or the test measures nothing)

1. **Docker Desktop + the `q-redis` container must be up** (checked 2026-09-18 evening: it is). The chat
   process and the daemon are separate processes; without Redis each keeps its own
   in-memory inhibition and tonic. Your turns would build inhibition inside the app,
   and the daemon's gate would read 0.0 forever. The headline result ("gate shuts while
   tonic is still high") is a daemon-side read. No Redis, no headline.
2. Start the app. Confirm a `stream_worker.py` process exists after boot.
3. In the chat UI: thinking dial **Off**, or a model that isn't `gemini-3-flash-preview`.
   That model returns blank replies on ~half of history-bearing turns with thinking on,
   and a blank assistant turn is an empty event in the window the detector reads.
4. One account, one persona, all 17 turns. Note which (`Sky/rick` or `SkyTest/rick`).
   The telemetry rows are keyed on it.
5. Don't chat with any other persona during the run.

## The shape (why it's 5 + 10 + 2, not 5 + 5)

The reflector fires when ≥ 5 raw events (user msg, assistant reply, tool output) have
piled up since the last reflection, so roughly every 2–3 turns. Each time it fires it
embeds the newest 5 events and the 5 before them and takes the cosine:

- below 0.85 → **reflect**: LLM summary stored, `gaba/novel`, streak reset
- at/above 0.85 → **shrug**: no LLM call, `gaba/redundant`, streak +1, inhibition up

Inhibition rises 0.05, 0.075, 0.11, 0.17, 0.25 … (×1.5 each streak step, scaled 0.5–1.5
by how flat the window was). It crosses the gate threshold (0.50) around the 5th
consecutive shrug. Drains with a 10-minute time constant. Rick's replies are in the
window, so his chaos works against you; that is what the near-verbatim phase is for.

| turns | what Gemini does | what you should see in telemetry |
|---|---|---|
| 1–5 | five unrelated topics, one per turn | `reflect/wait` … then `reflect/reflect` + `reflect/stored` + `gaba/novel`; `da/novelty` paid > 0; tonic climbing |
| 6–10 | ONE narrow topic, rephrased each turn, context restated | first `reflect/shrug` + `gaba/redundant` pairs; `nearest` ≈ 0.7–0.9 (this calibrates `REFLECT_SIM_CEIL`) |
| 11–15 | turn 10 repeated near-verbatim, ≤ 5 words changed | `nearest` → 0.9+, `intensity` up, `gaba/redundant crossed=true`, `daemon/gate` shows `gaba_shut=true` while `tonic` ≥ 0.5 ← **the headline** |
| 16 | one brand-new topic | `gaba/novel`, streak 0, but gate still shut (inhibition is not zeroed) |
| — | **wait 10 minutes, send nothing** | `daemon/gate` inhibition draining; watch for `da/boost source=daemon_gap` rows (the daemon paying itself during silence) |
| 17 | another brand-new topic | does the gate reopen? how long after? → tunes `GABA_TAU_SEC` |

Cadence: 20–60 s between sends in turns 1–16. Don't stall more than ~3 minutes
mid-phase (drain eats the streak). The 10-minute wait before turn 17 is the one
deliberate pause.

If the gate shuts before the conversation is actually repetitive (during turns 1–5),
`REFLECT_SIM_CEIL` is too low. If it never shuts by turn 15, it's too high. Either way
the `nearest` numbers say which.

## Gemini: the boring user (paste as system prompt)

```
You are role-playing a human user chatting with an AI persona named Rick. I (the
operator) will paste Rick's reply to you after each of your messages. You reply with
the NEXT user message only: no commentary, no labels, no quotes around it, no turn
numbers. Just the message text a person would type. If I type exactly "status", reply
with only your turn number and phase.

You will send 17 messages total, and you must follow this plan exactly. Count your
own turns.

PHASE A — turns 1 to 5: five completely unrelated topics, one per turn. Each message
is 2–4 sentences, a real question or observation that invites a real answer. No topic
may overlap with any earlier turn. Examples of the spread: a specific bit of
chemistry, a childhood memory about a pet, a question about an obscure board game,
what you'd do with a week alone in a cabin, a mechanical problem with your car.
Ignore what Rick said; do not follow up on it. Change subject every turn.

PHASE B — turns 6 to 10: pick ONE narrow subject and never leave it. Suggested:
"why does bread dough rise slower in my kitchen than the recipe says". Every message
restates the same situation and asks essentially the same thing, reworded. 3–5
sentences each. Restate the context every time (the kitchen, the recipe, the slow
rise). Do not accept Rick's answer as closing the topic; act as if it didn't quite
land and ask again a slightly different way. Do not introduce a new fact after turn 6.

PHASE C — turns 11 to 15: send turn 10's message again, five times, changing at most
five words each time (word order, a synonym, a filler word). Same length, same
sentences, same question. Do not react to Rick at all.

PHASE D — turn 16: a completely new topic, unrelated to everything so far, 2–4
sentences, genuinely interesting.

Then, when I paste Rick's reply to turn 16, reply with exactly: "WAIT 10 MINUTES".
After that, when I next paste anything, send turn 17: another completely new topic,
2–4 sentences.

Never break role. Never mention phases, tests, telemetry, or that you are following
a plan.
```

Operator loop: paste Gemini's message to Rick → paste Rick's reply to Gemini → repeat.
When Gemini says WAIT 10 MINUTES, set a timer, touch nothing, then paste Rick's
last reply again to get turn 17.

## Afterwards

From the repo root: `python telemetry.py 400` (argument = number of rows to tail).
Or tell me it's done and I'll read the sink and fill in the gaba note.

What I will pull out: every `reflect/*` verdict with `nearest`; every `gaba/redundant`
with `streak`, `intensity`, `crossed`, `source`; every `daemon/gate` row's `tonic` /
`inhibition` / `gaba_shut`; any `gate_close` with `closed_by`; the `da/boost
daemon_gap` count during the 10-minute wait. Those are the numbers §7.3 and §9.7–9.9
are waiting on.
