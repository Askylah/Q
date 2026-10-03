"""
persona_tail.py -- what a persona puts at the END of its own prompt.

The persona file sits at the head of the system prompt and describes a voice.
The tail is where a long prompt is still being listened to, so two things
belong there that the head cannot do:

- CONTRAST pairs: the failure next to the fix, in the persona's own voice.
  A persona file says "open mid-thought"; a pair shows the greeting it must
  not write and the line it writes instead. Showing beats describing.
- STANCE overrides: the same four dispositions efferent.py renders, written
  in this persona's idiom. efferent owns WHEN a stance fires; this file only
  owns how it reads. A bored Rick and a bored anyone are different sentences.

One file per persona, named by "tail_file" in personas.json. It borrows the
module header format and zettel_engine's parser so there is one parser, not
two. It borrows NOTHING else. This is not retrieval: zettel_engine decides
what lore loads from what the operator SAID; this file is gated on what the
organs ARE DOING, and never enters the graph.

    ID: CONTRAST-001            a pair. Body is free text, by convention
    Type: ALWAYS_LOAD           [REGRESSION] ... then [<NAME>] ...
    ---                         ALWAYS_LOAD means what it means in the persona
    ...body...                  file: in the prompt every turn.
    ---
    ID: CONTRAST-BORED-001      Type BAND: rides only while efferent reports
    Type: BAND                  one of the bands listed.
    Triggers: inhibited, restless
    ---
    ID: DISP-INHIBITED          a stance override for the band(s) listed.
    Type: BAND
    Triggers: inhibited

Triggers is the only list field the parser keeps, so the band list lives
there. In this file it holds BAND NAMES, never message keywords. Type
ON_DEMAND is refused here, loudly: it means "the zettel engine retrieves
this", and nothing in a tail file is retrieved.

The tail file must NOT be listed in on_demand_files. There the engine would
compile the pairs as lore and read "inhibited" as a keyword to match against
the operator's message. load_for() refuses a file configured both ways.

RULES:
1. No persona, no file, no tail. Absent config returns empty everything and
   the prompt is the prompt it was.
2. Read-only and stateless apart from an mtime cache. No organ is imported.
3. Lesionable. PERSONA_TAIL_OFF drops the ALWAYS_LOAD pairs, read per call,
   so a pair's effect on the voice can be measured rather than assumed. The
   band-gated content hangs off efferent's band and dies with its lesions.
"""

import os

BANDS = ("inhibited", "restless", "spike", "exploring")
TYPE_BAND = "BAND"
CONTRAST_PREFIX = "CONTRAST-"
STANCE_PREFIX = "DISP-"
# A tail is calibration, not a second persona file. Past this it is pushing
# the turn's real content up the prompt; pairs are dropped whole, last first.
MAX_CONTRAST_CHARS = int(os.getenv("PERSONA_TAIL_MAX_CHARS", "3000"))

CONTRAST_HEADER = (
    "Each pair below shows a failure first and your voice second. Never write "
    "the first kind. These are calibration, not script: do not quote them, "
    "reuse their wording, or mention them."
)

_EMPTY = {"stances": {}, "always": [], "gated": []}
_CACHE = {}   # path -> (mtime, parsed)


def _off(name: str) -> bool:
    return os.getenv(name, "") not in ("", "0", "false", "False")


def _resolve(path: str) -> str:
    if not path:
        return ""
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    return path


