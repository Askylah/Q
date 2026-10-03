"""
reflect_gate.py -- when should the Reflector actually reflect?

Pure decision logic, no I/O, no imports beyond the stdlib, so the reflector's
cadence can be tested without sqlite, sklearn or the sentence-transformer.
plugins/memory_plugin.py calls plan_reflection() and acts on the verdict.

Two questions, answered in order:

1. WATERMARK (cadence).  How many raw events have arrived since the last
   dense_observation?  Fewer than `turn_threshold` -> "wait".  Before this
   existed, reflect() counted raw events in the whole 20-row window and never
   remembered what it had already summarised, so from the moment the window
   held `turn_threshold` events it reflected on EVERY user message, forever.
   The "threshold" knob was a start delay, not a cadence. Measured 2026-09-06
   (lab_notes/gaba_inhibition.md §6.2): one dense_observation per turn, each
   summarising a window 18/20 identical to the previous one.

2. MEANING (novelty pre-check).  Did the newest `turn_threshold` raw events
   move relative to the `turn_threshold` raw events before them?  Cosine of
   the two chunks' embeddings, supplied by the caller as `similarity(new_text,
   prior_text) -> float | None`.  At or above `sim_ceil` -> "shrug": the
   conversation has not gone anywhere, do not pay for an LLM call to summarise
   it again.  Below -> "reflect".  No prior chunk (cold start) -> "reflect".
   similarity() returning None (no model) -> "reflect" (fail open; the
   watermark alone still fixes the every-turn bug).

Why newest-N against the N before them, and NOT new-events against the last
reflection: after a shrug nothing advances the watermark, so a "new events
since last reflection" blob keeps growing through a dull stretch. When one
genuinely novel turn finally arrives it is diluted by everything dull around
it, the blob still looks like the prior, and the reflector never fires until
the window rolls the prior out entirely. That is a livelock, the exact class
of bug this codebase has been fighting (§6.7, the rumination loop). N-vs-N
bounds the dilution to one threshold's worth of events.

The "shrug" verdict is the GABA organ's input (gaba_state.on_redundant); the
"reflect" verdict, once the store succeeds, is its reset (gaba_state.on_novel).
The reflector looking at the window and declining IS the redundancy event.
"""

RAW_EVENT_TYPES = ("user_message", "assistant_response", "tool_output")
REFLECTION_TYPE = "dense_observation"

# Per-event content cap when building the text that gets embedded. Tool
# outputs can be tens of KB; the first 500 chars carry the topic.
EMBED_CONTENT_CAP = 500


def boredom_intensity(nearest, sim_ceil: float = 0.85):
    """How flat was the window, 0..1, or None when there is no similarity.
    0 at the shrug ceiling, 1 at cosine 1.0, clipped. Five windows at 0.86
    and five at 0.98 are different signals; this is the number that lets
    gaba_state tell them apart (it scales the increment, not the streak)."""
    if nearest is None:
        return None
    c = float(sim_ceil)
    if c >= 1.0:
        return 1.0 if float(nearest) >= c else 0.0
    return max(0.0, min(1.0, (float(nearest) - c) / (1.0 - c)))


def window_limit(turn_threshold: int, prompt_rows: int = 20) -> int:
    """How many observation rows to fetch so that both the prompt transcript
    (last `prompt_rows`) and the two comparison chunks (2 * threshold raw
    events, which sit among daemon rows and reflections) are available."""
    t = max(1, int(turn_threshold))
    return max(int(prompt_rows), 2 * t + 10)


def chunk_text(events) -> str:
    return "\n".join(
        f"{e.get('type', '').upper()}: {str(e.get('content', ''))[:EMBED_CONTENT_CAP]}"
        for e in events
    )


def plan_reflection(logs, turn_threshold: int, similarity=None, sim_ceil: float = 0.85) -> dict:
    """
    logs: observation rows oldest -> newest, each {"type", "content", ...}.
    Returns {"verdict": "wait" | "shrug" | "reflect", "new_events": int,
             "nearest": float | None, "intensity": float | None, "reason": str}.
    intensity is boredom_intensity(nearest, sim_ceil): >0 only on a shrug.
    """
    t = max(1, int(turn_threshold))
    logs = list(logs or [])

    # -- 1. watermark ------------------------------------------------------
    last_ref = -1
    for i, l in enumerate(logs):
        if l.get("type") == REFLECTION_TYPE:
            last_ref = i
    new_raw = [l for l in logs[last_ref + 1:] if l.get("type") in RAW_EVENT_TYPES]
    if len(new_raw) < t:
        return {"verdict": "wait", "new_events": len(new_raw), "nearest": None,
                "reason": f"{len(new_raw)} new raw event(s) since last reflection, need {t}"}

    # -- 2. meaning --------------------------------------------------------
    raw_all = [l for l in logs if l.get("type") in RAW_EVENT_TYPES]
    if len(raw_all) < 2 * t:
        return {"verdict": "reflect", "new_events": len(new_raw), "nearest": None,
                "reason": f"cold start: {len(raw_all)} raw event(s) in window, need {2 * t} to compare"}
    if similarity is None:
        return {"verdict": "reflect", "new_events": len(new_raw), "nearest": None,
                "reason": "no similarity function (pre-check disabled)"}

    newest = raw_all[-t:]
    prior = raw_all[-2 * t:-t]
    try:
        nearest = similarity(chunk_text(newest), chunk_text(prior))
    except Exception as e:  # never let the pre-check take the reflector down
        return {"verdict": "reflect", "new_events": len(new_raw), "nearest": None,
                "reason": f"similarity failed ({type(e).__name__}); failing open"}
    if nearest is None:
        return {"verdict": "reflect", "new_events": len(new_raw), "nearest": None,
                "reason": "similarity unavailable (no model); failing open"}

    nearest = float(nearest)
    intensity = boredom_intensity(nearest, sim_ceil)
    if nearest >= float(sim_ceil):
        return {"verdict": "shrug", "new_events": len(new_raw), "nearest": nearest,
                "intensity": intensity,
                "reason": f"newest {t} raw events sit at {nearest:.3f} >= {sim_ceil} to the {t} before them"}
    return {"verdict": "reflect", "new_events": len(new_raw), "nearest": nearest,
            "intensity": intensity,
            "reason": f"conversation moved: {nearest:.3f} < {sim_ceil}"}
