import os
import logging

logger = logging.getLogger("redis_client")

_REDIS_AVAILABLE = False
_redis_conn = None

try:
    import redis
    _REDIS_AVAILABLE = True
except ImportError:
    logger.warning("[REDIS] 'redis' library not installed. Falling back to local in-memory/SQLite cache.")

import subprocess
import shutil
import time

def _auto_start_docker_redis() -> bool:
    """Attempts to automatically spin up or start a local Redis container named 'q-redis' if Docker is running."""
    if not shutil.which("docker"):
        return False
    try:
        # Check if Docker daemon is running
        res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=3)
        if res.returncode != 0:
            return False
            
        # Check if container 'q-redis' exists
        res = subprocess.run(["docker", "ps", "-a", "--filter", "name=q-redis", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=3)
        exists = "q-redis" in res.stdout.strip().split("\n")
        
        if exists:
            print("[REDIS] Found existing Docker container 'q-redis'. Starting container...")
            subprocess.run(["docker", "start", "q-redis"], capture_output=True, timeout=5)
            return True
        else:
            print("[REDIS] Container 'q-redis' not found. Pulling 'redis:alpine' and provisioning container in background (this may take a moment on the first run)...")
            subprocess.run(["docker", "run", "-d", "--name", "q-redis", "-p", "6379:6379", "redis:alpine"], capture_output=True, timeout=90)
            return True
    except Exception as e:
        logger.warning(f"[REDIS] Docker auto-start check failed: {e}")
        return False

if _REDIS_AVAILABLE:
    # Read Redis URL from environment variables, defaulting to local standard port
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        # Establish a client connection with short timeouts so we don't hang the API
        _redis_conn = redis.Redis.from_url(
            REDIS_URL, 
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
            decode_responses=False # Keep binary/bytes representation for pickle/numpy objects
        )
        # Verify connection viability
        _redis_conn.ping()
        print(f"[REDIS] Successfully connected to instance: {REDIS_URL}")
    except Exception as e:
        if "localhost" in REDIS_URL or "127.0.0.1" in REDIS_URL:
            print("[REDIS] Connection failed. Checking if Docker is running to auto-start Redis...")
            if _auto_start_docker_redis():
                time.sleep(2.0) # Give Redis a moment to bind to 6379
                try:
                    _redis_conn.ping()
                    print(f"[REDIS] Successfully connected to auto-started Docker Redis instance: {REDIS_URL}")
                except Exception as e2:
                    logger.warning(f"[REDIS] Auto-started Redis failed connection check: {e2}. Disabling Redis caching.")
                    _redis_conn = None
            else:
                logger.warning(f"[REDIS] Docker not running or unavailable. Disabling Redis caching.")
                _redis_conn = None
        else:
            logger.warning(f"[REDIS] Connection failed to {REDIS_URL}: {e}. Disabling Redis caching.")
            _redis_conn = None

def is_active() -> bool:
    """Checks if Redis is installed, configured, and responsive."""
    return _redis_conn is not None

def get(key: str) -> bytes:
    """Safely retrieves a value from Redis."""
    if not is_active():
        return None
    try:
        return _redis_conn.get(key)
    except Exception as e:
        logger.error(f"[REDIS ERROR] Safe get failed for key '{key}': {e}")
        return None

def set_val(key: str, val: bytes, ex: int = None) -> bool:
    """Safely writes a value to Redis with optional expiration in seconds."""
    if not is_active():
        return False
    try:
        return bool(_redis_conn.set(key, val, ex=ex))
    except Exception as e:
        logger.error(f"[REDIS ERROR] Safe set failed for key '{key}': {e}")
        return False

def delete(key: str) -> bool:
    """Safely deletes a key from Redis."""
    if not is_active():
        return False
    try:
        return bool(_redis_conn.delete(key))
    except Exception as e:
        logger.error(f"[REDIS ERROR] Safe delete failed for key '{key}': {e}")
        return False

def get_connection():
    """Returns the raw connection object for pipelines or advanced features."""
    return _redis_conn