def _parse(path: str) -> dict:
    # Lazy: zettel_engine pulls in the embedding stack. On the chat path it is
    # already imported by the time this runs; the parser itself is pure.
    from zettel_engine import parse_on_demand_file

    out = {"stances": {}, "always": [], "gated": []}
    for mod in parse_on_demand_file(path):
        mid = (mod.id or "").upper()
        mtype = (mod.type or "").upper()
        if not (mid.startswith(STANCE_PREFIX) or mid.startswith(CONTRAST_PREFIX)):
            continue   # not ours: this is not a lore file
        if mtype == "ON_DEMAND":
            print(f"[PERSONA_TAIL] {os.path.basename(path)}: {mod.id} is Type ON_DEMAND. "
                  f"That means zettel retrieval, and a tail file is never retrieved. "
                  f"Use Type: {TYPE_BAND} (gated on a band) or ALWAYS_LOAD; skipped.", flush=True)
            continue
        bands = [t for t in mod.triggers if t in BANDS]
        unknown = [t for t in mod.triggers if t not in BANDS]
        if unknown:
            print(f"[PERSONA_TAIL] {os.path.basename(path)}: {mod.id} names unknown "
                  f"band(s) {unknown}; known bands are {list(BANDS)}.", flush=True)
        if mid.startswith(STANCE_PREFIX):
            if not bands:
                print(f"[PERSONA_TAIL] {os.path.basename(path)}: {mod.id} has no band in "
                      f"Triggers; skipped.", flush=True)
            for b in bands:
                out["stances"][b] = mod.content
        elif mid.startswith(CONTRAST_PREFIX):
            if mtype == "ALWAYS_LOAD":
                out["always"].append((mod.id, mod.content))
            elif mtype == TYPE_BAND and bands:
                out["gated"].append((mod.id, set(bands), mod.content))
            else:
                print(f"[PERSONA_TAIL] {os.path.basename(path)}: {mod.id} (Type {mod.type!r}) "
                      f"names no known band, so it can never ride; skipped.", flush=True)
    return out


def load(path: str) -> dict:
    """Parsed tail for a file path, cached on mtime. Never raises."""
    path = _resolve(path)
    if not path or not os.path.exists(path):
        return _EMPTY
    try:
        mtime = os.path.getmtime(path)
        hit = _CACHE.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        parsed = _parse(path)
        _CACHE[path] = (mtime, parsed)
        return parsed
    except Exception as e:
        print(f"[PERSONA_TAIL] failed to load {path}: {e}", flush=True)
        return _EMPTY


def load_for(persona_data: dict) -> dict:
    """Tail for a personas.json entry. A file configured as BOTH tail and
    on-demand is refused: the zettel engine would be matching band names
    against the operator's message, and the pairs would load twice."""
    pd = persona_data or {}
    tail_file = pd.get("tail_file", "")
    if not tail_file:
        return _EMPTY
    on_demand = list(pd.get("on_demand_files") or [])
    if pd.get("on_demand_file"):
        on_demand.append(pd["on_demand_file"])
    if _resolve(tail_file) in {_resolve(p) for p in on_demand if p}:
        print(f"[PERSONA_TAIL] {tail_file} is listed as both tail_file and an on-demand "
              f"file. Remove it from on_demand_files; tail ignored until then.", flush=True)
        return _EMPTY
    return load(tail_file)


def select_contrast(tail: dict, band: str = "") -> list:
    """(id, content) pairs for this turn: ALWAYS_LOAD unless lesioned, then the
    ones gated on the current band. Gated pairs come first in the budget
    because they are about right now; the list is rendered always-first."""
    always = [] if _off("PERSONA_TAIL_OFF") else list(tail.get("always", []))
    gated = [(i, c) for (i, bands, c) in tail.get("gated", []) if band and band in bands]
    kept_gated, kept_always, used = [], [], 0
    for bucket, kept in ((gated, kept_gated), (always, kept_always)):
        for pid, content in bucket:
            if used + len(content) > MAX_CONTRAST_CHARS:
                continue
            kept.append((pid, content))
            used += len(content)
    return kept_always + kept_gated


def render_contrast(tail: dict, band: str = "") -> str:
    pairs = select_contrast(tail, band)
    if not pairs:
        return ""
    body = "\n\n".join(content for _, content in pairs)
    return f"\n[VOICE_CONTRAST]\n{CONTRAST_HEADER}\n\n{body}\n[/VOICE_CONTRAST]\n"
