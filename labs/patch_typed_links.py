"""Typed [[Links]] -> real edges; links die with their nodes; module IDs searchable.
Idempotent, preserves line endings. Run from repo root."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)


def patch(path, pairs, marker):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    txt = raw.decode("utf-8").replace("\r\n", "\n")
    if marker in txt:
        print("SKIP (already patched)", path); return
    for old, new in pairs:
        n = txt.count(old)
        assert n == 1, f"{path}: expected 1 match, got {n} for:\n{old[:200]}"
        txt = txt.replace(old, new)
    open(path, "wb").write((txt.replace("\n", "\r\n") if crlf else txt).encode("utf-8"))
    print("patched", path, f"({len(pairs)} edits, {'CRLF' if crlf else 'LF'})")


# ---------------- zettel_engine.py ----------------
patch("zettel_engine.py", [
# (2) links die with their nodes
("""        c.execute(\"\"\"
            DELETE FROM zettel_nodes
            WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?
        \"\"\", (username, persona, path))
        c.execute(\"\"\"
            DELETE FROM zettel_fts
            WHERE node_db_id NOT IN (SELECT id FROM zettel_nodes)
        \"\"\")
        conn.commit()
        conn.close()
""", """        # FIX(dangling-links): a recompile replaced this file's nodes with new
        # UUIDs but left every edge that pointed at the old ones. Rick had 85 of
        # 99 edges dangling by 2026-09-13. Edges go with their nodes.
        c.execute(\"\"\"
            DELETE FROM zettel_links
            WHERE source_node_id IN (SELECT id FROM zettel_nodes
                                     WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?)
               OR target_node_id IN (SELECT id FROM zettel_nodes
                                     WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?)
        \"\"\", (username, persona, path, username, persona, path))
        c.execute(\"\"\"
            DELETE FROM zettel_nodes
            WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?
        \"\"\", (username, persona, path))
        c.execute(\"\"\"
            DELETE FROM zettel_fts
            WHERE node_db_id NOT IN (SELECT id FROM zettel_nodes)
        \"\"\")
        conn.commit()
        conn.close()
"""),
# (1) build edges from the parsed Links header, after every file is compiled
("""    if any_recompiled:
        invalidate_zettel_cache(username, persona)


def query_knowledge_graph(""", """    if any_recompiled:
        invalidate_zettel_cache(username, persona)
    # FIX(typed-links): the header parser has always read `Links: [[A]], [[B]]`
    # into OnDemandModule.links, and this function never looked at it. Not one
    # typed link ever became an edge; query-time graph expansion had nothing to
    # walk and the daemon saw the whole KB as "isolated pockets". Rebuild after
    # any recompile, and once per process for a persona that has none yet.
    _ensure_typed_links(username, persona, on_demand_paths, force=any_recompiled)


TYPED_LINK_RELATIONSHIP = "links_to"
_TYPED_LINKS_OK = set()


def _ensure_typed_links(username: str, persona: str, on_demand_paths: list, force: bool = False):
    import sqlite3
    key = (username.lower(), persona.lower())
    if not force and key in _TYPED_LINKS_OK:
        return
    if not force:
        # Bootstrap: a persona compiled before this fix has behavioral nodes and
        # zero typed edges. One COUNT, once per process.
        try:
            conn = sqlite3.connect(db.DB_PATH)
            c = conn.cursor()
            c.execute(\"\"\"
                SELECT count(*) FROM zettel_links l JOIN zettel_nodes n ON n.id = l.source_node_id
                WHERE n.username COLLATE NOCASE=? AND n.persona COLLATE NOCASE=? AND l.relationship=?
            \"\"\", (username, persona, TYPED_LINK_RELATIONSHIP))
            have = c.fetchone()[0]
            conn.close()
        except Exception:
            have = 1  # unreadable -> do not thrash
        if have > 0:
            _TYPED_LINKS_OK.add(key)
            return
    try:
        rebuild_typed_links(username, persona, on_demand_paths)
    except Exception as e:
        print(f"[ZETTEL COMPILER] typed-link rebuild failed (non-fatal): {e}")
    _TYPED_LINKS_OK.add(key)


def rebuild_typed_links(username: str, persona: str, on_demand_paths: list) -> dict:
    \"\"\"Parse every on-demand file for the persona, resolve each module's typed
    Links against the persona's node_id tags, replace all `links_to` edges for
    the persona with the result, and prune any edge whose end no longer exists.
    Returns {"edges": int, "unresolved": [tag...], "pruned": int}. Pure SQLite,
    no model, no LLM.\"\"\"
    import sqlite3, uuid
    wanted = {}
    for path in on_demand_paths or []:
        if not path or not os.path.exists(path):
            continue
        for mod in parse_on_demand_file(path):
            if mod.links:
                wanted.setdefault(mod.id, [])
                wanted[mod.id].extend(l for l in mod.links if l and l != mod.id)
    conn = sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    c.execute(\"\"\"SELECT node_id, id FROM zettel_nodes
                 WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?\"\"\", (username, persona))
    tag_to_pk = {}
    for tag, pk in c.fetchall():
        tag_to_pk.setdefault(tag, pk)
        # lore nodes carry their brackets in node_id; typed links do not
        tag_to_pk.setdefault(tag.strip("[]"), pk)
    # prune edges with a missing end (global: a dangling edge is garbage for everyone)
    c.execute(\"\"\"DELETE FROM zettel_links
                 WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes)
                    OR target_node_id NOT IN (SELECT id FROM zettel_nodes)\"\"\")
    pruned = c.rowcount
    # replace this persona's typed edges wholesale (the file is the source of truth)
    c.execute(\"\"\"DELETE FROM zettel_links
                 WHERE relationship=? AND source_node_id IN
                       (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?)\"\"\",
              (TYPED_LINK_RELATIONSHIP, username, persona))
    edges = 0
    unresolved = set()
    seen = set()
    ts = str(__import__("datetime").datetime.now())
    for src_tag, targets in wanted.items():
        src_pk = tag_to_pk.get(src_tag)
        if not src_pk:
            continue
        for tgt_tag in targets:
            tgt_pk = tag_to_pk.get(tgt_tag)
            if not tgt_pk:
                unresolved.add(tgt_tag)
                continue
            if (src_pk, tgt_pk) in seen:
                continue
            seen.add((src_pk, tgt_pk))
            c.execute(\"\"\"INSERT OR IGNORE INTO zettel_links (id, source_node_id, target_node_id, relationship, strength, created_at, label)
                         VALUES (?, ?, ?, ?, ?, ?, ?)\"\"\",
                      (str(uuid.uuid4()), src_pk, tgt_pk, TYPED_LINK_RELATIONSHIP, 1.0, ts, "core"))
            edges += 1
    conn.commit()
    conn.close()
    print(f"[ZETTEL COMPILER] typed links for {username}/{persona}: {edges} edge(s) from "
          f"{len(wanted)} module(s); {len(unresolved)} unresolved tag(s); {pruned} dangling edge(s) pruned"
          + (f" | unresolved: {sorted(unresolved)[:8]}" if unresolved else ""), flush=True)
    return {"edges": edges, "unresolved": sorted(unresolved), "pruned": pruned}


def query_knowledge_graph("""),
], marker="def rebuild_typed_links")

# ---------------- database.py: module IDs are searchable ----------------
patch("database.py", [
("""    def search_zettel_fts(self, username, persona, query):
        \"\"\"Full-text search across zettel node content using tokenized keyword OR matching.\"\"\"
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
""", """    def search_zettel_fts(self, username, persona, query):
        \"\"\"Full-text search across zettel node content using tokenized keyword OR matching.
        FIX(id-lookup): node_id is not in the FTS index and content does not carry
        its own tag, so searching "SCAR-008" used to return the nodes that MENTION
        SCAR-008 and never the module. ID-shaped tokens in the query now resolve by
        exact node_id first and rank above every keyword hit.\"\"\"
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            exact = []
            seen_ids = set()
            for tag in re.findall(r'\\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\\d{3}\\b', query or ''):
                c.execute(\"\"\"
                    SELECT id, node_id, title, content, category, embedding FROM zettel_nodes
                    WHERE (node_id COLLATE NOCASE=? OR node_id COLLATE NOCASE=?)
                      AND username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
                \"\"\", (tag, f"[[{tag}]]", username, persona))
                for r in c.fetchall():
                    if r[0] not in seen_ids:
                        seen_ids.add(r[0])
                        exact.append({"id": r[0], "node_id": r[1], "title": r[2], "content": r[3],
                                      "category": r[4], "embedding": r[5], "fts_rank": -1e9})
"""),
("""            tokens = [w for w in clean.split() if len(w) > 2 and w not in stop_words]
            if not tokens:
                conn.close()
                return []
""", """            tokens = [w for w in clean.split() if len(w) > 2 and w not in stop_words]
            if not tokens:
                conn.close()
                return exact
"""),
("""            rows = c.fetchall()
            conn.close()
            return [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": r[5], "fts_rank": r[6]} for r in rows]
        except Exception as e:
            print(f"DB ERROR (search_zettel_fts): {e}")
            return []
""", """            rows = c.fetchall()
            conn.close()
            return exact + [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": r[5], "fts_rank": r[6]}
                            for r in rows if r[0] not in seen_ids]
        except Exception as e:
            print(f"DB ERROR (search_zettel_fts): {e}")
            return []
"""),
], marker="FIX(id-lookup)")
print("done")
