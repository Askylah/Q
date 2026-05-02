import sys
import os
import uuid

# Add the project directory to sys.path
sys.path.append(r"C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-main")

import zettel_engine
import database as db

def test_scaling():
    username = "test_user"
    persona = "Rick"
    title = "The Multiverse Bible"
    
    # Generate a massive text (simulated lorebook)
    print("Generating 500 chunks of lore...")
    lore_text = ""
    for i in range(500):
        lore_text += f"\n\nSector {i}: Information about the multiverse and its various dimensions. "
        lore_text += "This chunk contains critical details about the portal gun, the citadel, and the infinite ricks. "
        lore_text += f"Entity_{i} is a character who lives in Location_{i}."

    # Force a dummy API key to trigger the LLM pass logic (it will fail/fallback gracefully)
    dummy_api_keys = {"GEMINI_API_KEY": "AIza_DUMMY"}
    
    db_conn = db.UserManager()
    # Add entry
    entry_id = db_conn.add_zettel_entry(username, persona, title, lore_text)
    print(f"Entry created: {entry_id}")
    
    # Process entry (this handles chunking + batching)
    print("Starting ingestion (Batching should fire)...")
    zettel_engine.process_entry(username, persona, entry_id, dummy_api_keys)
    
    # Verify nodes
    nodes = db_conn.get_zettel_nodes_for_persona(username, persona)
    print(f"Total nodes created: {len(nodes)}")
    
    if len(nodes) > 0:
        print("SUCCESS: Ingestion completed for large-scale entry.")
    else:
        print("FAILURE: No nodes created.")

if __name__ == "__main__":
    test_scaling()
