import sqlite3
import hashlib
import requests
import json
import os

DB_PATH = "users.db"

def setup_test_user():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    key_hash = hashlib.sha256(b"testkey123").hexdigest()
    c.execute("INSERT OR REPLACE INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
              ("SkyTest", key_hash, "2026-06-18"))
    conn.commit()
    conn.close()

def run_live_tool_test():
    url = "http://127.0.0.1:8000/chat/rick/stream"
    headers = {
        "X-Profile-Username": "SkyTest",
        "X-Profile-Key": "testkey123",
        "Content-Type": "application/json"
    }
    
    payload = {
        "username": "SkyTest",
        "message": "Use your list_mcp_tools tool right now to inspect all available MCP tools across filesystem, git, and lab servers.",
        "chat_history": [],
        "target_model_id": "google/gemini-2.5-flash",
        "expert_model_id": "google/gemini-2.5-pro",
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 2048,
        "active_api_keys": {}
    }
    
    openrouter_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        payload["active_api_keys"]["openrouter"] = openrouter_key
        print("[LIVE TEST] Using OpenRouter key from ANTHROPIC_AUTH_TOKEN/OPENROUTER_API_KEY.")
    else:
        print("[LIVE TEST WARNING] OpenRouter key not found in environment.")

    print(f"[LIVE TEST] Sending request to {url}...")
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
        print(f"[LIVE TEST] Response HTTP Status: {response.status_code}")
        if response.status_code != 200:
            print(f"[LIVE TEST ERROR] Status {response.status_code}: {response.text}")
            return False
            
        print("\n--- STREAMED OUTPUT CHUNKS ---")
        received_chunks = []
        for chunk in response.iter_lines():
            if chunk:
                decoded = chunk.decode('utf-8')
                received_chunks.append(decoded)
                print(decoded)
                
        full_stream = "\n".join(received_chunks)
        if "error" in full_stream.lower() or "500" in full_stream:
            print("\n[LIVE TEST FAIL] Error detected in stream output.")
            return False
        else:
            print("\n[LIVE TEST SUCCESS] Stream completed cleanly without errors.")
            return True
    except Exception as e:
        print(f"\n[LIVE TEST EXCEPTION] {e}")
        return False

if __name__ == "__main__":
    setup_test_user()
    run_live_tool_test()
