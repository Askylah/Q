import time
import os
import sys
import json
import logging

import redis_client

logger = logging.getLogger("redis_pool")

# ── Auth-failure policy ───────────────────────────────────────────────────
# FIX(false-burn): a 401/403 used to mark a key BURNED on the spot, and BURNED
# was TERMINAL -- checkout_key only ever revived COOLDOWN, nothing anywhere set
# a burned key back to HEALTHY, and the HTTP API offered add/delete/list but no
# reset. Deleting and re-adding the key was the only recovery. Worse, the caller
# retries three times and checks out the NEXT healthy key each time, so a single
# request could permanently destroy three keys.
#
# Most 403s say nothing about the credential: API-not-enabled, a referrer/IP
# restriction, a region block, a model the project cannot reach, or a
# moderation refusal on the prompt -- and a blocked proxy exit IP produces 403
# from every provider at once, which is the worst case because it burns the
# whole pool while the bad proxy survives. Only the provider explicitly saying
# "this key is not valid" is evidence about the key.
AUTH_COOLDOWN_SECS = 900        # 15 min -- long enough to outlast a blip
BURN_AFTER_STRIKES = 3          # consecutive unexplained 401s before a burn
BURN_RETRY_SECS = 86400         # a burn is a hypothesis; re-probe it after 24h

# Substrings that mean the CREDENTIAL is bad, as opposed to the request, the
# project, the region or the exit IP. Matched case-insensitively against the
# response body.
HARD_KEY_ERRORS = (
    "api_key_invalid",
    "api key not valid",
    "api_key_expired",
    "api key expired",
    "invalid_api_key",
    "invalid api key",
    "incorrect api key provided",
    "invalid x-api-key",
    "no auth credentials found",
    "invalid authentication",
    "authentication_error",
)


def classify_auth_failure(status_code: int, body: str = "") -> str:
    """
    Decide what a 401/403 is actually evidence of. Pure, so it can be tested
    without Redis.

      'BURN'   -- the provider named the credential itself
      'STRIKE' -- an unexplained 401; burn only if it keeps happening
      'COOL'   -- a 403 about anything else, or an unexpected code
    """
    haystack = (body or "").lower()
    for needle in HARD_KEY_ERRORS:
        if needle in haystack:
            return "BURN"
    if status_code == 401:
        return "STRIKE"
    return "COOL"


def _as_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


