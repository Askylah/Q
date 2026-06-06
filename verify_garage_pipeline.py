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

    # Write test_battery.py dynamically so it does not need to be committed in Git
    os.makedirs("garage", exist_ok=True)
    with open("garage/test_battery.py", "w", encoding="utf-8") as f:
        f.write('''def execute(args):
    battery_id = args.get("battery_id", "unknown")
    return f"Microverse Battery {battery_id} is currently at 98.2% capacity. The slave civilization is working efficiently. Peace among worlds."
''')

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

    # 5.5 Test the create_sandbox_tool API
    print("[TEST] Testing create_sandbox_tool creation and registration...")
    test_code = """def execute(args):
    name = args.get("morty_name", "Morty")
    return f"Hey {name}, you're a little piece of shit. *burp*"
"""
    result = execute_api("create_sandbox_tool", {
        "name": "get_morty_insult",
        "description": "Returns a customized insult for Morty.",
        "parameters": {
            "type": "object",
            "properties": {
                "morty_name": {"type": "string"}
            }
        },
        "code": test_code
    })
    print("[TEST] create_sandbox_tool output:", result)
    
    # Check that it exists in Redis
    dynamic_keys = r.hkeys("q:tools:dynamic")
    print("[TEST] Active dynamic keys in Redis:", [k.decode('utf-8') for k in dynamic_keys])
    
    # Run the newly created tool
    print("[TEST] Running the dynamically created tool 'get_morty_insult'...")
    insult_result = execute_api("get_morty_insult", {"morty_name": "Jerry"})
    print("[TEST] dynamic tool result:", insult_result)

    # Clean up the dynamic tool
    print("[TEST] Cleaning up get_morty_insult...")
    r.hdel("q:tools:dynamic", "get_morty_insult")
    try:
        os.remove("garage/get_morty_insult.py")
    except OSError:
        pass

    # 6. Clean up
    print("[TEST] Cleaning up test tool from Redis...")
    r.hdel("q:tools:dynamic", "microverse_battery_status")
    try:
        os.remove("garage/test_battery.py")
    except OSError:
        pass
    print("[TEST] Verification complete.")

if __name__ == "__main__":
    run_verification()
