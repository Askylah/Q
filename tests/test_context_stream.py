import sqlite3
import hashlib
import requests
import json
import os

DB_PATH = "users.db"

def setup_test_user():
    print("Setting up SkyTest user...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    key_hash = hashlib.sha256(b"testkey123").hexdigest()
    c.execute("INSERT OR REPLACE INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
              ("SkyTest", key_hash, "2026-06-18"))
    conn.commit()
    conn.close()
    print("SkyTest setup complete.")

def check_health():
    url = "http://127.0.0.1:8000/health"
    try:
        print(f"Checking health at {url}...")
        response = requests.get(url, timeout=5)
        print(f"Health response status: {response.status_code}")
        print(f"Health response content: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Failed to reach health endpoint: {e}")
        return False

def test_stream():
    url = "http://127.0.0.1:8000/chat/rick/stream"
    headers = {
        "X-Profile-Username": "SkyTest",
        "X-Profile-Key": "testkey123",
        "Content-Type": "application/json"
    }
    payload = {
        "username": "SkyTest",
        "message": "Hello, who are you?",
        "chat_history": [],
        "target_model_id": "google/gemini-3-flash-preview",
        "expert_model_id": "google/gemini-3.1-pro-preview",
        "temperature": 0.9,
        "top_p": 1.0,
        "max_tokens": 1024,
        "active_api_keys": {}
    }
    
    # Try to load API keys from env
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if api_key:
        print("API Key found in environment.")
        if api_key.startswith("sk-or-"):
            payload["active_api_keys"]["openrouter"] = api_key
        else:
            payload["active_api_keys"]["google"] = api_key

    print(f"Sending POST request to {url}...")
    try:
        response = requests.post(url, headers=headers, json=payload, stream=True, timeout=15)
        print(f"Response status: {response.status_code}")
        print("Streaming chunks:")
        for chunk in response.iter_lines():
            if chunk:
                print(chunk.decode('utf-8'))
    except Exception as e:
        print(f"Exception during stream consumption: {e}")

if __name__ == "__main__":
    setup_test_user()
    if check_health():
        test_stream()
