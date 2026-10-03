"""
Mode Detection Engine — THE TRENCHES
Classifies user input to determine active character mode.
Developed for PersonaApp by askylah_

Modes determine which Global Rules module governs output:
  - immersive_rp:        Word ceilings enforced, one-beat rule, tonal contrast available
  - technical_utility:   Word ceilings suspended, task completion priority, code formatting allowed
  - creative_writer:     Word ceilings suspended, multi-beat scenes, narrative perspective unlocked
  - experiential_utility: Soft ceilings, warmth permitted, engagement-loop priority

The character's BASE type (from persona config) sets the default.
This engine detects when user input signals a MODE SHIFT away from that default.
"""

import re
from typing import Optional

# ============================================================
# SIGNAL PATTERNS
# ============================================================
# Each pattern set maps to a mode. Scored by match density.
# These are intentionally broad — false positives are cheaper
# than false negatives (missing a mode shift kills the response).

TECHNICAL_SIGNALS = {
    # Explicit code/tech markers
    "code_blocks": re.compile(r"```[\s\S]*?```", re.MULTILINE),
    "inline_code": re.compile(r"`[^`]+`"),
    "error_traces": re.compile(
        r"(traceback|error|exception|stack trace|segfault|errno|"
        r"syntaxerror|typeerror|valueerror|keyerror|importerror|"
        r"referenceerror|nullpointer|undefined is not)",
        re.IGNORECASE
    ),
    "file_extensions": re.compile(
        r"\b\w+\.(py|js|ts|jsx|tsx|html|css|json|yaml|yml|toml|sql|sh|rs|go|cpp|c|java|rb|php|swift|kt)\b",
        re.IGNORECASE
    ),
    # Intent keywords
    "tech_verbs": re.compile(
        r"\b(debug|fix|refactor|implement|deploy|compile|parse|optimize|"
        r"install|configure|build|migrate|test|lint|format|"
        r"write a (script|function|class|module|program|api|endpoint|query)|"
        r"explain\b.*?\b(code|error|bug|function|class|module)|"
        r"help\b.*?\b(code|program|build|set ?up|configure|connect|debug|fix|refactor)|"
        r"what does this (code|error|function|script) (do|mean)|"
        r"how (do|can|should) (i|you|we)\b.*?\b(code|program|build|set ?up|configure|connect|deploy))\b",
        re.IGNORECASE
    ),
    # Technical nouns (high signal)
    "tech_nouns": re.compile(
        r"\b(api|endpoint|database|server|docker|kubernetes|git|"
        r"terraform|nginx|redis|postgres|mongodb|graphql|rest|"
        r"webpack|vite|npm|pip|conda|virtualenv|"
        r"algorithm|data structure|regex|recursion|"
        r"frontend|backend|fullstack|devops|ci\/cd|"
        r"function|variable|class|method|constructor|"
        r"array|object|string|integer|boolean|null|"
        r"loop|iterator|callback|promise|async|await|"
        r"component|hook|state|props|render|"
        r"query|schema|migration|index|join|"
        r"commit|branch|merge|pull request|push)\b",
        re.IGNORECASE
    ),
}

CREATIVE_SIGNALS = {
    "creative_requests": re.compile(
        r"\b(write\b.*?\b(story|chapter|scene|poem|narrative|dialogue|monologue|"
        r"fiction|novel|screenplay|fanfic|backstory|lore)|"
        r"continue\b.*?\b(story|chapter|scene|narrative|writing)|"
        r"describe\b.*?\b(scene|setting|character|world|moment)|"
        r"what happens next|"
        r"start\b.*?\b(story|chapter|scene|narrative)|"
        r"tell me a story|"
        r"worldbuild|flesh out|expand\b.*?\bscene|"
        r"write\b.*?\bin (first|second|third) person|"
        r"from (his|her|their|the) (perspective|point of view|pov))",
        re.IGNORECASE
    ),
    "narrative_markers": re.compile(
        r"\b(chapter \d|act \d|scene \d|part \d|"
        r"protagonist|antagonist|narrator|"
        r"plot twist|climax|denouement|rising action|"
        r"point of view|pov|"
        r"genre|tone shift|narrative arc)\b",
        re.IGNORECASE
    ),
}

# Signals that the user is in casual/RP mode (reinforces default for RP characters)
CASUAL_SIGNALS = {
    "short_message": None,  # Handled by length check, not regex
    "rp_actions": re.compile(r"\*[^*]+\*"),  # Asterisk actions
    "emotional_content": re.compile(
        r"\b(feel|feeling|felt|scared|happy|sad|angry|anxious|"
        r"lonely|excited|nervous|worried|love|hate|miss|"
        r"thank you|thanks|sorry|please)\b",
        re.IGNORECASE
    ),
    "greetings": re.compile(
        r"^(hey|hi|hello|yo|sup|what'?s up|howdy|morning|evening|"
        r"good (morning|evening|night)|how are you|how'?s it going)",
        re.IGNORECASE
    ),
}


