import os
from rag_engine import PersonaRAG

def chunk_text(text, chunk_size=1000, overlap=100):
    """Simple chunking with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks

def ingest_knowledge_bases():
    print("Initializing RAG Engine...")
    rag = PersonaRAG()
    
    kb_dir = "./knowledge_bases"
    if not os.path.exists(kb_dir):
        print(f"Directory {kb_dir} not found.")
        return

    # Map filenames to persona names
    # format: filename -> persona_key in personas.json
    kb_map = {
        "dante_kb.txt": "dante",
        "vane_kb.txt": "vane",
        "Rick_kb.txt": "rick",
        # "calypso_kb.txt": "calypso"  # If we add one later
    }

    for filename in os.listdir(kb_dir):
        if filename in kb_map:
            persona_name = kb_map[filename]
            filepath = os.path.join(kb_dir, filename)
            
            print(f"Processing {filename} for persona: {persona_name}...")
            
            # Clear existing to avoid duplicates on re-run
            rag.clear_persona_knowledge(persona_name)
            
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            chunks = chunk_text(content)
            print(f"  - Found {len(chunks)} chunks.")
            
            for i, chunk in enumerate(chunks):
                rag.add_document(chunk, persona_name, source=filename)
                
            print(f"  - Ingested successfully.")

    print("Knowledge base ingestion complete.")

if __name__ == "__main__":
    ingest_knowledge_bases()
