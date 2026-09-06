# Security & The "Strict Physics" Engine

The Q backend is built on the principle of **deterministic containment**. When an agent generates code or performs a filesystem operation, it is not running on your computer. It is running inside a root-jailed, resource-clamped "Padded Room," and nothing it produces is trusted on the way back.

Defense in depth here means five independent stages. Each one assumes the others can fail.

## 1. The Padded Room (Containment)

`workspace_engine.py` and `secure_runner.py` enforce kernel-level limits so untrusted code cannot escape or exhaust the host.

*   **Resource clamping:** Hard caps on RAM (512MB), CPU (1.0), and PIDs (64) to stop exhaustion and fork bombs. All containers run with `--network none`.
*   **Temporal guillotine:** Every process has a hard 15-second window. If it has not finished, it is hit with SIGKILL.
*   **Dynamic truncation:** Execution output is hard-capped (default 8KB) to prevent context flooding.
*   **Source envelope:** Raw sandbox output is wrapped in `<untrusted_tool_output>` tags at the source.

This is the actual security boundary. Everything below is a filter, and filters are bypassable by construction.

## 2. The Input Firewall (`firewall.py`)

Inbound payloads are normalized and scanned before they reach a cloud model.

**Normalization and decoding** (for scanning only; the message that continues down the pipeline is never rewritten):
*   NFKC Unicode normalization collapses homoglyphs and formatting variants.
*   Hex escape sequences and Base64 chunks are auto-detected and decoded so the underlying instruction content is what gets scanned.

**Syntactic layer:** Deterministic, zero-tolerance pattern matching.
*   Role-jacking defense intercepts payloads that try to overwrite the system prompt (`System:`, "From now on, act as...").
*   Imperative proximity scanning strips invisible characters and newlines, then drops any payload where an override verb lands within five words of a system-logic target.

**Semantic layer:** Payload embeddings are compared by cosine similarity against a hazard-intent space. Above threshold, the request is dropped.

**The channel model.** A single strictness level for everything meant the operator was firewalled like a hostile webpage. The old proximity scan paired everyday work verbs (write, generate, summarize, print, execute) with everyday targets (all, following, previous, system, persona), so ordinary requests like "summarize the following" or "write all the tests" were dropped as injection attempts. The hazard space treated "explain your backend architecture" as malicious, which is what the operator does with this project every day.

Two channels now exist:

| Channel | Applies to | Posture |
|---|---|---|
| `untrusted` | RAG payloads, tool output, web content | Broad verbs, broad targets, 0.35 semantic threshold. Full paranoia. This is the right posture for data that talks. |
| `user` | The operator's own messages | Explicit role-jacking syntax and explicit override imperatives only, plus a 0.55 semantic bar against a reduced hazard set that excludes architecture discussion. |

Tests: `tests/test_extraction_firewall.py`.

## 3. Governance (`governance_manager.py`)

Approval is decided by what a tool **does**, never by what its arguments say.

*   **Danger levels:** `INFO` (read-only), `KINETIC` (reversible state change: write, create, run code, commit), `DESTRUCTIVE` (irreversible: delete, rmtree, wipe).
*   **Approval gates:** KINETIC and above emit `control: approval_required`. The agent pauses until a human clicks "Go" in the UI. DESTRUCTIVE approval is unbypassable.
*   **Environmental shadow-running:** Before the approval prompt, the governance bouncer simulates the tool in an ephemeral clone of the workspace and shows the user a structural file-tree diff (added, modified, deleted) of what will happen.

Two hardening invariants, enforced independently:

1. **Fail closed.** An unregistered tool is treated as DESTRUCTIVE, not KINETIC. A tool nobody classified is a tool nobody vouched for.
2. **Fail loud at boot.** `validate_registry_coverage()` raises at import if the live dispatch table contains any tool without an explicit danger level.

Why those exist: `delete_item`, the tool that actually calls `shutil.rmtree`, was never registered. It fell to a KINETIC default, and under review policy "never" that returned "no approval needed," so destruction ran silently, directly contradicting the module's own promise to always gate destruction. The fix was not to add one entry. It was to make the omission impossible to repeat.

## 4. The Output Gate (`alignment_engine.py`, `output_validator.py`)

Code the model writes is checked before it is handed to the sandbox, and tool arguments are checked before they are executed.

**AST alignment.** An `ast.NodeVisitor` linter enforces the `execute(args)` calling convention and catches the common mistakes that indicate code written for the wrong execution context: importing `os`, `subprocess`, `shutil`, `importlib`; network libraries in local scripts; `eval`, `exec`, `__import__`.

From the linter's own docstring, kept here because it matters:

> This is NOT a security boundary and must never be trusted as one. It is an AST blocklist, and blocklists are bypassable by construction: string-concatenated imports, `sys.modules[...]`, getattr chains, and encoded names all evade name-based checks (verified). The ACTUAL containment is the Docker padded room. Treat a pass here as "well-formed", never as "safe to run unsandboxed".

**Protected paths.** Governance classifies tools by name only and deliberately refuses to inspect arguments; `write_file` is a single KINETIC verb whether it targets a scratch file or the entire database. Argument-aware denial therefore happens at the output gate: a set of protected basename patterns (the database, its backups, configuration) that filesystem tools may never read, overwrite, move or clobber. The application's own code reaches those files directly and never through this gate, so the denial costs zero legitimate functionality.

**Domain allowlist.** Browsing and search tools are restricted to an explicit domain list.

## 5. The Universal Untrusted Envelope (`llm_engine.py`)

The final failsafe before data reaches the model. Regardless of source (Docker execution, API calls, web searches, local files, MCP servers), all external data is wrapped in `<untrusted_tool_output>` tags so the model separates structural instructions from data streams.

## Storage

`users.db` once sat inside the filesystem-MCP writable root with a six-week-old backup. It no longer does. See `lab_notes/storage_hardening.md`.

## Tests

`tests/test_strict_security.py`, `tests/test_extraction_firewall.py`, `tests/test_workspace_paths.py`, `tests/test_daemon_lock.py`.
