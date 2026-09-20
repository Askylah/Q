"""
Storage hardening: tenant-scoped cascades, complete erasure, usable indexes,
and a schema init that runs once instead of once per request.

Standalone, like the rest of tests/ -- no pytest. Everything runs against a
throwaway SQLite file selected via PERSONAAPP_DB_PATH, which MUST be set before
any project module is imported: database.py resolves DB_PATH at import time and
UserManager() executes schema DDL in its constructor.

Rows are inserted with direct SQL rather than through zettel_engine, which would
drag in the sentence-transformers model for a test that never looks at a vector.
"""
import os
import sys
import shutil
import sqlite3
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="storage_hardening_")
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP, "users.db")
os.environ["PERSONAAPP_DATA_DIR"] = TMP
os.environ["TELEMETRY_OFF"] = "1"

import database as db  # noqa: E402  (import order is the point)

# Fail loudly rather than mutating somebody's real data.
assert db.DB_PATH == os.path.join(TMP, "users.db"), (
    f"REFUSING TO RUN: database.DB_PATH is {db.DB_PATH!r}, not the temp file")
print(f"[isolation] database.DB_PATH = {db.DB_PATH}")

db.UserManager()   # build the schema in the throwaway file, once, up front

A, B = "alice", "bob"
P1, P2 = "rick", "morty"
CURATED = "source:persona_curation"


def conn():
    return sqlite3.connect(db.DB_PATH)


def q(sql, *args):
    c = conn()
    try:
        rows = c.execute(sql, args).fetchall()
        c.commit()
        return rows
    finally:
        c.close()


def add_node(pk, username, persona, source_entry_id, title="t", content="c"):
    """Insert a node + its FTS mirror directly (no embedding model)."""
    c = conn()
    try:
        c.execute("""INSERT INTO zettel_nodes
                     (id, username, persona, node_id, title, content, category,
                      embedding, source_entry_id, created_at, node_class, trigger_type, content_hash)
                     VALUES (?,?,?,?,?,?,?,NULL,?,'2026-01-01','lore','PROBABILISTIC',NULL)""",
                  (pk, username, persona, pk.upper(), title, content, "cat", source_entry_id))
        c.execute("INSERT INTO zettel_fts (node_db_id, content, title, category) VALUES (?,?,?,?)",
                  (pk, content, title, "cat"))
        c.commit()
    finally:
        c.close()


def add_link(link_id, src, dst):
    c = conn()
    try:
        c.execute("""INSERT INTO zettel_links (id, source_node_id, target_node_id,
                     relationship, strength, created_at, label)
                     VALUES (?,?,?,'links_to',1.0,'2026-01-01','related')""", (link_id, src, dst))
        c.commit()
    finally:
        c.close()


def add_entry(entry_id, username, persona):
    c = conn()
    try:
        c.execute("""INSERT INTO zettel_entries (id, username, persona, title, raw_content, processed, created_at)
                     VALUES (?,?,?,'title','body',0,'2026-01-01')""", (entry_id, username, persona))
        c.commit()
    finally:
        c.close()


def node_ids():
    return {r[0] for r in q("SELECT id FROM zettel_nodes")}


def fts_ids():
    return {r[0] for r in q("SELECT node_db_id FROM zettel_fts")}


def link_ids():
    return {r[0] for r in q("SELECT id FROM zettel_links")}


