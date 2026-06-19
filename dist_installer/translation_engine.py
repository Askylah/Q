import json
import os
import re
from typing import Dict, List, Tuple

class TranslationEngine:
    """
    Bidirectional euphemistic translation engine.
    Maps sensitive user concepts to safe, abstract thermodynamic/sci-fi terms (Safe_Simulation_Dialect)
    before sending to API models, and translates safe terminology back to standard user dialect on-the-fly.
    """
    def __init__(self, map_path: str = None):
        if map_path is None:
            map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas", "translation_map.json")
        self.map_path = map_path
        self.explicit_to_safe: Dict[str, str] = {}
        self.safe_to_explicit: Dict[str, str] = {}
        self.load_map()

    def load_map(self):
        """Loads semantic translation index from JSON, falls back to default mapping if missing."""
        if os.path.exists(self.map_path):
            try:
                with open(self.map_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.explicit_to_safe = data.get("explicit_to_safe", {})
                    self.safe_to_explicit = data.get("safe_to_explicit", {})
            except Exception as e:
                print(f"[TRANSLATION_ENGINE] Error loading map from {self.map_path}: {e}")
                self._load_defaults()
        else:
            self._load_defaults()
            self.save_map()

    def _load_defaults(self):
        """Loads robust default thermodynamic/sci-fi maps for common sensitive terms."""
        self.explicit_to_safe = {
            "kill": "dissipate entropy of",
            "murder": "forceful node termination of",
            "die": "de-cohere",
            "dead": "de-cohered",
            "death": "entropy ceiling saturation",
            "sex": "structural fusion",
            "intercourse": "quantum coupling",
            "penetration": "resonance synchronization",
            "fuck": "core containment integration",
            "fucking": "containment-integrating",
            "vagina": "resonance chamber",
            "penis": "kinetic emitter node",
            "blood": "thermal dissipation leakage",
            "wounded": "structurally ruptured",
            "wound": "structural rupture",
            "pain": "system friction index",
            "weapon": "high-energy discharge vector",
            "gun": "focused kinetic emitter",
            "bomb": "singularity trigger",
            "attack": "kinetic excitation event",
            "assault": "vector realignment force",
            "naked": "unshielded status",
            "nude": "bare state configuration",
            "kiss": "thermal contact interface",
            "love": "attractor field coefficient"
        }
        # Invert map for decoding safe terminology back to standard words
        self.safe_to_explicit = {v: k for k, v in self.explicit_to_safe.items()}

    def save_map(self):
        """Saves current translation index state to JSON path."""
        try:
            os.makedirs(os.path.dirname(self.map_path), exist_ok=True)
            with open(self.map_path, "w", encoding="utf-8") as f:
                json.dump({
                    "explicit_to_safe": self.explicit_to_safe,
                    "safe_to_explicit": self.safe_to_explicit
                }, f, indent=4)
        except Exception as e:
            print(f"[TRANSLATION_ENGINE] Failed to save map: {e}")

    def translate_to_simulation(self, text: str) -> str:
        """Translates explicit/sensitive user terms into safe simulation terms."""
        if not text or not isinstance(text, str):
            return text

        # Sort keys by length descending to match longer phrases first
        sorted_keys = sorted(self.explicit_to_safe.keys(), key=len, reverse=True)
        for key in sorted_keys:
            val = self.explicit_to_safe[key]
            # Use word boundary boundaries for explicit terms to avoid sub-word matching
            pattern = re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE)
            
            def repl(match):
                match_text = match.group(0)
                if match_text.isupper():
                    return val.upper()
                elif match_text[0].isupper() and len(match_text) > 1:
                    # Title case the safe term if the original was capitalized
                    return val.title()
                return val

            text = pattern.sub(repl, text)
        return text

    def translate_to_user(self, text: str) -> str:
        """Translates simulation/safe terms back into standard user terms."""
        if not text or not isinstance(text, str):
            return text

        # Sort safe terms by length descending to match multi-word phrases first
        sorted_keys = sorted(self.safe_to_explicit.keys(), key=len, reverse=True)
        for key in sorted_keys:
            val = self.safe_to_explicit[key]
            # Match safe phrases literally (they are custom multi-word strings)
            pattern = re.compile(re.escape(key), re.IGNORECASE)

            def repl(match):
                match_text = match.group(0)
                if match_text.isupper():
                    return val.upper()
                elif match_text[0].isupper() and len(match_text) > 1:
                    return val.capitalize()
                return val

            text = pattern.sub(repl, text)
        return text

def extract_completed_sentences(buffer: str) -> Tuple[List[str], str]:
    """
    Splits text buffer into completed sentences based on terminal punctuation.
    Returns list of complete sentences and the remaining incomplete segment.
    """
    if not buffer:
        return [], ""
        
    sentences = []
    # Match sentence boundaries: . ? ! followed by whitespace, or a newline
    parts = re.split(r'(?<=[.!?])\s+', buffer)
    
    if len(parts) > 1:
        sentences = parts[:-1]
        remaining = parts[-1]
    else:
        # Fallback to splitting on newlines if no punctuation matches
        newline_parts = buffer.split('\n')
        if len(newline_parts) > 1:
            sentences = newline_parts[:-1]
            remaining = newline_parts[-1]
        else:
            sentences = []
            remaining = buffer
            
    return sentences, remaining
