"""
Typed [[Links]] become edges; edges die with their nodes; module IDs are searchable.

Standalone, like the rest of tests/ -- no pytest. Runs against a throwaway
SQLite file (PERSONAAPP_DB_PATH) with the embedding model stubbed to None, so
it needs neither the model nor Redis.
"""
import os, sys, tempfile, shutil, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="typed_links_")
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
os.environ["TELEMETRY_OFF"] = "1"

import database as db
import zettel_engine as ze

ze.get_shared_model = lambda: None          # no embeddings needed for edges
FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


U, P = "tl_user", "tl_persona"
FILE = os.path.join(TMP, "ondemand.txt")

MODULES_V1 = """---
ID: TST-A-001
Title: Alpha
Type: ON_DEMAND
Links: [[TST-B-001]], [[TST-C-001]]
Triggers: alpha
---
Alpha body. Mentions [[TST-C-001]] in passing.

---
ID: TST-B-001
Title: Bravo
Type: ON_DEMAND
Links: [[TST-C-001]], [[NOPE-999]]
Triggers: bravo
---
Bravo body.

---
ID: TST-C-001
Title: Charlie
Type: ON_DEMAND
Triggers: charlie
---
Charlie body.
"""


def write(text):
    with open(FILE, "w", encoding="utf-8") as f:
        f.write(text)


def q(sql, *a):
    conn = sqlite3.connect(db.DB_PATH)
    rows = conn.execute(sql, a).fetchall()
    conn.commit()
    conn.close()
    return rows


def edges():
    return sorted(q("""SELECT a.node_id, l.relationship, b.node_id FROM zettel_links l
                       JOIN zettel_nodes a ON a.id=l.source_node_id
                       JOIN zettel_nodes b ON b.id=l.target_node_id
                       WHERE a.username=? AND a.persona=?""", U, P))


def dangling():
    return q("""SELECT count(*) FROM zettel_links
                WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes)
                   OR target_node_id NOT IN (SELECT id FROM zettel_nodes)""")[0][0]


dbm = db.UserManager()   # creates the schema in the throwaway file

# ── 1. typed links become edges ──────────────────────────────────────────
print("\n[1] compile: typed Links header -> links_to edges")
write(MODULES_V1)
ze.compile_behavioral_zettels(U, P, [FILE])
check("three modules compiled", q("SELECT count(*) FROM zettel_nodes WHERE username=? AND persona=?", U, P)[0][0], 3)
check("edges exactly as typed (unresolved NOPE-999 skipped, self/dupes ignored)", edges(),
      [("TST-A-001", "links_to", "TST-B-001"), ("TST-A-001", "links_to", "TST-C-001"),
       ("TST-B-001", "links_to", "TST-C-001")])
check("no dangling edges", dangling(), 0)

# ── 2. graph expansion can walk them ─────────────────────────────────────
print("\n[2] get_linked_nodes walks the typed edges")
a_pk = q("SELECT id FROM zettel_nodes WHERE node_id='TST-A-001'")[0][0]
linked = sorted(n["node_id"] for n in dbm.get_linked_nodes(a_pk, depth=1))
check("Alpha's 1-hop neighbours", linked, ["TST-B-001", "TST-C-001"])
c_pk = q("SELECT id FROM zettel_nodes WHERE node_id='TST-C-001'")[0][0]
check("edges are bidirectional for traversal (Charlie sees Alpha and Bravo)",
      sorted(n["node_id"] for n in dbm.get_linked_nodes(c_pk, depth=1)), ["TST-A-001", "TST-B-001"])
b_pk = q("SELECT id FROM zettel_nodes WHERE node_id='TST-B-001'")[0][0]
import uuid as _uuid
dbm.add_zettel_link(str(_uuid.uuid4()), b_pk, a_pk, "links_to", 1.0, "core")   # reciprocal B->A on top of A->B
check("a reciprocal edge does not list the neighbour twice",
      sorted(n["node_id"] for n in dbm.get_linked_nodes(a_pk, depth=1)), ["TST-B-001", "TST-C-001"])
q("DELETE FROM zettel_links WHERE source_node_id=? AND target_node_id=?", b_pk, a_pk)

