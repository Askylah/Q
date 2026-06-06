from enum import IntEnum
from typing import Dict, Any, List

class DangerLevel(IntEnum):
    INFO = 0         # Read-only, safe (e.g., searching, reading)
    KINETIC = 1      # State-changing (e.g., writing files, creating lore)
    DESTRUCTIVE = 2  # Irreversible (e.g., deleting files, wiping DB)

class GovernanceManager:
    def __init__(self):
        # Default policy: Ask for Kinetic and Destructive
        self.review_policy = "ask" # "always", "ask", "never"
        
        # Tool Mapping (Internal and Skill-based)
        self.registry: Dict[str, DangerLevel] = {
            # Core Tools
            "execute_python_lab": DangerLevel.KINETIC,
            "destructive_debug": DangerLevel.KINETIC,
            "read_file": DangerLevel.INFO,
            "list_dir": DangerLevel.INFO,
            "web_search": DangerLevel.INFO,
            "search_web": DangerLevel.INFO,
            "deep_lore_query": DangerLevel.INFO,
            
            # Skill Tree Tools
            "activate_skill": DangerLevel.INFO,
            "generate_logic_tree": DangerLevel.KINETIC,
            "create_zettel_link": DangerLevel.KINETIC,
            
            # Destructive (Hypothetical future tools)
            "delete_file": DangerLevel.DESTRUCTIVE,
            "wipe_memories": DangerLevel.DESTRUCTIVE
        }

    def get_danger_level(self, tool_name: str) -> DangerLevel:
        return self.registry.get(tool_name, DangerLevel.KINETIC) # Default to Kinetic if unknown

    def should_require_approval(self, tool_name: str, args: dict, username: str = "default") -> bool:
        import database as db
        level = self.get_danger_level(tool_name)
        
        # Load user-specific policy
        user_db = db.UserManager()
        settings = user_db.get_user_settings(username)
        policy = settings.get("review_policy", "ask")

        if level == DangerLevel.DESTRUCTIVE:
            return True # Always require approval for destruction
            
        if policy == "always":
            return True
            
        if policy == "ask" and level >= DangerLevel.KINETIC:
            return True
            
        return False

# Global Instance
governance = GovernanceManager()

def get_governance_manager():
    return governance

import os
import shutil
import tempfile
import hashlib

class ShadowSandbox:
    def __init__(self, real_root: str):
        self.real_root = os.path.abspath(real_root)
        self.temp_dir = tempfile.mkdtemp(prefix="shadow_sandbox_")
        self._copy_workspace()

    def _copy_workspace(self):
        ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.staging'}
        for item in os.listdir(self.real_root):
            if item in ignore_dirs:
                continue
            s = os.path.join(self.real_root, item)
            d = os.path.join(self.temp_dir, item)
            if os.path.isdir(s):
                try:
                    shutil.copytree(s, d, ignore=shutil.ignore_patterns('.git', '__pycache__', 'node_modules', '.venv', 'venv', '.staging'))
                except Exception:
                    pass
            else:
                try:
                    shutil.copy2(s, d)
                except Exception:
                    pass

    def get_file_metadata(self) -> dict:
        metadata = {}
        for root, dirs, files in os.walk(self.temp_dir):
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.staging'}]
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, self.temp_dir)
                try:
                    with open(abs_path, "rb") as f:
                        file_hash = hashlib.md5(f.read()).hexdigest()
                    metadata[rel_path] = file_hash
                except Exception:
                    metadata[rel_path] = "error"
        return metadata

    def run_tool(self, tool_name: str, args: dict) -> dict:
        from workspace_engine import SafeWorkspace
        before = self.get_file_metadata()

        # Monkeypatch SafeWorkspace.__init__ to redirect to temp_dir
        original_init = SafeWorkspace.__init__
        temp_path = self.temp_dir

        def mock_init(self_ws, root):
            original_init(self_ws, temp_path)

        SafeWorkspace.__init__ = mock_init
        prev_cwd = os.getcwd()
        os.chdir(temp_path)

        try:
            from api_parser import execute_api
            execute_api(tool_name, args)
        except Exception as e:
            print(f"[SHADOW SANDBOX] Tool execution error: {e}")
        finally:
            SafeWorkspace.__init__ = original_init
            os.chdir(prev_cwd)

        after = self.get_file_metadata()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

        added = []
        modified = []
        deleted = []
        for path, file_hash in after.items():
            if path not in before:
                added.append(path)
            elif before[path] != file_hash:
                modified.append(path)
        for path in before:
            if path not in after:
                deleted.append(path)

        return {"added": added, "modified": modified, "deleted": deleted}

def shadow_run_tool(tool_name: str, args: dict, real_root: str) -> dict:
    """Wrapper function to instantiate and run a shadow simulation."""
    sandbox = ShadowSandbox(real_root)
    return sandbox.run_tool(tool_name, args)
