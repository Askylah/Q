# Efferent Pathway — Lab Notes

## Status: BUILT, UNIT-VERIFIED, ONE LIVE TURN (SYNTHETIC SEED) — EVERY CONSTANT PROPOSED (2026-09-19)

> `dopamine_state` and `gaba_state` regulate memory decay and the daemon. Until 2026-09-19
> neither reached the chat turn: the persona spoke at the operator's fixed temperature
> whatever its posture was. `efferent.py` is the outbound wire, `persona_tail.py` is the
> persona's own wording for what the wire says.
>
> What is shown: the wire carries organ state into a live request (§4). What is NOT shown:
> that the stance or the temperature lean changes what the persona writes. That needs a
> lesioned A/B at N>=8 per arm (§5). `POLICY_CALIBRATED = False` and says so in telemetry.
>
> Provenance: §1-§3 were read off the code and the diff on 2026-09-19 by a session that did
> not build the modules. §4 was run by that session. Suite counts in §3 were re-measured
> the same day, not copied.

---

### 1. What was built **[D]**

`efferent.py` (270 lines). One reading of (tonic, phasic, inhibition, streak) per turn, two
channels out:

- **Sampling.** Bounded delta on the operator's temperature, bounded scale on max_tokens.
  The operator's values are the baseline, never replaced.
  - tonic -> temperature, piecewise linear through zero at `TONIC_BASELINE`:
    `+0.15` at tonic 1.0, `-0.10` at tonic 0.0, each side scaled to its own span (baseline
    is 0.30, not 0.50). Clamp `[0.2, 1.3]`, widened to include the operator's own value so
    a deliberate 1.5 is not pulled back.
  - inhibition -> max_tokens down to `x0.60` at inhibition 1.0; phasic -> up to `x1.15` at
    phasic 1.0; the two multiply. Floor 512 tokens (or the operator's value if lower).
- **Stance.** One `[DISPOSITION]` block appended at the END of the system prompt. Band
  priority: `inhibited` (the organ's own gate, `gaba_state.is_inhibited`) > `restless`
  (inhibition >= 0.25 and streak >= 2) > `spike` (phasic >= 0.40) > `exploring`
  (`dopamine_state.should_explore`). Inhibition first because gaba rule 4 says one shiny
  thing does not throw the gate open, so it does not rewrite the stance either.

Rules, same standing as the organs' own:
1. Read-only. Never writes DA or GABA state; neither organ imports it.
2. Silent at rest: zero delta, scale 1.0, empty stance. Same request as before the file existed.
3. Numbers go to the sampler, behaviour goes to the prompt. No float is rendered into prompt text.
4. Lesionable per call: `EFFERENT_OFF`, `EFFERENT_SAMPLING_OFF`, `EFFERENT_STANCE_OFF`.
5. Every constant PROPOSED, env-overridable (`EFF_*`).

Never raises: any failure returns the caller's kwargs untouched, no band, no stance.
Telemetry: `efferent/turn` row per chat turn (policy, calibrated, organ reading,
temperature/max_tokens in and out, band). Terminal: one `[EFFERENT]` line, only when
something is non-identity.

### 2. Persona tail **[D]**

`persona_tail.py` (179 lines) + `personas/rick_tail.txt`, named by `tail_file` in
`personas.json`. efferent owns WHEN a stance fires; the tail owns how it reads.

- `DISP-<BAND>` modules override the generic stance wording for that band.
- `CONTRAST-*` modules are regression/fix pairs in the persona's voice, rendered in a
  `[VOICE_CONTRAST]` block. `Type: ALWAYS_LOAD` rides every turn; `Type: BAND` rides only
  while efferent reports a band named in `Triggers`. Budget `PERSONA_TAIL_MAX_CHARS`
  (3000), pairs dropped whole, band-gated ones budgeted first.
- Reuses `zettel_engine.parse_on_demand_file` and nothing else. Never enters the graph.
  `Triggers` here holds BAND NAMES, not message keywords. `Type: ON_DEMAND` is refused
  loudly, and a file listed as both `tail_file` and an on-demand file is refused whole
  (the engine would match "inhibited" against the operator's message).
- Lesion: `PERSONA_TAIL_OFF` drops the ALWAYS_LOAD pairs. Band-gated content dies with
  efferent's lesions.

`rick_tail.txt` as of this note: CONTRAST-001..005 (ALWAYS_LOAD), CONTRAST-BORED-001 and
CONTRAST-ENGAGED-001 (BAND), DISP-INHIBITED / RESTLESS / SPIKE / EXPLORING.

### 3. Wiring and suites **[D]**

`llm_engine.py`: both modules are optional imports (absent -> `None`, turn unchanged).
In the chat path, after `kwargs_dict` is built: `persona_tail.load_for(persona_data)`,
then `efferent.for_turn(username, persona_key, kwargs_dict, stances=tail["stances"])`,
then `render_contrast(tail, band)`. `voice_contrast + efferent_stance` is sanitized like
the persona file and appended last to the masked system prompt. The
`[DIGITAL_PHYSIOLOGY_ENVELOPE]` sampling lines were relabelled "(operator setting)": the
live leaned values stay out of the prompt on purpose (rule 3, and a stable prompt head).

Suites, measured 2026-09-19 16:4x: `tests/test_efferent.py` 56 PASS / 0 FAIL,
`tests/test_persona_tail.py` 44 PASS / 0 FAIL. Standalone, isolated state.

### 4. Live turn, 2026-09-19 16:05 **[M]**

`python labs/efferent_live.py` against the app on :8000 (uvicorn worker reloaded 15:49,
after the wiring landed at 14:35; Redis up). Fresh account `efferent_live_20260919_160535`,
persona rick, `google/gemini-3-flash-preview`, thinking Off. Two real turns through
`/chat/rick/stream`. Output: `labs/live_out/efferent_20260919_160535/`.

| turn | seed | tonic | phasic | inhib | band | temperature | max_tokens | reply |
|---|---|---|---|---|---|---|---|---|
| rest | none | 0.30 | 0.0 | 0.0 | `''` | 0.9 -> 0.9 | 4096 -> 4096 | 1624 chars, stop |
| seeded | `boost_tonic(0.30)` | 0.60 | 0.0 | 0.0 | `exploring` | 0.9 -> 0.9643 | 4096 -> 4096 | 2363 chars, stop |

- Rule 2 holds live: the rest turn is the identity.
- The delta is the formula's: `0.15 * (0.60 - 0.30) / 0.70 = 0.0643`.
- `load_for(rick)` returns overrides for all four bands, so the block that rode was
  DISP-EXPLORING in Rick's wording, not the generic one, plus 1981 chars of
  `[VOICE_CONTRAST]` for band `exploring`. Rendered offline from the same inputs; see limits.
- Neither reply contains "disposition", "tonic", "inhibition", "temperature" or
  "operator setting". No dashboard narration at N=1.
- Both replies were real (no `⚠️ ... No API key provided`): the Vertex route answered.

Limits, all of them:
- The seed is SYNTHETIC. `boost_tonic` through the organ's public API, not earned by a
  conversation. This shows the wire, not that a real exchange reaches the band.
- N=1 per arm and the two turns asked different questions. The length difference
  (1624 vs 2363) is not evidence of anything.
- The assembled prompt is not visible from outside the process. The evidence that the
  block was appended is the telemetry band plus the code path, not a captured prompt.
  The app terminal's `[EFFERENT]` line was not read.
- max_tokens scaling was not exercised live (needs inhibition > 0 or phasic > 0).
- Bands `inhibited`, `restless`, `spike` have never fired live.

### 5. Open questions **[O]**

1. Does the stance change behaviour? Lesioned A/B on one seeded state and one fixed
   message: `EFFERENT_STANCE_OFF=1` vs on, N>=8 per arm. The env var is read per call in
   the APP process, so the arm switch has to reach the worker (restart with the var, or a
   per-request lesion header; neither exists yet).
2. Same for sampling alone (`EFFERENT_SAMPLING_OFF`). A 0.06 temperature lean may be
   below anything measurable; if so, say so and widen or drop it.
3. Capture the assembled prompt tail once (debug dump behind an env flag) so §4's
   "rendered offline" becomes "read from the request".
4. The GABA crossing run (`gaba_inhibition.md` next-list item 2) is now also the first
   chance to see `restless` and `inhibited` fire from earned state. Read the
   `efferent/turn` rows out of that run's digest.
5. `TOKENS_INHIBIT_MIN = 0.60` on a 4096 budget is 2457 tokens. Rick's replies here were
   ~400-600 tokens. The cap may never bind; the stance ("be brief") would be doing all
   the work. Check reply lengths against max_tokens_out in the crossing run.
6. `tail_file` reaches nobody but this machine. `personas.json` is gitignored
   (`.gitignore:14`), `custom_personas` has no `tail_file` column, the persona editor has
   no field for it. A fresh clone gets `rick_tail.txt` on disk and nothing pointing at it:
   rule 1 makes that silent (generic stances, no contrast pairs). Decide: a DB column + UI
   field like `on_demand_files`, or a convention (`personas/<key>_tail.txt` found by name).

---

## Next session — start here (2026-09-19)

1. Sky's go-ahead, then commit: `efferent.py`, `persona_tail.py`, `personas/rick_tail.txt`,
   the `llm_engine.py` wiring (the `tail_file` key lives in gitignored `personas.json`, see 5.6),
   `tests/test_efferent.py`, `tests/test_persona_tail.py`, `labs/efferent_live.py`, this note.
   NOT `labs/live_out/` (chat excerpts), NOT `Q_Installer.zip` / `dist_installer/*`.
2. §5.3, the prompt-tail dump: smallest thing that upgrades §4 from inferred to observed.
3. §5.1 needs a way to lesion the running worker per request before it can be run.
