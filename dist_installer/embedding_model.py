"""
embedding_model.py — Standalone SentenceTransformer Singleton Loader
Provides thread-safe, cached access to the shared embedding model across all subsystems.
Decoupled to eliminate circular dependency chains between zettel, memory, and firewall.
"""

import threading
import logging

logger = logging.getLogger("embedding_model")

_SHARED_MODEL = None
_MODEL_LOCK = threading.Lock()
MODEL_NAME = "all-MiniLM-L6-v2"


def get_shared_model():
    """
    Returns the shared SentenceTransformer model instance.
    Lazily initialized on first call in a thread-safe manner.
    """
    global _SHARED_MODEL
    if _SHARED_MODEL is None:
        with _MODEL_LOCK:
            if _SHARED_MODEL is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    print(f"[EMBEDDING_MODEL] Initializing shared SentenceTransformer ({MODEL_NAME})...")
                    _SHARED_MODEL = SentenceTransformer(MODEL_NAME)
                except Exception as e:
                    logger.error(f"[EMBEDDING_MODEL ERROR] Failed to load {MODEL_NAME}: {e}")
                    _SHARED_MODEL = None
    return _SHARED_MODEL
