import workspace_engine as workspace
import os
import time

def test_strict_security():
    print("--- [STRICT SECURITY VERIFICATION] ---")
    
    # Initialize workspace
    LAB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labs_test")
    os.makedirs(LAB_ROOT, exist_ok=True)
    ws = workspace.SafeWorkspace(LAB_ROOT)
    
    # 1. Test Fork Bomb (PIDs Limit)
    print("\n[TEST 1: FORK BOMB CONTAINMENT]")
    fork_bomb_code = """
import os
while True:
    try:
        os.fork()
    except:
        pass
"""
    # This should be killed by the temporal guillotine or hit the PIDs limit
    # We expect a timeout message or a crash
    result = ws.run_code_secure(fork_bomb_code, timeout=10)
    print(f"Result: {result}")
    
    # 2. Test Memory Clamp
    print("\n[TEST 2: MEMORY CLAMP]")
    memory_hog_code = """
# Attempt to allocate ~1GB of RAM
data = bytearray(1024 * 1024 * 1000)
print("Allocated 1GB")
"""
    result = ws.run_code_secure(memory_hog_code)
    print(f"Result: {result}")
    if "ENGINE_ERROR" in result or "Killed" in result or "137" in str(result):
        print("SUCCESS: Memory limit enforced (SIGKILL/OOM).")
    else:
        print("FAILURE: Memory limit might not be enforced.")

    # 3. Test Network Isolation
    print("\n[TEST 3: NETWORK ISOLATION]")
    network_code = """
import socket
try:
    socket.create_connection(("8.8.8.8", 53), timeout=2)
    print("NETWORK ACCESS DETECTED")
except Exception as e:
    print(f"Network Blocked: {e}")
"""
    result = ws.run_code_secure(network_code)
    print(f"Result: {result}")
    assert "NETWORK ACCESS DETECTED" not in result, "Network isolation failed!"

    # 4. Test Path Traversal
    print("\n[TEST 4: PATH TRAVERSAL]")
    try:
        ws.read_file_content("../../../secrets.txt")
        print("FAILURE: Path traversal allowed!")
    except workspace.SecurityViolation as e:
        print(f"SUCCESS: Path traversal blocked: {e}")

    # 5. Test Temporal Guillotine
    print("\n[TEST 5: TEMPORAL GUILLOTINE]")
    infinite_loop = "while True: pass"
    start_time = time.time()
    result = ws.run_code_secure(infinite_loop, timeout=5)
    duration = time.time() - start_time
    print(f"Result: {result}")
    print(f"Duration: {duration:.2f}s")
    if 4.5 < duration < 7:
         print("SUCCESS: Guillotine triggered correctly.")
    else:
         print("FAILURE: Timeout logic imprecise.")

if __name__ == "__main__":
    test_strict_security()
