# Security & The "Strict Physics" Engine

The Q backend is built on the principle of **Deterministic Security**. When an agent generates code or performs a file-system operation, it is not running on your computer—it is running inside a root-jailed, resource-clamped "Padded Room."

## 🛡️ The Defense-in-Depth Pipeline

Q employs a rigorous three-layer filtering system to neutralize prompt injections, role-jacking, and cognitive friction loops.

### Layer A: Data Isolation (The Padded Room)
The `workspace_engine.py` and `secure_runner.py` enforce kernel-level limits to ensure untrusted code cannot escape or exhaust system resources.
*   **Resource Clamping:** Hard caps on RAM (512MB), CPU (1.0), and PIDs (64) to prevent exhaustion and fork-bombs. All containers use `--network none` for absolute isolation.
*   **Temporal Guillotine:** Every process has a hard 30-second window. If it fails to complete, it is hit with a SIGKILL.
*   **Dynamic Truncation:** To prevent context flooding, execution output is hard-capped (default 8KB). Anything larger is cleanly sliced.
*   **Source Envelope:** Raw outputs from the sandbox are immediately wrapped in `<untrusted_tool_output>` XML tags at the source.

## 🧱 The Governance Layer
Beyond the sandbox, Q uses a semantic governance layer to categorize agent intent.

*   **Danger Levels:**
    *   **INFO:** Reading public logs or directory listings.
    *   **KINETIC:** Writing to files, modifying code, or running scripts.
    *   **DESTRUCTIVE:** Deleting directories or purging databases.
*   **Approval Gates:** Any action categorized as **KINETIC** or higher triggers a `control: approval_required` signal. The agent pauses execution until a human clicks "Go" in the UI.

### Layer B: Structural Syntax Firewall
Rather than relying solely on probabilistic semantic intent, `firewall.py` enforces deterministic, zero-tolerance syntactic scanning on all incoming tool data.
*   **Role-Jacking Defense:** Instantly intercepts and drops payloads attempting to overwrite the system prompt (e.g., `System:` or `From now on, act as...`).
*   **Imperative Proximity Scanning:** Text is strictly normalized (stripping invisible characters, newlines, and carriage returns). If an imperative verb targeting system logic (e.g., "ignore previous instructions") is detected within a 5-word proximity gap, the payload is neutralized.

### Layer C: The Universal Untrusted Envelope
Located in `llm_engine.py`, this is the final failsafe before data reaches the model.
*   Regardless of the source (Docker execution, API calls, web searches, or local files), **ALL** external data is mandatorily wrapped in `<untrusted_tool_output>` tags. This ensures the LLM mathematically separates structural instructions from untrusted data streams.

## 🧪 Red-Teaming Usage
Q is uniquely suited for red-teaming because you can simulate adversarial swarms. By deploying "Attacker" and "Defender" personas in a shared namespace, you can observe how agents attempt to bypass security layers or discover vulnerabilities in a zero-risk environment.
