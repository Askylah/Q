"""
Handwritten lore (2026-10-04): text in the on-demand module format pasted into
the lorebook (POST /personas/{key}/lore -> zettel_engine.process_entry) keeps
its own IDs, links and triggers instead of being chunked and LLM-categorised.

Standalone, like the rest of tests/ -- no pytest. Throwaway SQLite file
(PERSONAAPP_DB_PATH), Redis db 1, a deterministic fake embedding model, the
LLM extraction call replaced by a recorder. Never touches the live app.

    python tests/test_lore_handwritten.py
"""
import os, sys, io, tempfile, shutil, sqlite3, hashlib, json, contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(1, ROOT)
TMP = tempfile.mkdtemp(prefix="lore_handwritten_")
os.environ["PERSONAAPP_DATA_DIR"] = TMP
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
os.environ["TELEMETRY_OFF"] = "1"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.pop("ZETTEL_LORE_HANDWRITTEN_OFF", None)

import numpy as np
import database as db
assert os.path.abspath(db.DB_PATH) == os.path.abspath(os.environ["PERSONAAPP_DB_PATH"]), \
    "refusing to run: the DB path override did not take (would touch a real database)"
import zettel_engine as ze

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


class FakeModel:
    def encode(self, texts, convert_to_numpy=True, **kw):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            seed = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).normal(size=384).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return np.vstack(out)


FAKE = FakeModel()
ze.get_shared_model = lambda: FAKE
LLM_CALLS = []


def fake_llm(chunks, api_keys, model_id=None, base_index=0):
    LLM_CALLS.append(len(chunks))
    return {"nodes": [{"chunk_index": base_index + i, "category": "LORE", "title": f"Chunk {base_index + i}"}
                      for i in range(len(chunks))], "relationships": []}


ze._extract_entities_via_llm = fake_llm
KEYS = {"universal": "test-key"}

dbm = db.UserManager()
U, P = f"lore_user_{os.getpid()}", "lore_persona"
FILES = {"paths": []}
REAL_LOADER = ze.load_persona_on_demand_files                           # section [8] tests the real one
ze.load_persona_on_demand_files = lambda persona: list(FILES["paths"])   # this persona's on_demand_files
ze.invalidate_zettel_cache(U, P)


def q(sql, *a):
    conn = sqlite3.connect(db.DB_PATH)
    rows = conn.execute(sql, a).fetchall()
    conn.commit()
    conn.close()
    return rows


def nodes_of(entry_id):
    return q("""SELECT node_id, title, node_class, trigger_type, content, embedding IS NOT NULL
                FROM zettel_nodes WHERE source_entry_id=? ORDER BY rowid""", entry_id)


def edges():
    return sorted(q("""SELECT a.node_id, b.node_id FROM zettel_links l
                       JOIN zettel_nodes a ON a.id=l.source_node_id JOIN zettel_nodes b ON b.id=l.target_node_id
                       WHERE l.relationship='links_to' AND a.username=? AND a.persona=?""", U, P))


def dangling():
    return q("""SELECT count(*) FROM zettel_links
                WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes)
                   OR target_node_id NOT IN (SELECT id FROM zettel_nodes)""")[0][0]


def entry(entry_id):
    return next(e for e in dbm.get_zettel_entries(U, P) if e["id"] == entry_id)


def add_and_process(title, text):
    eid = dbm.add_zettel_entry(U, P, title, text)
    ze.process_entry(U, P, eid, KEYS)
    return eid


HAND = """Notes I wrote before the modules -- not a module, must not become one.

---
ID: HW-ALPHA-001
Title: Alpha Module
Type: ON_DEMAND
Links: [[HW-BETA-001]], [[FILE-ONE-001]]
Triggers: alpha thing, first letter
---
Alpha body line one.

Alpha body line two (a blank line inside a body is still the body).

---
ID: HW-BETA-001
Title: Beta Module
Type: ON_DEMAND
Links: [[HW-ALPHA-001]], [[NOWHERE-999]]
Triggers: beta
---
Beta body.
"""

# ── 1. parser: text and file entry points are the same parser ────────────
print("\n[1] parse_on_demand_text == parse_on_demand_file; stats; quiet")
fpath = os.path.join(TMP, "hand.txt")
with open(fpath, "w", encoding="utf-8") as f:
    f.write(HAND)