# ── 3. module IDs are searchable ─────────────────────────────────────────
print("\n[3] search_zettel_fts resolves an ID-shaped token to the module itself, first")
hits = dbm.search_zettel_fts(U, P, "TST-C-001")
check("first hit is the module, not a node that mentions it", hits[0]["node_id"] if hits else None, "TST-C-001")
check_true("the mentioning node (Alpha) still appears after it", any(h["node_id"] == "TST-A-001" for h in hits[1:]))
hits = dbm.search_zettel_fts(U, P, "tell me about tst-c-001 please")
check("case-insensitive inside a sentence", hits[0]["node_id"] if hits else None, "TST-C-001")
check("plain keyword search unchanged", [h["node_id"] for h in dbm.search_zettel_fts(U, P, "bravo body")][:1], ["TST-B-001"])
check("unknown ID -> no exact hit, no crash", dbm.search_zettel_fts(U, P, "ZZZ-000"), [])

# ── 4. recompile: edges die with nodes, then come back ───────────────────
print("\n[4] recompile after an edit: no dangling edges, typed edges rebuilt")
old_pks = set(r[0] for r in q("SELECT id FROM zettel_nodes WHERE username=? AND persona=?", U, P))
write(MODULES_V1.replace("Charlie body.", "Charlie body, edited."))
ze.compile_behavioral_zettels(U, P, [FILE])
new_pks = set(r[0] for r in q("SELECT id FROM zettel_nodes WHERE username=? AND persona=?", U, P))
check_true("nodes were replaced (new primary keys)", not (old_pks & new_pks))
check("no dangling edges after recompile (the 85/99 bug)", dangling(), 0)
check("typed edges rebuilt against the new nodes", len(edges()), 3)
check("no duplicate edges", q("SELECT count(*) FROM zettel_links")[0][0], 3)

# ── 5. bootstrap: a persona compiled before the fix gets its edges once ──
print("\n[5] bootstrap: zero typed edges + unchanged file -> rebuilt once per process")
q("DELETE FROM zettel_links")
ze._TYPED_LINKS_OK.clear()
ze.compile_behavioral_zettels(U, P, [FILE])   # hash unchanged: no recompile, bootstrap path
check("edges rebuilt without a recompile", len(edges()), 3)
q("DELETE FROM zettel_links")
ze.compile_behavioral_zettels(U, P, [FILE])   # same process: must NOT thrash
check("second call in the same process does not rebuild (cached)", len(edges()), 0)
r = ze.rebuild_typed_links(U, P, [FILE])
check("explicit rebuild reports counts", (r["edges"], r["unresolved"]), (3, ["NOPE-999"]))

# ── 6. links to a different file's modules resolve ───────────────────────
print("\n[6] cross-file links resolve once both files are compiled")
FILE2 = os.path.join(TMP, "kb.txt")
with open(FILE2, "w", encoding="utf-8") as f:
    f.write("---\nID: TST-KB-001\nTitle: Kilo\nType: ON_DEMAND\nLinks: [[TST-A-001]]\nTriggers: kilo\n---\nKilo body.\n")
ze.compile_behavioral_zettels(U, P, [FILE, FILE2])
check("edge from the second file into the first", ("TST-KB-001", "links_to", "TST-A-001") in edges(), True)
check("total edges", len(edges()), 4)

# ── 7. a missing '---' between a body and the next header ────────────────
print("\n[7] parser: a body fused with the next header (missing separator) loses nothing")
FUSED = (
    "---\nID: FZ-A-001\nTitle: Aye\nType: ON_DEMAND\nLinks: [[FZ-B-001]]\nTriggers: aye\n---\n"
    "Aye body line one.\nAye body line two.\n\n\n"          # <- no '---' here
    "ID: FZ-B-001\nTitle: Bee\nType: ON_DEMAND\nTriggers: bee\n---\n"
    "Bee body.\n\n"                                            # <- and none here
    "ID: FZ-C-001\nTitle: Cee\nType: ON_DEMAND\nTriggers: cee\n---\n"
    "Cee body.\n---\n"
    "ID: FZ-D-001\nTitle: Dee\nType: ON_DEMAND\nTriggers: dee\n---  \n"   # trailing spaces on the separator
    "Dee body mentions ID: and Title: as words, which is not a header.\n"
)
FILE3 = os.path.join(TMP, "fused.txt")
with open(FILE3, "w", encoding="utf-8") as f:
    f.write(FUSED)
