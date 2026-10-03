"""
mcp_router.py — Q Multi-Server MCP Router

Replaces the single-server MCPClientManager with a registry of named MCP server
processes. Each server is identified by a short name. Tools are exposed to the LLM
with the namespace prefix `server__tool` to guarantee zero collision across servers.

Usage:
    from mcp_router import get_router

    router = get_router()
    tools  = router.list_all_tools_sync()       # flat list, namespaced
    result = router.route_call_sync("lab__execute_python_lab", {"code": "print(1)"})
"""

import asyncio
import os
import sys
import json
import shutil
import threading
from contextlib import AsyncExitStack
from typing import Dict, List, Optional, Tuple

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

# ──────────────────────────────────────────────────────────────────────────────
# Server Registry Entry
# ──────────────────────────────────────────────────────────────────────────────

class _ServerEntry:
    """Holds connection state for a single registered MCP server."""

    def __init__(self, name: str, params: StdioServerParameters):
        self.name       = name
        self.params     = params
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()

    async def connect(self):
        if self.session is not None:
            return
        try:
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(self.params)
            )
            self.session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self.session.initialize()
            print(f"[MCP_ROUTER] OK Connected: '{self.name}'")
        except Exception as e:
            print(f"[MCP_ROUTER] WARN Failed to connect '{self.name}': {e}")
            self.session = None

    async def disconnect(self):
        await self._exit_stack.aclose()
        self.session = None

    async def list_tools(self) -> List[dict]:
        """Returns namespaced OpenAI-schema tool definitions from this server."""
        if self.session is None:
            return []
        try:
            response = await self.session.list_tools()
            tools = []
            for t in response.tools:
                namespaced_name = f"{self.name}__{t.name}"
                tools.append({
                    "type": "function",
                    "function": {
                        "name": namespaced_name,
                        "description": f"[{self.name}] {t.description}",
                        "parameters": t.inputSchema
                    }
                })
            return tools
        except Exception as e:
            print(f"[MCP_ROUTER] Error listing tools for '{self.name}': {e}")
            return []

    async def call_tool(self, raw_name: str, arguments: dict) -> str:
        """Calls a tool by its un-namespaced name on this server."""
        if self.session is None:
            return f"Error: Server '{self.name}' is not connected."
        try:
            result = await self.session.call_tool(raw_name, arguments)
            if hasattr(result, "content") and isinstance(result.content, list):
                texts = [c.text for c in result.content if hasattr(c, "text")]
                body = "\n".join(texts)
            else:
                body = str(result)

            # FIX(tool-attribution): MCP signals tool failure with isError ON THE
            # RESULT. It does not raise. Without this check a failed call returns
            # through the success path as the provider's bare text — "Unknown
            # tool: git_foo", "Input validation error: 'code' is a required
            # property", "ENOENT: no such file or directory" — none of which carry
            # any marker dopamine_state.classify_tool_outcome recognises. Every
            # one of them scored as a clean SUCCESS, so the agent's competence
            # signal was being *rewarded* for failed tool calls. Since this router
            # carries virtually all external tool traffic, this single missing
            # check accounted for most of the mis-scoring.
            # isError is a plain bool defaulting to False in the SDK's
            # CallToolResult, so getattr stays safe against older result shapes.
            if getattr(result, "isError", False):
                return f"Error: MCP tool '{self.name}/{raw_name}' failed: {body}"
            return body
        except Exception as e:
            # The colon in "Error:" is load-bearing — it is the exact marker the
            # dopamine classifier looks for. The previous wording, "Error
            # executing ...", has no colon after Error and therefore matched no
            # failure marker at all, so every transport-level exception here was
            # scored as a success.
            return f"Error: executing '{self.name}/{raw_name}' failed: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# Multi-Server Router
# ──────────────────────────────────────────────────────────────────────────────

