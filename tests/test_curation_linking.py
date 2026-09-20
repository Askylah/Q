"""
Curation fixes: curated links must be visible to query-time expansion,
store_curated_observation must auto-link new curated notes to similar lore,
and a linking failure must not fail the store. Plus a dumb little schema
check for the deep_memories index.

unittest, no pytest. Isolated temp SQLite DB via PERSONAAPP_DB_PATH (set
BEFORE importing any project module -- database.py/memory_engine.py run
schema code at import/construction time). Embedder is stubbed to a
deterministic one-hot encoder so similarity is fully controllable.

Run directly: python tests/test_curation_linking.py
"""
import os
import sys
import sqlite3
import shutil
import tempfile
import unittest
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP_DIR = tempfile.mkdtemp(prefix="curation_linking_")
os.environ["PERSONAAPP_DB_PATH"] = os.path.join(TMP_DIR, "users.db")
os.environ["TELEMETRY_OFF"] = "1"

import database as db  # noqa: E402
import zettel_engine as ze  # noqa: E402
import memory_engine  # noqa: E402

print(f"[TEST] database.DB_PATH = {db.DB_PATH}")
assert db.DB_PATH == os.environ["PERSONAAPP_DB_PATH"], "DB_PATH did not resolve to the test temp file!"

DIM = 384
U, P = "curation_user", "curation_persona"


def one_hot(idx, dim=DIM):
    v = np.zeros(dim, dtype=np.float32)
    v[idx] = 1.0
    return v


def near(idx, dim=DIM, off=0.05):
    """A vector close to but not identical to one_hot(idx) -- cos sim just under 1.0."""
    v = one_hot(idx, dim)
    v[(idx + 1) % dim] = off
    return v / np.linalg.norm(v)


class StubEmbedder:
    """Deterministic exact-string -> vector map. encode() mirrors
    SentenceTransformer's shape contract: a str in -> 1D vector out,
    a list in -> 2D array out."""

    def __init__(self):
        self.vecmap = {}

    def register(self, text, vec):
        self.vecmap[text] = np.asarray(vec, dtype=np.float32)

    def encode(self, texts, convert_to_numpy=True, **kwargs):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        vecs = [self.vecmap.get(t, np.zeros(DIM, dtype=np.float32)) for t in items]
        arr = np.array(vecs, dtype=np.float32)
        return arr[0] if single else arr


def add_lore_node(node_tag, title, content, vec, node_class="lore"):
    pk = f"pk-{node_tag}"
    db.UserManager().add_zettel_node(
        node_id_pk=pk,
        username=U,
        persona=P,
        node_id_tag=node_tag,
        title=title,
        content=content,
        category="CONCEPT",
        embedding_blob=vec.tobytes() if vec is not None else None,
        source_entry_id="test",
        node_class=node_class,
    )
    return pk