mods = {m.id: m for m in ze.parse_on_demand_file(FILE3)}
check("all four modules parsed", sorted(mods), ["FZ-A-001", "FZ-B-001", "FZ-C-001", "FZ-D-001"])
check("Aye keeps its own body, not Bee's header", mods["FZ-A-001"].content if "FZ-A-001" in mods else None,
      "Aye body line one.\nAye body line two.")
check("Bee's body is Bee's", mods.get("FZ-B-001").content if "FZ-B-001" in mods else None, "Bee body.")
check("Cee's body is Cee's", mods.get("FZ-C-001").content if "FZ-C-001" in mods else None, "Cee body.")
check("a body that merely mentions 'ID:' and 'Title:' stays a body",
      mods.get("FZ-D-001").content if "FZ-D-001" in mods else None,
      "Dee body mentions ID: and Title: as words, which is not a header.")
check("Aye's typed link survived the recovery", mods["FZ-A-001"].links if "FZ-A-001" in mods else None, ["FZ-B-001"])

# the other omission: no '---' AFTER the header, header runs into its body; and a chain of them
FUSED2 = (
    "---\nID: FY-A-001\nTitle: Aye\nType: ON_DEMAND\nTriggers: aye\n"      # <- no '---' here
    "Aye body.\n\n"
    "ID: FY-B-001\nTitle: Bee\nType: ON_DEMAND\nLinks: [[FY-A-001]]\nTriggers: bee\n"   # <- nor here
    "Bee body line one.\nBee body line two.\n---\n"
    "ID: FY-C-001\nTitle: Cee\nType: ON_DEMAND\nTriggers: cee\n---\n"
    "Cee body.\n"
)
FILE4 = os.path.join(TMP, "fused2.txt")
with open(FILE4, "w", encoding="utf-8") as f:
    f.write(FUSED2)
m2 = {m.id: m for m in ze.parse_on_demand_file(FILE4)}
check("header without a closing '---': all three modules parsed", sorted(m2), ["FY-A-001", "FY-B-001", "FY-C-001"])
check("Aye's body is just Aye's", m2["FY-A-001"].content if "FY-A-001" in m2 else None, "Aye body.")
check("Bee's body is just Bee's (chained recovery)", m2["FY-B-001"].content if "FY-B-001" in m2 else None,
      "Bee body line one.\nBee body line two.")
check("Bee's typed link intact", m2["FY-B-001"].links if "FY-B-001" in m2 else None, ["FY-A-001"])
check("Bee's triggers intact", m2["FY-B-001"].triggers if "FY-B-001" in m2 else None, ["bee"])
check("Cee (well-formed) unaffected", m2["FY-C-001"].content if "FY-C-001" in m2 else None, "Cee body.")

print("\n[7b] parser-version salt: an unchanged file recompiles when the parser changes")
ze.compile_behavioral_zettels(U, P, [FILE3])
n1 = q("SELECT count(*) FROM zettel_nodes WHERE username=? AND persona=? AND source_entry_id=?", U, P, FILE3)[0][0]
check("fused file compiled to four nodes", n1, 4)
stored = q("SELECT content_hash FROM zettel_nodes WHERE source_entry_id=? LIMIT 1", FILE3)[0][0]
check_true("stored hash carries the parser version", stored.endswith(f":p{ze.ON_DEMAND_PARSER_VERSION}"))
q("UPDATE zettel_nodes SET content_hash=? WHERE source_entry_id=?", stored.split(":p")[0], FILE3)   # pretend: compiled by the old parser
pks_before = set(r[0] for r in q("SELECT id FROM zettel_nodes WHERE source_entry_id=?", FILE3))
ze.compile_behavioral_zettels(U, P, [FILE3])
pks_after = set(r[0] for r in q("SELECT id FROM zettel_nodes WHERE source_entry_id=?", FILE3))
check_true("unchanged bytes + old-parser hash -> recompiled (new primary keys)", not (pks_before & pks_after))
ze.compile_behavioral_zettels(U, P, [FILE3])
check("...and a second call with the current hash does not",
      set(r[0] for r in q("SELECT id FROM zettel_nodes WHERE source_entry_id=?", FILE3)) == pks_after, True)

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} FAILED:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
