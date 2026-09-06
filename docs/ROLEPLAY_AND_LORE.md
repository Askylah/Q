# Roleplay & Lore Management

Q is designed to be more than a chatbot. It is a storyteller's workbench. This guide covers how to use the cognitive systems to create immersive, stateful worlds.

## 🎭 Persona Management
Every persona in Q is defined by a manifest that dictates personality, knowledge access, and mode sensitivity.
*   **System prompts:** Hardcoded character instructions, layered on top of `personas/global_rules.txt`.
*   **On-demand knowledge:** Specific text files or documentation a persona can pull into context when needed.
*   **Custom avatars:** Any emoji or image as a persona's visual identity in the chat history.

## 🎚️ Modes (`mode_engine.py`)
Each user turn is classified to detect a shift away from the persona's default operating mode. Modes decide which global rule set governs the output:
*   **immersive_rp:** word ceilings enforced, one-beat rule, tonal contrast available.
*   **technical_utility:** word ceilings suspended, task completion first, code formatting allowed.
*   **creative_writer:** word ceilings suspended, multi-beat scenes, narrative perspective unlocked.
*   **experiential_utility:** soft ceilings, warmth permitted, engagement-loop priority.

Detection is pattern-based and intentionally broad: a false positive is cheaper than missing a mode shift.

## 🧱 Cognitive Friction
Personas are structurally forbidden from mirroring the user's vocabulary, formatting, or rhetorical framing. This is a security measure disguised as characterization: a model that echoes an attacker's framing will, on the next turn, parse its own echo as truth. The doctrine and its implementation layers are documented in [cognitive_friction_schema.md](../cognitive_friction_schema.md).

## 👥 Group Chat & Namespaces
Group Chat orchestration lets multiple personas interact in one session.
*   **Session namespaces:** Run distinct conversations with the same characters. Changing the session name (`DnD_Campaign_1` vs `Red_Team_Alpha`) creates isolated database tables for that thread.
*   **Observer mode:** A persona watches the conversation silently and injects a single, high-level reflection at the end of each round.
*   **Turn limits:** Control pacing by setting how many times each persona responds before the round ends.

## 🧠 The Auto-Zettel Knowledge Graph (`zettel_engine.py`)
The memory of Q is a hybrid architecture designed for narrative consistency.
*   **Write path** (once per entry): lore is chunked, embedded, and passed through a flash-model step that extracts entities and `[[CATEGORY-NAME-###]]` links. Nodes and edges land in SQLite and are cross-linked against existing nodes.
*   **Read path** (every message, zero LLM cost): the query is embedded, vector search and FTS5 keyword search run in parallel, the results are fused with reciprocal rank fusion, the graph is expanded one hop, and the resulting subgraph is injected into context.
*   **Observational memory:** Conversation turns are distilled into observations that enter the same graph and decay over time. Decay is modulated by the persona's dopamine state; see [Cognition](COGNITION.md).
*   **Lorebooks:** Upload whole world-building documents into a persona's lorebook and they become graph nodes.

## 🎲 Running Campaigns
To use Q for a tabletop RPG or a LARP:
1.  **Define your cast** in the workshop.
2.  **Initialize a namespace** in the Group Chat config (e.g. `Shadowrun_Session_0`).
3.  **Use an Observer** as a co-DM who summarizes party status or world-state changes.
4.  **Save to lore:** Use the Scribe skill branch to make agents document important plot points back into the knowledge graph.