def wipe_all():
    """Truncate the zettel tables between cases without touching the schema."""
    c = conn()
    try:
        for t in ("zettel_nodes", "zettel_links", "zettel_fts", "zettel_entries"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
    finally:
        c.close()


class CrossTenantCascade(unittest.TestCase):
    """The curated-node source_entry_id is a constant shared by every account."""

    def setUp(self):
        wipe_all()
        self.dbm = db.UserManager()

    def test_curated_delete_does_not_reach_another_user(self):
        add_node("a_cur", A, P1, CURATED)
        add_node("b_cur", B, P1, CURATED)          # different USER, same source id
        add_node("a_cur_p2", A, P2, CURATED)       # same user, different PERSONA
        add_link("l_ab", "a_cur", "b_cur")         # edge straddling the tenant boundary
        add_link("l_bb", "b_cur", "b_cur")         # entirely bob's

        # No zettel_entries row exists for the curated pseudo-id; that is exactly
        # the case the old code cascaded on before discovering it had no entry.
        self.dbm.delete_zettel_entry(A, P1, CURATED)

        self.assertNotIn("a_cur", node_ids(), "alice's own curated node should go")
        self.assertIn("b_cur", node_ids(), "bob's curated node must survive alice's delete")
        self.assertIn("a_cur_p2", node_ids(), "alice's OTHER persona must be untouched")
        self.assertIn("b_cur", fts_ids(), "bob's FTS row must survive")
        self.assertIn("a_cur_p2", fts_ids())
        self.assertNotIn("a_cur", fts_ids())
        self.assertIn("l_bb", link_ids(), "a link wholly inside bob's scope must survive")

    def test_curated_update_does_not_reach_another_user(self):
        add_node("a_cur", A, P1, CURATED)
        add_node("b_cur", B, P1, CURATED)
        add_node("a_cur_p2", A, P2, CURATED)
        add_link("l_bb", "b_cur", "b_cur")

        self.dbm.update_zettel_entry(A, P1, CURATED, "new title", "new body")

        self.assertNotIn("a_cur", node_ids())
        self.assertIn("b_cur", node_ids(), "bob's curated node must survive alice's update")
        self.assertIn("a_cur_p2", node_ids())
        self.assertIn("b_cur", fts_ids())
        self.assertIn("l_bb", link_ids())

    def test_normal_lore_delete_still_cascades(self):
        """The fix must not neuter the case it was protecting."""
        add_entry("e1", A, P1)
        add_node("n1", A, P1, "e1")
        add_node("n2", A, P1, "e1")
        add_node("keep", A, P1, "e2")              # a different entry of the same user
        add_link("l_in", "n1", "n2")               # both ends inside the doomed entry
        add_link("l_src", "n1", "keep")            # doomed node as SOURCE
        add_link("l_tgt", "keep", "n2")            # doomed node as TARGET

        self.assertTrue(self.dbm.delete_zettel_entry(A, P1, "e1"))

        self.assertEqual(node_ids(), {"keep"})
        self.assertEqual(fts_ids(), {"keep"})
        self.assertEqual(link_ids(), set(), "links must die from EITHER end")
        self.assertEqual(q("SELECT count(*) FROM zettel_entries WHERE id='e1'")[0][0], 0)

    def test_normal_lore_update_clears_nodes_and_resets_flag(self):
        add_entry("e1", A, P1)
        add_node("n1", A, P1, "e1")
        add_link("l_self", "n1", "n1")
        q("UPDATE zettel_entries SET processed=1 WHERE id='e1'")

        self.assertTrue(self.dbm.update_zettel_entry(A, P1, "e1", "T2", "B2"))

        self.assertEqual(node_ids(), set())
        self.assertEqual(fts_ids(), set())
        self.assertEqual(link_ids(), set())
        row = q("SELECT title, raw_content, processed FROM zettel_entries WHERE id='e1'")[0]
        self.assertEqual(row, ("T2", "B2", 0))

    def test_foreign_entry_id_is_a_no_op(self):
        """Alice aiming a real entry id belonging to bob changes nothing."""
        add_entry("e_bob", B, P1)
        add_node("n_bob", B, P1, "e_bob")
        add_link("l_bob", "n_bob", "n_bob")

        self.dbm.delete_zettel_entry(A, P1, "e_bob")

        self.assertIn("n_bob", node_ids())
        self.assertIn("n_bob", fts_ids())
        self.assertIn("l_bob", link_ids())
        self.assertEqual(q("SELECT count(*) FROM zettel_entries WHERE id='e_bob'")[0][0], 1)


class DeepMemoryErasure(unittest.TestCase):
    """deep_memories is created lazily by memory_engine and was never wiped."""

    DDL = """CREATE TABLE IF NOT EXISTS deep_memories (
                 id TEXT PRIMARY KEY, username TEXT, persona TEXT, content TEXT,
                 importance REAL DEFAULT 0.5, active INTEGER DEFAULT 1, created_at TEXT)"""

    def setUp(self):
        self.dbm = db.UserManager()
        self.drop()

    def drop(self):
        c = conn()
        try:
            c.execute("DROP TABLE IF EXISTS deep_memories")
            c.commit()
        finally:
            c.close()

    def seed(self):
        c = conn()
        try:
            c.execute(self.DDL)
            # memory_engine stores exact case; the wipe must still find these.
            for mid, u, p in (("m_a1", "Alice", P1), ("m_a2", A, P1),
                              ("m_a_p2", A, P2), ("m_b", B, P1)):
                c.execute("INSERT INTO deep_memories (id, username, persona, content, created_at)"
                          " VALUES (?,?,?,'x','2026-01-01')", (mid, u, p))
            c.commit()
        finally:
            c.close()

    def ids(self):
        return {r[0] for r in q("SELECT id FROM deep_memories")}

    def test_wipe_memories_tolerates_a_missing_table(self):
        self.assertTrue(self.dbm.wipe_memories(A, P1),
                        "a wipe must succeed on a database that never used deep memory")

    def test_persona_delete_tolerates_a_missing_table(self):
        self.assertTrue(self.dbm.delete_custom_persona(A, P1))

    def test_wipe_memories_erases_only_that_scope(self):
        self.seed()
        self.assertTrue(self.dbm.wipe_memories(A, P1))
        self.assertEqual(self.ids(), {"m_a_p2", "m_b"},
                         "case variants of the scope must go; other scopes must not")

    def test_persona_delete_erases_only_that_scope(self):
        self.seed()
        self.assertTrue(self.dbm.delete_custom_persona(A, P1))
        self.assertEqual(self.ids(), {"m_a_p2", "m_b"})


class DeepMemoryHelperScope(unittest.TestCase):
    """The helper's swallow is narrow: only 'no such table'."""

    def test_missing_table_returns_zero(self):
        c = conn()
        try:
            c.execute("DROP TABLE IF EXISTS deep_memories")
            c.commit()
            self.assertEqual(db._delete_deep_memories(c.cursor(), A, P1), 0)
        finally:
            c.close()

    def test_other_operational_errors_raise(self):
        c = conn()
        try:
            c.execute("DROP TABLE IF EXISTS deep_memories")
            # a deep_memories with no `persona` column: a real schema fault,
            # which must NOT be mistaken for "not created yet".
            c.execute("CREATE TABLE deep_memories (id TEXT, username TEXT)")
            c.commit()
            with self.assertRaises(sqlite3.OperationalError):
                db._delete_deep_memories(c.cursor(), A, P1)
        finally:
            c.execute("DROP TABLE IF EXISTS deep_memories")
            c.commit()
            c.close()


class IndexUsage(unittest.TestCase):
    """A BINARY index cannot serve a COLLATE NOCASE comparison."""

    NODE_Q = ("SELECT id FROM zettel_nodes "
              "WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?")

    def plan(self, sql, *args):
        return " | ".join(r[-1] for r in q("EXPLAIN QUERY PLAN " + sql, *args))

    def test_nocase_node_query_uses_an_index(self):
        plan = self.plan(self.NODE_Q, A, P1)
        self.assertIn("SEARCH", plan, f"expected an index SEARCH, got: {plan}")
        self.assertNotIn("SCAN zettel_nodes", plan, f"still a full scan: {plan}")

    def test_nocase_entries_query_uses_an_index(self):
        plan = self.plan("SELECT id FROM zettel_entries "
                         "WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", A, P1)
        self.assertIn("SEARCH", plan, f"expected an index SEARCH, got: {plan}")

    def test_source_entry_cascade_uses_an_index(self):
        plan = self.plan("SELECT id FROM zettel_nodes WHERE source_entry_id=?", CURATED)
        self.assertIn("SEARCH", plan, f"expected an index SEARCH, got: {plan}")

    def test_the_old_binary_index_is_still_present(self):
        names = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_znodes_user_persona", names)
        self.assertIn("idx_znodes_user_persona_nocase", names)


class SchemaInitOnce(unittest.TestCase):
    """_init_db is ~25 statements and used to run on every UserManager()."""

    def setUp(self):
        self.calls = []
        self.real = db.UserManager._init_db

        def counting(inner_self):
            self.calls.append(db.DB_PATH)
            return self.real(inner_self)

        db.UserManager._init_db = counting

    def tearDown(self):
        db.UserManager._init_db = self.real

    def test_repeat_construction_does_not_reinitialise(self):
        db.UserManager()
        db.UserManager()
        db.UserManager()
        self.assertEqual(self.calls, [], "schema init re-ran for an already-built database")

    def test_a_new_db_path_is_initialised(self):
        original = db.DB_PATH
        other = os.path.join(TMP, "other.db")
        try:
            db.DB_PATH = other
            db.UserManager()
            db.UserManager()
            self.assertEqual(self.calls, [other],
                             "a rebound DB_PATH must be initialised exactly once")
            self.assertTrue(os.path.exists(other))
            tables = {r[0] for r in sqlite3.connect(other).execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("zettel_nodes", tables)
        finally:
            db.DB_PATH = original

    def test_a_vanished_database_is_rebuilt(self):
        original = db.DB_PATH
        gone = os.path.join(TMP, "gone.db")
        try:
            db.DB_PATH = gone
            db.UserManager()
            os.remove(gone)
            db.UserManager()
            self.assertEqual(self.calls, [gone, gone],
                             "a deleted database must be re-initialised, not assumed present")
        finally:
            db.DB_PATH = original


class ConnectionPragmas(unittest.TestCase):
    """synchronous is per-connection; setting it only in _init_db set it nowhere."""

    def test_app_db_connections_get_synchronous_normal(self):
        c = sqlite3.connect(db.DB_PATH)
        try:
            self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 1)  # 1 == NORMAL
            self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        finally:
            c.close()

    def test_unrelated_databases_are_left_alone(self):
        for target in (":memory:", os.path.join(TMP, "unrelated.db")):
            c = sqlite3.connect(target)
            try:
                self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2,
                                 f"{target} should keep sqlite's FULL default")
            finally:
                c.close()

    def test_matching_is_robust_to_a_rebound_db_path(self):
        original = db.DB_PATH
        rebound = os.path.join(TMP, "rebound.db")
        try:
            db.DB_PATH = rebound
            c = sqlite3.connect(rebound)
            try:
                self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 1)
            finally:
                c.close()
            # ...and the previous path is no longer special
            c = sqlite3.connect(original)
            try:
                self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2)
            finally:
                c.close()
        finally:
            db.DB_PATH = original

    def test_a_uri_connection_is_not_pragma_d(self):
        """mode=ro URIs (app_paths._looks_populated) must not be written to."""
        self.assertFalse(db._is_app_db(f"file:{db.DB_PATH}?mode=ro"))


if __name__ == "__main__":
    try:
        result = unittest.main(exit=False, verbosity=2).result
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(0 if result.wasSuccessful() else 1)
