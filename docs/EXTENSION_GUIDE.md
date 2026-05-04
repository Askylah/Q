# Extension Guide: Plugins & Skill Trees

Q is built with a decoupled architecture. The core logic of the `llm_engine.py` is thin; the actual capabilities of the agents are provided by a dynamic plugin system and a modular skill tree.

## 🔌 The Plugin System
Located in `/plugins`, these Python modules are loaded on startup and can hook into the engine's lifecycle.

*   **Gatekeepers:** Plugins that validate or block requests (e.g., the Firewall).
*   **Enrichers:** Plugins that modify the prompt before it hits the LLM (e.g., injecting Memory).
*   **Observers:** Plugins that perform async side-effects (e.g., Logging, Distillation).
*   **Tool Providers:** Plugins that dynamically register new capabilities for the agents to use.

## 🌳 The Skill Tree (`skills.json`)
Agent capabilities are not static. They are lazy-loaded based on the "Skill Branch" activated in the UI or by the agent's context.

*   **Scout:** Environmental awareness and discovery tools.
*   **Architect:** System planning and architectural logic.
*   **Scribe:** Documentation and Knowledge Graph management.

To add a new capability, you define it in `skills.json` and provide the corresponding function in a plugin.

## 🤝 Contribution & Sovereign Design
While Q is designed for a single-operator environment, the modular structure allows for future expansion:
*   **New Plugins:** Add features without touching the core LLM logic.
*   **New Personas:** Define manifests in `personas.json`.
*   **SafeWorkspace:** Always use the `workspace_engine.py` for file or code operations to ensure sandbox integrity.
