"""get_linked_nodes: a neighbour reachable by two edges (A->B and B->A, now
common with typed links) appeared twice and could eat two expansion slots.
Dedup results by node id. Also extends test [2]."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

p = "database.py"
raw = open(p, "rb").read()
crlf = b"\r\n" in raw
s = raw.decode("utf-8").replace("\r\n", "\n")
if "seen_results" in s:
    print("SKIP database.py (already patched)")
else:
    old1 = """            visited = set()
            results = []
            frontier = [node_id]
"""
    new1 = """            visited = set()
            seen_results = set()   # FIX(reciprocal-edges): A->B and B->A yielded B twice
            results = []
            frontier = [node_id]
"""
    old2 = """                    for row in c.fetchall():
                        if row[0] not in visited:
                            results.append({
"""
    new2 = """                    for row in c.fetchall():
                        if row[0] not in visited and row[0] not in seen_results:
                            seen_results.add(row[0])
                            results.append({
"""
    assert s.count(old1) == 1, s.count(old1)
    assert s.count(old2) == 1, s.count(old2)
    s = s.replace(old1, new1).replace(old2, new2)
    open(p, "wb").write((s.replace("\n", "\r\n") if crlf else s).encode("utf-8"))
    print("patched database.py (get_linked_nodes dedup)")

t = "tests/test_typed_links.py"
s = open(t, encoding="utf-8").read()
if "reciprocal" in s:
    print("SKIP test (already extended)")
else:
    old = """check("edges are bidirectional for traversal (Charlie sees Alpha and Bravo)",
      sorted(n["node_id"] for n in dbm.get_linked_nodes(c_pk, depth=1)), ["TST-A-001", "TST-B-001"])
"""
    new = """check("edges are bidirectional for traversal (Charlie sees Alpha and Bravo)",
      sorted(n["node_id"] for n in dbm.get_linked_nodes(c_pk, depth=1)), ["TST-A-001", "TST-B-001"])
b_pk = q("SELECT id FROM zettel_nodes WHERE node_id='TST-B-001'")[0][0]
import uuid as _uuid
dbm.add_zettel_link(str(_uuid.uuid4()), b_pk, a_pk, "links_to", 1.0, "core")   # reciprocal B->A on top of A->B
check("a reciprocal edge does not list the neighbour twice",
      sorted(n["node_id"] for n in dbm.get_linked_nodes(a_pk, depth=1)), ["TST-B-001", "TST-C-001"])
q("DELETE FROM zettel_links WHERE source_node_id=? AND target_node_id=?", b_pk, a_pk)
"""
    assert s.count(old) == 1
    open(t, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))
    print("test [2] extended")
