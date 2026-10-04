"""
Which model each daemon call runs on, in one place.

stream_worker uses these defaults for the actual calls; GET /settings/{username}
reports effective() so the Providers panel shows what the daemon really runs.
Before this module the panel showed its own hardcoded placeholder, which was
not the daemon's default, and saving it untouched changed the model.

Precedence, the same as stream_worker._daemon_model:
env override > the user's saved setting > the default.
"""
import os

# Two call sites, two jobs: the NLI gate returns a one-word verdict that
# resolve_semantic_conflict then acts on, so a wrong answer is a wrong row,
# and it gets the smartest flash; the monologue is persona prose.
DAEMON_NLI_MODEL_OPENROUTER = "google/gemini-3.5-flash"
DAEMON_MONOLOGUE_MODEL_OPENROUTER = "google/gemini-3.7-flash"

# settings column -> (env override variable, default model id)
SLOTS = {
    "daemon_nli_model": ("DAEMON_NLI_MODEL", DAEMON_NLI_MODEL_OPENROUTER),
    "daemon_monologue_model": ("DAEMON_MONOLOGUE_MODEL", DAEMON_MONOLOGUE_MODEL_OPENROUTER),
}


def effective(settings):
    """For each slot: {"model", "source" ("env" | "setting" | "default"),
    "default", "env", "env_var"} where "env" is the override (empty when unset)."""
    settings = settings or {}
    out = {}
    for setting, (env_var, default) in SLOTS.items():
        override = (os.getenv(env_var) or "").strip()
        saved = (settings.get(setting) or "").strip()
        if override:
            model, source = override, "env"
        elif saved:
            model, source = saved, "setting"
        else:
            model, source = default, "default"
        out[setting] = {"model": model, "source": source, "default": default,
                        "env": override, "env_var": env_var}
    return out
