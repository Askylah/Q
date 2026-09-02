# Tool-Outcome Attribution — Lab Notes

## Status: FIXED (uncommitted) — 4 files — 2026-08-29

Origin: a single log line during live testing of the entropic-gap fixes —

```
[DA] tool_reward rpe=-0.2099 tonic=0.4389 phasic=0.3479 infra_discounted=False
```

— and the owner's question: *"does this mean it still logs tool failures as its own
failures?"*

The answer was no, and then the investigation found something worse in the other
direction. Separate subsystem from `entropic_gap_livelock.md`; cross-referenced there
only in passing.

Rollback: `%LOCALAPPDATA%\PersonaApp\rollback_20260829_preDA\` + `SHA256SUMS.txt`
(`llm_engine.py`, `mcp_router.py`, `dopamine_state.py`, `secure_runner.py`,
`mcp_client.py`).

---

### 1. The premise of the question was wrong — a failure cannot hurt tonic **[D]**

`dopamine_state.py` `_update_ema_and_fire`:

```python
new_phasic = min(PHASIC_MAX, max(0.0, cur_phasic + max(-0.3, rpe) * phasic_weight))
new_tonic  = min(1.0, max(0.0, cur_tonic + (tonic_gain * rpe if rpe > 0 else 0.0)))
```

**A negative rpe never moves tonic.** Simulated with the exact observed values:
tonic `0.4536 -> 0.4536`, delta `+0.0000`, on `rpe=-0.1821`. The observed
`0.4536 -> 0.4389` in the log was **272 seconds of pure time decay** toward the 0.30
baseline (`TONIC_TAU_SEC` 2700), which matched the wall-clock gap exactly.

So a misattributed failure costs only: a `phasic` dip decaying with a **90-second**
constant, and a lowered `tool_ema` expectation — which makes the *next* success produce
a **larger** positive rpe. Partially self-correcting. The "learned helplessness" the
module docstring warns about cannot occur through tonic, because tonic is
ratchet-up-only from RPE.

And when infra *is* recognised the discount works: 5 infra failures, 0 others ->
`rpe=0.0`, tonic untouched, `infra_discounted=True`. **[D]**

---

### 2. The real bug: the agent was being REWARDED for failed tool calls **[D]**

`infra_discounted=False` never meant "attribution is off". It meant the classifier saw
zero infra failures. The audit then found that it was seeing almost no failures at all.

**10 of 13 real failure paths scored as a clean `success`.** Two root causes:

**2.1 `mcp_router.py:94` — the missing colon.**
```python
return f"Error executing '{self.name}/{raw_name}': {e}"
```
`_FAILURE_MARKERS_EXACT` contains `"Error:"` — with the colon. `"Error executing"` has
no colon after Error, so it matched **no marker**, and every transport-level exception
on the router scored as a win.

**2.2 `mcp_router.py:87-91` — `isError` was never checked. This is the big one.**
MCP signals tool failure with `isError` **on the result**; it does not raise. The router
returned `result.content` text unconditionally, so failures came back through the
*success* branch as the provider's bare prose:

| what actually failed | old verdict |
|---|---|
| `Unknown tool: git_log` | success |
| `Input validation error: 'code' is a required property` | success |
| `ENOENT: no such file or directory` | success |
| `EPERM: operation not permitted` | success |
| `Server cannot operate: No allowed directories available.` | success |

Bad arguments to an MCP tool — the single most common genuine action failure — scored as
a success. And since `mcp_router` carries virtually all external tool traffic, this one
missing check accounted for most of the mis-scoring in the system.

**2.3 `query_second_brain` is advertised and undispatched. [D]**
`llm_engine.py:1881` adds the tool whenever `expert_model_id` is set, and `:2062`
instructs the model to delegate to it. There was **no dispatch branch**. It fell through
to `execute_api`, whose `EXECUTION_MAP` is empty (`plugins/schemas/` does not exist),
returning `"Error: API function query_second_brain not found in execution map."` — which
*does* match `"Error:"`, so it scored as an **action failure**. The agent took a
competence penalty every time it obeyed its own system prompt. The plumbing was already
complete: `expert_model_id` is threaded into the generator's `**kwargs` at `:2171`. Only
the branch was missing.

**2.4 One case backwards. [D]** `secure_runner.py:87` returned
`"CRITICAL FAILURE: Execution timed out (Possible infinite loop...)"`. `timed out` is
high-confidence *infra* vocabulary — so the one failure on that path that is
unambiguously the agent's own fault was the one being excused.

---

### 3. Applied

| file | change |
|---|---|
| `mcp_router.py` | check `getattr(result,"isError",False)`, return `Error: MCP tool 'srv/tool' failed: <body>`; except-path reworded to carry the `Error:` marker |
| `llm_engine.py` | `query_second_brain` dispatch branch mirroring `call_sub_agent`, using `kwargs["expert_model_id"]`; two empty-completion fallbacks reworded (§4.4) |
| `dopamine_state.py` | success shapes split in two; `_JSON_ERROR` added; envelope-line skip; probe widening confined to the JSON test; transport-only vocabulary additions |
| `secure_runner.py` | timeout message no longer contains "timed out" — wording *is* the attribution signal on that path |

**Design note worth keeping: fix the source, not the regex.** Checking `isError` in the
router repaired `Unknown tool`, `Input validation error`, `ENOENT` and `EPERM` all at
once, because they now arrive with a marker attached. Every one of those would otherwise
have needed its own pattern, and the pattern list is exactly where over-detection breeds.

---

### 4. The first fix attempt introduced four regressions. Found by an adversarial pass, not by reading. **[D]**

All four were mine, all verified by execution before being believed, all now fixed and
covered by the suite in §5.

**4.1 Probe widening poisoned successful payloads.** To catch pretty-printed error
objects I widened the probe to 4 lines when line 1 is a bare `{`/`[`. But
`list_mcp_tools` (`llm_engine.py:1356`) emits `json.dumps(..., indent=2)` of a list, so
line 1 is always `[` — and lines 2-4 are tool **descriptions**. A successful call scored
`infra_fail` the moment any tool's description contained a word like "unreachable".
*Fix:* widen only for the `_JSON_ERROR` test, never for the infra vocabulary or failure
markers, and only re-use the widened text for *attribution* once the payload is already
established as an error.

**4.2 New vocabulary re-created the exact bug §2.4 had just removed.** Because the router
now wraps the provider's prose as `... failed: <body>`, that prose gets judged. Terms
like `process exited`, `unreachable`, `cannot connect`, `quota exceeded`,
`resource exhausted` describe the agent's own broken code far more often than an outage:
`process exited with code 1: SyntaxError` is a bug the agent wrote. *Fix:* the list is
now **transport-only** — every term names a connection dying, never a remote program
misbehaving.

**4.3 The `json_error` veto broke `grep_workspace`.** Applying it to *all* success shapes
meant any grep whose first hit contained an error key lost its success shape and was
judged on its own payload. Grepping this repo for `"status"\s*:\s*"error` scored
`infra_fail` — because the match was a comment in `dopamine_state.py` that quotes
`{"status": "error"}` and a `502`. **The classifier scored a successful search of its own
source file as an outage.** *Fix:* split `_TOOL_OUTPUT_SHAPES` (grep hits, file headers,
`NO MATCHES`) which win **outright** — everything after `path:NN:` is quoted foreign text
— from `_JSON_OK_SHAPE`, which alone is subject to the veto.

**4.4 `no response from` captured the engine's own fallbacks.** `llm_engine.py:1375` and
the new second-brain branch emit that string when `call_llm` returns a dict with no
content — a malformed completion, frequently downstream of the agent's own prompt, not a
transport failure. *Fix:* term dropped, and both fallbacks reworded to
`"... returned an empty completion."`

**Bonus, pre-existing, fixed in passing:** `secure_runner.py:84` wraps output in
`[UNTRUSTED_TOOL_OUTPUT]`, so line 1 was that token and a sandboxed script that died with
a traceback scored as a clean success. `classify_tool_outcome` now skips a leading
envelope line before choosing its probe.

**Amplifier that makes all of this expensive rather than cosmetic [A, arithmetic D]:**
`tool_reward` computes `observed = s/(s+a)` and returns early with `rpe=0.0` and **no EMA
update** when `informative == 0`. So a turn whose only call is misclassified as infra
produces *zero* learning instead of a positive reward. A false infra reading does not
merely discount the event — it deletes it.

---

### 5. Regression suite — 33 cases, all passing **[D]**

Three groups, and the third is the one that matters most:

1. failures that used to score as success (MCP unknown-tool, arg validation, ENOENT,
   EPERM, transport exceptions, JSON error payloads, sandbox crashes);
2. infra that used to be blamed on the agent (broken pipe, socket hang up, proxy,
   server disconnected, router not-connected/offline, Vertex quota);
3. **over-detection guard** — real successes that must stay successes: a grep hit quoting
   `ConnectionResetError`, `TIMEOUT_503 = 30` in a config file, prose about retry
   timeouts, healthy JSON envelopes, `NO MATCHES`, and a clean `list_mcp_tools` dump.

Group 3 is not optional. The module's own docstring says it: *"miss an outage and the
neuron blames itself for someone else's server, over-detect one and real incompetence
goes unlearned."* Every regression in §4 was a group-3 failure. The suite currently lives
only in the session transcript — **it should be lifted into `tests/`.**

---

### Open

1. **Persist the §5 suite as a real test file.** It is the only thing standing between
   this vocabulary and the next well-meaning pattern addition.
2. **`mcp_client.py:83`** still has the colon-less `f"Error executing {name}: {str(e)}"`.
   It is the deprecated path (`llm_engine.py:1318`, only live when `mcp_client` is set)
   so it was left alone, but it has the same defect §2.1 fixed in the router.
3. **`secure_runner.py:89`** — `SECURE RUNNER ERROR:` is detected (it contains `ERROR:`)
   but attributed to the agent, which is wrong when the cause is a missing docker binary
   (`[WinError 2] The system cannot find the file specified`). That string is
   indistinguishable from a legitimate file-not-found action failure, so it cannot be
   fixed in shared vocabulary — it needs docker-absent detection at the source.
4. **`__duplicate_call_guard` is hard-coded to `action_fail`** (`llm_engine.py:1500`).
   A retry storm triggered by an upstream outage therefore books as repeated agent
   incompetence. Not investigated.
5. The runtime path of the new `query_second_brain` branch has **not** been exercised —
   it needs a request with `expert_model_id` set. Only its static shape was verified
   against `call_sub_agent` parameter-by-parameter.
