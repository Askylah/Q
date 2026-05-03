# Persona App — The Strict Security Sandbox

To run the program:
1. **Backend**: `cd C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-merged` -> `py main.py`
2. **Frontend**: `cd vite-project` -> `npm run dev`

---

## 🏛️ Architectural Doctrine: "Strict Physics"

This sandbox has transitioned from a probabilistic security model to a **Deterministic Security Triad**. It is designed for high-fidelity experimentation with untrusted code and adversarial agentic swarms.

### 1. The Physics Layer (Docker Sandboxing)
The code execution engine (`workspace_engine.py`) no longer relies on "hope." It enforces kernel-level limits via Docker:
- **Resource Clamping**: 512MB RAM, 1.0 CPU, and a 64-PID limit (Fork-bomb protection).
- **Network Isolation**: Zero inbound/outbound networking (`--network none`).
- **Temporal Guillotine**: Hard 30-second SIGKILL for all processes.
- **Root-Jailed Filesystem**: Absolute path-prefix validation using `pathlib` ensures zero directory traversal.

### 2. The Cognitive Layer (Skill Tree Orchestrator)
To prevent "Model Drift" and "Token Bloat," capabilities are now modularized. An agent starts with **Zero Modification Power**.
- **Lazy-Loading**: Tools and expert prompts are only injected when a specific "Skill Branch" is activated.
- **Branches**: 
    - **Scout**: Discovery and environmental awareness.
    - **Architect**: High-level system planning and logic.
    - **Scribe**: Knowledge Graph linking and documentation.

### 3. The Governance Layer (Approval Gates)
Every action is categorized by a **Danger Level** (INFO, KINETIC, DESTRUCTIVE).
- **Human-in-the-Loop**: Kinetic actions (like writing files) trigger a `control: approval_required` signal, halting execution until the user provides a "Go" signal in the UI.

---

## 🔌 Dynamic Plugin System

The core logic (`llm_engine.py`) is now decoupled from feature implementation. The `PluginManager` handles:
- **GATEKEEPERS**: Semantic firewalls and validation.
- **ENRICHERS**: Context and prompt modification.
- **OBSERVERS**: Async side-effects like memory distillation and audit logging.
- **TOOL_PROVIDERS**: Dynamic injection of skill-based capabilities.

---

## 🧠 Memory & Knowledge Graph
- **Auto-Zettel Knowledge Graph**: Hybrid retrieval (Vector + FTS5) with 1-hop graph expansion.
- **Observational Memory**: Autonomous reflection that distills conversations into dense observations every N turns.
- **Deep Memory**: Associative graph with emotional weighting and temporal decay.

---

## 🛠️ Stack & Setup

| Layer | Tech |
|---|---|
| **Backend** | Python 3.14+ / FastAPI |
| **Frontend** | Vite + React (JSX) |
| **Security** | Docker Engine (Required for execution) |
| **Database** | SQLite + FTS5 |
| **Models** | OpenRouter / Anthropic / Google / OpenAI |

### Quick Start
```bash
# Backend
pip install -r requirements.txt
# Ensure Docker is running for the Workspace Engine
python main.py

# Frontend
cd vite-project
npm install
npm run dev
```

---

## 🤝 Collaboration
This project is currently moving from a "One Woman Show" to a collaborative modular architecture. 
- **Plugins**: New features should be added as standalone plugins in the `/plugins` directory.
- **Skills**: New agentic capabilities should be defined in `skills.json`.
- **Security**: Never bypass the `SafeWorkspace` engine for file or code operations.

---

## Project Structure
```
PersonaApp/
├── main.py              # API entrypoint
├── llm_engine.py        # Dynamic orchestration engine
├── workspace_engine.py  # Strict Docker sandbox
├── skill_orchestrator.py# Skill tree management
├── governance_manager.py# Danger-level & Approval gates
├── database.py          # SQLite persistence
├── plugins/             # Decoupled features (Firewall, Memory)
├── skills.json          # Skill tree definitions
├── vite-project/        # React UI
└── personas/            # Persona manifests & prompts
```