# ============================================================
# SCORING ENGINE
# ============================================================

def _count_matches(text: str, patterns: dict) -> int:
    """Count total pattern matches across a signal set."""
    score = 0
    for key, pattern in patterns.items():
        if pattern is None:
            continue
        matches = pattern.findall(text)
        score += len(matches)
    return score


def detect_mode(
    user_message: str,
    base_type: str = "immersive_rp",
    chat_history: list = None,
    sensitivity: float = 1.0
) -> dict:
    """
    Detect the active mode for this turn based on user input.

    Args:
        user_message:  The current user message.
        base_type:     The character's default type from persona config.
        chat_history:  Recent messages for context (optional, uses last 2).
        sensitivity:   Multiplier for mode-shift thresholds. 
                       Higher = harder to shift away from base. Default 1.0.

    Returns:
        dict with:
            - "active_mode": str — the mode to use for this turn
            - "base_type": str — the character's default type (unchanged)
            - "confidence": float — 0.0 to 1.0
            - "signals": dict — raw scores per mode
            - "shifted": bool — True if mode differs from base_type
    """
    text = user_message.strip()
    msg_length = len(text.split())

    # Score each mode
    tech_score = _count_matches(text, TECHNICAL_SIGNALS)
    creative_score = _count_matches(text, CREATIVE_SIGNALS)
    casual_score = _count_matches(text, CASUAL_SIGNALS)

    # Creative requests are typically one explicit phrase, not scattered keywords.
    # A single creative match ("write me a story") is a strong signal — boost it.
    if creative_score >= 1:
        creative_score = max(creative_score * 2, 2)

    # Short messages boost casual score
    if msg_length <= 8:
        casual_score += 3
    elif msg_length <= 15:
        casual_score += 1

    # Attached code blocks are strong technical signals
    if "```" in text:
        tech_score += 5

    # File attachments boost technical
    if "[Attached file:" in text:
        tech_score += 3

    # Context from recent history (if provided)
    if chat_history and len(chat_history) >= 2:
        recent = " ".join([m.get("content", "") for m in chat_history[-2:]])
        # If recent conversation was technical, lower the shift threshold
        recent_tech = _count_matches(recent, TECHNICAL_SIGNALS)
        if recent_tech >= 3:
            tech_score += 2  # Momentum bonus

    # ============================================================
    # THRESHOLD LOGIC
    # ============================================================
    # The base_type gets a "home advantage" — it takes more signal
    # to shift AWAY from the character's natural mode than to stay.

    shift_threshold = max(2, int(2 * sensitivity))  # Default: need 2+ signals to shift

    scores = {
        "technical_utility": tech_score,
        "creative_writer": creative_score,
        "immersive_rp": casual_score,
        "experiential_utility": casual_score,  # Same casual signals apply
    }

    # Determine winner
    active_mode = base_type
    confidence = 0.5  # Default: uncertain

    # Check if any non-base mode clears the shift threshold
    shift_candidates = []
    for mode, score in scores.items():
        if mode == base_type:
            continue
        # Map experiential_utility casual signals to immersive_rp for comparison
        if mode == "experiential_utility" and base_type == "immersive_rp":
            continue
        if mode == "immersive_rp" and base_type == "experiential_utility":
            continue
        if score >= shift_threshold:
            shift_candidates.append((mode, score))

    if shift_candidates:
        # Pick the strongest shift candidate
        shift_candidates.sort(key=lambda x: -x[1])
        best_mode, best_score = shift_candidates[0]
        # Shift only if the candidate clearly outscores the base's natural signals
        base_score = scores.get(base_type, 0)
        if best_score > base_score or best_score >= shift_threshold:
            active_mode = best_mode

    # Calculate confidence
    total = sum(scores.values()) or 1
    winning_score = scores.get(active_mode, 0)
    confidence = min(1.0, winning_score / max(total, 1))

    # Edge case: if BOTH technical and creative are high, technical wins
    # (code explanation > story writing when ambiguous)
    if tech_score >= shift_threshold and creative_score >= shift_threshold:
        if tech_score >= creative_score:
            active_mode = "technical_utility"
        else:
            active_mode = "creative_writer"

    shifted = active_mode != base_type

    return {
        "active_mode": active_mode,
        "base_type": base_type,
        "confidence": round(confidence, 2),
        "signals": {
            "technical": tech_score,
            "creative": creative_score,
            "casual": casual_score,
        },
        "shifted": shifted,
    }


# ============================================================
# CONVENIENCE
# ============================================================

def get_mode_label(mode: str) -> str:
    """Human-readable mode label for debugging/logging."""
    labels = {
        "immersive_rp": "🎭 Immersive RP",
        "technical_utility": "💻 Technical",
        "creative_writer": "✍️ Creative",
        "experiential_utility": "🎅 Experiential",
    }
    return labels.get(mode, mode)