class RedisKeyPool:
    """
    Manages API keys and proxies dynamically in Redis to prevent rate limits
    and avoid correlation shadowbans.
    """
    def __init__(self):
        self.redis = redis_client.get_connection()

    def is_active(self) -> bool:
        return redis_client.is_active()

    def add_key(self, provider: str, key_val: str, key_id: str = None, proxy_url: str = "") -> bool:
        """Adds or updates an API key in the Redis pool."""
        if not self.is_active():
            return False
        if not key_id:
            # Generate a simple short hash of the key to use as identifier
            import hashlib
            key_id = hashlib.sha256(key_val.encode()).hexdigest()[:8]
        
        redis_key = f"q:pool:key:{provider}:{key_id}"
        data = {
            "value": key_val,
            "provider": provider,
            "status": "HEALTHY",
            "cooldown_until": "0",
            "proxy": proxy_url or "",
            "failures": "0",
            "burned_at": "0",
            "last_error": ""
        }
        try:
            self.redis.hset(redis_key, mapping=data)
            logger.info(f"[COMPUTE_POOL] Registered {provider} key '{key_id}' with proxy: '{proxy_url or 'None'}'")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Failed to add key: {e}")
            return False

    def _effective_status(self, k_str: str, fields: dict, now: float) -> str:
        """
        The status a key really has right now, applying cooldown expiry and burn
        re-probing as side effects. Returns HEALTHY, COOLDOWN or BURNED.

        Split out of checkout_key so the selection loop stays readable, and so a
        malformed cooldown_until degrades to "expired" instead of raising a
        ValueError that would take the whole checkout down.
        """
        status = fields.get("status", "HEALTHY")

        if status == "COOLDOWN" and now > _as_float(fields.get("cooldown_until")):
            status = "HEALTHY"
            self.redis.hset(k_str, "status", "HEALTHY")
            self.redis.hset(k_str, "cooldown_until", "0")

        # FIX(false-burn): a burn is a hypothesis, not a verdict. Re-probe it
        # instead of losing the key forever. Keys burned before that change carry
        # no burned_at, read 0, and revive on first checkout -- which is the
        # point: they were burned by the old one-strike rule and were probably
        # never bad.
        if status == "BURNED":
            burned_at = _as_float(fields.get("burned_at"))
            if now - burned_at > BURN_RETRY_SECS:
                status = "HEALTHY"
                self.redis.hset(k_str, "status", "HEALTHY")
                self.redis.hset(k_str, "failures", "0")
                self.redis.hset(k_str, "burned_at", "0")
                _kid = k_str.split(":")[-1]
                if burned_at <= 0:
                    logger.warning(
                        f"[COMPUTE_POOL] Key '{_kid}' was burned by the old "
                        f"one-strike rule and has no recorded time. Restoring it "
                        f"-- it was probably never bad.")
                else:
                    logger.warning(
                        f"[COMPUTE_POOL] Key '{_kid}' was burned "
                        f"{(now - burned_at) / 3600:.1f}h ago; re-probing it rather "
                        f"than leaving it dead.")

        return status

    def checkout_key(self, provider: str) -> tuple[str, str, str]:
        """
        Check out the next healthy key for this provider, round-robin.

        FIX(no-rotation): this used to return the FIRST healthy key in
        redis.keys() order, so one credential absorbed every single request until
        it cooled or burned while the rest of the pool sat idle. That defeats the
        entire point of a multi-key pool -- spreading work across several
        providers' free tiers -- and it concentrated every rate limit and every
        auth failure onto one key, which in turn made the old one-strike burn
        rule far more destructive than it looked. The proxy half of this file has
        done round-robin since it was written (checkout_proxy,
        q:pool:proxy_index); keys never did.

        Two differences from checkout_proxy, both deliberate:
          - the candidate list is SORTED, so the rotation order is stable rather
            than following redis.keys()'s arbitrary ordering;
          - the cursor indexes ALL keys for the provider, not just the healthy
            ones, so a key going into cooldown does not silently reshuffle
            everyone else's position.

        Returns: (key_id, api_key_value, proxy_url) or (None, None, None)
        """
        if not self.is_active():
            return None, None, None

        try:
            # k is bytes because decode_responses=False is set in redis_client.py
            candidates = sorted(k.decode('utf-8')
                                for k in self.redis.keys(f"q:pool:key:{provider}:*"))
            if not candidates:
                return None, None, None

            now = time.time()
            cursor_key = f"q:pool:key_cursor:{provider}"
            raw_cursor = self.redis.get(cursor_key)
            start = int(_as_float(raw_cursor.decode('utf-8') if raw_cursor else 0))

            for offset in range(len(candidates)):
                pos = (start + offset) % len(candidates)
                k_str = candidates[pos]
                data = self.redis.hgetall(k_str)
                fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}

                if self._effective_status(k_str, fields, now) != "HEALTHY":
                    continue

                # Advance PAST the key being handed out, so the next request
                # starts on the following one even when this one succeeds.
                self.redis.set(cursor_key, str(pos + 1).encode('utf-8'))
                return (k_str.split(":")[-1],
                        fields.get("value", ""),
                        fields.get("proxy", ""))

            return None, None, None
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Checkout failed: {e}")
            return None, None, None
    def release_key(self, provider: str, key_id: str, status: str, cooldown_duration: int = 0) -> bool:
        """
        Releases a key back to the pool, updating its health status.
        status: HEALTHY, COOLDOWN, or BURNED
        """
        if not self.is_active():
            return False

        redis_key = f"q:pool:key:{provider}:{key_id}"
        now = time.time()
        
        try:
            if status == "COOLDOWN":
                cooldown_until = now + cooldown_duration
                self.redis.hset(redis_key, "status", "COOLDOWN")
                self.redis.hset(redis_key, "cooldown_until", str(cooldown_until))
                logger.warning(f"[COMPUTE_POOL] Key '{key_id}' cooled down until {time.strftime('%H:%M:%S', time.localtime(cooldown_until))}")
            elif status == "BURNED":
                self.redis.hset(redis_key, "status", "BURNED")
                self.redis.hset(redis_key, "burned_at", str(now))
                logger.error(
                    f"[COMPUTE_POOL] Key '{key_id}' marked as BURNED/BANNED. It will be "
                    f"re-probed in {BURN_RETRY_SECS // 3600}h, or immediately via "
                    f"unburn_key().")
            else:
                # A success clears the strike history -- consecutive is the only
                # count that means anything here.
                self.redis.hset(redis_key, "status", "HEALTHY")
                self.redis.hset(redis_key, "cooldown_until", "0")
                self.redis.hset(redis_key, "failures", "0")
                self.redis.hset(redis_key, "burned_at", "0")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Release failed for key '{key_id}': {e}")
            return False

    def note_auth_failure(self, provider: str, key_id: str, status_code: int,
                          body: str = "", allow_burn: bool = True) -> str:
        """
        Record a 401/403 against one key and return what was done to it:
        "BURNED", "COOLDOWN", or "" if the pool is inactive.

        This replaces the old unconditional burn. See classify_auth_failure for
        why a 403 is almost never evidence about the credential. `allow_burn`
        lets a caller cap how much damage a single request can do -- the retry
        loop passes False once it has already burned one key.
        """
        if not self.is_active():
            return ""

        redis_key = f"q:pool:key:{provider}:{key_id}"
        verdict = classify_auth_failure(status_code, body)
        excerpt = (body or "")[:200]

        try:
            self.redis.hset(redis_key, "last_error", f"{status_code}: {excerpt}")

            if verdict == "COOL":
                self.redis.hincrby(redis_key, "failures", 1)
                self.release_key(provider, key_id, "COOLDOWN",
                                 cooldown_duration=AUTH_COOLDOWN_SECS)
                logger.warning(
                    f"[COMPUTE_POOL] Key '{key_id}' got a {status_code} that does not "
                    f"name the credential; cooling it for {AUTH_COOLDOWN_SECS}s instead "
                    f"of burning it. Body: {excerpt}")
                return "COOLDOWN"

            if verdict == "BURN":
                if not allow_burn:
                    self.release_key(provider, key_id, "COOLDOWN",
                                     cooldown_duration=AUTH_COOLDOWN_SECS)
                    logger.warning(
                        f"[COMPUTE_POOL] Key '{key_id}' looks invalid, but this request "
                        f"has already burned a key; cooling instead so one bad call "
                        f"cannot empty the pool.")
                    return "COOLDOWN"
                self.release_key(provider, key_id, "BURNED")
                return "BURNED"

            # STRIKE: an unexplained 401. Burn only on repetition.
            failures = self.redis.hincrby(redis_key, "failures", 1)
            if failures >= BURN_AFTER_STRIKES and allow_burn:
                logger.error(
                    f"[COMPUTE_POOL] Key '{key_id}' has failed auth {failures} times in a "
                    f"row without the provider ever naming the credential. Burning it.")
                self.release_key(provider, key_id, "BURNED")
                return "BURNED"

            self.release_key(provider, key_id, "COOLDOWN",
                             cooldown_duration=AUTH_COOLDOWN_SECS)
            logger.warning(
                f"[COMPUTE_POOL] Key '{key_id}' strike {failures}/{BURN_AFTER_STRIKES} "
                f"({status_code}); cooling for {AUTH_COOLDOWN_SECS}s. A success clears "
                f"the count.")
            return "COOLDOWN"
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] note_auth_failure failed for '{key_id}': {e}")
            return ""

    def unburn_key(self, provider: str, key_id: str) -> bool:
        """Return a key to HEALTHY and clear its strike history."""
        if not self.is_active():
            return False
        redis_key = f"q:pool:key:{provider}:{key_id}"
        try:
            if not self.redis.exists(redis_key):
                return False
            self.redis.hset(redis_key, "status", "HEALTHY")
            self.redis.hset(redis_key, "cooldown_until", "0")
            self.redis.hset(redis_key, "failures", "0")
            self.redis.hset(redis_key, "burned_at", "0")
            self.redis.hset(redis_key, "last_error", "")
            logger.info(f"[COMPUTE_POOL] Key '{key_id}' restored to HEALTHY by request.")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] unburn failed for '{key_id}': {e}")
            return False

    def add_pool_proxy(self, proxy_url: str) -> bool:
        """Adds a proxy URL to the global rotation pool."""
        if not self.is_active():
            return False
        import hashlib
        p_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:8]
        redis_key = f"q:pool:proxy:{p_id}"
        data = {
            "url": proxy_url,
            "status": "HEALTHY",
            "cooldown_until": "0",
            "failures": "0"
        }
        try:
            self.redis.hset(redis_key, mapping=data)
            logger.info(f"[COMPUTE_POOL] Registered rotational proxy: '{proxy_url}'")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Failed to add proxy to pool: {e}")
            return False

    def delete_pool_proxy(self, proxy_url: str) -> bool:
        """Removes a proxy URL from the global rotation pool."""
        if not self.is_active():
            return False
        import hashlib
        p_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:8]
        redis_key = f"q:pool:proxy:{p_id}"
        try:
            self.redis.delete(redis_key)
            logger.info(f"[COMPUTE_POOL] Removed proxy from pool: '{proxy_url}'")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Failed to delete proxy: {e}")
            return False

    def checkout_proxy(self) -> str:
        """
        Retrieves a healthy proxy URL from the rotation pool (round-robin).
        Returns the proxy URL string, or None if no healthy proxies are available.
        """
        if not self.is_active():
            return None

        try:
            keys = self.redis.keys("q:pool:proxy:*")
            now = time.time()
            healthy_proxies = []

            for k in keys:
                k_str = k.decode('utf-8')
                data = self.redis.hgetall(k_str)
                fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}
                
                url = fields.get("url", "")
                status = fields.get("status", "HEALTHY")
                cooldown_until = float(fields.get("cooldown_until", 0))

                # Expire cooldown
                if status == "COOLDOWN" and now > cooldown_until:
                    status = "HEALTHY"
                    self.redis.hset(k_str, "status", "HEALTHY")
                    self.redis.hset(k_str, "cooldown_until", "0")
                    self.redis.hset(k_str, "failures", "0")

                if status == "HEALTHY" and url:
                    healthy_proxies.append(url)

            if not healthy_proxies:
                return None

            # Get rotation index to achieve round-robin
            idx = 0
            idx_bytes = self.redis.get("q:pool:proxy_index")
            if idx_bytes:
                idx = int(idx_bytes.decode('utf-8'))
            
            selected_proxy = healthy_proxies[idx % len(healthy_proxies)]
            
            # Increment index
            self.redis.set("q:pool:proxy_index", str(idx + 1).encode('utf-8'))
            return selected_proxy
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Proxy checkout failed: {e}")
            return None

    def release_proxy(self, proxy_url: str, status: str, cooldown_duration: int = 60) -> bool:
        """
        Updates the health status of a rotational proxy.
        status: HEALTHY or COOLDOWN
        """
        if not self.is_active():
            return False

        import hashlib
        p_id = hashlib.sha256(proxy_url.encode()).hexdigest()[:8]
        redis_key = f"q:pool:proxy:{p_id}"
        now = time.time()

        try:
            if status == "COOLDOWN":
                cooldown_until = now + cooldown_duration
                # Increment failures
                fail_bytes = self.redis.hget(redis_key, "failures")
                failures = int(fail_bytes.decode('utf-8')) + 1 if fail_bytes else 1
                
                self.redis.hset(redis_key, "status", "COOLDOWN")
                self.redis.hset(redis_key, "cooldown_until", str(cooldown_until))
                self.redis.hset(redis_key, "failures", str(failures))
                logger.warning(f"[COMPUTE_POOL] Proxy '{proxy_url}' cooled down for {cooldown_duration}s (failures: {failures})")
            else:
                self.redis.hset(redis_key, "status", "HEALTHY")
                self.redis.hset(redis_key, "cooldown_until", "0")
                self.redis.hset(redis_key, "failures", "0")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Release proxy failed for '{proxy_url}': {e}")
            return False

    def get_pool_status(self) -> dict:
        """Returns the current breakdown of the key pool."""
        if not self.is_active():
            return {"active": False, "reason": "Redis is not running"}

        status_report = {"active": True, "keys": {}, "proxies": []}
        try:
            # Query keys
            keys = self.redis.keys("q:pool:key:*")
            for k in keys:
                k_str = k.decode('utf-8')
                parts = k_str.split(":")
                provider = parts[3]
                key_id = parts[4]
                
                data = self.redis.hgetall(k_str)
                fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}
                
                status = fields.get("status", "HEALTHY")
                proxy = fields.get("proxy", "")
                
                if provider not in status_report["keys"]:
                    status_report["keys"][provider] = []
                
                status_report["keys"][provider].append({
                    "id": key_id,
                    "status": status,
                    "proxy": proxy or "None",
                    "failures": int(_as_float(fields.get("failures"))),
                    "burned_at": _as_float(fields.get("burned_at")),
                    "last_error": fields.get("last_error", "")
                })

            # Query proxies
            p_keys = self.redis.keys("q:pool:proxy:*")
            for pk in p_keys:
                pk_str = pk.decode('utf-8')
                data = self.redis.hgetall(pk_str)
                fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}
                status_report["proxies"].append({
                    "url": fields.get("url", ""),
                    "status": fields.get("status", "HEALTHY"),
                    "failures": int(fields.get("failures", 0))
                })

            return status_report
        except Exception as e:
            return {"active": False, "error": str(e)}

    def seed_keys_from_env(self) -> int:
        """Seeds the key pool using the environment keys as a fallback if the pool is empty."""
        if not self.is_active():
            return 0
            
        env_keys = {
            "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
            "google": os.getenv("GOOGLE_API_KEY", ""),
        }
        
        seeded = 0
        try:
            existing = self.redis.keys("q:pool:key:*")
            if len(existing) == 0:
                for provider, val in env_keys.items():
                    if val:
                        proxy_var = f"{provider.upper()}_PROXY"
                        proxy_url = os.getenv(proxy_var, "")
                        if self.add_key(provider, val, key_id="default", proxy_url=proxy_url):
                            seeded += 1

            # Seed rotational proxies from env
            proxy_list = os.getenv("ROTATIONAL_PROXIES", "")
            if proxy_list:
                existing_proxies = self.redis.keys("q:pool:proxy:*")
                if len(existing_proxies) == 0:
                    for prx in proxy_list.split(","):
                        if prx.strip():
                            self.add_pool_proxy(prx.strip())

            return seeded
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Seeding failed: {e}")
            return 0

# Global pool instance
pool = RedisKeyPool()
