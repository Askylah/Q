# Q

**Q** is a hardened, model-agnostic multi-agent execution container designed for adversarial agentic research, security-first tool execution, and immersive simulation environments. It combines a dynamic React interface with a sandboxed "Strict Physics" backend, allowing developers to safely unleash autonomous, high-entropy personas in resource-constrained, secure environments. *Side note:* On PC, it is basically a model-agnostic Antigravity on steroids with some dedicated roleplay elements and much stronger security.

---

## 🏛️ System Architecture & Pillars

### 1. Strict Physics (Sandboxed Security)
Q is engineered around the principle of zero-trust agent execution. Autonomous agents run inside a root-jailed, network-isolated "Padded Room" container to prevent arbitrary host execution.
*   **Docker Containment:** Hard limits on compute resources (512MB RAM, 1 CPU, 64 PIDs limit).
*   **Temporal Guillotine:** Automatic SIGKILL enforcement for runaway processes (30s execution cap).
*   **Governance Gates:** Human-in-the-loop approval routing for sensitive tool calls (e.g., file writes, system edits) with live environmental diff analysis before permission is granted.
*   **Layer A/B Firewalls:** Real-time syntax and semantic classifiers checking inputs for role-jacking or prompt injection vectors before the payload is sent to cloud APIs.

### 2. Auto-Zettel Memory & Temporal Decay
Context management in Q utilizes a hybrid, graph-based memory structure designed to prevent token bloat and stale context poisoning.
*   **Auto-Zettel Knowledge Graph:** A 1-hop relational lorebook powered by a hybrid Vector + SQLite FTS5 database.
*   **Temporal Memory Decay:** Memories and dense observations age dynamically, decaying in weight or dropping out of the immediate context window to simulate organic memory retention.
*   **Token-Optimizing Observers:** Background Reflector threads compress long chat transcripts into structured `<agent_reflections>` and `<conversation_summary>` blocks asynchronously.

### 3. Immersive Simulation & Narrative Inversion
For agentic alignment testing, creative scenarios, and tabletop orchestration, Q hosts complex multi-model rooms.
*   **Direct-Wire Inversion:** A proprietary bypass architecture that uses pre-fill handshakes and axiomatic framing to enforce strict persona alignment and suppress system disclaimer drift.
*   **Model-Agnostic Rooms:** Mix and match models (Claude, Gemini, GPT) in a single unified group session with distinct namespaces.
*   **Observer Mode:** Allows inactive agent profiles to observe transcripts asynchronously and inject reflections.

---

## 🚀 Quick Start

### Prerequisites
*   **Python 3.14+**
*   **Node.js & npm**
*   **Docker Desktop** (Required for the sandboxed Workspace Engine)

### Setup

1. **Clone the repository.**
2. **Install Backend Dependencies:**
   ```bash
   py -m pip install -r requirements.txt
   ```
3. **Install Frontend Dependencies:**
   ```bash
   cd vite-project
   npm install
   ```

### Running the App

1. **Launch the Backend:**
   ```bash
   # From the project root (e.g. C:\path\to\your\project\PersonaApp-merged)
   py main.py
   ```
2. **Launch the Frontend:**
   ```bash
   cd vite-project
   npm run dev
   ```
3. **Configure:** Open the **Settings** tab in the UI to input your API keys (Google, Anthropic, OpenRouter) and select your target models.

---

## 📖 Deep Dives
*   [**Security & Sandbox**](docs/SECURITY_AND_SANDBOX.md) — Dive into Docker resource clamping, firewalls, and output gates.
*   [**Roleplay & Lore**](docs/ROLEPLAY_AND_LORE.md) — Mastering namespaces, group chats, and temporal memories.
*   [**Extension Guide**](docs/EXTENSION_GUIDE.md) — Custom plugins, tools, and the dynamic Skill Tree.