class CurationLinkingTests(unittest.TestCase):

    def setUp(self):
        # Fresh DB per test: cheapest way is to nuke the tables we touch.
        db.UserManager()  # ensures schema exists
        conn = sqlite3.connect(db.DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM zettel_nodes")
        c.execute("DELETE FROM zettel_links")
        c.execute("DELETE FROM zettel_fts")
        conn.commit()
        conn.close()
        with ze._CACHE_LOCK:
            ze._EMBEDDING_CACHE.clear()
        ze._TRIGGER_REGEX_CACHE.clear()
        self.embedder = StubEmbedder()
        self._orig_get_shared_model = ze.get_shared_model
        ze.get_shared_model = lambda: self.embedder
        import redis_client
        self._orig_redis_active = redis_client.is_active
        redis_client.is_active = lambda: False

    def tearDown(self):
        ze.get_shared_model = self._orig_get_shared_model
        import redis_client
        redis_client.is_active = self._orig_redis_active

    # ── (a) curated link visibility at query time ────────────────────────

    def test_curated_link_bypasses_strength_floor(self):
        seed_vec = one_hot(0)
        curated_target_vec = one_hot(50)   # unrelated content-wise
        weak_target_vec = one_hot(60)

        seed_pk = add_lore_node("LORE-SEED", "Seed", "Zorblatt frobnication sequence alpha.", seed_vec)
        curated_pk = add_lore_node("LORE-CURATED", "Curated Target", "Quixotic marmalade expedition report.", curated_target_vec)
        weak_pk = add_lore_node("LORE-WEAK", "Weak Target", "Untouched glacier survey findings.", weak_target_vec)

        # Curated link at the create_curated_link() default strength (0.5, well under LINK_FLOOR=0.70)
        r = ze.create_curated_link(U, P, "LORE-SEED", "LORE-CURATED")
        self.assertTrue(r.startswith("SUCCESS"), r)

        # A non-curated link, also below the floor, must still NOT be traversed
        db.UserManager().add_zettel_link(
            link_id="link-weak", source_node_id=seed_pk, target_node_id=weak_pk,
            relationship="related_to", strength=0.3, label="incidental_but_not_excluded",
        )

        query = "Tell me about the zorblatt frobnication sequence."
        self.embedder.register(query, seed_vec)
        context = ze.query_knowledge_graph(U, P, query, top_k=5)

        self.assertIn("LORE-CURATED", context, "curated link at default strength must be followed")
        self.assertNotIn("LORE-WEAK", context, "a non-curated sub-floor link must still be excluded")

    # ── (b) link-on-write for curated notes ───────────────────────────────

    def test_store_curated_observation_auto_links_similar_lore(self):
        new_vec = one_hot(1)
        similar1 = near(1, off=0.02)   # very close -> sim near 1.0
        similar2 = near(1, off=0.10)   # still close, but weaker than similar1
        dissimilar = one_hot(200)      # orthogonal -> sim 0.0
        behavioral_similar = near(1, off=0.02)

        add_lore_node("LORE-SIM-1", "Similar One", "Similar lore one.", similar1)
        add_lore_node("LORE-SIM-2", "Similar Two", "Similar lore two.", similar2)
        add_lore_node("LORE-FAR", "Far", "Unrelated lore.", dissimilar)
        add_lore_node("BEHAV-SIM", "Behavioral twin", "Should never be linked.", behavioral_similar, node_class="behavioral")

        content = "A brand new curated observation about the topic."
        self.embedder.register(content, new_vec)

        result = ze.store_curated_observation(U, P, "New Note", content)
        self.assertTrue(result.startswith("SUCCESS"), result)

        new_pk = sqlite3.connect(db.DB_PATH).execute(
            "SELECT id FROM zettel_nodes WHERE username=? AND persona=? AND title=?", (U, P, "New Note")
        ).fetchone()[0]

        links = sqlite3.connect(db.DB_PATH).execute(
            "SELECT source_node_id, target_node_id FROM zettel_links WHERE source_node_id=?", (new_pk,)
        ).fetchall()
        linked_targets = {t for _, t in links}

        self.assertIn("pk-LORE-SIM-1", linked_targets)
        self.assertIn("pk-LORE-SIM-2", linked_targets)
        self.assertNotIn("pk-LORE-FAR", linked_targets, "dissimilar lore must not be linked")
        self.assertNotIn("pk-BEHAV-SIM", linked_targets, "behavioral nodes must never be auto-linked")
        self.assertNotIn(new_pk, linked_targets, "must never self-link")
        self.assertLessEqual(len(links), 3, "at most 3 auto-links")

    def test_store_curated_observation_no_links_when_nothing_similar(self):
        new_vec = one_hot(5)
        add_lore_node("LORE-FAR-A", "Far A", "Far lore A.", one_hot(210))
        add_lore_node("LORE-FAR-B", "Far B", "Far lore B.", one_hot(220))

        content = "Another isolated curated note."
        self.embedder.register(content, new_vec)

        result = ze.store_curated_observation(U, P, "Isolated Note", content)
        self.assertTrue(result.startswith("SUCCESS"), result)

        new_pk = sqlite3.connect(db.DB_PATH).execute(
            "SELECT id FROM zettel_nodes WHERE username=? AND persona=? AND title=?", (U, P, "Isolated Note")
        ).fetchone()[0]
        links = sqlite3.connect(db.DB_PATH).execute(
            "SELECT count(*) FROM zettel_links WHERE source_node_id=?", (new_pk,)
        ).fetchone()[0]
        self.assertEqual(links, 0)

    def test_store_curated_observation_caps_at_three_links(self):
        new_vec = one_hot(2)
        for i in range(5):
            add_lore_node(f"LORE-MANY-{i}", f"Many {i}", f"Many lore {i}.", near(2, off=0.01 * (i + 1)))

        content = "A note with five similar candidates."
        self.embedder.register(content, new_vec)

        result = ze.store_curated_observation(U, P, "Capped Note", content)
        self.assertTrue(result.startswith("SUCCESS"), result)

        new_pk = sqlite3.connect(db.DB_PATH).execute(
            "SELECT id FROM zettel_nodes WHERE username=? AND persona=? AND title=?", (U, P, "Capped Note")
        ).fetchone()[0]
        links = sqlite3.connect(db.DB_PATH).execute(
            "SELECT count(*) FROM zettel_links WHERE source_node_id=?", (new_pk,)
        ).fetchone()[0]
        self.assertEqual(links, 3)

    # ── (c) linking failure must not fail the store ───────────────────────

    def test_linking_failure_still_returns_success(self):
        new_vec = one_hot(3)
        add_lore_node("LORE-TRIP", "Trip", "Trip lore.", near(3, off=0.01))

        content = "A note whose linking step will explode."
        self.embedder.register(content, new_vec)

        orig_add_link = db.UserManager.add_zettel_link

        def boom(self, *a, **kw):
            raise RuntimeError("simulated link failure")

        db.UserManager.add_zettel_link = boom
        try:
            result = ze.store_curated_observation(U, P, "Doomed Note", content)
        finally:
            db.UserManager.add_zettel_link = orig_add_link

        self.assertTrue(result.startswith("SUCCESS"), result)
        row = sqlite3.connect(db.DB_PATH).execute(
            "SELECT id FROM zettel_nodes WHERE username=? AND persona=? AND title=?", (U, P, "Doomed Note")
        ).fetchone()
        self.assertIsNotNone(row, "the node must exist even though linking blew up")

    # ── (d) deep_memories index ────────────────────────────────────────────

    def test_deep_memories_has_scope_index(self):
        memory_engine._ensure_table()
        conn = sqlite3.connect(db.DB_PATH)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='deep_memories' AND name='idx_deep_mem_scope'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row, "idx_deep_mem_scope must exist on deep_memories")


if __name__ == "__main__":
    try:
        result = unittest.main(exit=False).result
        success = result.wasSuccessful()
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    sys.exit(0 if success else 1)
