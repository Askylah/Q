"""
test_oauth_engine.py
Unit tests for external OAuth flows, session detection, and PKCE exchange.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import oauth_engine
from main import app


class TestOAuthEngine(unittest.TestCase):

    def setUp(self):
        import main
        main.app.dependency_overrides[main.get_current_user] = lambda: "alice"
        self.client = TestClient(app)

    def tearDown(self):
        import main
        main.app.dependency_overrides.clear()

    def test_detect_claude_session(self):
        """Should detect local Claude Code session if credentials exist."""
        info = oauth_engine.detect_claude_code_session()
        if info:
            self.assertTrue(info.get("found"))
            self.assertIn("subscription_type", info)

    def test_detect_google_session(self):
        """Should detect local Antigravity/Google account if present."""
        info = oauth_engine.detect_google_session()
        if info:
            self.assertTrue(info.get("found"))
            self.assertIn("account", info)

    def test_openrouter_pkce_start(self):
        """OpenRouter PKCE start should return valid auth URL with code_challenge."""
        url = oauth_engine.start_openrouter_oauth("test_user_oauth", "http://127.0.0.1:8000")
        self.assertTrue(url.startswith("https://openrouter.ai/auth?"))
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("callback_url=", url)

    def test_openrouter_pkce_expired_state(self):
        """Finishing OpenRouter OAuth with invalid state should raise ValueError."""
        with self.assertRaises(ValueError):
            oauth_engine.finish_openrouter_oauth("fake_code", "invalid_state_123")

    @patch("oauth_engine.requests.post")
    def test_openrouter_pkce_success_mock(self, mock_post):
        """Simulate successful code exchange with OpenRouter."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"key": "sk-or-v1-mock-oauth-key-1234567890"}
        mock_post.return_value = mock_resp

        # Inject pending state
        oauth_engine._PENDING_OAUTH["mock_state"] = {
            "provider": "openrouter",
            "username": "test_user_oauth",
            "code_verifier": "verifier123",
            "callback_url": "http://127.0.0.1:8000/oauth/callback/openrouter",
            "created_at": 9999999999
        }

        with patch("provider_registry.connect") as mock_connect:
            mock_connect.return_value = {"status": "connected"}
            res = oauth_engine.finish_openrouter_oauth("valid_code", "mock_state")
            self.assertEqual(res["status"], "connected")
            self.assertEqual(res["provider"], "openrouter")
            mock_connect.assert_called_once()

    def test_endpoint_oauth_overview(self):
        """GET /oauth/overview returns structured detection data."""
        resp = self.client.get("/oauth/overview")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("providers", data)
        self.assertIn("openrouter", data["providers"])
        self.assertIn("anthropic", data["providers"])
        self.assertIn("google", data["providers"])

    def test_endpoint_oauth_start_openrouter(self):
        """GET /oauth/openrouter/start returns auth_url."""
        resp = self.client.get("/oauth/openrouter/start")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("auth_url", data)
        self.assertTrue(data["auth_url"].startswith("https://openrouter.ai/auth?"))

    @patch("oauth_engine.requests.get")
    def test_import_google_session_mock(self, mock_get):
        """Simulate importing Google Antigravity session."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        with patch("provider_registry.connect") as mock_connect:
            mock_connect.return_value = {"status": "connected"}
            res = oauth_engine.import_google_session("alice")
            self.assertEqual(res["status"], "connected")
            self.assertEqual(res["provider"], "google")
            self.assertEqual(res["source"], "antigravity_session")
            mock_connect.assert_called_once()

    @patch("oauth_engine.import_google_session")
    def test_endpoint_oauth_import_google(self, mock_import):
        """POST /oauth/import/google routes to import_google_session."""
        mock_import.return_value = {
            "status": "connected",
            "provider": "google",
            "account": "deniseann249@gmail.com"
        }
        resp = self.client.post("/oauth/import/google")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "connected")
        self.assertEqual(data["provider"], "google")
        mock_import.assert_called_once_with("alice")


if __name__ == "__main__":
    unittest.main()

