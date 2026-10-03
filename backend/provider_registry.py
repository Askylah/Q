"""
Provider registry, encrypted credential store, and the single API-key resolver.

Why this exists
---------------
Credentials used to be merged in three places in main.py (env, then the request
body) and rebuilt from env only in stream_worker.py, so the background daemon
could never see a key connected through the UI. This module is the one source of
truth for both.

Design rules (each one is a scar from the failure cascade run before building it)
---------------------------------------------------------------------------------
* Secrets are Fernet-encrypted at rest. The master key comes from
  PERSONA_MASTER_KEY (headless / service use) or the OS keyring.
* FAIL CLOSED. The master key is generated ONLY when the credential table holds
  no ciphertext. If rows exist and the key is missing, the store is LOCKED and
  raises ProviderStoreLocked -- it never regenerates, because a regenerated key
  turns every stored credential into InvalidToken at once.
* A locked store or an undecryptable row degrades to "needs_reauth" and is
  skipped by the resolver. Callers (the daemon) check has_usable_key() and stand
  down rather than burning retries against upstream APIs with empty keys.
* Key material is never returned by list_status().
* Auth methods are declared per provider ("api_key" today). An "oauth" entry can
  be added to a provider's auth_methods later without touching the store.
"""
import logging
import os
import sqlite3
import threading
import time

import requests
from cryptography.fernet import Fernet, InvalidToken

from app_paths import DB_PATH

log = logging.getLogger("providers")

KEYRING_SERVICE = "PersonaApp"
KEYRING_ACCOUNT = "provider-master-key"
MASTER_KEY_ENV = "PERSONA_MASTER_KEY"

_RESOLVE_TTL = 5.0          # seconds a decrypted per-user key set is reused
_LOCK_LOG_INTERVAL = 60.0   # rate limit for the "store locked" warning


class ProviderStoreLocked(RuntimeError):
    """Stored credentials cannot be decrypted (master key missing/unavailable)."""


class ProviderRejected(ValueError):
    """A connect attempt was refused (unknown provider, malformed or invalid key)."""


# validate: how to cheaply prove a key works. None = no endpoint we are certain
# of, so the key is saved as "unverified" instead of guessed at.
PROVIDERS = {
    "openrouter": dict(
        label="OpenRouter", env="OPENROUTER_API_KEY", hint="sk-or-...",
        auth_methods=("api_key",),
        validate=dict(url="https://openrouter.ai/api/v1/auth/key", auth="bearer")),
    "anthropic": dict(
        label="Anthropic", env="ANTHROPIC_API_KEY", hint="sk-ant-...",
        auth_methods=("api_key",),
        validate=dict(url="https://api.anthropic.com/v1/models", auth="x-api-key")),
    "google": dict(
        label="Google AI Studio", env="GOOGLE_API_KEY", hint="AIza...",
        auth_methods=("api_key",),
        validate=dict(url="https://generativelanguage.googleapis.com/v1beta/models",
                      auth="x-goog-api-key")),
    "openai": dict(
        label="OpenAI", env="OPENAI_API_KEY", hint="sk-...",
        auth_methods=("api_key",),
        validate=dict(url="https://api.openai.com/v1/models", auth="bearer")),
    "opencodezen": dict(
        label="OpenCode Zen", env=None, hint="",
        auth_methods=("api_key",), validate=None),
    "xai": dict(
        label="xAI", env="XAI_API_KEY", hint="xai-...",
        auth_methods=("api_key",), validate=None),
    "featherless": dict(
        label="Featherless", env="FEATHERLESS_API_KEY", hint="",
        auth_methods=("api_key",), validate=None),
    "perplexity": dict(
        label="Perplexity", env="PERPLEXITY_API_KEY", hint="pplx-...",
        auth_methods=("api_key",), validate=None),
    # Key slot used with a custom base URL (the base URL itself stays a
    # per-request UI setting).
    "universal": dict(
        label="Custom endpoint key", env=None, hint="",
        auth_methods=("api_key",), validate=None),
}

_fernet_cache = None
_fernet_lock = threading.Lock()
_resolve_cache = {}          # username -> (timestamp, {provider: secret})
_resolve_lock = threading.Lock()
_last_lock_log = 0.0


# --- keyring seam (monkeypatched in tests) -----------------------------------
def _keyring_get():
    import keyring
    return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)


