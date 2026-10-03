"""
Provider wiring regression: the /providers API, the /settings None filter, and
the daemon's stand-down / key / model behaviour.

Run:  py tests/test_provider_wiring.py

What each group exists to prevent:
  * daemon stand-down  -- with no usable key the NLI sweep (up to ~153 blocking
    calls, 3 retries each) and the monologue must make ZERO upstream calls.
  * settings None-filter -- an ordinary settings save must not erase the stored
    background-task models.
  * API -- key material must never come back out of any response.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.pop("PERSONA_MASTER_KEY", None)

import provider_registry as pr
import stream_worker as sw
import llm_engine


class _FakeKeyring:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, v):
        self.value = v


class _Base(unittest.TestCase):
    ENV_KEYS = ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY", "XAI_API_KEY", "FEATHERLESS_API_KEY",
                "PERPLEXITY_API_KEY", "DAEMON_NLI_MODEL", "DAEMON_MONOLOGUE_MODEL")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (pr.DB_PATH, pr._keyring_get, pr._keyring_set)
        pr.DB_PATH = os.path.join(self._tmp.name, "t.db")
        self.kr = _FakeKeyring()
        pr._keyring_get, pr._keyring_set = self.kr.get, self.kr.set
        pr._reset_for_tests()
        self._env = {k: os.environ.pop(k, None) for k in self.ENV_KEYS}

    def tearDown(self):
        pr.DB_PATH, pr._keyring_get, pr._keyring_set = self._orig
        pr._reset_for_tests()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


# ── daemon ───────────────────────────────────────────────────────────────────
class DaemonModelTest(_Base):
    class _Mgr:
        def __init__(self, settings=None, boom=False):
            self._s, self._boom = settings or {}, boom

        def get_user_settings(self, username):
            if self._boom:
                raise RuntimeError("db down")
            return self._s

    def _model(self, mgr, **kw):
        with mock.patch.object(sw.db, "UserManager", lambda: mgr):
            return sw._daemon_model("DAEMON_NLI_MODEL", "default/model",
                                    username="u", setting="daemon_nli_model", **kw)

    def test_default_when_nothing_set(self):
        self.assertEqual(self._model(self._Mgr({})), "default/model")

    def test_user_setting_beats_default(self):
        self.assertEqual(self._model(self._Mgr({"daemon_nli_model": "anthropic/claude-x"})),
                         "anthropic/claude-x")

    def test_env_override_beats_user_setting(self):
        os.environ["DAEMON_NLI_MODEL"] = "env/model"
        self.assertEqual(self._model(self._Mgr({"daemon_nli_model": "anthropic/claude-x"})),
                         "env/model")

    def test_blank_setting_falls_through(self):
        self.assertEqual(self._model(self._Mgr({"daemon_nli_model": "   "})), "default/model")

    def test_settings_read_failure_never_breaks_a_cycle(self):
        self.assertEqual(self._model(self._Mgr(boom=True)), "default/model")


class DaemonStandDownTest(_Base):
    def _worker(self):
        w = object.__new__(sw.ConsciousnessWorker)
        w._claim_dissonance_slot = lambda *a, **k: (True, 0)
        w.DISSONANCE_CAP = 99
        return w

    def test_sweep_makes_zero_calls_without_a_usable_key(self):
        w = self._worker()
        w.get_db_connection = mock.Mock(side_effect=AssertionError("DB touched"))
        w.call_nli_gate = mock.Mock(side_effect=AssertionError("NLI called"))
        with mock.patch.object(llm_engine, "call_llm",
                               side_effect=AssertionError("upstream call")):
            self.assertIsNone(w.analyze_semantic_conflicts("u", "p"))
        w.call_nli_gate.assert_not_called()

    def test_sweep_resolves_keys_once_and_forwards_them(self):
        pr.connect("u", "openai", "sk-stored", validate=False)
        w = self._worker()

        def _conn():
            c = sqlite3.connect(":memory:")
            c.row_factory = sqlite3.Row
            c.execute("CREATE TABLE zettel_nodes (id, node_id, title, content, username, persona)")
            c.execute("INSERT INTO zettel_nodes VALUES (1,'a','A','alpha beta gamma delta epsilon','u','p')")
            c.execute("INSERT INTO zettel_nodes VALUES (2,'b','B','alpha beta gamma delta zeta','u','p')")
            return c
        w.get_db_connection = _conn
        w.call_nli_gate = mock.Mock(return_value="NEUTRAL")
        with mock.patch.object(pr, "resolve_api_keys", wraps=pr.resolve_api_keys) as spy:
            self.assertIsNone(w.analyze_semantic_conflicts("u", "p"))
        self.assertEqual(spy.call_count, 1, "keys must be resolved once per sweep")
        kwargs = w.call_nli_gate.call_args.kwargs
        self.assertEqual(kwargs["username"], "u")
        self.assertEqual(kwargs["api_keys"]["openai"], "sk-stored")

    def test_monologue_makes_zero_calls_and_keeps_its_mark_without_a_key(self):
        w = self._worker()
        w.get_db_connection = mock.Mock(side_effect=AssertionError("DB touched"))
        w._mark_monologue_processed = mock.Mock()
        with mock.patch.object(llm_engine, "call_llm",
                               side_effect=AssertionError("upstream call")):
            w.generate_idle_monologue("u", "p", "2026-01-01 00:00:00", None, None, exploring=True)
        w._mark_monologue_processed.assert_not_called()

    def test_call_nli_gate_uses_stored_key_and_user_model(self):
        pr.connect("u", "anthropic", "sk-ant-stored", validate=False)
        w = self._worker()
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return {"choices": [{"message": {"content": "NEUTRAL"}}]}
        mgr = DaemonModelTest._Mgr({"daemon_nli_model": "anthropic/claude-x"})
        with mock.patch.object(llm_engine, "call_llm", _fake), \
                mock.patch.object(sw.db, "UserManager", lambda: mgr):
            w.call_nli_gate("a", "b", username="u")
        self.assertEqual(captured["api_keys"]["anthropic"], "sk-ant-stored")
        self.assertEqual(captured["model_id"], "anthropic/claude-x")


# ── HTTP API ─────────────────────────────────────────────────────────────────
class ApiTest(_Base):
    @classmethod
    def setUpClass(cls):
        import main
        from fastapi.testclient import TestClient
        cls.main = main
        main.app.dependency_overrides[main.get_current_user] = lambda: "alice"
        cls.client = TestClient(main.app)   # no `with`: startup hooks must not run

    @classmethod
    def tearDownClass(cls):
        cls.main.app.dependency_overrides.clear()

    def test_connect_list_disconnect_and_no_key_leak(self):
        r = self.client.post("/providers/openrouter/connect",
                             json={"api_key": "sk-or-supersecret", "validate_key": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("sk-or-supersecret", r.text)
        listing = self.client.get("/providers")
        self.assertEqual(listing.status_code, 200)
        self.assertNotIn("sk-or-supersecret", listing.text)
        row = next(p for p in listing.json()["providers"] if p["id"] == "openrouter")
        self.assertTrue(row["connected"])
        d = self.client.delete("/providers/openrouter")
        self.assertEqual(d.json()["removed"], True)

    def test_bad_input_is_400_and_never_echoes_the_key(self):
        r = self.client.post("/providers/nope/connect", json={"api_key": "sk-x", "validate_key": False})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/providers/openai/connect",
                             json={"api_key": "sk-leak\nX: 1", "validate_key": False})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("sk-leak", r.text)

    def test_unknown_extra_field_rejected(self):
        r = self.client.post("/providers/openai/connect",
                             json={"api_key": "sk-x", "validate_key": False, "extra": 1})
        # main.py LAYER 3 blackholes any invalid payload with 444 (not FastAPI's 422).
        self.assertEqual(r.status_code, 444)
        row = next(p for p in self.client.get("/providers").json()["providers"] if p["id"] == "openai")
        self.assertFalse(row["connected"], "a rejected payload must store nothing")

    def test_locked_store_is_503_not_a_regenerated_key(self):
        pr.connect("alice", "openai", "sk-a", validate=False)
        self.kr.value = None
        pr._reset_for_tests()
        r = self.client.post("/providers/google/connect",
                             json={"api_key": "AIza-x", "validate_key": False})
        self.assertEqual(r.status_code, 503)
        self.assertIsNone(self.kr.value)

    def test_delete_unknown_provider_404(self):
        self.assertEqual(self.client.delete("/providers/nope").status_code, 404)

    def test_settings_save_without_daemon_fields_keeps_them(self):
        calls = []

        class _Mgr:
            def update_user_settings(self, username, settings):
                calls.append(settings)
        with mock.patch.object(self.main.db, "UserManager", _Mgr):
            self.client.post("/settings", json={"username": "alice", "review_policy": "ask"})
            self.client.post("/settings", json={"username": "alice", "daemon_nli_model": "x/y"})
            self.client.post("/settings", json={"username": "alice", "daemon_nli_model": ""})
        self.assertNotIn("daemon_nli_model", calls[0])
        self.assertNotIn("daemon_monologue_model", calls[0])
        self.assertEqual(calls[1]["daemon_nli_model"], "x/y")
        self.assertEqual(calls[2]["daemon_nli_model"], "", "empty string clears the setting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
