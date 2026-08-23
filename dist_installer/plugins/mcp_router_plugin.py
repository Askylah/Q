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


import time

LAZY_MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_mcp_tool",
            "description": "Execute a tool on a connected MCP server. Active servers: 'filesystem' (read_file, write_file, edit_file, list_directory, search_files), 'lab' (execute_python_lab, search_web), 'git' (git_status, git_diff, git_log).",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {"type": "string", "description": "The target MCP server ('filesystem', 'lab', 'git')."},
                    "tool_name": {"type": "string", "description": "The tool to execute (e.g. 'read_file', 'list_directory', 'execute_python_lab', 'git_status')."},
                    "arguments": {"type": "object", "description": "Key-value dictionary of arguments for the tool."}
                },
                "required": ["server_name", "tool_name", "arguments"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_mcp_tools",
            "description": "Discover available tools and exact parameter schemas across connected MCP servers ('filesystem', 'lab', 'git').",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {"type": "string", "description": "Optional server name filter ('filesystem', 'lab', 'git'). Leave empty for all."}
                }
            }
        }
    }
]

def provide_mcp_router_tools(*args, **kwargs):
    """
    TOOL_PROVIDER hook: returns lazy-loaded MCP meta-tools.
    Avoids dumping dozens of raw schemas into the context window.
    """
    return LAZY_MCP_TOOLS


def register(manager):
    """Called automatically by PluginManager.load_plugins()."""
    try:
        from plugin_manager import HookType
        manager.register_hook(HookType.TOOL_PROVIDER, provide_mcp_router_tools)
        print("[MCP_ROUTER_PLUGIN] [OK] MCP Router bridged to TOOL_PROVIDER hook.")
    except Exception as e:
        print(f"[MCP_ROUTER_PLUGIN] Registration failed: {e}")
