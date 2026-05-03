import os
import json
from typing import List, Dict, Any
from plugin_manager import HookType, PluginManager

class SkillOrchestrator:
    def __init__(self, skills_file: str):
        self.skills_file = skills_file
        self.skills: Dict[str, Dict] = {}
        self.active_skills: Dict[str, List[str]] = {} # session_id -> list of skill_ids
        self.load_skills()

    def load_skills(self):
        if os.path.exists(self.skills_file):
            with open(self.skills_file, 'r') as f:
                self.skills = json.load(f)
            print(f"[SKILL_ORCHESTRATOR] Loaded {len(self.skills)} skill definitions.")
        else:
            print(f"[SKILL_ORCHESTRATOR] Warning: Skills file {self.skills_file} not found.")

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
                tools.extend(self.skills[sid].get("tools", []))
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
SKILLS_FILE = os.path.join(os.path.dirname(__file__), "skills.json")
orchestrator = SkillOrchestrator(SKILLS_FILE)

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
