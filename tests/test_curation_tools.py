import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import uuid
import sqlite3
import database as db
import zettel_engine

class TestCurationTools(unittest.TestCase):
    def setUp(self):
        self.username = "curation_test_user"
        self.persona = "test_persona"
        self.db = db.UserManager()
        
        # Clean test user
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("DELETE FROM zettel_nodes WHERE username = ?", (self.username,))
        conn.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username = ?)", (self.username,))
        conn.commit()
        conn.close()
        zettel_engine.invalidate_zettel_cache(self.username, self.persona)

    def tearDown(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("DELETE FROM zettel_nodes WHERE username = ?", (self.username,))
        conn.commit()
        conn.close()
        zettel_engine.invalidate_zettel_cache(self.username, self.persona)

    def test_store_and_resolve_curated_observation(self):
        # 1. Store observation
        res = zettel_engine.store_curated_observation(
            username=self.username,
            persona=self.persona,
            title="Curated Quantum Battery Note",
            content="Microverse batteries generate power using spatial dilation chambers.",
            category="DISCOVERY"
        )
        self.assertIn("SUCCESS", res)
        self.assertIn("DISCOVERY", res)

        # 2. Resolve tag by title
        pk, tag, err = zettel_engine.resolve_tag_to_pk(self.username, self.persona, "Curated Quantum Battery Note")
        self.assertIsNone(err)
        self.assertIsNotNone(pk)
        self.assertIsNotNone(tag)

        # 3. Resolve tag with brackets
        pk2, tag2, err2 = zettel_engine.resolve_tag_to_pk(self.username, self.persona, f"[[{tag}]]")
        self.assertIsNone(err2)
        self.assertEqual(pk, pk2)

    def test_create_curated_link(self):
        # Create two nodes
        zettel_engine.store_curated_observation(self.username, self.persona, "Node A", "Content for node Alpha.", "CONCEPT")
        zettel_engine.store_curated_observation(self.username, self.persona, "Node B", "Content for node Beta.", "CONCEPT")

        # Link them
        link_res = zettel_engine.create_curated_link(
            username=self.username,
            persona=self.persona,
            source_tag="Node A",
            target_tag="Node B",
            relationship="powers",
            strength=0.85
        )
        self.assertIn("SUCCESS", link_res)
        self.assertIn("powers", link_res)

        # Self-link should fail
        self_res = zettel_engine.create_curated_link(self.username, self.persona, "Node A", "Node A")
        self.assertIn("LINK_ERROR", self_res)

    def test_ambiguity_fails_loud(self):
        # Insert two nodes with identical titles
        pk1 = str(uuid.uuid4())
        pk2 = str(uuid.uuid4())
        self.db.add_zettel_node(
            node_id_pk=pk1,
            username=self.username,
            persona=self.persona,
            node_id_tag="[[CONCEPT-DUP-001]]",
            title="Identical Title",
            content="Content 1",
            category="CONCEPT",
            embedding_blob=None,
            source_entry_id="source:manual"
        )
        self.db.add_zettel_node(
            node_id_pk=pk2,
            username=self.username,
            persona=self.persona,
            node_id_tag="[[CONCEPT-DUP-002]]",
            title="Identical Title",
            content="Content 2",
            category="CONCEPT",
            embedding_blob=None,
            source_entry_id="source:manual"
        )

        pk, tag, err = zettel_engine.resolve_tag_to_pk(self.username, self.persona, "Identical Title")
        self.assertIsNone(pk)
        self.assertIn("AMBIGUOUS", err)
        self.assertIn("CONCEPT-DUP-001", err)
        self.assertIn("CONCEPT-DUP-002", err)

if __name__ == "__main__":
    unittest.main()
