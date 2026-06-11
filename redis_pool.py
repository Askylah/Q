import time
import os
import sys
import json
import logging

import redis_client

logger = logging.getLogger("redis_pool")

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
            "proxy": proxy_url or ""
        }
        try:
            self.redis.hset(redis_key, mapping=data)
            logger.info(f"[COMPUTE_POOL] Registered {provider} key '{key_id}' with proxy: '{proxy_url or 'None'}'")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Failed to add key: {e}")
            return False

    def checkout_key(self, provider: str) -> tuple[str, str, str]:
        """
        Atomically finds and checks out a healthy key for the requested provider.
        Returns: (key_id, api_key_value, proxy_url) or (None, None, None)
        """
        if not self.is_active():
            return None, None, None

        try:
            # Get all keys for this provider
            pattern = f"q:pool:key:{provider}:*"
            keys = self.redis.keys(pattern)
            now = time.time()

            for k in keys:
                # k is bytes because decode_responses=False is set in redis_client.py
                k_str = k.decode('utf-8')
                data = self.redis.hgetall(k_str)
                
                # Decode the hash fields
                fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}
                
                status = fields.get("status", "HEALTHY")
                cooldown_until = float(fields.get("cooldown_until", 0))
                
                # Check for cooldown expiration
                if status == "COOLDOWN" and now > cooldown_until:
                    status = "HEALTHY"
                    self.redis.hset(k_str, "status", "HEALTHY")
                    self.redis.hset(k_str, "cooldown_until", "0")

                if status == "HEALTHY":
                    key_id = k_str.split(":")[-1]
                    key_value = fields.get("value", "")
                    proxy = fields.get("proxy", "")
                    return key_id, key_value, proxy
                    
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
                logger.error(f"[COMPUTE_POOL] Key '{key_id}' marked as BURNED/BANNED.")
            else:
                self.redis.hset(redis_key, "status", "HEALTHY")
                self.redis.hset(redis_key, "cooldown_until", "0")
            return True
        except Exception as e:
            logger.error(f"[COMPUTE_POOL ERROR] Release failed for key '{key_id}': {e}")
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
                    "proxy": proxy or "None"
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
