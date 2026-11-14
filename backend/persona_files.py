"""
persona_files.py — a persona's prompt file is its prompt.

WHY THIS MODULE EXISTS
======================

personas.json maps a key to a prompt file ("file": "personas/rick.txt"), and those
files are what gets edited by hand. The DB row used to win everywhere anyway:

    main.py           personas = {**personas, **custom}   (the row replaced the file)
    stream_worker.py  SELECT name, system_prompt FROM custom_personas   (never opened the file)

So every hand edit needed a hand sync into the row. The rick.txt edit of 2026-10-19
never got one, and both chat and the daemon kept running the 10-14 row.

Now, when personas.json lists a file for a key and that file exists, its text is the
prompt for that key on every account on this machine, in chat and in the daemon.
The DB row is still written by the Workshop and still supplies everything else
(name, avatar, memory flags); only system_prompt comes from the file.

The Workshop writes the file too, or an edit made there would vanish behind it.
The personas/ files are gitignored, so before a save replaces one, its old bytes are
copied to DATA_DIR/persona_history/. That copy is the only undo there is.

personas.json is read from the repo root here, not via main.py's _APP_ROOT, which is
reassigned to backend/ in the workspace section of main.py.
"""
import json
import os
from datetime import datetime

import app_paths

PERSONAS_JSON = os.path.join(app_paths.APP_ROOT, "personas.json")
HISTORY_DIR = os.path.join(app_paths.DATA_DIR, "persona_history")


def load_specs() -> dict:
    """personas.json as a dict; {} if it is missing, unreadable or not an object."""
    try:
        with open(PERSONAS_JSON, "r", encoding="utf-8") as f:
            specs = json.load(f)
    except (OSError, ValueError):
        return {}
    return specs if isinstance(specs, dict) else {}


def prompt_path(key: str, specs: dict = None):
    """Absolute path of key's prompt file when personas.json lists one that exists, else None.

    Keys match case-insensitively, like the custom_personas queries (COLLATE NOCASE).
    Relative paths resolve against the directory personas.json lives in.
    """
    if not key:
        return None
    specs = load_specs() if specs is None else specs
    for k, spec in specs.items():
        if k.lower() != key.lower() or not isinstance(spec, dict) or not spec.get("file"):
            continue
        path = spec["file"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(PERSONAS_JSON), path)
        path = os.path.normpath(path)
        return path if os.path.isfile(path) else None
    return None


def read_prompt(key: str, specs: dict = None):
    """The text of key's prompt file, or None when it has none (the DB row stands)."""
    path = prompt_path(key, specs)
    if path is None:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def write_prompt(key: str, text: str, specs: dict = None):
    """Save text as key's prompt file. Returns the path written, or None when the key has
    no file or the file already holds exactly this text.

    Keeps the file's own newline style (rick.txt is CRLF; a textarea posts LF). Copies
    the old bytes to HISTORY_DIR first. Raises OSError if the file can't be written,
    so the caller can tell the Workshop the save didn't land.
    """
    path = prompt_path(key, specs)
    if path is None or text is None:
        return None
    with open(path, "rb") as f:
        old = f.read()
    newline = b"\r\n" if b"\r\n" in old else b"\n"
    new = text.replace("\r\n", "\n").encode("utf-8").replace(b"\n", newline)
    if new == old:
        return None
    os.makedirs(HISTORY_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with open(os.path.join(HISTORY_DIR, f"{stem}_{stamp}.txt"), "wb") as f:
        f.write(old)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path
