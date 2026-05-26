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
