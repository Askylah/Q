import os
import pickle
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

# Storage path
DB_PATH = "./knowledge_bases/vector_store.pkl"

# Singleton model to avoid reloading on every class instantiation
_SHARED_MODEL = None

def get_shared_model():
    global _SHARED_MODEL
    if _SHARED_MODEL is None and SentenceTransformer is not None:
        import sys
        sys.stderr.write("[RAG_ENGINE] Initializing SentenceTransformer (all-MiniLM-L6-v2)...\n")
        _SHARED_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    return _SHARED_MODEL

class PersonaRAG:
    def __init__(self):
        self.model = get_shared_model()
        if self.model is None and SentenceTransformer is None:
            import sys
            sys.stderr.write("[RAG_ENGINE] WARNING: sentence-transformers is not installed. RAG will not work.\n")
            
        self.data = self._load_data()
        self._update_index()

    def _load_data(self):
        """Load data from pickle file or return empty structure."""
        if os.path.exists(DB_PATH):
            try:
                with open(DB_PATH, "rb") as f:
                    data = pickle.load(f)
                    
                    if "embeddings" not in data:
                         data["embeddings"] = []
                         
                    # In case of old TF-IDF cache, clear embeddings if they aren't list/array
                    if data["embeddings"] is not None and len(data["embeddings"]) > 0:
                        if not isinstance(data["embeddings"], (list, np.ndarray)):
                             data["embeddings"] = []
                    return data
            except:
                pass
        return {"documents": [], "embeddings": [], "metadatas": []}

    def _save_data(self):
        """Save data to pickle file."""
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(DB_PATH, "wb") as f:
            pickle.dump(self.data, f)

    def _update_index(self):
        """Calculate embeddings for the currently loaded documents if missing."""
        if not self.model:
            return

        if self.data["documents"]:
            # Check if we need to regenerate embeddings
            if len(self.data.get("embeddings", [])) != len(self.data["documents"]):
                print("Computing semantic embeddings for RAG Knowledge Base...")
                self.data["embeddings"] = self.model.encode(self.data["documents"], convert_to_numpy=True).tolist()
                self._save_data()
            self.vectors = np.array(self.data["embeddings"])
        else:
            self.data["embeddings"] = []
            self.vectors = None

    def add_document(self, text, persona_name, username, source="manual"):
        """Add a document and rebuild the index."""
        MAX_DOC_CHARS = 5000 
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS] + "... [TRUNCATED]"
            
        self.data["documents"].append(text)
        self.data["metadatas"].append({
            "persona": persona_name, 
            "username": username, 
            "source": source
        })
        
        # Invalidate old embeddings list length so it regenerates
        self.data["embeddings"] = []
        
        self._update_index()
        self._save_data()

    def query(self, query_text, persona_name, active_username, n_results=3):
        """Retrieve relevant context using semantic similarity."""
        if not self.data["documents"] or self.vectors is None or not self.model:
            return ""

        # Filter indices
        indices = []
        for i, meta in enumerate(self.data["metadatas"]):
            doc_owner = meta.get("username", "System")
            if meta.get("persona") == persona_name and doc_owner in [active_username, "System"]:
                indices.append(i)
        
        if not indices:
            return ""

        try:
            # Generate embedding for the query
            query_vec = self.model.encode([query_text], convert_to_numpy=True)
            persona_vectors = self.vectors[indices]
            
            # Semantic cosine similarity
            similarities = cosine_similarity(query_vec, persona_vectors).flatten()
            top_k = np.argsort(similarities)[::-1][:n_results]
            
            results = []
            for idx in top_k:
                # Dense semantic vectors often have higher similarity scores than TF-IDF baseline,
                # so the threshold shouldn't be too high. 0.2 is very safe for MiniLM.
                if similarities[idx] > 0.15: 
                    original_idx = indices[idx]
                    doc_text = self.data["documents"][original_idx]
                    meta = self.data["metadatas"][original_idx]
                    uploader = meta.get("username", "System")
                    results.append(f"[Fact from {uploader}]: {doc_text}")
            
            return "\n---\n".join(results)
        except Exception as e:
            import sys
            sys.stderr.write(f"[RAG_ENGINE] RAG Query Error: {e}\n")
            return ""

    def clear_persona_knowledge(self, persona_name, active_username):
        """Delete knowledge and rebuild index."""
        new_docs = []
        new_meta = []
        new_embeds = []
        
        for i, meta in enumerate(self.data["metadatas"]):
            doc_owner = meta.get("username", "System")
            if not(meta.get("persona") == persona_name and doc_owner == active_username):
                new_docs.append(self.data["documents"][i])
                new_meta.append(self.data["metadatas"][i])
                if i < len(self.data.get("embeddings", [])):
                    new_embeds.append(self.data["embeddings"][i])
        
        self.data["documents"] = new_docs
        self.data["metadatas"] = new_meta
        self.data["embeddings"] = new_embeds
        
        self._update_index()
        self._save_data()
