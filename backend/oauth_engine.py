"""
oauth_engine.py
Modular External OAuth and Interactive Authentication Manager for Q.
Supports:
1. OpenRouter PKCE OAuth Flow (opens browser, user authorizes, captures key via callback)
2. Anthropic Interactive OAuth (imports live Claude Code OAuth session or setup-token)
3. Google OAuth Flow (imports live Antigravity / Google Cloud session or browser OAuth)
"""

import os
import json
import secrets
import hashlib
import base64
import urllib.parse
import time
import requests
from typing import Optional, Dict, Any

import provider_registry

# In-memory store for pending PKCE authorization requests: state -> metadata
_PENDING_OAUTH: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 600  # 10 minute window for user to authorize in browser


def _clean_expired_pending():
    now = time.time()
    expired = [k for k, v in _PENDING_OAUTH.items() if now - v.get("created_at", 0) > _PENDING_TTL_SECONDS]
    for k in expired:
        _PENDING_OAUTH.pop(k, None)


# ============================================================================
# 1. OPENROUTER PKCE OAUTH
# ============================================================================

def start_openrouter_oauth(username: str, callback_base: str) -> str:
    """
    Generate PKCE challenge and return OpenRouter authorization URL.
    """
    _clean_expired_pending()

    # Generate 64-byte random code verifier
    code_verifier = secrets.token_urlsafe(64)
    # S256 code challenge
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    state = secrets.token_hex(16)
    callback_url = f"{callback_base.rstrip('/')}/oauth/callback/openrouter"

    _PENDING_OAUTH[state] = {
        "provider": "openrouter",
        "username": username,
        "code_verifier": code_verifier,
        "callback_url": callback_url,
        "created_at": time.time()
    }

    params = {
        "callback_url": callback_url,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state
    }
    return f"https://openrouter.ai/auth?{urllib.parse.urlencode(params)}"


