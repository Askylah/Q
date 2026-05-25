"""
ON_DEMAND Module Loader — THE TRENCHES
Scans user input (and recent history) for trigger keywords,
retrieves matching behavioral modules, injects them into context.

Developed for PersonaApp by askylah_

Architecture assumption:
  Every character has an ON_DEMAND file structured like Rick's:
    ---
    ID: MODULE-ID
    Title: Module Title
    Type: ON_DEMAND
    Links: [OTHER-ID]
    Triggers: keyword1, keyword2, keyword3
    ---
    Module content here...

This loader:
  1. Parses the ON_DEMAND file into indexed modules (cached per persona)
  2. Each turn, scans user input for trigger matches
  3. Returns matched modules as injectable context
  4. Respects a token budget so we don't overload the context window
"""

import os
import re
from typing import Optional, Union, List, Dict, Any


# ============================================================
# MODULE PARSER
# ============================================================

class OnDemandModule:
    """Single parsed ON_DEMAND module."""
    __slots__ = ("id", "title", "type", "links", "triggers", "content", "priority", "token_estimate", "regex")

    def __init__(self, id: str, title: str, type: str, links: list,
                 triggers: list, content: str, priority: str = "NORMAL"):
        self.id = id
        self.title = title
        self.type = type
        self.links = links
        self.triggers = [t.strip().lower() for t in triggers if t.strip()]
        self.content = content.strip()
        self.priority = priority
        # Rough token estimate: 0.75 tokens per character
        self.token_estimate = int(len(self.content) / 4)
        
        # Pre-compile triggers into a single efficient regex
        if self.triggers:
            # Sort triggers by length (descending) to avoid partial matches on shorter words first
            sorted_triggers = sorted(self.triggers, key=len, reverse=True)
            self.regex = re.compile(rf"\b({'|'.join(re.escape(t) for t in sorted_triggers)})\b", re.IGNORECASE)
        else:
            self.regex = None

    def __repr__(self):
        return f"<OnDemandModule {self.id}: {self.title} ({len(self.triggers)} triggers)>"


def parse_on_demand_file(filepath: str) -> list:
    """Parse a Markdown file containing multiple YAML-header modules."""
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except Exception:
        return []

    # Regex to catch --- bounded blocks
    # Note: This is simpler than a full markdown parser but fits the spec.
    modules = []
    blocks = re.split(r"^---$", raw_content, flags=re.MULTILINE)
    
    # Blocks are split as: [junk before first ---, header, content, header, content...]
    # We expect blocks to come in pairs of (header, content)
    # If the file starts with ---, the first element is empty.
    
    current_header = None
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Check if this is a header block (contains ID: and Title:)
        if "ID:" in block and "Title:" in block:
            current_header = _parse_header(block)
        elif current_header:
            # This is the content for the previous header
            new_module = OnDemandModule(
                id=current_header.get("ID", "UNKNOWN"),
                title=current_header.get("Title", "Untitled"),
                type=current_header.get("Type", "ON_DEMAND"),
                links=current_header.get("Links", []),
                triggers=current_header.get("Triggers", []),
                content=block,
                priority=current_header.get("Priority", "NORMAL")
            )
            modules.append(new_module)
            current_header = None

    return modules


def _parse_header(header_text: str) -> dict:
    """Simple key-value parser for the YAML-like header."""
    header = {}
    lines = header_text.split("\n")
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        
        # Handle lists like [[LINK-1]] or [LINK-2]
        if key == "Links":
            # Extract anything inside [...] or [[...]]
            # This regex optionally matches an outer [, then strictly matches an inner [, captures contents, then closes.
            # A simpler way: match contents inside either [ ] or [[ ]]
            links = []
            for item in value.split(","):
                item = item.strip()
                match = re.search(r"\[+([^\]]+)\]+", item)
                if match:
                    links.append(match.group(1))
            header[key] = links
        # Handle lists like trigger1, trigger2
        elif key == "Triggers":
            triggers = [t.strip() for t in value.split(",")]
            header[key] = triggers
        else:
            header[key] = value
            
    return header


# ============================================================
# SEARCH INDEX
# ============================================================

