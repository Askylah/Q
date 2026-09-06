# Q

**Q** is an architecture for an autonomous agent mind: neuromodulator-driven motivation, a decaying graph memory, and a daemon that keeps thinking between conversations. Because a thing like that has to be allowed to act, Q is also built around zero-trust execution: sandboxed tools, AST-gated code, injection screening on every inbound payload, and human approval on anything that matters.

It is model-agnostic (Claude, Gemini, OpenAI-compatible endpoints, OpenRouter, Vertex), runs locally, and ships as a FastAPI backend with a React front end.

---

## 🏛️ System Architecture & Pillars

### 1. Cognition (The Consciousness Daemon)
A background daemon runs a homeostasis sweep over every active persona whether or not anyone is talking to it. What it does on each cycle is decided by drive state, not by a schedule.
*   **Neuromodulator State:** Two variables per persona, on two timescales. *Tonic* is a slow baseline that sets exploration versus consolidation. *Phasic* is a fast spike envelope fired by reward-prediction-error events: expected versus observed, so predicted novelty is not rewarding. Redis-backed with an in-memory fallback.
*   **Gap Picker:** Scans the memory graph for entropic gaps (isolated clusters, low link density) and semantic contradictions (token-overlap prefilter, then an NLI gate on a strong model). Resolutions are written back to the graph and fed into the next sweep.
*   **Rest Gate:** Low tonic shuts the gap picker. If the gate is shut and there is nothing to integrate, the daemon rests and writes no monologue. This replaced an idle branch that, in July 2026, produced 69 of 74 monologues as unprompted self-interrogation and stored every one of them as memory.
*   **Dissonance Cap and Telemetry:** Daemon-authored writes are rate-limited per persona per window, by kind. Every daemon event is logged with a reflection score and surfaced in the UI.

Deep dive: [Cognition](docs/COGNITION.md). Bugs, dead ends and retractions: [lab notes](lab_notes/).

### 2. Memory (Auto-Zettel Graph & Temporal Decay)
Context management is a hybrid graph memory designed to prevent token bloat and stale-context poisoning.
*   **Auto-Zettel Knowledge Graph:** Lore and observations are chunked, embedded, entity-extracted by a flash-model pass, and auto-linked into a graph stored in SQLite.
*   **Zero-Cost Read Path:** Every message runs vector search plus FTS5 keyword search, fuses them with reciprocal rank fusion, expands one hop across the graph, and injects the subgraph. No LLM call on the read path.
*   **Temporal Decay:** Memories age and drop out of the active window. The decay rate is modulated by tonic dopamine; memories written during a phasic spike receive an importance bonus.
*   **Reflector Threads:** Background observers compress long transcripts into structured `<agent_reflections>` and `<conversation_summary>` blocks asynchronously.

### 3. Strict Physics (Zero-Trust Execution)
Agents that can run code run it inside a root-jailed, network-isolated Docker container, and nothing they emit is trusted on the way back in.
*   **Padded Room:** Hard limits on RAM (512MB), CPU (1), and PIDs (64). `--network none`. A 15-second SIGKILL cap on every process.
*   **Input Firewall:** Payloads are normalized (NFKC, hex and Base64 unwrapped) and scanned by a syntactic role-jacking filter, then by a semantic hazard classifier. Two channels: untrusted content gets the paranoid threshold, the operator's own messages get a high-precision rule set, because a single strictness level was dropping "summarize the following" as an injection attempt.
*   **Governance Gates:** Tools are classified INFO, KINETIC or DESTRUCTIVE by what they do, never by their arguments. Unregistered tools fail closed as DESTRUCTIVE. The registry is validated at boot. KINETIC and above pause for human approval, with a file-tree diff of the simulated effect shown first.
*   **Output Gate:** Generated code passes an AST linter that enforces the sandbox calling convention and blocks the common escapes. The linter's own docstring states it is not a security boundary; the container is.

Deep dive: [Security & Sandbox](docs/SECURITY_AND_SANDBOX.md).

### 4. Simulation & Modes
For alignment testing, creative work and tabletop orchestration, Q hosts multi-model rooms with stateful personas.
*   **Model-Agnostic Rooms:** Mix models in one group session under distinct namespaces.
*   **Observer Mode:** A persona watches silently and injects a single reflection at the end of each round.
*   **Mode Engine:** Classifies each user turn into one of four operating modes (immersive roleplay, technical utility, creative writing, experiential utility) and swaps the governing rule set accordingly.
*   **Cognitive Friction:** A documented anti-sycophancy doctrine. Personas are structurally forbidden from mirroring the user's framing, which closes a real permission-hijack path. See [cognitive_friction_schema.md](cognitive_friction_schema.md).

Deep dive: [Roleplay & Lore](docs/ROLEPLAY_AND_LORE.md).

---

## ✅ Verification

**Tests** live in `tests/` and cover the daemon lock, novelty reward, extraction firewall, strict security, workspace paths, zettel retrieval and scaling, MCP routing, and live tool execution.

**Lab notes** live in `lab_notes/`. Each one tracks a single problem from first symptom to fix, with a status banner, numbered sections, and explicit retractions where an earlier session's analysis turned out to be wrong.

| Note | Subject |
|---|---|
| `entropic_gap_livelock.md` | The daemon livelocked on an entropic gap. Nine sessions, one major retraction. |
| `da_neuron_log.md` | The dopamine system from first live traffic to the novelty channel and rest gate. |
| `tool_outcome_attribution.md` | Failed tool calls were being scored as successes. The first fix caused four regressions. |
| `storage_hardening.md` | The database sat inside a writable tool root. |

---

## 🚀 Quick Start

### Prerequisites
*   **Python 3.14+**
*   **Node.js & npm**
*   **Docker Desktop** (required for the sandboxed Workspace Engine)
*   **Redis** (optional; everything degrades to in-memory fallbacks without it)

### Setup

1. **Clone the repository.**
2. **Install backend dependencies:**
   ```bash
   py -m pip install -r requirements.txt
   ```
3. **Install frontend dependencies:**
   ```bash
   cd vite-project
   npm install
   ```

### Running the App

1. **Launch the backend** (the consciousness daemon starts with it, under a single-instance lock):
   ```bash
   # From the project root
   py main.py
   ```
2. **Launch the frontend:**
   ```bash
   cd vite-project
   npm run dev
   ```
3. **Configure:** Open the **Settings** tab in the UI to enter API keys (Google, Anthropic, OpenRouter) and select target models.

---

## 📖 Deep Dives
*   [**Cognition**](docs/COGNITION.md) — The daemon, neuromodulator state, gap picker, rest gate, telemetry.
*   [**Security & Sandbox**](docs/SECURITY_AND_SANDBOX.md) — Containment, the two-channel firewall, governance, output gates.
*   [**Roleplay & Lore**](docs/ROLEPLAY_AND_LORE.md) — Namespaces, group chats, memory, modes.
*   [**Extension Guide**](docs/EXTENSION_GUIDE.md) — Plugins, the skill tree, the MCP router.
*   [**Cognitive Friction Schema**](cognitive_friction_schema.md) — The anti-mirroring doctrine.
*   [**Lab Notes**](lab_notes/) — How the hard bugs were actually found.