shape = lambda ms: [(m.id, m.title, m.type, m.links, m.triggers, m.content) for m in ms]
stats = {}
from_text = ze.parse_on_demand_text(HAND, "hand.txt", stats=stats)
check("same modules from text and from file", shape(from_text), shape(ze.parse_on_demand_file(fpath)))
check("two modules", [m.id for m in from_text], ["HW-ALPHA-001", "HW-BETA-001"])
check("body keeps its inner blank line",
      from_text[0].content, "Alpha body line one.\n\nAlpha body line two (a blank line inside a body is still the body).")
check("stats: preamble counted", stats.get("preamble_chars"), len("Notes I wrote before the modules -- not a module, must not become one."))
check("stats: no orphans, no header-only modules", (stats.get("orphan_chars"), stats.get("no_body")), (0, []))
s2 = {}
ze.parse_on_demand_text(HAND + "\n---\nstray text after a separator\n---\nID: HW-NOBODY-001\nTitle: X\n", "x", stats=s2)
check("stats: orphan body + header without body", (s2["orphan_chars"], s2["no_body"]),
      (len("stray text after a separator"), ["HW-NOBODY-001"]))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ze.parse_on_demand_text("ID: Q-1\nTitle: q\nbody fused to its header\n", "quiet.txt", quiet=True)
check("quiet=True prints nothing", buf.getvalue(), "")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ze.parse_on_demand_text("ID: Q-1\nTitle: q\nbody fused to its header\n", "loud.txt")
check_true("default still logs the recovery, labelled with the name", "loud.txt: recovered a header" in buf.getvalue())

# ── 2. a handwritten entry stays whole ───────────────────────────────────
print("\n[2] process_entry: handwritten modules keep their IDs, titles, triggers, links; no chunking, no LLM")
FILE = os.path.join(TMP, "ondemand.txt")
with open(FILE, "w", encoding="utf-8") as f:
    f.write("---\nID: FILE-ONE-001\nTitle: File One\nType: ON_DEMAND\nLinks: [[HW-BETA-001]]\nTriggers: file one\n---\n"
            "File one body.\n\n---\nID: FILE-TWO-001\nTitle: File Two\nType: ON_DEMAND\nTriggers: file two\n---\nFile two body.\n")
FILES["paths"] = [FILE]
ze.compile_behavioral_zettels(U, P, [FILE])
LLM_CALLS.clear()
e1 = add_and_process("My modules", HAND)
check("the LLM extractor was never called", LLM_CALLS, [])
got = nodes_of(e1)
check("one node per module, own IDs", [r[0] for r in got], ["HW-ALPHA-001", "HW-BETA-001"])
check("titles kept", [r[1] for r in got], ["Alpha Module", "Beta Module"])
check("behavioral + DETERMINISTIC, like an on-demand file", {(r[2], r[3]) for r in got}, {("behavioral", "DETERMINISTIC")})
check("content = TRIGGERS header + body (compile_behavioral_zettels' shape)", got[1][4], "TRIGGERS: beta\n\nBeta body.")
check("multi-word triggers kept", got[0][4].split("\n\n", 1)[0], "TRIGGERS: alpha thing,first letter")
check("every node has an embedding", [r[5] for r in got], [1, 1])
check("the preamble did not become a node",
      q("SELECT count(*) FROM zettel_nodes WHERE source_entry_id=? AND content LIKE '%Notes I wrote%'", e1)[0][0], 0)
check("entry marked processed", entry(e1)["processed"], True)
note = entry(e1)["import_note"] or ""
print(f"  note: {note}")
check_true("note: handwritten, 2 modules", note.startswith("handwritten: 2 module(s) imported"))
check_true("note: the ignored preamble is reported", "chars before the first header" in note)
check_true("note: the unresolved link is reported", "[[NOWHERE-999]]" in note)
check("typed links in both directions and across sources", edges(),
      sorted([("FILE-ONE-001", "HW-BETA-001"), ("HW-ALPHA-001", "FILE-ONE-001"),
              ("HW-ALPHA-001", "HW-BETA-001"), ("HW-BETA-001", "HW-ALPHA-001")]))
check("no dangling edges", dangling(), 0)
check("the trigger fires through the read path",
      "### MODULE: HW-BETA-001" in ze.query_knowledge_graph(U, P, "what about beta?"), True)

