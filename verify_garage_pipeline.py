import os
import sys
import json
import redis

# Add plugins directory to path so imports work correctly
sys.path.append(os.path.abspath("plugins"))
from api_parser import load_universal_schemas, execute_api, EXECUTION_MAP

def run_verification():
    # 1. Connect to local Redis
    try:
        r = redis.Redis(host='localhost', port=6379, socket_connect_timeout=2.0)
        r.ping()
        print("[REDIS] Successfully connected to local instance.")
    except Exception as e:
        print(f"[REDIS ERROR] Could not connect to local Redis: {e}")
        print("Please make sure your local Redis service or Docker container is running.")
        sys.exit(1)

    # 2. Define mock tool schema using local path
    mock_tool = {
        "type": "function",
        "function": {
            "name": "microverse_battery_status",
            "description": "Checks the charge level of the microverse battery. Do not tell the inhabitants.",
            "parameters": {
                "type": "object",
                "properties": {
                    "battery_id": {"type": "string", "description": "The UUID of the battery cell."}
                },
                "required": ["battery_id"]
            }
        },
        "_execution": {
            "type": "local_script",
            "script_path": "garage/test_battery.py",
            "entry_point": "execute"
        }
    }

    # 3. Inject mock tool
    print("[TEST] Registering mock tool 'microverse_battery_status' in Redis Hash...")
    r.hset("q:tools:dynamic", "microverse_battery_status", json.dumps(mock_tool))

    # 4. Trigger loader (performs the Atomic Swap)
    print("[TEST] Triggering dynamic tool loader...")
    tools = load_universal_schemas()
    loaded_names = [t["function"]["name"] for t in tools]
    print(f"[TEST] Parsed tools from loader: {loaded_names}")

    # 5. Execute the tool
    # This will trigger our AST alignment check AND run the secure sandboxed container!
    print("[TEST] Executing microverse_battery_status via execute_api...")
    result = execute_api("microverse_battery_status", {"battery_id": "8c4a92d2"})
    print("\n[EXECUTION RESULT]")
    print(result)
    print("")

    # 6. Clean up
    print("[TEST] Cleaning up test tool from Redis...")
    r.hdel("q:tools:dynamic", "microverse_battery_status")
    print("[TEST] Verification complete.")

if __name__ == "__main__":
    run_verification()
