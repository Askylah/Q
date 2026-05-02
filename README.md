# App/Q- To run the program you need two powershell instances, Locate your app and type example: cd C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-merged then type py main.py or python main.py then for the other you do example: cd C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-merged\vite-project and npm run dev

You can name your app folder whatever you want though, so the path name may vary. Rick is integrated for now, fully deletable if you prefer. We have group chat logic. Thats the latest thing. To get started you want to click settings and input an API key. Its pretty straight forward. I don't reccommend messing with parameters if you don't understand them. There is more refinement to come. As of now I am a one woman show. This isn't just a roleplay app....Its an IDE but thats got a fair bit more work. There is also a security architecture I built but it relies on docker. Its entirely optional. Parts of the firewall might still work. It can be turned off though under maintenence in settings. Its your system after all.


---

A private AI companion sandbox built with **FastAPI + Vite + React**. Houses multiple distinct AI personas with persistent memory, lorebook-driven knowledge graphs, and multi-model routing across OpenRouter, Google, Anthropic, and OpenAI. An IDE...its also an IDE in progress, you will see.

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python / FastAPI |
| Frontend | Vite + React (JSX) |
| Database | SQLite (`database.py`) |
| Vector Store | sentence-transformers (`all-MiniLM-L6-v2`) |
| LLM Routing | OpenRouter + native Google/Anthropic/OpenAI |

---

## Core Systems

### 🧠 Memory Architecture
- **Observational Memory** — autonomous reflection that runs every N turns, distilling conversation into dense observations
- **Deep Memory** (`memory_engine.py`) — associative graph with emotional weighting and temporal decay
- **Conversation Summaries** — rolling compressed history

### 📚 Lorebook / Zettel Knowledge Graph (`zettel_engine.py`)
Invisible to the user but active on every message. Write path:
1. User adds a lore entry via the Workshop UI
2. Text is chunked (~256 tokens), embedded, and entity-extracted via a flash LLM pass
3. Nodes and edges stored in SQLite with FTS5 index

Read path (zero LLM cost per message):
1. Query is embedded + tokenized
2. Hybrid retrieval: vector cosine similarity + FTS5 keyword OR search
3. Reciprocal Rank Fusion merges both result sets
4. 1-hop graph expansion pulls in linked nodes
5. Top context injected into the system prompt as `[KNOWLEDGE_GRAPH]`

### 🔀 Multi-Model Routing (`mode_engine.py` + `llm_engine.py`)
Detects message intent (immersive RP, technical, creative) and routes to the appropriate model tier (base flash vs expert pro).

## 🛡️ Optional Security Architecture

This sandbox is designed for high-fidelity experimentation. To protect your hardware, it includes a multi-layered security stack that can be toggled via **Settings > Maintenance**.

### 1. Semantic Firewall (`firewall.py`)
A dual-pass semantic intent scanner. It analyzes both **inbound user messages** and **outbound retrieval results** (RAG/Zettel) for malicious instructions or prompt injection attempts. If a violation is detected, the system automatically redacts the context or drops the connection before it reaches the expert model.

### 2. Root-Jailed Workspace (`workspace_engine.py`)
The IDE/Workspace integration uses a jailed filesystem interface. 
- **Traversal Protection:** All file operations are resolved against a strict workspace root. Any attempt to escape via `../` traversal or symlinks triggers a `SecurityViolation`.
- **Docker Ready:** Designed to run inside an isolated Docker container for total OS-level isolation while maintaining a persistent code sandbox.

### 3. API Blackholing (The Blackhole Handler)
The FastAPI backend is hardened against scanners and probing. If a request payload contains even a single unexpected key, the server returns an **Nginx-style 444 (No Response)**. It doesn't reject with an error; it simply drops the connection, making the API invisible to automated probing tools.

### 4. Semantic Firewall Toggle
You own the hardware. If the firewall interferes with your research or "red teaming" experiments, it can be completely disabled in the **Maintenance** tab of the app settings.

---

### 📄 On-Demand Loader (`on_demand_loader.py`)
Attaches large reference documents to personas. Splits into modules and injects only the most semantically relevant sections per message.

---

## Setup

```bash
# Backend
pip install -r requirements.txt
cp .env.example .env   # fill in your API keys
python main.py

# Frontend
cd vite-project
npm install
npm run dev
```

---

## Project Structure

```
PersonaApp_Sandbox/
├── main.py              # FastAPI entrypoint + all API endpoints
├── llm_engine.py        # Context assembly + LLM streaming
├── zettel_engine.py     # Auto-Zettel knowledge graph engine
├── database.py          # SQLite schema + all DB methods
├── memory_engine.py     # Deep Memory (associative graph)
├── rag_engine.py        # Vector store (sentence-transformers)
├── mode_engine.py       # Multi-model routing / intent detection
├── on_demand_loader.py  # Document injection module
├── firewall.py          # Semantic security layer
├── personas/            # Character system prompts (.txt)
├── personas.json        # Built-in persona manifest
├── plugins/             # Observational memory plugin
├── vite-project/        # React frontend (Vite)
│   └── src/
│       ├── App.jsx      # Main UI
│       ├── api.js       # Frontend API client
│       └── App.css      # Styles
└── requirements.txt
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

```
OPENROUTER_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
```
