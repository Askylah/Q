import os
import importlib.util
import sys
import traceback
from enum import Enum
from typing import List, Callable, Any, Dict

class HookType(Enum):
    GATEKEEPER = "gatekeeper"           # Security/Validation
    ENRICHER = "enricher"               # Prompt modification
    OBSERVER = "observer"               # Async side-effects
    TOOL_PROVIDER = "tool_provider"     # Dynamic tool injection
    PROMPT_PROVIDER = "prompt_provider" # Dynamic system prompt injection

class PluginManager:
    def __init__(self):
        self.hooks: Dict[HookType, List[Callable]] = {
            HookType.GATEKEEPER: [],
            HookType.ENRICHER: [],
            HookType.OBSERVER: [],
            HookType.TOOL_PROVIDER: [],
            HookType.PROMPT_PROVIDER: []
        }
        self.plugins_loaded = []

    def register_hook(self, hook_type: HookType, callback: Callable):
        """Manually register a callback for a specific hook."""
        if hook_type in self.hooks:
            self.hooks[hook_type].append(callback)
            print(f"[PLUGIN_SYSTEM] Registered {hook_type.value} hook: {callback.__name__}")

    def load_plugins(self, directory: str):
        """Scans a directory for .py files and calls their register() function."""
        if not os.path.exists(directory):
            print(f"[PLUGIN_SYSTEM] Warning: Directory {directory} not found.")
            return

        print(f"[PLUGIN_SYSTEM] Scanning for plugins in {directory}...")
        
        # Add directory to sys.path to handle internal imports within plugins
        if directory not in sys.path:
            sys.path.append(directory)

        for root, _, files in os.walk(directory):
            for filename in files:
                if filename.endswith(".py") and not filename.startswith("__"):
                    plugin_path = os.path.join(root, filename)
                    plugin_name = filename[:-3]
                    
                    try:
                        spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        
                        if hasattr(module, "register"):
                            module.register(self)
                            self.plugins_loaded.append(plugin_name)
                        else:
                            # print(f"[PLUGIN_SYSTEM] Skipping {filename}: No register() function found.")
                            pass
                    except Exception as e:
                        print(f"[PLUGIN_SYSTEM] Error loading plugin {filename}:")
                        traceback.print_exc()

    def run_gatekeepers(self, *args, **kwargs) -> bool:
        """Runs all security checks. Returns True if ANY gatekeeper blocks the request."""
        for callback in self.hooks[HookType.GATEKEEPER]:
            try:
                if callback(*args, **kwargs):
                    return True
            except Exception as e:
                print(f"[PLUGIN_SYSTEM] Gatekeeper {callback.__name__} failed: {e}")
        return False

    def run_enrichers(self, context: str, *args, **kwargs) -> str:
        """Sequentially runs all enrichers to modify the context string."""
        current_context = context
        for callback in self.hooks[HookType.ENRICHER]:
            try:
                current_context = callback(current_context, *args, **kwargs)
            except Exception as e:
                print(f"[PLUGIN_SYSTEM] Enricher {callback.__name__} failed: {e}")
        return current_context

    def run_observers(self, *args, **kwargs):
        """Runs all side-effect observers."""
        for callback in self.hooks[HookType.OBSERVER]:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"[PLUGIN_SYSTEM] Observer {callback.__name__} failed: {e}")

    def run_tool_providers(self, current_tools: List[Dict], *args, **kwargs) -> List[Dict]:
        """Collects dynamic tools from all providers."""
        all_tools = list(current_tools)
        for callback in self.hooks[HookType.TOOL_PROVIDER]:
            try:
                new_tools = callback(*args, **kwargs)
                if new_tools and isinstance(new_tools, list):
                    all_tools.extend(new_tools)
            except Exception as e:
                print(f"[PLUGIN_SYSTEM] Tool Provider {callback.__name__} failed: {e}")
        return all_tools

    def run_prompt_providers(self, *args, **kwargs) -> List[str]:
        """Collects dynamic system prompt segments from all providers."""
        segments = []
        for callback in self.hooks[HookType.PROMPT_PROVIDER]:
            try:
                segment = callback(*args, **kwargs)
                if segment and isinstance(segment, str):
                    segments.append(segment)
            except Exception as e:
                print(f"[PLUGIN_SYSTEM] Prompt Provider {callback.__name__} failed: {e}")
        return segments

# Global Instance
manager = PluginManager()

def get_plugin_manager():
    return manager
