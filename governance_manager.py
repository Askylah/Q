"""
governance_manager.py — capability-based approval gating.

Design rule: approval is decided by what a tool DOES (its DangerLevel), never by
what its arguments say. Two hardening invariants, enforced independently:

  1. FAIL CLOSED. An unregistered tool is treated as DESTRUCTIVE, not KINETIC.
     A tool nobody classified is a tool nobody vouched for — gate it.

  2. FAIL LOUD AT BOOT. validate_registry_coverage() raises if the live dispatch
     table contains any tool without an explicit DangerLevel. You find the gap
     at import, not the first time an unclassified rmtree runs unattended.

Why this file exists: `delete_item` (the tool that actually calls shutil.rmtree)
was never registered. It fell to a KINETIC default, and under review_policy
"never" that returned "no approval needed" — so destruction ran silently,
directly contradicting the module's own "always approve destruction" promise.
The fix is not to add one entry; it's to make the omission impossible to repeat.
"""

from enum import IntEnum
from typing import Dict, Iterable, Set


class DangerLevel(IntEnum):
    INFO = 0         # Read-only, no state change (read, list, search, stage-preview)
    KINETIC = 1      # Reversible state change (write file, create, run code, commit)
    DESTRUCTIVE = 2  # Irreversible (delete, rmtree, wipe) — approval is unbypassable


class RegistryError(RuntimeError):
    """Raised at import if a dispatchable tool has no explicit DangerLevel."""


# ──────────────────────────────────────────────────────────────────────────────
# The registry. Keys MUST match the real dispatch names in api_parser or base names.
# Every tool that can be dispatched needs a line here — see validate_registry_
# coverage() below, which enforces exactly that at boot.
# ──────────────────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, DangerLevel] = {
    # --- INFO (Read-only / Safe) ---
    "read_file":                 DangerLevel.INFO,
    "read_text_file":            DangerLevel.INFO,
    "read_media_file":           DangerLevel.INFO,
    "read_multiple_files":       DangerLevel.INFO,
    "read_file_content":         DangerLevel.INFO,
    "list_dir":                  DangerLevel.INFO,
    "list_directory":            DangerLevel.INFO,
    "list_directory_with_sizes": DangerLevel.INFO,
    "directory_tree":            DangerLevel.INFO,
    "get_file_tree":             DangerLevel.INFO,
    "get_file_info":             DangerLevel.INFO,
    "list_allowed_directories":  DangerLevel.INFO,
    "search_files":              DangerLevel.INFO,
    "web_search":                DangerLevel.INFO,
    "search_web":                DangerLevel.INFO,
    "deep_lore_query":           DangerLevel.INFO,
    "activate_skill":            DangerLevel.INFO,
    "stage_write":               DangerLevel.INFO,   # touches no real file — pure preview
    "discard_staged_write":      DangerLevel.INFO,   # discards a pending stage, no real write
    "list_staged_writes":        DangerLevel.INFO,
    "query_second_brain":        DangerLevel.INFO,   # static reasoning delegation
    "list_mcp_tools":            DangerLevel.INFO,   # MCP discovery meta-tool
    "call_mcp_tool":             DangerLevel.INFO,   # MCP dynamic router meta-tool
    "read_file_lines":           DangerLevel.INFO,   # offset-based line-range file read
    "grep_workspace":            DangerLevel.INFO,   # regex content search across workspace
    
    # Git Read-only
    "git_status":                DangerLevel.INFO,
    "git_diff_unstaged":         DangerLevel.INFO,
    "git_diff_staged":           DangerLevel.INFO,
    "git_diff":                  DangerLevel.INFO,
    "git_log":                   DangerLevel.INFO,
    "git_show":                  DangerLevel.INFO,
    "git_branch":                DangerLevel.INFO,

    # --- KINETIC (Reversible state change / writes) ---
    "save_file_content":         DangerLevel.KINETIC,
    "write_file":                DangerLevel.KINETIC,
    "edit_file":                 DangerLevel.KINETIC,
    "create_item":               DangerLevel.KINETIC,
    "create_directory":          DangerLevel.KINETIC,
    "move_file":                 DangerLevel.KINETIC,
    "commit_staged_write":       DangerLevel.KINETIC,  # the stage->real write; has .bak backup
    "run_code_secure":           DangerLevel.KINETIC,
    "execute_python_lab":        DangerLevel.KINETIC,
    "generate_logic_tree":       DangerLevel.KINETIC,
    "create_zettel_link":        DangerLevel.KINETIC,
    "store_zettel_observation":  DangerLevel.KINETIC,
    "create_sandbox_tool":       DangerLevel.KINETIC,  # dynamic tool creation/compilation
    "call_sub_agent":            DangerLevel.KINETIC,  # recursive agent delegation
    
    # Git Write-path
    "git_commit":                DangerLevel.KINETIC,
    "git_add":                   DangerLevel.KINETIC,
    "git_reset":                 DangerLevel.KINETIC,
    "git_create_branch":         DangerLevel.KINETIC,
    "git_checkout":              DangerLevel.KINETIC,

    # --- DESTRUCTIVE (Irreversible — approval always required) ---
    "delete_item":               DangerLevel.DESTRUCTIVE,  # <-- calls shutil.rmtree
    "delete_file":               DangerLevel.DESTRUCTIVE,
    "wipe_memories":             DangerLevel.DESTRUCTIVE,
    "destructive_debug":         DangerLevel.DESTRUCTIVE,  # garage telemetry injector tool
}


