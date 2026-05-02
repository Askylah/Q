import asyncio
import os
import sys
import json
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from contextlib import AsyncExitStack

class MCPClientManager:
    """Manages the lifecycle and tool execution for the local FastMCP lab server."""
    
    def __init__(self, server_path="mcp_server.py"):
        self.server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), server_path)
        env_copy = os.environ.copy()
        env_copy["TRANSFORMERS_VERBOSITY"] = "error"
        env_copy["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=[self.server_path],
            env=env_copy
        )
        self.session = None
        self.exit_stack = AsyncExitStack()
        
    async def connect(self):
        """Connects to the stdio MCP server."""
        if self.session is not None:
            return
        
        try:
            read, write = await self.exit_stack.enter_async_context(stdio_client(self.server_params))
            self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            print("[MCP_CLIENT] Connected to Lab Server successfully.")
        except Exception as e:
            print(f"[MCP_CLIENT] Error connecting to MCP server: {e}")
            self.session = None

    async def get_tools(self):
        """Fetches tools from the MCP server and formats them into OpenAI schemas."""
        if self.session is None:
            await self.connect()
        if self.session is None:
            return []

        try:
            response = await self.session.list_tools()
            tools = []
            for t in response.tools:
                # Convert MCP Tool to OpenAI JSON schema
                tools.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema
                    }
                })
            return tools
        except Exception as e:
            print(f"[MCP_CLIENT] Error listing tools: {e}")
            return []

    async def call_tool(self, name: str, arguments: dict):
        """Executes a parsed tool call on the MCP server."""
        if self.session is None:
            await self.connect()
        if self.session is None:
            return f"Error: Not connected to MCP Server ({name})."
            
        try:
            result = await self.session.call_tool(name, arguments)
            # result structure usually contains 'content' array.
            if hasattr(result, "content") and isinstance(result.content, list):
                # Extract text responses.
                texts = [c.text for c in result.content if hasattr(c, "text")]
                return "\n".join(texts)
            return str(result)
        except Exception as e:
            error_msg = f"Error executing {name}: {str(e)}"
            return error_msg
            
    async def cleanup(self):
        """Closes the subprocess cleanly."""
        await self.exit_stack.aclose()
        self.session = None

# Global instance for easy importing
_mcp_manager = None

async def get_mcp_manager():
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPClientManager()
        await _mcp_manager.connect()
    return _mcp_manager

# Global event loop for sync hooks
_sync_loop = None

def _get_or_create_loop():
    global _sync_loop
    if _sync_loop is None or _sync_loop.is_closed():
        try:
            _sync_loop = asyncio.get_event_loop()
            if _sync_loop.is_closed():
                raise RuntimeError("Closed")
        except RuntimeError:
            _sync_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_sync_loop)
    return _sync_loop

# Synchronous wrapper hooks for FastAPI / llm_engine interaction
def sync_get_mcp_tools():
    loop = _get_or_create_loop()
    async def _fetch():
        mgr = await get_mcp_manager()
        return await mgr.get_tools()
    
    if loop.is_running():
        # Fallback if somehow called from an already running async context
        import nest_asyncio
        nest_asyncio.apply()
    
    return loop.run_until_complete(_fetch())

def sync_call_mcp_tool(name, arguments):
    loop = _get_or_create_loop()
    async def _call():
        mgr = await get_mcp_manager()
        return await mgr.call_tool(name, arguments)
        
    if loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        
    return loop.run_until_complete(_call())

if __name__ == "__main__":
    tools = sync_get_mcp_tools()
    print("Discovered Tools:", json.dumps(tools, indent=2))
    print("Testing lab execution...")
    res = sync_call_mcp_tool("execute_python_lab", {"code": "print('Hello from the new MCP integration!')"})
    print("Result:", res)
