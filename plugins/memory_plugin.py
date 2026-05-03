from datetime import datetime
from database import UserManager
from plugin_manager import HookType, PluginManager
import threading

# Deep Memory integration (optional)
try:
    from memory_engine import DeepMemory
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

class Observer:
    def __init__(self, db: UserManager, username: str, persona: str):
        self.db = db
        self.username = username
        self.persona = persona

    def log_event(self, event_type: str, content: str, reflection_score: float = 0.0):
        self.db.add_observation(
            username=self.username,
            persona=self.persona,
            event_type=event_type,
            content=content,
            reflection_score=reflection_score
        )

class Reflector:
    def __init__(self, db: UserManager, username: str, persona: str, llm_callback):
        self.db = db
        self.username = username
        self.persona = persona
        self.llm_callback = llm_callback

    def reflect(self, turn_threshold: int = 5):
        logs = self.db.get_observation_log(self.username, self.persona, limit=20)
        raw_events = [l for l in logs if l['type'] in ['user_message', 'assistant_response', 'tool_output']]
        if len(raw_events) < turn_threshold:
            return

        log_text = "\n".join([f"[{l['timestamp']}] {l['type'].upper()}: {l['content']}" for l in logs])
        reflection_prompt = f"""
        ANALYZE THE FOLLOWING INTERACTION LOG AND CREATE A DENSE OBSERVATION.
        
        TRANSCRIPT:
        {log_text}
        
        TASK:
        1. Summarize the facts learned about the user.
        2. Note any emotional shifts or relationship milestones.
        3. Identify successful or failed patterns in tool usage.
        4. Compress this into a single, high-density paragraph (the 'Dense Observation').
        
        OUTPUT FORMAT:
        Return ONLY the raw paragraph. No titles, no intro.
        """
        dense_observation = self.llm_callback(reflection_prompt)
        
        if dense_observation and len(dense_observation.strip()) > 10:
            self.db.add_observation(
                username=self.username,
                persona=self.persona,
                event_type="dense_observation",
                content=dense_observation.strip(),
                reflection_score=1.0
            )
            if _DEEP_MEMORY_AVAILABLE:
                try:
                    dm = DeepMemory(username=self.username, persona=self.persona)
                    dm.store(
                        content=dense_observation.strip()[:500],
                        memory_type="emotional",
                        domain="relationship",
                        emotions={},
                        tags=["observation", "auto-generated"],
                        importance=6
                    )
                except Exception as e:
                    print(f"DEEP_MEMORY BRIDGE ERROR: {e}")
            return True
        return False

def memory_observer_hook(event_type: str, content: str, **kwargs):
    """Observer hook: Logs interaction events."""
    db = kwargs.get("db")
    username = kwargs.get("username")
    persona_key = kwargs.get("persona_key")
    
    if not (db and username and persona_key):
        return

    observer = Observer(db, username, persona_key)
    observer.log_event(event_type, content)
    
    # Trigger reflection check if this is a user message
    if event_type == "user_message":
        om_enabled = kwargs.get("om_enabled", True)
        om_threshold = kwargs.get("om_turn_threshold", 5)
        llm_callback = kwargs.get("llm_callback")
        
        if om_enabled and llm_callback:
            def invoke_reflector():
                try:
                    # Note: We should use a separate DB connection for thread safety
                    from database import UserManager
                    thread_db = UserManager()
                    reflector = Reflector(thread_db, username, persona_key, llm_callback)
                    reflector.reflect(turn_threshold=om_threshold)
                except Exception as e:
                    print(f"[MEMORY_PLUGIN] Reflector thread failed: {e}")
            
            threading.Thread(target=invoke_reflector, daemon=True).start()

def register(manager: PluginManager):
    manager.register_hook(HookType.OBSERVER, memory_observer_hook)
