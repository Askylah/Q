import os
import json
from typing import List, Dict, Any
from plugin_manager import HookType, PluginManager

class SkillOrchestrator:
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: Dict[str, Dict] = {}
        self.active_skills: Dict[str, List[str]] = {} # session_id -> list of skill_ids
        self.load_skills()

    def load_skills(self):
        if not os.path.exists(self.skills_dir):
            print(f"[SKILL_ORCHESTRATOR] Warning: Skills directory {self.skills_dir} not found.")
            return

        for item in os.listdir(self.skills_dir):
            item_path = os.path.join(self.skills_dir, item)
            
            # Rule 1: Text file (Prompt-Only Skill)
            if os.path.isfile(item_path) and item.endswith('.txt'):
                skill_id = item[:-4]
                try:
                    with open(item_path, 'r', encoding='utf-8') as f:
                        prompt = f.read().strip()
                        self.skills[skill_id] = {
                            "name": skill_id.capitalize(),
                            "description": "Drop-in text skill.",
                            "prompt_addition": prompt,
                            "prerequisites": [],
                            "tools": []
                        }
                except Exception as e:
                    print(f"[SKILL_ORCHESTRATOR] Error loading text skill {item}: {e}")

            # Rule 2: Subdirectory (Complex Skill with manifest)
            elif os.path.isdir(item_path):
                manifest_path = os.path.join(item_path, "manifest.json")
                if os.path.exists(manifest_path):
                    skill_id = item
                    try:
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            self.skills[skill_id] = json.load(f)
                    except Exception as e:
                        print(f"[SKILL_ORCHESTRATOR] Error loading manifest for {skill_id}: {e}")
                        
        print(f"[SKILL_ORCHESTRATOR] Loaded {len(self.skills)} modular skill definitions from {self.skills_dir}.")

    def get_available_skills(self, session_id: str) -> List[str]:
        """Returns skills the agent can currently see/activate."""
        active = self.active_skills.get(session_id, ["scout"]) # Default to Scout
        available = []
        for sid, data in self.skills.items():
            prereq = data.get("prerequisites", [])
            if not prereq or all(p in active for p in prereq):
                available.append(sid)
        return available

    def activate_skill(self, session_id: str, skill_id: str) -> bool:
        """Unlocks/Activates a skill if prerequisites are met."""
        if skill_id not in self.skills:
            return False
        
        prereqs = self.skills[skill_id].get("prerequisites", [])
        active = self.active_skills.get(session_id, ["scout"])
        
        if all(p in active for p in prereqs):
            if skill_id not in active:
                active.append(skill_id)
                self.active_skills[session_id] = active
                print(f"[SKILL_ORCHESTRATOR] Session {session_id} activated skill: {skill_id}")
            return True
        return False

    def get_active_tools(self, session_id: str) -> List[Dict]:
        """Returns tool definitions for all currently active skills."""
        active = self.active_skills.get(session_id, ["scout"])
        tools = []
        for sid in active:
            if sid in self.skills:
                for tool in self.skills[sid].get("tools", []):
                    t_copy = dict(tool)
                    if "type" not in t_copy:
                        t_copy["type"] = "function"
                    tools.append(t_copy)
        return tools

    def get_active_prompts(self, session_id: str) -> str:
        """Returns concatenated prompt additions for all active skills."""
        active = self.active_skills.get(session_id, ["scout"])
        prompts = []
        for sid in active:
            if sid in self.skills:
                prompts.append(self.skills[sid].get("prompt_addition", ""))
        return "\n\n".join(filter(None, prompts))

# Global Instance
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
orchestrator = SkillOrchestrator(SKILLS_DIR)

def register(manager: PluginManager):
    """Register the orchestrator with the plugin manager hooks."""
    
    def tool_provider_hook(**kwargs) -> List[Dict]:
        session_id = kwargs.get("session_id", "default")
        return orchestrator.get_active_tools(session_id)

    def prompt_provider_hook(**kwargs) -> str:
        session_id = kwargs.get("session_id", "default")
        return orchestrator.get_active_prompts(session_id)

    manager.register_hook(HookType.TOOL_PROVIDER, tool_provider_hook)
    manager.register_hook(HookType.PROMPT_PROVIDER, prompt_provider_hook)
