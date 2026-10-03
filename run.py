"""
run.py — Universal root entrypoint for Q server.
Launches the FastAPI application and initializes the Consciousness Daemon.
"""

import os
import sys

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_ROOT_DIR, "backend")

if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(1, _ROOT_DIR)

if __name__ == "__main__":
    import uvicorn
    print("[SYSTEM] Starting Q Server from backend/main:app...", flush=True)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, http="h11", reload=True, reload_excludes=["*labs*", "*labs\\*", "labs\\*", "*lab_exec*"])
