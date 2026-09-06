# Extension Guide: Plugins, Skill Trees & the MCP Router

Q is built with a decoupled architecture. The core logic of `llm_engine.py` is thin; the actual capabilities of the agents are provided by a plugin system, a modular skill tree, and a multi-server MCP router.

## 🔌 The Plugin System (`plugins/`)

Python modules loaded on startup by `plugin_manager.py`. Each hooks into one or more points in the engine's lifecycle.

*   **Gatekeepers** validate or block requests (`firewall_plugin.py`).
*   **Enrichers** modify the prompt before it hits the model (`memory_plugin.py`, which also logs telemetry events with reflection scores).
*   **Observers** perform async side effects such as logging and distillation.
*   **Tool providers** register new capabilities for the agents (`mcp_router_plugin.py`, `skill_tree_plugin.py`, `api_parser.py`).

`persona_tool_map.json` in the same directory controls which personas can see which tools.

## 🌳 The Skill Tree (`skills/`)

Agent capabilities are not static. They are lazy-loaded based on the skill branch activated in the UI or by the agent's context. Each branch is a directory under `skills/`:

*   `skills/scout/` — environmental awareness and discovery tools.
*   `skills/architect/` — system planning and architectural logic.
*   `skills/scribe/` — documentation and knowledge-graph management.

To add a capability, add it to the relevant branch directory and provide the corresponding function in a plugin. Orchestration lives in `skill_orchestrator.py`.

## 🔀 The MCP Router (`mcp_router.py`)

Q talks to tools over the Model Context Protocol through a registry of named server processes rather than a single client. Each server is identified by a short name, and its tools are exposed to the model with the namespace prefix `server__tool` so there are no collisions across servers.

```python
from mcp_router import get_router

router = get_router()
tools  = router.list_all_tools_sync()                # flat list, namespaced
result = router.route_call_sync("lab__execute_python_lab", {"code": "print(1)"})
```

Servers are launched as stdio subprocesses. `mcp_server.py` is Q's own MCP server, exposing the sandboxed lab to any MCP client, and `mcp_client.py` holds the lower-level session handling. Every tool reached through the router still passes the governance gate and the untrusted envelope; the router changes where tools come from, not what they are allowed to do.

Tests: `tests/test_router_direct.py`, `tests/test_live_mcp_tool_execution.py`.

## 🤝 Contribution & Sovereign Design

Q is designed for a single-operator environment, but the modular structure allows expansion:

*   **New plugins:** add features without touching the core engine.
*   **New personas:** define manifests in `personas.json` (see `personas.json.example`); persona text files live in `personas/`.
*   **New tool servers:** register an MCP server with the router rather than wiring a tool directly into the engine.
*   **SafeWorkspace:** always use `workspace_engine.py` for file or code operations to preserve sandbox integrity.
