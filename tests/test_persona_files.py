"""
Persona prompt files (2026-10-28): when personas.json lists a file for a key, that
file is the prompt, over the DB row, in chat and in the daemon. The Workshop
(POST /personas) writes the file as well as the row.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_persona_files.py

Throwaway SQLite file (PERSONAAPP_DB_PATH) and a temp personas.json + prompt files;
persona_files is pointed at those, so the real personas/ folder is never read or
written. Redis db 1. No LLM, no embedding call.

Each check names the failure it exists to prevent.
"""
import os, sys, tempfile, shutil, sqlite3, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, ROOT)
TMP = tempfile.mkdtemp(prefix="persona_files_")
os.environ["PERSONAAPP_DATA_DIR"] = TMP
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
os.environ["TELEMETRY_OFF"] = "1"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

import database as db
assert os.path.abspath(db.DB_PATH) == os.path.abspath(os.environ["PERSONAAPP_DB_PATH"]), \
    "refusing to run: the DB path override did not take (would touch a real database)"
import persona_files as pf

REAL_JSON = pf.PERSONAS_JSON
SPECS = {
    "rick": {"name": "Rick Sanchez", "file": "personas/rick.txt"},
    "lfp": {"file": "personas/lfp.txt"},
    "ghost": {"file": "personas/missing.txt"},
    "nofile": {"name": "no file listed"},
}
os.makedirs(os.path.join(TMP, "personas"))
with open(os.path.join(TMP, "personas.json"), "w", encoding="utf-8") as f:
    json.dump(SPECS, f)
RICK = os.path.join(TMP, "personas", "rick.txt")
LFP = os.path.join(TMP, "personas", "lfp.txt")
with open(RICK, "wb") as f:
    f.write(b"line one\r\nline two\r\n")
with open(LFP, "wb") as f:
    f.write(b"a\nb\n")
pf.PERSONAS_JSON = os.path.join(TMP, "personas.json")
pf.HISTORY_DIR = os.path.join(TMP, "history")
assert pf.PERSONAS_JSON != REAL_JSON

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def raw(path):
    with open(path, "rb") as f:
        return f.read()


def history():
    return sorted(os.listdir(pf.HISTORY_DIR)) if os.path.isdir(pf.HISTORY_DIR) else []


print("read_prompt")
check("a listed file is the prompt", pf.read_prompt("rick"), "line one\nline two\n")
check("keys match case-insensitively, like COLLATE NOCASE", pf.read_prompt("RICK"), "line one\nline two\n")
check("a listed file that is missing leaves the row standing", pf.read_prompt("ghost"), None)
check("a spec without a file leaves the row standing", pf.read_prompt("nofile"), None)
check("an unlisted key leaves the row standing", pf.read_prompt("eni"), None)
check("an empty key is not a lookup", pf.read_prompt(""), None)

print("write_prompt")
got = pf.write_prompt("rick", "line one\nline two changed\n")
check("a save returns the file it wrote", got, RICK)
check("a textarea's LF lands as the file's CRLF", raw(RICK), b"line one\r\nline two changed\r\n")
check("the old bytes go to history first", [raw(os.path.join(pf.HISTORY_DIR, h)) for h in history()],
      [b"line one\r\nline two\r\n"])
check("saving the same text again writes nothing", pf.write_prompt("rick", "line one\nline two changed\n"), None)
check("and adds no history copy", len(history()), 1)
pf.write_prompt("lfp", "a\r\nb changed\r\n")
check("an LF file stays LF even when CRLF is posted", raw(LFP), b"a\nb changed\n")
check("a key without a file writes nothing", pf.write_prompt("nofile", "x"), None)
check("a missing listed file is not created", (pf.write_prompt("ghost", "x"),
      os.path.exists(os.path.join(TMP, "personas", "missing.txt"))), (None, False))
check("no .tmp is left beside the file", sorted(os.listdir(os.path.join(TMP, "personas"))), ["lfp.txt", "rick.txt"])

print("chat: main.load_personas_logic")
import main
from fastapi.testclient import TestClient
um = db.UserManager()
um.add_custom_persona("Sky", "rick", "rick", "Rick (row)", "", "", "STALE ROW TEXT")
um.add_custom_persona("Sky", "eni", "eni", "Eni", "", "", "ENI ROW TEXT")
personas = main.load_personas_logic("Sky")
check("the file beats the DB row", personas["rick"]["system_prompt"], "line one\nline two changed\n")
check("the rest of the row still comes from the DB", personas["rick"]["name"], "Rick (row)")
check("a persona with no file keeps its row", personas["eni"]["system_prompt"], "ENI ROW TEXT")

print("workshop: POST /personas")
main.app.dependency_overrides[main.get_current_user] = lambda: "Sky"
client = TestClient(main.app)   # no `with`: startup hooks must not run


def post(key, prompt):
    return client.post("/personas", json={"username": "Sky", "key": key, "original_key": key,
                                          "name": key, "avatar": "", "tagline": "", "system_prompt": prompt})


def row_prompt(key):
    c = sqlite3.connect(db.DB_PATH)
    try:
        return c.execute("SELECT system_prompt FROM custom_personas WHERE username='Sky' AND persona_key=?",
                         (key,)).fetchone()[0]
    finally:
        c.close()


n = len(history())
r = post("rick", "edited in the workshop\n")
check("a workshop save succeeds", r.status_code, 200)
check("the edit lands in the file, so the next read sees it", raw(RICK), b"edited in the workshop\r\n")
check("the row is written too", row_prompt("rick"), "edited in the workshop\n")
check("the replaced file text is kept in history", len(history()), n + 1)
check("and the next chat read returns the edit", main.load_personas_logic("Sky")["rick"]["system_prompt"],
      "edited in the workshop\n")
r = post("rick", "edited in the workshop\n")
check("re-posting the file's own text (gaba_live.ensure_persona) adds no history", len(history()), n + 1)
r = post("eni", "ENI EDITED")
check("a persona with no file saves to the row only", (r.status_code, row_prompt("eni")), (200, "ENI EDITED"))
check("and no file appears for it", os.path.exists(os.path.join(TMP, "personas", "eni.txt")), False)
real_history = pf.HISTORY_DIR
blocker = os.path.join(TMP, "not_a_dir")
open(blocker, "w").close()
pf.HISTORY_DIR = blocker            # makedirs on a file raises, like a locked folder would
r = post("rick", "this save cannot land")
pf.HISTORY_DIR = real_history
check("a file that can't be written is an error, not a silent DB-only save", r.status_code, 500)
check("and the file is untouched", raw(RICK), b"edited in the workshop\r\n")
main.app.dependency_overrides.clear()

print("daemon: stream_worker monologue prompt")
with open(os.path.join(ROOT, "backend", "stream_worker.py"), encoding="utf-8") as f:
    src = f.read()
start = src.find("SELECT name, system_prompt FROM custom_personas")
block = src[start:src.find("monologue_system_prompt", start)] if start >= 0 else ""
check_true("the daemon's row lookup is still where this check looks", start >= 0)
check_true("the daemon reads the file over the row (same rule as chat)", "persona_files.read_prompt(persona)" in block)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
