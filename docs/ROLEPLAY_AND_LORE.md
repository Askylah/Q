# Roleplay & Lore Management

Q is designed to be more than a chatbot—it is a storyteller's workbench. This guide covers how to leverage the cognitive systems to create immersive, stateful worlds.

## 🎭 Persona Management
Every persona in Q is defined by a manifest that dictates their personality, knowledge access, and "mode sensitivity."
*   **System Prompts:** Hardcoded character instructions.
*   **On-Demand Knowledge:** Specific text files or documentation that a persona can "pull" into their context when needed.
*   **Custom Avatars:** Use any emoji or image to define a persona's visual identity in the chat history.

## 👥 Group Chat & Namespaces
The Group Chat orchestration allows up to 4 personas to interact simultaneously.
*   **Session Namespaces:** You can have multiple distinct conversations with the same characters. By changing the "Session Name" (e.g., `DnD_Campaign_1` vs `Red_Team_Alpha`), the system creates isolated database tables for that specific thread.
*   **Observer Mode:** A specialized role where a persona silently watches the conversation and provides a single, high-level reflection at the end of each round.
*   **Turn Limits:** Control the pacing of the conversation by setting how many times each persona responds before the round ends.

## 🧠 The Auto-Zettel Knowledge Graph
The "Memory" of Q is a hybrid architecture designed for narrative consistency.
*   **Observational Memory:** The system autonomously distills conversation turns into "Observations." These are stored in a Knowledge Graph and retrieved during future chats.
*   **Lorebooks:** You can upload entire world-building documents into a persona's lorebook. The engine uses a combination of Vector similarity and FTS5 (Full-Text Search) to find the most relevant lore for the current conversation.
*   **Zettel Engine:** Inspired by the Zettelkasten method, the engine links related pieces of information, allowing personas to "connect the dots" across thousands of messages.

## 🎲 Running Campaigns
To use Q for a Tabletop RPG or a LARP:
1.  **Define your cast** in the workshop.
2.  **Initialize a Namespace** in the Group Chat config (e.g., `Shadowrun_Session_0`).
3.  **Use an Observer** to act as a "Co-DM" who summarizes the party's status or world-state changes.
4.  **Save to Lore:** Use the "Scribe" skill (if enabled) to force agents to document important plot points back into the Knowledge Graph.
