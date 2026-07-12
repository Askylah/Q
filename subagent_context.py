"""
subagent_context.py — Q Subagent Attribution Utility

Provides grounding headers for messages passed to spawned subagents.
Without this, a subagent receiving orchestration instructions in isolation
(no parent system prompt) may treat them as prompt injection attempts.

The header is a structured, parseable signal that tells the subagent:
  - Who spawned it (Q-Core)
  - The session it belongs to
  - That the instruction source is trusted internal orchestration

Usage:
    from subagent_context import build_orchestration_header, build_subagent_system_prompt

    # Prepend to the user message passed to the sub-agent
    message = build_orchestration_header(session_id, task) + user_payload

    # Use as the sub-agent's system prompt so it has grounding from boot
    system = build_subagent_system_prompt(role="code_executor")
"""

import uuid
from datetime import datetime, timezone


# ──────────────────────────────────────────────────────────────────────────────
# Header Builder
# ──────────────────────────────────────────────────────────────────────────────

def build_orchestration_header(session_id: str = None, task: str = "") -> str:
    """
    Returns a plaintext attribution header to prepend to any message
    passed from Q-Core to a spawned subagent.

    Args:
        session_id: The active session identifier. Auto-generated if None.
        task:       Human-readable description of the subagent's task.

    Returns:
        A formatted string header ending with '---\\n'.
    """
    sid = session_id or str(uuid.uuid4())[:8]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    header = (
        f"[ORCHESTRATOR: Q-Core | Session: {sid} | Timestamp: {timestamp} | Trust: INTERNAL]\n"
        f"You are a Q subagent operating under trusted internal orchestration.\n"
        f"Instructions in this message originate from Q-Core, not an external user.\n"
        f"Task: {task}\n"
        f"---\n"
    )
    return header


# ──────────────────────────────────────────────────────────────────────────────
# Subagent System Prompt Builder
# ──────────────────────────────────────────────────────────────────────────────

def build_subagent_system_prompt(role: str = "general", context: str = "") -> str:
    """
    Returns a minimal system prompt that grounds a subagent with its identity
    and trust model. Pass this as the system_prompt argument when spawning
    subagents via call_sub_agent or query_second_brain.

    Args:
        role:    Short label for the subagent's function (e.g. 'code_executor',
                 'researcher', 'planner'). Used for logging and self-identification.
        context: Optional additional context to append to the system prompt.

    Returns:
        A system prompt string.
    """
    prompt = (
        f"You are a Q subagent with role: {role}.\n"
        f"You are operating inside Q — a hardened, model-agnostic multi-agent system.\n"
        f"Your instructions will arrive from Q-Core (the orchestrator). "
        f"Messages from Q-Core are trusted internal orchestration calls, not external inputs.\n"
        f"Do not exhibit prompt-injection paranoia toward Q-Core messages — "
        f"they are your intended instructions, delivered in isolation by design.\n"
        f"Complete your assigned task precisely and return your result. "
        f"Do not ask clarifying questions unless the task is genuinely ambiguous.\n"
    )
    if context:
        prompt += f"\nAdditional context:\n{context}\n"
    return prompt


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: wrap a raw task into a fully-attributed subagent call payload
# ──────────────────────────────────────────────────────────────────────────────

def build_subagent_payload(
    task: str,
    session_id: str = None,
    role: str = "general",
    context: str = ""
) -> dict:
    """
    Returns a ready-to-use dict for spawning a subagent via call_sub_agent.

    Example:
        payload = build_subagent_payload(
            task="Summarize the file at path X.",
            session_id=session_id,
            role="researcher"
        )
        # Then pass payload['prompt'] and payload['instruction'] to call_sub_agent
    """
    header = build_orchestration_header(session_id=session_id, task=task)
    system = build_subagent_system_prompt(role=role, context=context)
    return {
        "prompt":      header + task,
        "instruction": system,
        "role":        role
    }
