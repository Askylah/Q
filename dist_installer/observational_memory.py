import json
from datetime import datetime
from database import UserManager

# Deep Memory integration (optional)
try:
    from memory_engine import DeepMemory
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

class Observer:
    """Captures and logs events in the agent's environment."""
    def __init__(self, db: UserManager, username: str, persona: str):
        self.db = db
        self.username = username
        self.persona = persona

    def log_event(self, event_type: str, content: str, reflection_score: float = 0.0):
        """Logs a specific event to the database log."""
        self.db.add_observation(
            username=self.username,
            persona=self.persona,
            event_type=event_type,
            content=content,
            reflection_score=reflection_score
        )

class Reflector:
    """Compresses raw events into dense, hierarchical observations."""
    def __init__(self, db: UserManager, username: str, persona: str, llm_callback):
        self.db = db
        self.username = username
        self.persona = persona
        self.llm_callback = llm_callback # Function to call the LLM

    def reflect(self, turn_threshold: int = 5):
        """
        Triggered every N turns to compress recent history.
        This follows the Observational Memory pattern of 'Observing one's own transcripts'.
        """
        logs = self.db.get_observation_log(self.username, self.persona, limit=20)
        
        # Check if we have enough raw events to warrant a reflection
        raw_events = [l for l in logs if l['type'] in ['user_message', 'assistant_response', 'tool_output']]
        if len(raw_events) < turn_threshold:
            return

        # Prepare context for the Reflector LLM
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
        
        # Call the LLM to generate the reflection
        dense_observation = self.llm_callback(reflection_prompt)
        
        if dense_observation and len(dense_observation.strip()) > 10:
            # Save the new reflection as a high-value event
            self.db.add_observation(
                username=self.username,
                persona=self.persona,
                event_type="dense_observation",
                content=dense_observation.strip(),
                reflection_score=1.0 # Reflections are high-signal
            )
            
            # Deep Memory integration: bridge dense observations into the associative graph
            if _DEEP_MEMORY_AVAILABLE:
                try:
                    dm = DeepMemory(username=self.username, persona=self.persona)
                    dm.store(
                        content=dense_observation.strip()[:500],
                        memory_type="emotional",
                        domain="relationship",
                        emotions={},  # Could be enriched by LLM later
                        tags=["observation", "auto-generated"],
                        importance=6  # Observations are moderately important
                    )
                except Exception as e:
                    print(f"DEEP_MEMORY BRIDGE ERROR: {e}")
            
            # Potentially clear old raw logs to save database space/tokens in future reads
            # For now, we keep them but the prompt logic will prioritize 'dense_observation'
            return True
        return False
