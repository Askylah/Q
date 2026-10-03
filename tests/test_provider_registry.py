"""
Provider credential store regression.

Run:  py tests/test_provider_registry.py

The heart of this file is the FAIL-CLOSED group: a missing/erroring keyring must
never cause a new master key to be generated while ciphertext exists, because
that would orphan every stored credential at once and send the background daemon
into a retry storm of unauthenticated calls.
"""
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
if _ROOT not in sys.path:
    sys.path.insert(1, _ROOT)
os.environ.pop("PERSONA_MASTER_KEY", None)

import provider_registry as pr
from cryptography.fernet import Fernet


class _FakeKeyring:
    def __init__(self):
        self.value = None
        self.sets = 0
        self.broken = False

    def get(self):
        if self.broken:
            raise RuntimeError("no keyring backend")
        return self.value

    def set(self, v):
        if self.broken:
            raise RuntimeError("no keyring backend")
        self.sets += 1
        self.value = v


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (pr.DB_PATH, pr._keyring_get, pr._keyring_set)
        pr.DB_PATH = os.path.join(self._tmp.name, "t.db")
        self.kr = _FakeKeyring()
        pr._keyring_get, pr._keyring_set = self.kr.get, self.kr.set
        pr._reset_for_tests()
        self._env = {k: os.environ.pop(k, None) for k in
                     ("OPENROUTER_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
                      "OPENAI_API_KEY", "PERSONA_MASTER_KEY")}

    def tearDown(self):
        pr.DB_PATH, pr._keyring_get, pr._keyring_set = self._orig
        pr._reset_for_tests()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    @staticmethod
    def _query(sql):
        """One-shot read that closes its connection (Windows cannot delete an
        open SQLite file, which would break tearDown)."""
        import sqlite3
        conn = sqlite3.connect(pr.DB_PATH)
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()

    # --- basics -------------------------------------------------------------
    def test_round_trip_and_encrypted_at_rest(self):
        pr.connect("u", "openrouter", "sk-or-abc123", validate=False)
        self.assertEqual(pr.resolve_api_keys("u")["openrouter"], "sk-or-abc123")
        raw = self._query("SELECT secret_enc FROM provider_credentials")
        self.assertNotIn(b"sk-or-abc123", bytes(raw))

    def test_status_never_leaks_secret(self):
        pr.connect("u", "openai", "sk-secretvalue", validate=False)
        self.assertNotIn("sk-secretvalue", repr(pr.list_status("u")))

    def test_users_are_isolated(self):
        pr.connect("a", "openai", "sk-a", validate=False)
        self.assertEqual(pr.resolve_api_keys("b")["openai"], "")

    def test_disconnect(self):
        pr.connect("u", "openai", "sk-a", validate=False)
        self.assertTrue(pr.disconnect("u", "openai"))
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")
        self.assertFalse(pr.disconnect("u", "openai"))

    def test_precedence_env_lt_stored_lt_request(self):
        os.environ["GOOGLE_API_KEY"] = "env-g"
        self.assertEqual(pr.resolve_api_keys("u")["google"], "env-g")
        pr.connect("u", "google", "stored-g", validate=False)
        self.assertEqual(pr.resolve_api_keys("u")["google"], "stored-g")
        self.assertEqual(
            pr.resolve_api_keys("u", {"google": "req-g"})["google"], "req-g")
        self.assertEqual(
            pr.resolve_api_keys("u", {"google": ""})["google"], "stored-g")

    # --- input hardening ----------------------------------------------------
    def test_rejects_unknown_provider_and_bad_keys(self):
        for prov, sec in [("nope", "k"), ("openai", ""), ("openai", "   "),
                          ("openai", "sk-a\nX-Evil: 1"), ("openai", "sk-é")]:
            with self.assertRaises(pr.ProviderRejected, msg=(prov, sec)):
                pr.connect("u", prov, sec, validate=False)

    def test_provider_rejected_key_is_not_stored(self):
        orig = pr._validate
        pr._validate = lambda p, s: ("invalid", "provider rejected the key (401)")
        try:
            with self.assertRaises(pr.ProviderRejected):
                pr.connect("u", "openai", "sk-bad")
        finally:
            pr._validate = orig
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")

    def test_unreachable_provider_does_not_block_connect(self):
        orig = pr._validate
        pr._validate = lambda p, s: ("unverified", "could not reach provider")
        try:
            res = pr.connect("u", "openai", "sk-ok")
        finally:
            pr._validate = orig
        self.assertEqual(res["status"], "unverified")

    # --- FAIL CLOSED --------------------------------------------------------
    def test_missing_master_key_with_ciphertext_never_regenerates(self):
        pr.connect("u", "openai", "sk-a", validate=False)
        self.assertEqual(self.kr.sets, 1)
        self.kr.value = None          # keyring entry lost
        pr._reset_for_tests()
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")
        status = pr.list_status("u")
        self.assertTrue(status["locked"])
        self.assertFalse(next(p for p in status["providers"]
                              if p["id"] == "openai")["connected"])
        with self.assertRaises(pr.ProviderStoreLocked):
            pr.connect("u", "google", "AIza-new", validate=False)
        self.assertEqual(self.kr.sets, 1, "a new master key must NOT be written")
        self.assertIsNone(self.kr.value)

    def test_recovers_when_keyring_returns(self):
        pr.connect("u", "openai", "sk-a", validate=False)
        good = self.kr.value
        self.kr.value = None
        pr._reset_for_tests()
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")
        self.kr.value = good          # backend comes back
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "sk-a")

    def test_erroring_keyring_locks_instead_of_regenerating(self):
        pr.connect("u", "openai", "sk-a", validate=False)
        self.kr.broken = True
        pr._reset_for_tests()
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")
        self.assertTrue(pr.list_status("u")["locked"])
        self.assertEqual(self.kr.sets, 1)

    def test_wrong_master_key_marks_needs_reauth(self):
        pr.connect("u", "openai", "sk-a", validate=False)
        self.kr.value = Fernet.generate_key().decode()   # a different valid key
        pr._reset_for_tests()
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "")
        st = next(p for p in pr.list_status("u")["providers"] if p["id"] == "openai")
        self.assertEqual(st["status"], "needs_reauth")
        self.assertFalse(st["connected"])

    def test_first_connect_with_broken_keyring_fails_without_partial_state(self):
        self.kr.broken = True
        with self.assertRaises(pr.ProviderStoreLocked):
            pr.connect("u", "openai", "sk-a", validate=False)
        n = self._query("SELECT COUNT(*) FROM provider_credentials")
        self.assertEqual(n, 0)

    def test_env_master_key_supports_headless(self):
        os.environ["PERSONA_MASTER_KEY"] = Fernet.generate_key().decode()
        self.kr.broken = True         # keyring never consulted
        pr._reset_for_tests()
        pr.connect("u", "openai", "sk-a", validate=False)
        self.assertEqual(pr.resolve_api_keys("u")["openai"], "sk-a")

    # --- daemon guard -------------------------------------------------------
    def test_has_usable_key(self):
        self.assertFalse(pr.has_usable_key({}))
        self.assertFalse(pr.has_usable_key({"openai": "", "google": "  "}))
        self.assertTrue(pr.has_usable_key({"openai": "", "google": "AIza"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