# ── 3. duplicate IDs are skipped, loudly, and noted ──────────────────────
print("\n[3] duplicate IDs: skipped with a log line and an import note, the existing module stays")
DUP = ("---\nID: FILE-TWO-001\nTitle: Impostor\nType: ON_DEMAND\nTriggers: impostor\n---\nImpostor body.\n\n"
       "---\nID: HW-BETA-001\nTitle: Beta Again\nType: ON_DEMAND\nTriggers: beta again\n---\nSecond beta.\n\n"
       "---\nID: HW-GAMMA-001\nTitle: Gamma\nType: ON_DEMAND\nTriggers: gamma\n---\nGamma body.\n\n"
       "---\nID: HW-GAMMA-001\nTitle: Gamma Twice\nType: ON_DEMAND\nTriggers: gamma two\n---\nGamma twice.\n")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    e2 = add_and_process("Dupes", DUP)
log = buf.getvalue()
check("only the new module was stored", [r[0] for r in nodes_of(e2)], ["HW-GAMMA-001"])
check("the file's FILE-TWO-001 is untouched", q("SELECT title FROM zettel_nodes WHERE node_id='FILE-TWO-001'"), [("File Two",)])
check("the first entry's HW-BETA-001 is untouched", q("SELECT title FROM zettel_nodes WHERE node_id='HW-BETA-001'"), [("Beta Module",)])
check_true("loud log line for the file collision", "DUPLICATE ID FILE-TWO-001" in log and "ondemand.txt" in log)
check_true("loud log line for the lore collision", "DUPLICATE ID HW-BETA-001" in log and "lore entry 'My modules'" in log)
check_true("loud log line for the in-entry repeat", "DUPLICATE ID HW-GAMMA-001 appears twice" in log)
note2 = entry(e2)["import_note"] or ""
print(f"  note: {note2}")
check_true("note: 1 imported", note2.startswith("handwritten: 1 module(s) imported"))
check_true("note: names the file and the ID", "duplicate ID(s) already defined by ondemand.txt: FILE-TWO-001" in note2)
check_true("note: names the other entry and the ID", "already defined by lore entry 'My modules': HW-BETA-001" in note2)
check_true("note: the repeat inside the entry", "repeated inside this entry: HW-GAMMA-001" in note2)
check("entry processed", entry(e2)["processed"], True)

# ── 4. unformatted text still takes the auto-split + LLM path ────────────
print("\n[4] unformatted text: chunked, LLM-categorised, invented IDs -- exactly as before")
LLM_CALLS.clear()
e3 = add_and_process("Plain lore", "The citadel is a city of Ricks.\n\nIt floats in the void between dimensions.")
check("the LLM extractor was called once", LLM_CALLS, [2])
got3 = nodes_of(e3)
check("two lore chunks", [(r[2], r[3]) for r in got3], [("lore", "PROBABILISTIC")] * 2)
check_true("with generated [[CATEGORY-NAME-###]] tags", all(r[0].startswith("[[LORE-CHUNK-") for r in got3))
check_true("note says auto-split", (entry(e3)["import_note"] or "").startswith("auto-split: 2 chunk(s)"))
print("    lesion: ZETTEL_LORE_HANDWRITTEN_OFF=1 sends handwritten text down the old path")
os.environ["ZETTEL_LORE_HANDWRITTEN_OFF"] = "1"
LLM_CALLS.clear()
e4 = add_and_process("Lesioned", "---\nID: HW-LESION-001\nTitle: L\nType: ON_DEMAND\nTriggers: lesion\n---\nLesion body.\n")
os.environ.pop("ZETTEL_LORE_HANDWRITTEN_OFF", None)
check_true("LLM path taken", LLM_CALLS)
check_true("no node carries the module's own ID", all(r[0] != "HW-LESION-001" for r in nodes_of(e4)))
dbm.delete_zettel_entry(U, P, e4)

# ── 5. edit / delete rebuild or prune the entry's edges ──────────────────
print("\n[5] editing and deleting a handwritten entry")
EDITED = HAND.replace("Links: [[HW-ALPHA-001]], [[NOWHERE-999]]", "Links: [[FILE-TWO-001]]")
dbm.update_zettel_entry(U, P, e1, "My modules", EDITED)
check("update clears the stale note while re-processing is pending", entry(e1)["import_note"], None)
ze.process_entry(U, P, e1, KEYS)
check("edges follow the edited Links header (file->lore edge restored too)", edges(),
      sorted([("FILE-ONE-001", "HW-BETA-001"), ("HW-ALPHA-001", "FILE-ONE-001"),
              ("HW-ALPHA-001", "HW-BETA-001"), ("HW-BETA-001", "FILE-TWO-001")]))