def validate_registry_coverage(known_tools: Iterable[str]) -> None:
    """
    Assert every dispatchable tool has an explicit DangerLevel. Call this ONCE at
    startup with the live dispatch table, e.g.:

        import api_parser
        validate_registry_coverage(api_parser.TOOL_REGISTRY.keys())

    Raises RegistryError listing any unclassified tools. This is the boot-time
    shield: it turns "someone added a tool and forgot to classify it" from a
    silent runtime hole into a startup crash you cannot miss.
    """
    known: Set[str] = set(known_tools)
    missing = []
    for tool in known:
        if tool in _REGISTRY:
            continue
        if "__" in tool:
            base_name = tool.split("__", 1)[1]
            if base_name in _REGISTRY:
                continue
        missing.append(tool)

    if missing:
        raise RegistryError(
            "Tools dispatchable but unclassified in governance registry: "
            + ", ".join(sorted(missing))
            + ". Add an explicit DangerLevel for each before starting."
        )


class GovernanceManager:
    def __init__(self):
        # Default friction preference. NOT a security control — DESTRUCTIVE
        # ignores it entirely. This only tunes how often you're asked about
        # reversible (KINETIC) writes: "always" gates everything, "ask" gates
        # writes, "never" waves writes through. Deletion is never waved through.
        self.review_policy = "ask"  # "always" | "ask" | "never"
        self.registry = _REGISTRY

    def get_danger_level(self, tool_name: str) -> DangerLevel:
        """
        Fail closed: an unregistered tool is DESTRUCTIVE, never KINETIC. If boot
        validation is wired up this branch is unreachable — it's the belt to the
        validator's suspenders.
        """
        if tool_name in self.registry:
            return self.registry[tool_name]
            
        if "__" in tool_name:
            base_name = tool_name.split("__", 1)[1]
            if base_name in self.registry:
                return self.registry[base_name]

        print(f"[GOVERNANCE] Unregistered tool '{tool_name}' -> "
              f"treating as DESTRUCTIVE (fail-closed).")
        return DangerLevel.DESTRUCTIVE

    def _resolve_policy(self, username: str) -> str:
        """Load the user's friction preference; default to 'ask' on any failure."""
        try:
            import database as db
            settings = db.UserManager().get_user_settings(username)
            return settings.get("review_policy", "ask")
        except Exception as e:
            print(f"[GOVERNANCE] policy load failed for '{username}': {e} -> "
                  f"defaulting to 'ask'.")
            return "ask"

    def should_require_approval(self, tool_name: str, args: dict,
                                username: str = "default") -> bool:
        level = self.get_danger_level(tool_name)

        # Invariant: destruction always gets a human in the loop, whatever the
        # policy says. This is the line the old code let "never" override.
        if level == DangerLevel.DESTRUCTIVE:
            return True

        policy = self._resolve_policy(username)

        if policy == "always":
            return True
        if policy == "ask" and level >= DangerLevel.KINETIC:
            return True
        # policy == "never": reversible writes pass without a prompt (by design);
        # INFO under any policy needs no approval.
        return False


# Global instance
governance = GovernanceManager()


def get_governance_manager() -> GovernanceManager:
    return governance
