import sys
import os

# Add parent dir to path to find skill_orchestrator
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import skill_orchestrator

def register(manager):
    skill_orchestrator.register(manager)
    print("[SKILL_TREE_PLUGIN] Successfully bridged SkillOrchestrator to PluginManager.")
