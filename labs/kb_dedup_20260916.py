"""Back up the live DB, then remove the duplicate KB lore ingest (entry 11dc01bb...) with the same cascade as delete_zettel_entry, plus a before/after census."""
import sqlite3, os, datetime, sys
LIVE = os.path.join(os.environ["LOCALAPPDATA"], "PersonaApp", "users.db")
ROOT = r"C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-merged"
ENTRY = "11dc01bb-efa9-4c81-8ed3-230799c9332e"
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
bak = os.path.join(os.path.dirname(LIVE), "backups", f"users_{stamp}_pre_kb_dedup.db")
os.makedirs(os.path.dirname(bak), exist_ok=True)
for side in ("-wal", "-shm"):
    if os.path.exists(LIVE + side):
        sys.exit(f"sidecar present: {LIVE + side} -- app running? abort")

src = sqlite3.connect(LIVE)
dst = sqlite3.connect(bak)
src.backup(dst)
dst.close()
print("backup:", bak, os.path.getsize(bak), "bytes")

c = src.cursor()
def census(tag):
    n = c.execute("SELECT COUNT(*) FROM zettel_nodes WHERE username='Sky' AND persona='rick'").fetchone()[0]
    kb = c.execute("SELECT COUNT(*) FROM zettel_nodes WHERE source_entry_id=?", (ENTRY,)).fetchone()[0]
    l = c.execute("SELECT COUNT(*) FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (ENTRY, ENTRY)).fetchone()[0]
    f = c.execute("SELECT COUNT(*) FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (ENTRY,)).fetchone()[0]
    e = c.execute("SELECT COUNT(*) FROM zettel_entries WHERE id=?", (ENTRY,)).fetchone()[0]
    tl = c.execute("SELECT COUNT(*) FROM zettel_links").fetchone()[0]
    dangling = c.execute("SELECT COUNT(*) FROM zettel_links WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes) OR target_node_id NOT IN (SELECT id FROM zettel_nodes)").fetchone()[0]
    print(f"{tag}: Sky/rick nodes={n}  KB nodes={kb}  KB links={l}  KB fts={f}  entry={e}  all links={tl}  dangling={dangling}")
census("before")
row = c.execute("SELECT username,persona,title FROM zettel_entries WHERE id=?", (ENTRY,)).fetchone()
assert row == ("Sky", "rick", "KB"), row
c.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (ENTRY, ENTRY))
print("links deleted:", c.rowcount)
c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (ENTRY,))
print("fts deleted:", c.rowcount)
c.execute("DELETE FROM zettel_nodes WHERE source_entry_id=?", (ENTRY,))
print("nodes deleted:", c.rowcount)
c.execute("DELETE FROM zettel_entries WHERE id=? AND username='Sky' AND persona='rick'", (ENTRY,))
print("entries deleted:", c.rowcount)
src.commit()
census("after")
print("remaining Sky/rick by class:", c.execute("SELECT node_class, COUNT(*) FROM zettel_nodes WHERE username='Sky' AND persona='rick' GROUP BY 1").fetchall())
print("integrity:", c.execute("PRAGMA integrity_check").fetchone())
src.close()
for side in ("-wal", "-shm"):
    if os.path.exists(LIVE + side):
        print("WARNING sidecar left:", LIVE + side)