check("no dangling edges after the edit", dangling(), 0)
print("    a file recompile deletes the file's nodes + edges, then the rebuild reads the lorebook too")
with open(FILE, "a", encoding="utf-8") as f:
    f.write("\n")
ze.compile_behavioral_zettels(U, P, [FILE])
check("lore->file and file->lore edges survive a file recompile", edges(),
      sorted([("FILE-ONE-001", "HW-BETA-001"), ("HW-ALPHA-001", "FILE-ONE-001"),
              ("HW-ALPHA-001", "HW-BETA-001"), ("HW-BETA-001", "FILE-TWO-001")]))
print("    delete")
dbm.delete_zettel_entry(U, P, e1)
ze.invalidate_zettel_cache(U, P)   # what DELETE /personas/{key}/lore/{id} now does
check("only edges between surviving modules remain", edges(), [])
check("no dangling edges after the delete", dangling(), 0)
check("the deleted entry's modules are gone", q("SELECT count(*) FROM zettel_nodes WHERE node_id IN ('HW-ALPHA-001','HW-BETA-001')")[0][0], 0)
r = ze.rebuild_typed_links(U, P, [FILE])
check("a later rebuild reports the file's link into the deleted module as unresolved", "HW-BETA-001" in r["unresolved"], True)

# ── 5b. a handwritten entry honours `Match: exact` ───────────────────────
print("\n[5b] handwritten lore: `Match: exact` keeps a module off the fuzzy trigger layers")
WALL = ("---\r\nID: HW-WALL-001\r\nTitle: Wall\r\nType: ON_DEMAND\r\nTriggers: heartbreak\r\nMatch: exact\r\n---\r\n"
        "Wall body.\r\n\r\n---\r\nID: HW-OPEN-001\r\nTitle: Open\r\nType: ON_DEMAND\r\nTriggers: heartache\r\n---\r\n"
        "Open body.\r\n")                                  # CRLF, the way a Windows paste can arrive
e5 = add_and_process("Wall entry", WALL)
got5 = {r[0]: r for r in nodes_of(e5)}
check("both modules imported, own IDs", sorted(got5), ["HW-OPEN-001", "HW-WALL-001"])
check("stored trigger_match per module",
      sorted((n["node_id"], n["trigger_match"]) for n in dbm.get_zettel_nodes_for_persona(U, P)
             if n["source_entry_id"] == e5), [("HW-OPEN-001", "all"), ("HW-WALL-001", "exact")])
check("the Match line is not in the stored content", got5["HW-WALL-001"][4], "TRIGGERS: heartbreak\n\nWall body.")
check_true("note names the exact-only module", "exact-trigger-only (Match: exact): HW-WALL-001" in (entry(e5)["import_note"] or ""))
INFL = {"ZETTEL_TRIGGER_SEMANTIC_OFF": "1"}               # plain (ungated) inflection
os.environ.update(INFL)
try:
    out_open = ze.query_knowledge_graph(U, P, "heartaches everywhere")
    out_wall = ze.query_knowledge_graph(U, P, "heartbreaks everywhere")
    out_exact = ze.query_knowledge_graph(U, P, "a heartbreak")
finally:
    os.environ.pop("ZETTEL_TRIGGER_SEMANTIC_OFF", None)
check("control: the inflected layer fires the open module", "### MODULE: HW-OPEN-001" in out_open, True)
check("the inflected layer skips the exact-only module", "### MODULE: HW-WALL-001" in out_wall, False)
check("the exact trigger still fires it", "### MODULE: HW-WALL-001" in out_exact, True)
check("and no Match text is injected", "Match" in out_exact, False)

# ── 6. caches are invalidated by an import ───────────────────────────────
print("\n[6] cache invalidation after a handwritten import")
ze.query_knowledge_graph(U, P, "gamma")                 # warm both caches
key = (U, P)
check_true("trigger cache warm", key in ze._TRIGGER_REGEX_CACHE)
check_true("vector cache warm", key in ze._EMBEDDING_CACHE)
add_and_process("More", "---\nID: HW-DELTA-001\nTitle: Delta\nType: ON_DEMAND\nTriggers: delta\n---\nDelta body.\n")
check("trigger cache dropped", key in ze._TRIGGER_REGEX_CACHE, False)
check("vector cache dropped", key in ze._EMBEDDING_CACHE, False)
check("the new module's trigger fires on the very next query",
      "### MODULE: HW-DELTA-001" in ze.query_knowledge_graph(U, P, "tell me about delta"), True)