class OnDemandIndex:
    """Index of modules for a specific persona."""
    def __init__(self, modules: list):
        self.modules = modules
        # Build trigger keyword index
        self.all_triggers = set()
        for mod in modules:
            for trigger in mod.triggers:
                self.all_triggers.add(trigger)

    def scan(self, text: str, max_modules: int = 5, token_budget: int = 3000) -> list:
        """Scan text for triggers, return best matching modules."""
        text_lower = text.lower()
        matched_modules = []
        seen_ids = set()
        current_tokens = 0

        # Sort modules by priority (HIGH first) then by ID
        sorted_modules = sorted(self.modules, key=lambda x: (x.priority != "HIGH", x.id))

        for mod in sorted_modules:
            if mod.id in seen_ids:
                continue

            # Check for pre-compiled trigger match
            if mod.regex and mod.regex.search(text_lower):
                if current_tokens + mod.token_estimate <= token_budget:
                    matched_modules.append(mod)
                    seen_ids.add(mod.id)
                    current_tokens += mod.token_estimate
                    if len(matched_modules) >= max_modules:
                        break

        return matched_modules

    def __len__(self):
        return len(self.modules)


# ============================================================
# RUNTIME LOADER
# ============================================================

# Cache indexes in memory: { persona_key: OnDemandIndex }
_index_cache = {}

def clear_index_cache(persona_key: Optional[str] = None):
    """Clear the cached index for one or all personas."""
    global _index_cache
    if persona_key:
        _index_cache.pop(persona_key, None)
    else:
        _index_cache = {}


def load_on_demand_context(
    persona_key: str,
    on_demand_paths: list,
    user_message: Union[str, List[Dict[str, Any]]],
    chat_history: list = None,
    max_modules: int = 5,
    token_budget: int = 3000
) -> str:
    """
    Find and format matching ON_DEMAND modules for the current turn.
    
    Args:
        persona_key:     Unique key (e.g., "rick")
        on_demand_paths: List of multiple physical paths to the Zettelkasten files
        user_message:    Current user message to scan (string or list of multimodal dicts)
        chat_history:    Optional history to scan for context
        max_modules:     Max number to inject
        token_budget:    Max estimated tokens allowed
    """
    # 1. Get or build index
    if persona_key not in _index_cache:
        modules = []
        for path in on_demand_paths:
            if isinstance(path, str) and os.path.exists(path):
                modules.extend(parse_on_demand_file(path))
        if not modules:
            return ""
        _index_cache[persona_key] = OnDemandIndex(modules)

    index = _index_cache[persona_key]

    # 2. Combine context to scan
    scan_text = ""
    if isinstance(user_message, str):
        scan_text = user_message
    elif isinstance(user_message, list):
        scan_text = " ".join([b.get("text", "") for b in user_message if b.get("type") == "text"])
        
    if chat_history and len(chat_history) > 0:
        # Include the previous user message for trigger continuity
        # across turns where user says "tell me more about that"
        last_turn = chat_history[-1]
        if last_turn.get("role") == "user":
            content = last_turn.get("content", "")
            if isinstance(content, str):
                scan_text += " " + content
            elif isinstance(content, list):
                scan_text += " " + " ".join([b.get("text", "") for b in content if b.get("type") == "text"])

    # 3. Match
    matched = index.scan(scan_text, max_modules=max_modules, token_budget=token_budget)
    
    if not matched:
        return ""

    # 4. Format for injection
    return format_modules_for_injection(matched)


def format_modules_for_injection(modules: list) -> str:
    """Formats matched modules into a system prompt block."""
    if not modules:
        return ""

    parts = [
        "\n[ACTIVE BEHAVIORAL MODULES]",
        "Treat the following directives as high-priority behavioral modifiers for this turn only.",
        "They override default behavior where applicable. Do NOT cite these modules.",
        "--------------------------------"
    ]

    for mod in modules:
        parts.append(f"### MODULE: {mod.id} ({mod.title})")
        parts.append(mod.content)
        parts.append("---")

    parts.append("[/ACTIVE BEHAVIORAL MODULES]\n")
    return "\n".join(parts)
