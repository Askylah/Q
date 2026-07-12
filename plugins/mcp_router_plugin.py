"""
plugins/mcp_router_plugin.py

Bridges the MCPRouter into Q's plugin system as a TOOL_PROVIDER hook.
Automatically discovered and loaded by PluginManager.load_plugins().

This plugin registers a single hook: provide_mcp_router_tools()
which returns the full namespaced tool list from all connected MCP servers.
The tool list is refreshed on every call, so new server connections are
reflected without a backend restart.
"""

import sys
import os

# Ensure app root is importable from plugin context
_app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _app_root not in sys.path:
    sys.path.insert(0, _app_root)


def provide_mcp_router_tools(*args, **kwargs):
    """
    TOOL_PROVIDER hook: returns all namespaced tools from the MCP router.
    Called by PluginManager.run_tool_providers() on each request.
    """
    try:
        from mcp_router import get_router
        return get_router().list_all_tools_sync()
    except Exception as e:
        print(f"[MCP_ROUTER_PLUGIN] Failed to fetch router tools: {e}")
        return []


def register(manager):
    """Called automatically by PluginManager.load_plugins()."""
    try:
        from plugin_manager import HookType
        manager.register_hook(HookType.TOOL_PROVIDER, provide_mcp_router_tools)
        print("[MCP_ROUTER_PLUGIN] ✅ MCP Router bridged to TOOL_PROVIDER hook.")
    except Exception as e:
        print(f"[MCP_ROUTER_PLUGIN] Registration failed: {e}")
