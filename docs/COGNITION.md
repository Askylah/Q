# Cognition: The Consciousness Daemon

Q's personas keep thinking when nobody is talking to them. This document describes the machinery that makes that happen, what it is allowed to do, and what it learned not to do. Source files are named literally; the lab notes in `lab_notes/` record how each piece was actually debugged.

## Where it lives

*   `stream_worker.py` - the daemon. Runs the sweep, the gap picker, the NLI contradiction check, the monologue writer, and the dissonance cap.
*   `dopamine_state.py` - neuromodulator state. Tonic and phasic values per (user, persona).
*   `llm_engine.py` - the tool loop, and where reward-prediction-error events are emitted after tool outcomes.
*   `memory_engine.py` - decay and storage. Consumes dopamine state.
*   `zettel_engine.py` - the graph the daemon reads from and writes back to.

## The sweep

On every cycle the daemon writes a heartbeat to Redis, enumerates active personas, and for each one:

1. **Reads dopaminergic posture.** Low tonic means consolidation mode: the gap picker stands down entirely. If dopamine state is unavailable the daemon defaults to exploring.
2. **Scans for entropic gaps.** Isolated clusters and low link density in the persona's memory graph.
3. **Scans for semantic contradictions.** Candidate pairs are prefiltered on token Jaccard overlap, then checked by an NLI gate. The NLI model is a deliberate choice: a verdict the system acts on gets the strongest model available, not the cheapest.
4. **Resolves what it found**, writes the resolution to the graph, and that resolution becomes input to the next sweep.
5. **Rests if there is nothing to do.** See the rest gate below.

A gated-idle cycle is about 60 seconds. An active NLI sweep was measured at 548 to 577 seconds, which is why the cap and the gate exist.

## Neuromodulator state

Two variables per (user, persona), on two timescales.

*   **Tonic** (0.0 to 1.0): slow baseline. High tonic chases voids, protects memories from decay, favors novelty. Low tonic is consolidation: the decay leak runs cold and the gap picker stands down.
*   **Phasic** (0.0 to 1.0): fast spike envelope, decays in minutes. Fired by reward-prediction-error events. Gates write-time stamping: memories born during a spike get an importance bonus.

Design rules the code enforces:

1. Dopamine is not per-memory salience. It never reads or writes a memory's importance directly. That is a different organ.
2. Reward-prediction-error, not raw novelty. `fire_rpe()` takes expected versus observed. Positive deltas spike, negative deltas dip. Predicted novelty is not rewarding.
3. State is ephemeral context, not a record. Redis-backed floats with a timestamped exponential return to baseline, plus an in-memory fallback when Redis is offline.

Consumers: `memory_engine.decay_cycle()` scales its decay rate by tonic; `memory_engine.store()` adds a stamp bonus from phasic; the daemon's gap picker explores only above a tonic threshold.

**Tool-outcome attribution.** Tool results feed reward-prediction-error, so the classifier that decides whether a tool call succeeded is part of the learning loop. An audit prompted by one log line found that 10 of 13 real failure paths were scoring as clean successes: the MCP router never checked the protocol's `isError` flag, so bad arguments, missing tools and permission errors came back through the success branch, and the agent was being rewarded for failing. The first fix introduced four regressions of its own, found by an adversarial pass rather than by reading, and the classifier now has a 33-case regression suite. Infrastructure failures (transport dying, not remote programs misbehaving) are discounted before they reach the neuron. See `lab_notes/tool_outcome_attribution.md`.

**Novelty channel.** A separate reward channel for genuinely new material, added after the livelock work. See `tests/test_novelty_reward.py` and `lab_notes/da_neuron_log.md`.

## The rest gate

The original idle branch, taken when the sweep found no conflict and no gap, asked the persona to "reflect on your current state of existence, your beliefs, and what you want next" with the full persona prompt loaded and nothing to work on, then stored the answer as knowledge.

In July 2026, 69 of 74 monologues were that branch. The gap detector was joining on the wrong column, so it almost never found anything, and every idle reflection was later read back as the persona's own memory. Low tonic shut the gap picker, which guaranteed the idle branch, which produced rumination: the less drive, the more self-interrogation. Backwards.

Low tonic in a brain is rest. The fix: gate shut plus nothing to integrate equals no monologue at all. The processed mark is still armed so the branch does not refire every cycle.

This bug was not caught by any metric. Every number was green. It was caught by reading the daemon's monologue output and noticing it had gone flat.

## Dissonance cap and telemetry

Daemon-authored writes are rate-limited per (user, persona) per window, by kind: `entropic_gap` and `semantic_contradiction` each hold their own slot. When the cap is hit the sweep for that kind is skipped and logged. The cap is Redis-backed with a process-local fallback.

Every daemon event is logged through `plugins/memory_plugin.py` with an event type and a reflection score, and surfaced in the UI's telemetry panel with per-type badges.

## Daemon lock

The daemon starts with the backend under a single-instance socket lock so two backends cannot run two daemons against one database. The lock is zombie-safe: a stale holder is detected and reclaimed. See `tests/test_daemon_lock.py`.

## What it is not

*   It is not a scheduler. Nothing runs on a timetable except the sweep itself; what the sweep does is decided by state.
*   It is not unattended authority over the host. The daemon's outputs are memory-graph writes and telemetry events, and those writes are capped.
*   It is not finished. The lab notes' status banners are the source of truth for what is live, what is uncommitted, and what was retracted.