def _keyring_set(value):
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)


# --- storage -----------------------------------------------------------------
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS provider_credentials (
               username TEXT NOT NULL,
               provider TEXT NOT NULL,
               secret_enc BLOB NOT NULL,
               status TEXT NOT NULL DEFAULT 'unverified',
               last_verified REAL,
               updated_at REAL NOT NULL,
               PRIMARY KEY (username, provider)
           )""")
    return conn


def _reset_for_tests():
    global _fernet_cache, _last_lock_log
    with _fernet_lock:
        _fernet_cache = None
    with _resolve_lock:
        _resolve_cache.clear()
    _last_lock_log = 0.0


def _get_fernet(conn, allow_create):
    """Return the Fernet for the master key, or raise ProviderStoreLocked."""
    global _fernet_cache
    with _fernet_lock:
        if _fernet_cache is not None:
            return _fernet_cache

        raw = os.getenv(MASTER_KEY_ENV, "").strip()
        if not raw:
            try:
                raw = (_keyring_get() or "").strip()
            except Exception as e:
                raise ProviderStoreLocked(f"OS keyring unavailable: {e}") from e

        if not raw:
            if not allow_create:
                raise ProviderStoreLocked("master key not found")
            # Serialise bootstrap across processes: take the write lock, then
            # decide. Generation is legal only while NO ciphertext exists.
            conn.execute("BEGIN IMMEDIATE")
            try:
                n = conn.execute(
                    "SELECT COUNT(*) FROM provider_credentials").fetchone()[0]
                if n:
                    raise ProviderStoreLocked(
                        f"master key missing but {n} stored credential(s) exist; "
                        "refusing to generate a new key")
                try:  # another process may have created it while we waited
                    raw = (_keyring_get() or "").strip()
                except Exception as e:
                    raise ProviderStoreLocked(f"OS keyring unavailable: {e}") from e
                if not raw:
                    raw = Fernet.generate_key().decode()
                    try:
                        _keyring_set(raw)
                    except Exception as e:
                        raise ProviderStoreLocked(
                            f"cannot persist master key to OS keyring: {e}") from e
            finally:
                conn.rollback()

        try:
            _fernet_cache = Fernet(raw.encode())
        except Exception as e:
            raise ProviderStoreLocked(f"master key is malformed: {e}") from e
        return _fernet_cache


def _invalidate(username):
    with _resolve_lock:
        _resolve_cache.pop(username, None)


# --- validation --------------------------------------------------------------
def _validate(provider, secret):
    """('valid'|'invalid'|'unverified', detail). Only a definitive 401/403 from
    the provider counts as invalid; network trouble must not block connecting."""
    spec = PROVIDERS[provider].get("validate")
    if not spec:
        return "unverified", "no validation endpoint configured"
    headers = {}
    if spec["auth"] == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif spec["auth"] == "x-api-key":
        headers["x-api-key"] = secret
        headers["anthropic-version"] = "2023-06-01"
    elif spec["auth"] == "x-goog-api-key":
        headers["x-goog-api-key"] = secret
    try:
        res = requests.get(spec["url"], headers=headers, timeout=8)
    except Exception as e:
        return "unverified", f"could not reach provider: {e.__class__.__name__}"
    if res.status_code in (401, 403):
        return "invalid", f"provider rejected the key ({res.status_code})"
    if 200 <= res.status_code < 300:
        return "valid", "ok"
    return "unverified", f"unexpected status {res.status_code}"


# --- public API --------------------------------------------------------------
def connect(username, provider, secret, validate=True):
    """Store a provider credential. Returns a status dict (no key material)."""
    if provider not in PROVIDERS:
        raise ProviderRejected(f"unknown provider '{provider}'")
    secret = (secret or "").strip()
    if not secret:
        raise ProviderRejected("empty key")
    # Keys end up in HTTP headers: control characters and non-ASCII crash the
    # transport (and newlines are header injection).
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in secret):
        raise ProviderRejected("key contains whitespace or non-ASCII characters")

    status, detail = ("unverified", "validation skipped")
    if validate:
        status, detail = _validate(provider, secret)
        if status == "invalid":
            raise ProviderRejected(detail)

    conn = _db()
    try:
        fernet = _get_fernet(conn, allow_create=True)
        now = time.time()
        conn.execute(
            """INSERT INTO provider_credentials
                   (username, provider, secret_enc, status, last_verified, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(username, provider) DO UPDATE SET
                   secret_enc=excluded.secret_enc, status=excluded.status,
                   last_verified=excluded.last_verified, updated_at=excluded.updated_at""",
            (username, provider, fernet.encrypt(secret.encode()), status,
             now if status == "valid" else None, now))
        conn.commit()
    finally:
        conn.close()
    _invalidate(username)
    return {"provider": provider, "status": status, "detail": detail}


def disconnect(username, provider):
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM provider_credentials WHERE username=? AND provider=?",
            (username, provider))
        conn.commit()
        removed = cur.rowcount > 0
    finally:
        conn.close()
    _invalidate(username)
    return removed


def _mark_needs_reauth(username, provider):
    conn = _db()
    try:
        conn.execute(
            "UPDATE provider_credentials SET status='needs_reauth' "
            "WHERE username=? AND provider=?", (username, provider))
        conn.commit()
    finally:
        conn.close()


def _stored_keys(username):
    """Decrypted {provider: secret} for a user; degrades to {} when locked."""
    global _last_lock_log
    now = time.time()
    with _resolve_lock:
        hit = _resolve_cache.get(username)
        if hit and now - hit[0] < _RESOLVE_TTL:
            return hit[1]

    conn = _db()
    try:
        rows = conn.execute(
            "SELECT provider, secret_enc, status FROM provider_credentials "
            "WHERE username=?", (username,)).fetchall()
        keys = {}
        if rows:
            try:
                fernet = _get_fernet(conn, allow_create=False)
            except ProviderStoreLocked as e:
                if now - _last_lock_log > _LOCK_LOG_INTERVAL:
                    _last_lock_log = now
                    log.warning("[PROVIDERS] store locked, stored credentials "
                                "unavailable: %s", e)
                return {}   # not cached: recover as soon as the key returns
            for provider, blob, status in rows:
                try:
                    keys[provider] = fernet.decrypt(bytes(blob)).decode()
                except InvalidToken:
                    log.warning("[PROVIDERS] %s/%s cannot be decrypted with the "
                                "current master key; needs re-auth", username, provider)
                    if status != "needs_reauth":
                        _mark_needs_reauth(username, provider)
    finally:
        conn.close()

    with _resolve_lock:
        _resolve_cache[username] = (now, keys)
    return keys


def resolve_api_keys(username=None, request_keys=None):
    """The single merge: env fallback < stored credentials < per-request override.

    Env sourcing is exactly the set main.py always read, so behaviour for
    existing deployments is unchanged until a credential is connected.
    """
    keys = {pid: (os.getenv(spec["env"], "") if spec["env"] else "")
            for pid, spec in PROVIDERS.items() if spec["env"]}
    if username:
        for pid, secret in _stored_keys(username).items():
            if secret:
                keys[pid] = secret
    for pid, secret in (request_keys or {}).items():
        if secret:
            keys[pid] = secret
    return keys


def has_usable_key(keys):
    """True if any provider slot holds a non-empty key. The daemon uses this to
    stand down instead of retrying calls that cannot authenticate."""
    return any(str(v).strip() for v in (keys or {}).values())


def list_status(username):
    """Per-provider connection status for the UI. Never contains key material."""
    conn = _db()
    try:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT provider, status, last_verified FROM provider_credentials "
            "WHERE username=?", (username,)).fetchall()}
        locked = False
        if rows:
            try:
                _get_fernet(conn, allow_create=False)
            except ProviderStoreLocked:
                locked = True
    finally:
        conn.close()

    out = []
    for pid, spec in PROVIDERS.items():
        env_set = bool(spec["env"] and os.getenv(spec["env"], "").strip())
        stored = rows.get(pid)
        if stored:
            status = "needs_reauth" if locked else stored[0]
            source = "stored"
        elif env_set:
            status, source = "valid", "env"
        else:
            status, source = "not_connected", None
        out.append({
            "id": pid,
            "label": spec["label"],
            "hint": spec["hint"],
            "auth_methods": list(spec["auth_methods"]),
            "connected": source is not None and status != "needs_reauth",
            "source": source,
            "status": status,
            "last_verified": stored[1] if stored else None,
        })
    return {"locked": locked, "providers": out}
