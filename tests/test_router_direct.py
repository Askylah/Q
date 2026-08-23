import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp_router

router = mcp_router.get_router()
print("Connecting all servers...")
router.connect_all_sync()
tools = router.list_all_tools_sync()
print(f"Direct Router Test Found {len(tools)} tools:")
for t in tools:
    print(" -", t["function"]["name"])