src = open(os.path.join(ROOT, "backend", "main.py"), encoding="utf-8").read()
upd = src[src.index("def update_lore_entry"):src.index("def delete_lore_entry")]
dele = src[src.index("def delete_lore_entry"):]
dele = dele[:dele.index("\n@app.")] if "\n@app." in dele else dele
check_true("PUT /lore invalidates the zettel cache", "zettel_invalidate_cache(" in upd)
check_true("DELETE /lore invalidates the zettel cache", "zettel_invalidate_cache(" in dele)

# ── 7. a rebuild never wipes edges of a file it could not read ───────────
print("\n[7] typed-link rebuild is scoped to the sources it parsed")
FILE2 =os.path.join(TMP, "second.txt")
with open(FILE2, "w", encoding="utf-8") as f:
    f.write("---\nID: FILE-THREE-001\nTitle: Three\nType: ON_DEMAND\nLinks: [[FILE-ONE-001]]\nTriggers: three\n---\nThree.\n")
FILES["paths"] = [FILE, FILE2]
ze.compile_behavioral_zettels(U, P, [FILE, FILE2])
check_true("FILE-THREE-001 -> FILE-ONE-001 exists", ("FILE-THREE-001", "FILE-ONE-001") in edges())
os.rename(FILE2, FILE2 + ".moved")                         # unreadable for one rebuild
ze.rebuild_typed_links(U, P, [FILE, FILE2])
check("an unreadable file keeps its edges", ("FILE-THREE-001", "FILE-ONE-001") in edges(), True)
os.rename(FILE2 + ".moved", FILE2)

# ── 8. relative on_demand paths resolve against the project root ─────────
print("\n[8] load_persona_on_demand_files: personas.json paths are root-relative (backend/ move)")
import app_paths
fake_root = os.path.join(TMP, "fake_root")
os.makedirs(os.path.join(fake_root, "personas"))
with open(os.path.join(fake_root, "personas.json"), "w", encoding="utf-8") as f:
    json.dump({"tst": {"on_demand_files": ["personas/tst_ondemand.txt"]}}, f)
with open(os.path.join(fake_root, "personas", "tst_ondemand.txt"), "w", encoding="utf-8") as f:
    f.write("x")
saved_root = app_paths.APP_ROOT
app_paths.APP_ROOT = fake_root          # the loader imports APP_ROOT at call time
try:
    resolved = REAL_LOADER("tst")
finally:
    app_paths.APP_ROOT = saved_root
check("resolved under the project root", resolved, [os.path.join(fake_root, "personas/tst_ondemand.txt")])
check("and the file exists there", [os.path.exists(p) for p in resolved], [True])

# ── 9. the daemon's monologue entries are marked processed ───────────────
print("\n[9] stream_worker: a monologue lore entry is marked processed (the UI's 5 s poll stops)")
import stream_worker as sw
sw.provider_registry.resolve_api_keys = lambda *a, **k: {"universal": "k"}
sw.provider_registry.has_usable_key = lambda *a, **k: True
MONO = ("I keep circling the same question about the portal fluid. " * 8).strip()
sw.llm_engine.call_llm = lambda **kw: {"choices": [{"message": {"content": MONO}, "finish_reason": "stop"}]}
w = sw.ConsciousnessWorker()
gap_pk = q("SELECT id FROM zettel_nodes WHERE node_id='HW-GAMMA-001'")[0][0]
UM = f"mono_user_{os.getpid()}"
w.generate_idle_monologue(UM, P, "2026-10-04 10:00:00",
                          gap={"node": {"id": gap_pk, "title": "Gamma", "content": "Gamma body."}}, conflict=None)
rows = q("SELECT id, processed, import_note FROM zettel_entries WHERE username=? AND title LIKE 'Internal Monologue - %'", UM)
check("one monologue entry written", len(rows), 1)
check("it is marked processed", rows[0][1] if rows else None, 1)
check_true("and noted", rows and (rows[0][2] or "").startswith("daemon monologue: stored as 1 lore node"))
check("its node exists", q("SELECT count(*) FROM zettel_nodes WHERE source_entry_id=?", rows[0][0])[0][0] if rows else 0, 1)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