def finish_openrouter_oauth(code: str, state: str) -> Dict[str, Any]:
    """
    Exchange authorization code for OpenRouter API key and store in vault.
    """
    pending = _PENDING_OAUTH.pop(state, None)
    if not pending:
        raise ValueError("Invalid or expired OAuth state session. Please try signing in again.")

    username = pending["username"]
    code_verifier = pending["code_verifier"]

    payload = {
        "code": code,
        "code_verifier": code_verifier,
        "code_challenge_method": "S256"
    }

    resp = requests.post(
        "https://openrouter.ai/api/v1/auth/keys",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=15
    )

    if resp.status_code != 200:
        raise ValueError(f"OpenRouter token exchange failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    key = data.get("key")
    if not key:
        raise ValueError(f"OpenRouter response did not contain key: {resp.text}")

    # Store encrypted in provider_registry
    res = provider_registry.connect(username, "openrouter", key, validate=False)
    return {
        "status": "connected",
        "provider": "openrouter",
        "source": "oauth",
        "key_hint": key[:8] + "..." + key[-4:] if len(key) > 12 else key[:4] + "..."
    }


# ============================================================================
# 2. ANTHROPIC CLAUDE CODE OAUTH IMPORT & INTERACTIVE AUTH
# ============================================================================

def detect_claude_code_session() -> Optional[Dict[str, Any]]:
    """
    Detects if an active Claude Code OAuth session exists on this machine.
    """
    home = os.path.expanduser("~")
    cred_path = os.path.join(home, ".claude", ".credentials.json")
    if not os.path.exists(cred_path):
        return None

    try:
        with open(cred_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        oauth = data.get("claudeAiOauth")
        if not oauth or not oauth.get("accessToken"):
            return None

        expires_at = oauth.get("expiresAt")
        # Check if expired (millisecond timestamp)
        now_ms = time.time() * 1000
        is_expired = bool(expires_at and expires_at < now_ms)

        return {
            "found": True,
            "expired": is_expired,
            "subscription_type": oauth.get("subscriptionType", "claude_user"),
            "rate_limit_tier": oauth.get("rateLimitTier", "default"),
            "expires_at": expires_at
        }
    except Exception as e:
        return None


def import_claude_code_session(username: str) -> Dict[str, Any]:
    """
    Imports the active Claude Code OAuth accessToken directly into Q's encrypted vault.
    """
    home = os.path.expanduser("~")
    cred_path = os.path.join(home, ".claude", ".credentials.json")
    if not os.path.exists(cred_path):
        raise FileNotFoundError("Claude Code credentials not found at ~/.claude/.credentials.json")

    with open(cred_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    oauth = data.get("claudeAiOauth")
    if not oauth or not oauth.get("accessToken"):
        raise ValueError("No active Claude Code OAuth session found in credentials file.")

    access_token = oauth["accessToken"]

    # Verify token with Anthropic models endpoint
    resp = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-version": "2023-06-01"
        },
        timeout=10
    )

    if resp.status_code != 200:
        raise ValueError(f"Claude Code token validation failed ({resp.status_code}): {resp.text}")

    # Store encrypted in provider_credentials
    provider_registry.connect(username, "anthropic", access_token, validate=False)

    return {
        "status": "connected",
        "provider": "anthropic",
        "source": "claude_code_oauth",
        "subscription": oauth.get("subscriptionType", "active"),
        "key_hint": access_token[:8] + "..." + access_token[-4:]
    }


# ============================================================================
# 3. GOOGLE / ANTIGRAVITY OAUTH DETECTION & IMPORT
# ============================================================================

def detect_google_session() -> Optional[Dict[str, Any]]:
    """
    Detects if an active Google/Antigravity OAuth session exists on this machine.
    """
    home = os.path.expanduser("~")
    # Check Antigravity / JetSki standalone token and Google accounts
    accounts_path = os.path.join(home, ".gemini", "google_accounts.json")
    account_email = None
    if os.path.exists(accounts_path):
        try:
            with open(accounts_path, "r", encoding="utf-8") as f:
                acc_data = json.load(f)
                active = acc_data.get("active")
                if isinstance(active, dict) and active.get("email"):
                    account_email = active.get("email")
                elif isinstance(active, str) and "@" in active:
                    account_email = active
                elif acc_data.get("old") and len(acc_data["old"]) > 0:
                    account_email = acc_data["old"][0]
        except Exception:
            pass

    # Check ~/.gemini/.env for GEMINI_API_KEY
    has_local_key = False
    env_path = os.path.join(home, ".gemini", ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("GEMINI_API_KEY=") or line.strip().startswith("GOOGLE_API_KEY="):
                        has_local_key = True
                        break
        except Exception:
            pass

    token_path = os.path.join(home, ".gemini", "jetski-standalone-oauth-token")
    if os.path.exists(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                tok = d.get("token", {})
                refresh_token = tok.get("refresh_token")
                expiry = tok.get("expiry")
                return {
                    "found": True,
                    "account": account_email or "Antigravity Google Account",
                    "has_refresh_token": bool(refresh_token),
                    "has_local_key": has_local_key,
                    "expiry": expiry
                }
        except Exception:
            pass

    if account_email or has_local_key:
        return {
            "found": True,
            "account": account_email or "Antigravity Google Account",
            "has_local_key": has_local_key,
            "has_refresh_token": False
        }

    return None


def import_google_session(username: str) -> Dict[str, Any]:
    """
    Imports the active Antigravity/Google Gemini session directly into Q's encrypted vault.
    Synchronously pre-validates against Google's API to enforce the failure-cascade safeguard.
    """
    home = os.path.expanduser("~")
    gemini_key = None

    # 1. Check ~/.gemini/.env
    env_path = os.path.join(home, ".gemini", ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            gemini_key = val
                            break
        except Exception:
            pass

    # 2. Check environment variables fallback
    if not gemini_key:
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    # 3. Detect account name
    account_info = detect_google_session()
    account_name = account_info.get("account") if account_info else "Antigravity Google Session"

    if not gemini_key:
        raise ValueError(f"No active Gemini key or credentials found for {account_name}.")

    # 4. Mandatory Pre-Validation Safeguard against Google's live models endpoint
    resp = requests.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}",
        headers={"Content-Type": "application/json"},
        timeout=10
    )

    if resp.status_code != 200:
        raise ValueError(f"Google credentials validation failed ({resp.status_code}): {resp.text[:200]}")

    # 5. Store encrypted in provider_credentials
    provider_registry.connect(username, "google", gemini_key, validate=False)

    return {
        "status": "connected",
        "provider": "google",
        "source": "antigravity_session",
        "account": account_name,
        "key_hint": gemini_key[:6] + "..." + gemini_key[-4:] if len(gemini_key) > 10 else "AIza..."
    }



def get_oauth_status_overview(username: str) -> Dict[str, Any]:
    """
    Returns detection and connection status across all supported OAuth providers.
    """
    claude_info = detect_claude_code_session()
    google_info = detect_google_session()
    provider_status = provider_registry.list_status(username)

    connected_map = {p["id"]: p for p in provider_status.get("providers", [])}

    return {
        "providers": {
            "openrouter": {
                "id": "openrouter",
                "label": "OpenRouter",
                "connected": connected_map.get("openrouter", {}).get("connected", False),
                "source": connected_map.get("openrouter", {}).get("source"),
                "hint": connected_map.get("openrouter", {}).get("hint"),
                "oauth_supported": True,
                "flow_type": "pkce_browser"
            },
            "anthropic": {
                "id": "anthropic",
                "label": "Anthropic",
                "connected": connected_map.get("anthropic", {}).get("connected", False),
                "source": connected_map.get("anthropic", {}).get("source"),
                "hint": connected_map.get("anthropic", {}).get("hint"),
                "oauth_supported": True,
                "detected_session": claude_info,
                "flow_type": "claude_code_import"
            },
            "google": {
                "id": "google",
                "label": "Google AI Studio / Gemini",
                "connected": connected_map.get("google", {}).get("connected", False),
                "source": connected_map.get("google", {}).get("source"),
                "hint": connected_map.get("google", {}).get("hint"),
                "oauth_supported": True,
                "detected_session": google_info,
                "flow_type": "antigravity_session_import"
            }
        }
    }