class MCPRouter:
    """
    Registry and dispatcher for multiple MCP server processes.

    Servers are registered before connect_all() is called.
    After connection, list_all_tools() and route_call() provide a unified
    interface across all registered servers.
    """

    NAMESPACE_SEP = "__"

    def __init__(self):
        self._servers: Dict[str, _ServerEntry] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_evt: Optional[asyncio.Event] = None
        ready_evt = threading.Event()

        def _thread_target():
            import anyio
            async def _main():
                self._loop = asyncio.get_running_loop()
                self._stop_evt = asyncio.Event()
                ready_evt.set()
                await self._stop_evt.wait()
            try:
                anyio.run(_main)
            except Exception as e:
                print(f"[MCP_ROUTER] Event loop thread exited: {e}")

        self._thread = threading.Thread(target=_thread_target, daemon=True, name="mcp-event-loop")
        self._thread.start()
        ready_evt.wait(timeout=5.0)

    # ── Registration ──────────────────────────────────────────────────────────

    def register_server(
        self,
        name: str,
        command: str,
        args: List[str],
        env: Optional[dict] = None,
        require_on_path: Optional[str] = None
    ):
        """
        Register an MCP server by name.

        Args:
            name:            Short identifier (used as namespace prefix, e.g. 'lab').
            command:         Executable to run (e.g. sys.executable, 'npx').
            args:            CLI arguments for the server process.
            env:             Optional environment variables dict. Defaults to os.environ.
            require_on_path: If set, skips registration if this binary is not on PATH.
                             Prevents hard crashes when optional tools (e.g. npx) are absent.
        """
        if require_on_path and not shutil.which(require_on_path):
            print(f"[MCP_ROUTER] SKIP Skipping '{name}': '{require_on_path}' not found on PATH.")
            return

        resolved_env = env if env is not None else os.environ.copy()
        resolved_env["TRANSFORMERS_VERBOSITY"] = "error"
        resolved_env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

        params = StdioServerParameters(command=command, args=args, env=resolved_env)
        self._servers[name] = _ServerEntry(name, params)
        print(f"[MCP_ROUTER] REG Registered server: '{name}' ({command} {' '.join(args[:2])}...)")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect_all(self):
        """Connect to all registered servers concurrently."""
        tasks = [entry.connect() for entry in self._servers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def disconnect_all(self):
        """Cleanly shut down all server subprocesses."""
        tasks = [entry.disconnect() for entry in self._servers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        print("[MCP_ROUTER] All servers disconnected.")

    # ── Tool Discovery ────────────────────────────────────────────────────────

    async def list_all_tools(self) -> List[dict]:
        """
        Query all connected servers and return a flat, namespaced tool list.
        Format: server__tool_name (double-underscore separator).
        """
        all_tools = []
        for entry in self._servers.values():
            tools = await entry.list_tools()
            all_tools.extend(tools)
        print(f"[MCP_ROUTER] TOOLS Total tools across all servers: {len(all_tools)}")
        return all_tools

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def route_call(self, namespaced_name: str, arguments: dict) -> str:
        """
        Route a tool call using the server__tool namespace format.

        Raises ValueError if the tool name does not contain the separator
        or the server is not registered.
        """
        if self.NAMESPACE_SEP not in namespaced_name:
            return f"Error: Tool '{namespaced_name}' is not a namespaced MCP router call (missing '{self.NAMESPACE_SEP}')."

        server_name, raw_tool_name = namespaced_name.split(self.NAMESPACE_SEP, 1)

        entry = self._servers.get(server_name)
        if entry is None:
            return f"Error: No server registered under name '{server_name}'."

        if entry.session is None:
            # Attempt reconnect once before failing
            await entry.connect()
            if entry.session is None:
                return f"Error: Server '{server_name}' is offline and could not reconnect."

        return await entry.call_tool(raw_tool_name, arguments)

    # ── Synchronous Bridge ────────────────────────────────────────────────────

    def _run_sync(self, coro):
        """
        Schedule a coroutine on the dedicated MCP event loop and block
        until it completes.  Uses asyncio.run_coroutine_threadsafe(),
        which is the official cross-thread dispatch API and does not
        require nest_asyncio.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def list_all_tools_sync(self) -> List[dict]:
        """Synchronous wrapper for list_all_tools(). Safe to call from FastAPI threads."""
        return self._run_sync(self.list_all_tools())

    def route_call_sync(self, namespaced_name: str, arguments: dict) -> str:
        """Synchronous wrapper for route_call(). Safe to call from FastAPI threads."""
        return self._run_sync(self.route_call(namespaced_name, arguments))

    def connect_all_sync(self):
        """Synchronous wrapper for connect_all(). Call once at startup."""
        self._run_sync(self.connect_all())

    def disconnect_all_sync(self):
        """Synchronous wrapper for disconnect_all(). Call at shutdown."""
        self._run_sync(self.disconnect_all())


# ──────────────────────────────────────────────────────────────────────────────
# Default Router Instance & Startup Registration
# ──────────────────────────────────────────────────────────────────────────────

_router: Optional[MCPRouter] = None

def get_router() -> MCPRouter:
    """
    Returns the global MCPRouter singleton.
    On first call, registers all default servers and connects them.
    """
    global _router
    if _router is not None:
        return _router

    _router = MCPRouter()

    # ── Built-in lab server (existing mcp_server.py) ─────────────────────────
    lab_server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    _router.register_server(
        name    = "lab",
        command = sys.executable,
        args    = [lab_server_path]
    )

    # ── Community: Filesystem tools ───────────────────────────────────────────
    # Provides: read_file, write_file, list_directory, create_directory,
    #           move_file, search_files, get_file_info, list_allowed_dirs
    project_root = os.path.dirname(os.path.abspath(__file__))
    _router.register_server(
        name             = "filesystem",
        command          = "npx",
        args             = ["-y", "@modelcontextprotocol/server-filesystem", project_root],
        require_on_path  = "npx"
    )

    # ── Official: Git tools (mcp-server-git via PyPI) ─────────────────────────
    # Provides: git_status, git_diff_unstaged, git_diff_staged, git_diff,
    #           git_commit, git_add, git_reset, git_log, git_create_branch,
    #           git_checkout, git_show, git_init
    # Installed via: py -m pip install mcp-server-git
    _router.register_server(
        name    = "git",
        command = sys.executable,
        args    = ["-m", "mcp_server_git", "--repository", project_root]
    )

    # Connect all registered servers
    _router.connect_all_sync()
    return _router


# ──────────────────────────────────────────────────────────────────────────────
# Standalone Test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== MCP Router Standalone Test ===")
    router = get_router()

    tools = router.list_all_tools_sync()
    print(f"\nDiscovered {len(tools)} tools total:")
    for t in tools:
        print(f"  - {t['function']['name']}")

    print("\nTesting lab__execute_python_lab...")
    result = router.route_call_sync("lab__execute_python_lab", {"code": "print('MCP Router: online.')"})
    print(f"Result: {result}")
