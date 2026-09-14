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

# Neuromodulator coupling (optional)
try:
    import dopamine_state
    _DA_AVAILABLE = True
except ImportError:
    _DA_AVAILABLE = False

# Inhibition organ (optional). The Reflector is its main input: a "shrug"
# (window looked at, nothing new, no LLM call) is one redundancy tick, a
# successful reflection resets the streak.
try:
    import gaba_state
    _GABA_AVAILABLE = True
except ImportError:
    _GABA_AVAILABLE = False

import os as _os
import reflect_gate
try:
    import telemetry as _tm
except Exception:  # telemetry must never be load-bearing
    _tm = None


def _emit(*a, **k):
    if _tm is not None:
        try:
            _tm.emit(*a, **k)
        except Exception:
            pass
# Novelty pre-check: at/above this cosine between the newest N raw events and
# the N before them, the reflector shrugs instead of paying for an LLM call.
# PROPOSED (lab_notes/gaba_inhibition.md §7): same-register chunks of one
# conversation run hotter than reflection-vs-corpus, so this sits above the
# 0.70 the lab note measured for that. The replay sets it.
REFLECT_SIM_CEIL = float(_os.getenv("REFLECT_SIM_CEIL", "0.85"))
REFLECT_NOVELTY_GATE = _os.getenv("REFLECT_NOVELTY_GATE", "1") not in ("0", "false", "False", "")


def _chunk_similarity(new_text: str, prior_text: str):
    """Cosine between two text chunks on the shared MiniLM. None when the
    model is unavailable so the gate fails open (reflect_gate handles None)."""
    try:
        from embedding_model import get_shared_model
        model = get_shared_model()
        if not model:
            return None
        import numpy as _np
        v = model.encode([new_text, prior_text], convert_to_numpy=True)
        a, b = v[0], v[1]
        den = float(_np.linalg.norm(a) * _np.linalg.norm(b))
        if den == 0.0:
            return None
        return float(_np.dot(a, b) / den)
    except Exception:
        return None

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
        # FIX(reflect-every-turn): this used to count raw events in the 20-row
        # window and reflect whenever there were >= turn_threshold of them --
        # with no memory of what it had already summarised, so from turn 5 on
        # it fired on EVERY user message (one dense_observation per turn, each
        # 18/20 identical to the last; lab_notes/gaba_inhibition.md §6.2). The
        # threshold was a start delay, not a cadence. reflect_gate now answers
        # two questions: enough NEW raw events since the last reflection
        # (watermark), and did the conversation actually MOVE (novelty
        # pre-check on the shared MiniLM, no LLM call spent to find out).
        full = self.db.get_observation_log(
            self.username, self.persona,
            limit=reflect_gate.window_limit(turn_threshold, prompt_rows=20))
        logs = full[-20:]   # the transcript the LLM sees is unchanged: last 20 rows
        plan = reflect_gate.plan_reflection(
            full, turn_threshold,
            similarity=_chunk_similarity if REFLECT_NOVELTY_GATE else None,
            sim_ceil=REFLECT_SIM_CEIL)
        _near = plan["nearest"]
        _near_s = "none" if _near is None else f"{_near:.3f}"
        _intensity = plan.get("intensity")
        # Dopamine posture at the moment of the verdict, recorded (NOT acted
        # on): lets the calibration split shrugs by whether the explore gate
        # was even open. Gating the watermark on tonic is a later decision,
        # taken from these rows, not before them.
        _tonic = None
        _gate_open = None
        if _DA_AVAILABLE:
            try:
                _tonic = dopamine_state.get_state(self.username, self.persona)["tonic"]
                _gate_open = bool(dopamine_state.should_explore(_tonic))
            except Exception:
                _tonic = None
        # Every verdict lands on disk, including "wait": the wait rows are the
        # cadence trace (one per user turn) and cost nothing.
        _emit("reflect", plan["verdict"], self.username, self.persona,
              nearest=_near, intensity=_intensity, tonic=_tonic, gate_open=_gate_open,
              new_events=plan["new_events"], threshold=turn_threshold,
              sim_ceil=REFLECT_SIM_CEIL, novelty_gate=REFLECT_NOVELTY_GATE,
              window_rows=len(full), reason=plan["reason"])
        if plan["verdict"] == "wait":
            return
        if plan["verdict"] == "shrug":
            # The redundancy event. Logged on the [DA] channel next to the
            # novelty lines so the replay can count both from one grep.
            print(f"[DA] reflect: shrug nearest={_near_s} new_events={plan['new_events']} "
                  f"-- {plan['reason']}", flush=True)
            if _GABA_AVAILABLE:
                try:
                    gaba_state.on_redundant(self.username, self.persona, nearest=_near,
                                            source="reflect", intensity=_intensity)
                except Exception as e:
                    print(f"[GABA] on_redundant failed (non-fatal): {e}", flush=True)
            return False
        print(f"[DA] reflect: go nearest={_near_s} new_events={plan['new_events']} "
              f"-- {plan['reason']}", flush=True)

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
        Return the raw paragraph, then on the FINAL line output exactly:
        VALENCE: <a float between 0.0 and 1.0 rating the user's emotional tone across this transcript, where 0.0 is very negative and 1.0 is very positive>
        """
        dense_observation = self.llm_callback(reflection_prompt)
        
        # ---- SOCIAL RPE: extract valence rating from the reflection ----
        valence_observed = None
        if dense_observation:
            import re as _re
            _match = _re.search(r"VALENCE:\s*([0-9.]+)\s*$", dense_observation.strip())
            if _match:
                try:
                    valence_observed = float(_match.group(1))
                except ValueError:
                    valence_observed = None
                dense_observation = _re.sub(r"\s*VALENCE:\s*[0-9.]+\s*$", "", dense_observation).strip()
        
        if _DA_AVAILABLE and valence_observed is not None:
            try:
                _soc = dopamine_state.social_reward(self.username, self.persona, valence_observed)
                # §6.8: this writer used to print nothing on success.
                print(f"[DA] social: valence={valence_observed:.2f} expected={_soc.get('expected')} "
                      f"rpe={_soc.get('rpe')} tonic={_soc.get('tonic')} phasic={_soc.get('phasic')}",
                      flush=True)
                _emit("da", "social", self.username, self.persona, valence=valence_observed,
                      expected=_soc.get("expected"), rpe=_soc.get("rpe"),
                      tonic=_soc.get("tonic"), phasic=_soc.get("phasic"))
            except Exception as e:
                print(f"[DA] social_reward failed (non-fatal): {e}")
                _emit("da", "social_failed", self.username, self.persona, error=str(e))
        
        if dense_observation and len(dense_observation.strip()) > 10:
            self.db.add_observation(
                username=self.username,
                persona=self.persona,
                event_type="dense_observation",
                content=dense_observation.strip(),
                reflection_score=1.0
            )
            _emit("reflect", "stored", self.username, self.persona, nearest=_near,
                  chars=len(dense_observation.strip()), valence=valence_observed)
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
            if _GABA_AVAILABLE:
                try:
                    gaba_state.on_novel(self.username, self.persona, nearest=_near, source="reflect")
                except Exception as e:
                    print(f"[GABA] on_novel failed (non-fatal): {e}", flush=True)
            return True
        _emit("reflect", "empty", self.username, self.persona, nearest=_near,
              chars=len((dense_observation or "").strip()))
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
