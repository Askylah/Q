"""Recompile every persona's on-demand files on the live DB (parser v2 salt
forces it), rebuild typed edges, then re-run the daemon's gap census and the
ID lookups. Read-back only after the write; prints a before/after table."""
import os, sys, sqlite3, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEMETRY_OFF", "1")
import zettel_engine as ze
import database as db

PERSONAS = [("Sky", "rick"), ("SkyTest", "rick"), ("Sky", "eni"), ("Sky", "v")]


def census(c, u, p):
    nodes = [dict(r) for r in c.execute("SELECT id,node_id,title,content,node_class FROM zettel_nodes WHERE username=? AND persona=?", (u, p))]
    links = [dict(r) for r in c.execute("SELECT source_node_id,target_node_id FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username=? AND persona=?)", (u, p))]
    deg = {n["id"]: 0 for n in nodes}
    for l in links:
        if l["source_node_id"] in deg: deg[l["source_node_id"]] += 1
        if l["target_node_id"] in deg: deg[l["target_node_id"]] += 1
    iso = [n for n in nodes if deg[n["id"]] <= 1 and len(n.get("content") or "") > 150]
    beh = [n for n in nodes if n["node_class"] == "behavioral"]
    beh_iso = [n for n in iso if n["node_class"] == "behavioral"]
    return dict(nodes=len(nodes), behavioral=len(beh), links=len(links), isolated=len(iso),
                behavioral_isolated=[n["node_id"] for n in beh_iso])


print("DB:", db.DB_PATH)
c = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True); c.row_factory = sqlite3.Row
before = {up: census(c, *up) for up in PERSONAS}
c.close()

for u, p in PERSONAS:
    paths = ze.load_persona_on_demand_files(p)
    if not paths:
        continue
    print(f"\n=== compile {u}/{p} ({[os.path.basename(x) for x in paths]}) ===")
    ze.compile_behavioral_zettels(u, p, paths)     # salt mismatch -> recompiles, then rebuilds typed edges

c = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True); c.row_factory = sqlite3.Row
print("\n=== census before -> after ===")
for up in PERSONAS:
    b, a = before[up], census(c, *up)
    print(f"{up[0]}/{up[1]}: behavioral {b['behavioral']}->{a['behavioral']} | links {b['links']}->{a['links']} | "
          f"isolated_dense {b['isolated']}->{a['isolated']} | behavioral still isolated: {a['behavioral_isolated']}")
print("dangling links now:", c.execute("SELECT count(*) FROM zettel_links WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes) OR target_node_id NOT IN (SELECT id FROM zettel_nodes)").fetchone()[0])
print("stored hash sample:", c.execute("SELECT content_hash FROM zettel_nodes WHERE username='Sky' AND persona='rick' AND node_class='behavioral' LIMIT 1").fetchone()[0][-8:])
dbm = db.UserManager()
print("\n=== ID lookups (Sky/rick) ===")
for qs in ("SCAR-008", "TOOL-SYS-001", "RESEARCH-002", "what does [[SCAR-009]] say"):
    hits = dbm.search_zettel_fts("Sky", "rick", qs)
    print(f"  {qs!r:30} -> {[h['node_id'] for h in hits[:3]]}")
pk = c.execute("SELECT id FROM zettel_nodes WHERE username='Sky' AND persona='rick' AND node_id='SCAR-008'").fetchone()
if pk:
    print("1-hop from SCAR-008:", sorted(n["node_id"] for n in dbm.get_linked_nodes(pk[0], depth=1)))
