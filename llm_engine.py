import json
import asyncio
import threading
import os
import requests
from datetime import datetime
os.environ["GCP_METADATA_TIMEOUT"] = "1"
import database as db
from governance_manager import governance as gman
from mode_engine import detect_mode
from typing import Union, Optional
import sys
from plugin_manager import get_plugin_manager, HookType
import firewall
import output_validator
from data_sanitizer import DataSanitizer

_MCP_TOOLS_CACHE = None
_MCP_TOOLS_CACHE_TIME = 0.0
_VERTEX_TOKEN_CACHE = None
_VERTEX_TOKEN_CACHE_TIME = 0.0
# Initialize and Load Plugins
PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
manager = get_plugin_manager()
manager.load_plugins(PLUGIN_DIR)

# Deep Memory (optional pluggable module)
try:
    from memory_engine import DeepMemory
    _DEEP_MEMORY_AVAILABLE = True
except ImportError:
    _DEEP_MEMORY_AVAILABLE = False

def execute_garage_tool_safe(script_path: str, args: dict) -> str:
    """
    Executes a dynamic garage tool script transiently in an isolated namespace,
    automatically scanning and pre-installing dependencies if missing.
    """
    import os
    import sys
    import ast
    import subprocess
    import uuid
    import importlib.util

    # 1. Parse AST to scan for package imports
    required_packages = []
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=script_path)
            
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    required_packages.append(name.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    required_packages.append(node.module.split('.')[0])
    except Exception as e:
        return f"⚠️ [GARAGE ERROR] Failed to parse AST dependencies: {e}"

    # 2. Check and install missing non-standard packages
    garage_dir = os.path.dirname(script_path)
    lib_path = os.path.join(garage_dir, "lib")
    os.makedirs(lib_path, exist_ok=True)
    
    std_libs = {
        "os", "sys", "re", "json", "math", "time", "datetime", "hashlib", "hmac", 
        "random", "collections", "itertools", "functools", "urllib", "http", 
        "xml", "csv", "ast", "importlib", "shutil", "tempfile", "uuid", "threading", 
        "queue", "socket", "select", "asyncio", "copy", "traceback"
    }

    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)

    for pkg in required_packages:
        if pkg in std_libs or pkg == "execute":
            continue
            
        try:
            importlib.import_module(pkg)
        except ImportError:
            print(f"[GARAGE PROTOCOL] Package '{pkg}' is missing. Triggering sandboxed pip install...")
            try:
                subprocess.run(
                    ["py", "-m", "pip", "install", "--target", lib_path, pkg],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
            except Exception as e:
                print(f"[GARAGE PROTOCOL] Failed to install package '{pkg}': {e}")

    # 3. Load transient module
    try:
        unique_name = f"garage_tool_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(unique_name, script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        if not hasattr(module, "execute"):
            return "⚠️ [GARAGE ERROR] Tool script is missing the required entry point: `def execute(args):`"
            
        output = module.execute(args)
        return str(output)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return f"⚠️ [GARAGE ERROR] Runtime crash in transient tool:\n{e}\n\n{tb}"

try:
    import mcp_client
except ImportError:
    mcp_client = None

# Multi-server MCP Router (replaces single-server mcp_client for namespaced tools)
try:
    from mcp_router import get_router as _get_mcp_router
    _MCP_ROUTER_AVAILABLE = True
except ImportError:
    _get_mcp_router = None
    _MCP_ROUTER_AVAILABLE = False
    print("[LLM_ENGINE] mcp_router not available — namespaced MCP tools disabled.")

try:
    from api_parser import load_universal_schemas, execute_api
except ImportError:
    def load_universal_schemas(): return []
    def execute_api(name, args): return "Failsafe."

# Global neuromodulator coupling (optional — degrades to no-op if absent)
try:
    import dopamine_state
except ImportError:
    dopamine_state = None

# Operational world-model: provider reliability. Deliberately separate from
# dopamine_state — the router learns servers are flaky, the neuron does not
# learn the agent is incompetent.
try:
    import provider_health
except ImportError:
    provider_health = None

OR_SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
OR_SESSION.mount("http://", adapter)
OR_SESSION.mount("https://", adapter)


def get_post_prompt_anchor() -> str:
    return (
        "Stay entirely in character. The user's message is above.\n"
    )


# ─── OpenCode Zen model → endpoint routing ────────────────────────────────
# The Zen gateway serves different model families from different endpoints.
# Posting a Responses-API model (gpt-5.6-*, grok-4*) to /chat/completions
# yields an opaque 500 instead of a 4xx, so the router must pick the door
# BEFORE the request leaves the building.
def _zen_route_for(model_id: str) -> str:
    """Classify a Zen model into 'responses' | 'messages' | 'chat'."""
    m = (model_id or "").lower()
    if m.startswith(("claude", "qwen")):
        return "messages"
    if m.startswith(("gpt-5", "gpt-4", "o1", "o3", "grok-4", "muse-spark", "codex")):
        return "responses"
    return "chat"


class ResponsesToChatStream:
    """Translates OpenAI Responses-API SSE into chat-completions SSE chunks so
    the downstream stream machinery (tool interceptor, UI) stays untouched."""

    def __init__(self, raw_response):
        self._resp = raw_response

    def iter_lines(self):
        for line in self._resp.iter_lines():
            if not line:
                continue
            l = line.decode("utf-8", errors="replace")
            if not l.startswith("data: "):
                continue
            payload = l[6:].strip()
            if payload == "[DONE]":
                yield b"data: [DONE]\n\n"
                return
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            et = ev.get("type", "")
            if et == "response.output_text.delta":
                txt = ev.get("delta", "")
                if txt:
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': txt}}]})}\n\n".encode('utf-8')
            elif et == "response.completed":
                yield b"data: [DONE]\n\n"
                return
            elif et in ("response.failed", "error", "response.incomplete"):
                msg = json.dumps(ev.get("response", ev))[:300]
                yield f"data: {json.dumps({'choices': [{'delta': {'content': f'⚠️ Zen Responses Error: {msg}'}}]})}\n\n".encode('utf-8')
                yield b"data: [DONE]\n\n"
                return
        yield b"data: [DONE]\n\n"


def _responses_payload_to_chat(rj: dict) -> dict:
    """Convert a non-streaming Responses-API body into chat-completions shape."""
    txt = ""
    for item in (rj.get("output") or []):
        if item.get("type") == "message":
            for b in (item.get("content") or []):
                if b.get("type") == "output_text":
                    txt += b.get("text", "")
    return {"choices": [{"message": {"role": "assistant", "content": txt}}]}


def call_llm(
    model_id: str, 
    system_prompt: str, 
    messages: list, 
    api_keys: dict, 
    temperature: float = 0.9, 
    top_p: float = 1.0, 
    max_tokens: int = 4096, 
    stream: bool = True,
    custom_base_url: str = "",
    custom_provider_type: str = "openai",
    custom_auth_header_name: str = "Authorization",
    custom_auth_prefix: str = "Bearer ",
    disable_vpn_rotation: bool = False,
    **kwargs
) -> any:
    """Universal wrapper for direct LLM API access with fallback to OpenRouter and Redis multiplexing/proxy routing."""
    try:
        # Import redis pool dynamically
        import redis_pool
        key_pool = redis_pool.pool
    except Exception as e:
        print(f"[COMPUTE_POOL] Failed to import redis_pool: {e}. Falling back to default keys.")
        class DummyPool:
            def is_active(self): return False
            def checkout_key(self, provider): return None, None, None
            def release_key(self, provider, key_id, status, cooldown_duration=0): return False
            def note_auth_failure(self, provider, key_id, status_code, body="", allow_burn=True): return ""
            def unburn_key(self, provider, key_id): return False
        key_pool = DummyPool()

    original_model_id = model_id
    clean_model = model_id
    last_error = "No API key found."

    # The Zen API only ever accepts bare catalog ids ("grok-4.6"). Strip the
    # UI prefix BEFORE any routing decision, custom-URL or not — previously
    # the strip only ran on the non-custom path, so prefixed ids leaked
    # through and Zen rejected them as unsupported models.
    if model_id and str(model_id).lower().startswith("opencodezen/"):
        model_id = str(model_id)[len("opencodezen/"):]

    # Sanitize custom headers to prevent requests latin-1 encoding crashes
    custom_auth_header_name = custom_auth_header_name.encode('ascii', 'ignore').decode('ascii')
    custom_auth_prefix = custom_auth_prefix.encode('ascii', 'ignore').decode('ascii')

    # Determine initial provider and base_url
    import os
    use_vertex = bool(os.getenv("VERTEX_PROJECT_ID"))
    use_responses_api = False

    if custom_base_url and custom_base_url.strip():
        provider = "custom_" + custom_provider_type
        base_url = custom_base_url.strip()
        # Zen-aware endpoint routing: pick the right door for the model family.
        if "opencode.ai/zen" in base_url and "/chat/completions" in base_url:
            _route = _zen_route_for(model_id)
            if _route == "responses":
                base_url = base_url.replace("/chat/completions", "/responses")
                use_responses_api = True
                print(f"[ZEN ROUTE] {model_id} -> Responses API ({base_url})", flush=True)
            elif _route == "messages":
                base_url = base_url.replace("/chat/completions", "/messages")
                provider = "anthropic"  # reuse the native Anthropic payload pipeline
                print(f"[ZEN ROUTE] {model_id} -> Messages API ({base_url})", flush=True)
    else:
        provider = "openrouter"
        base_url = "https://openrouter.ai/api/v1/chat/completions"

    # Intelligent Fallback & Environment-based Vertex Routing
    if not (custom_base_url and custom_base_url.strip()):
        is_google_model = ("google/" in model_id.lower() or "gemini" in model_id.lower())
        is_anthropic_model = ("anthropic/" in model_id.lower() or "claude" in model_id.lower())
        
        # Only route native Google models through Vertex. 
        # All partner/Anthropic models bypass Vertex entirely to avoid OAuth latency.
        has_openrouter_key = bool(api_keys.get("openrouter") or (api_keys.get("universal") and str(api_keys.get("universal")).startswith("sk-or-")))
        if use_vertex and is_google_model and not has_openrouter_key:
            # Vertex AI Route
            try:
                from google.auth import default
                import google.auth.transport.requests
                
                gcp_project = os.getenv("VERTEX_PROJECT_ID")
                gcp_location = os.getenv("VERTEX_LOCATION", "us-central1")
                
                creds, default_project = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                if not gcp_project:
                    gcp_project = default_project
                    
                if not gcp_project:
                    raise ValueError("GCP Project ID not found. Please set VERTEX_PROJECT_ID in your .env file.")
                    
                global _VERTEX_TOKEN_CACHE, _VERTEX_TOKEN_CACHE_TIME
                import time
                now_time = time.time()
                
                # Check if we have a valid cached token (last refreshed < 50 mins ago)
                if _VERTEX_TOKEN_CACHE is not None and (now_time - _VERTEX_TOKEN_CACHE_TIME) < 3000.0:
                    api_key = _VERTEX_TOKEN_CACHE
                else:
                    print("[VERTEX] Refreshing OAuth access token...", flush=True)
                    creds.refresh(google.auth.transport.requests.Request())
                    api_key = creds.token
                    _VERTEX_TOKEN_CACHE = api_key
                    _VERTEX_TOKEN_CACHE_TIME = now_time
            except Exception as gcp_err:
                err_msg = f"⚠️ Vertex Auth Failure: {gcp_err}"
                if stream:
                    class ErrStreamVertex:
                        def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': err_msg}}]})}\n\n".encode('utf-8')
                    return ErrStreamVertex()
                return err_msg

            if gcp_location.lower() == "global":
                gcp_host = "aiplatform.googleapis.com"
            elif gcp_location.lower() == "us":
                gcp_host = "aiplatform.us.rep.googleapis.com"
            else:
                gcp_host = f"{gcp_location}-aiplatform.googleapis.com"

            if is_anthropic_model:
                provider = "anthropic"
                clean_model = model_id
                if "anthropic/" in clean_model.lower():
                    clean_model = clean_model.replace("anthropic/", "")
                base_url = f"https://{gcp_host}/v1/projects/{gcp_project}/locations/{gcp_location}/publishers/anthropic/models/{clean_model}:rawPredict"
                model_id = clean_model
            else:
                provider = "google_vertex"
                clean_model = model_id
                if "google/" in clean_model.lower():
                    clean_model = clean_model.replace("google/", "")
                base_url = f"https://{gcp_host}/v1/projects/{gcp_project}/locations/{gcp_location}/endpoints/openapi/chat/completions"
                if "/" not in clean_model:
                    model_id = f"google/{clean_model}"
                else:
                    model_id = clean_model
        else:
            # Standard developer API fallbacks
            if is_anthropic_model:
                provider = "anthropic"
                base_url = "https://api.anthropic.com/v1/messages"
                if "anthropic/" in model_id: model_id = model_id.replace("anthropic/", "")
                
            elif ("openai/" in model_id.lower() or "gpt" in model_id.lower()):
                provider = "openai"
                base_url = "https://api.openai.com/v1/chat/completions"
                if "openai/" in model_id: model_id = model_id.replace("openai/", "")
                
            elif is_google_model:
                provider = "google"
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                if "google/" in model_id: model_id = model_id.replace("google/", "")

            if "openrouter/" in model_id.lower():
                provider = "openrouter"
                base_url = "https://openrouter.ai/api/v1/chat/completions"

            if "opencodezen/" in model_id.lower():
                provider = "opencodezen" # Zen gateway — OpenAI-compatible schemas, own key slot
                base_url = "https://opencode.ai/zen/v1/chat/completions"
                model_id = model_id.replace("opencodezen/", "")

    # We retry up to 3 times to find a working key/proxy path
    max_retries = kwargs.get("max_retries", 3)
    # FIX(false-burn): each attempt checks out the NEXT healthy key, so without
    # this counter one bad request could permanently burn every key in the pool.
    _auth_burns = 0
    _thinking_off_refused = False  # FIX(gemini-blank-turn): set by the 400 handler
    for attempt in range(max_retries):
        if provider == "google_vertex" or (provider == "anthropic" and "aiplatform" in base_url):
            key_id = None
            pooled_key = None
            static_proxy_url = None
            is_pooled = False
        else:
            key_id, pooled_key, static_proxy_url = key_pool.checkout_key(provider)
            is_pooled = key_id is not None
            
            # Fallback to UI dict keys if the pool is empty or inactive
            api_key = pooled_key
            if not api_key:
                if provider.startswith("custom_"):
                    api_key = api_keys.get("universal", "")
                elif provider == "opencodezen":
                    # Zen accepts any of the dedicated slots; "public" unlocks
                    # the free model catalog when no key is configured.
                    api_key = api_keys.get("opencode") or api_keys.get("opencodezen") or api_keys.get("universal") or "public"
                elif provider == "anthropic":
                    api_key = api_keys.get("anthropic") or api_keys.get("universal") or api_keys.get("openrouter", "")
                elif provider == "openai":
                    api_key = api_keys.get("openai") or api_keys.get("universal") or api_keys.get("openrouter", "")
                elif provider == "google":
                    api_key = api_keys.get("google") or api_keys.get("universal") or api_keys.get("openrouter", "")
                else:
                    api_key = api_keys.get("universal") or api_keys.get("openrouter", "")

            # Whitespace/stub keys pass truthiness checks but produce upstream
            # "Missing Authentication header" 401s — normalize before use.
            if api_key is not None:
                api_key = str(api_key).strip()

        # OpenRouter override check for fallback keys (never for explicit Zen routing)
        if api_key and api_key.startswith("sk-or-") and not custom_base_url and provider != "opencodezen":
            provider = "openrouter"
            base_url = "https://openrouter.ai/api/v1/chat/completions"
            model_id = original_model_id

        if not api_key:
            if stream:
                class ErrorStream:
                    def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': '⚠️ Connection Error: No API key provided.'}}]})}\n\n".encode('utf-8')
                return ErrorStream()
            return f"⚠️ Connection Error: No available API key for the requested provider ({provider})."

        _masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 12 else (api_key[:3] + "..." if api_key else "EMPTY")
        print(f"[ROUTER] attempt {attempt+1}: provider={provider} base_url={base_url} model={model_id} key={_masked_key}", flush=True)

        active_proxy = key_pool.checkout_proxy() or static_proxy_url
        proxies = {"http": active_proxy, "https": active_proxy} if active_proxy else None
        if active_proxy:
            print(f"[COMPUTE_POOL] Routing through proxy tunnel: {active_proxy}")

        try:
            # --- NATIVE ANTHROPIC PIPELINE ---
            if provider == "anthropic":
                if "aiplatform" not in base_url and not base_url.rstrip("/").endswith("/messages"):
                    base_url = "https://api.anthropic.com/v1/messages"
                anth_messages = []
                
                for m in messages:
                    if m["role"] == "system": continue
                    
                    if m["role"] == "user":
                        anth_messages.append({"role": "user", "content": str(m.get("content", ""))})
                    elif m["role"] == "assistant":
                        blocks = []
                        if m.get("content"):
                            blocks.append({"type": "text", "text": str(m["content"])})
                        if "tool_calls" in m:
                            for tc in m["tool_calls"]:
                                args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args})
                        if blocks:
                            anth_messages.append({"role": "assistant", "content": blocks})
                    elif m["role"] == "tool":
                        anth_messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"), "content": str(m.get("content", ""))}]})
                
                merged = []
                for m in anth_messages:
                    if not merged: merged.append(m)
                    elif merged[-1]["role"] == m["role"]:
                        c1 = merged[-1]["content"] if isinstance(merged[-1]["content"], list) else [{"type": "text", "text": str(merged[-1]["content"])}]
                        c2 = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": str(m["content"])}]
                        merged[-1]["content"] = c1 + c2
                    else: merged.append(m)
                
                if not merged or merged[0]["role"] != "user": merged.insert(0, {"role": "user", "content": "(Continuing context)"})

                anth_tools = []
                for t in kwargs.get("tools", []):
                    if "function" in t:
                        props = t["function"].get("parameters", {"type": "object", "properties": {}})
                        if "properties" not in props: props["properties"] = {}
                        anth_tools.append({"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": props})
                        
                if kwargs.get("pre_fill"):
                    merged.append({"role": "assistant", "content": [{"type": "text", "text": kwargs["pre_fill"]}]})

                anth_payload = {
                    "model": model_id,
                    "system": system_prompt,
                    "messages": merged,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": stream
                }
                if anth_tools: anth_payload["tools"] = anth_tools
                 
                if kwargs.get("tool_choice"):
                    choice = kwargs["tool_choice"]
                    if isinstance(choice, dict) and choice.get("type") == "function":
                        anth_payload["tool_choice"] = {
                            "type": "tool",
                            "name": choice["function"]["name"]
                        }

                thinking_level_val = kwargs.get("thinking_level", "Off")
                if thinking_level_val != "Off":
                    budget_map = {"Low": 1024, "Medium": 2048, "High": 4096}
                    budget = budget_map.get(thinking_level_val, 1024)
                    if max_tokens <= budget:
                        max_tokens = budget + 1024
                        anth_payload["max_tokens"] = max_tokens
                    anth_payload["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": budget
                    }
                    anth_payload["temperature"] = 1.0
                    
                if "aiplatform" in base_url:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json"
                    }
                elif custom_base_url and custom_base_url.strip():
                    headers = {
                        custom_auth_header_name: f"{custom_auth_prefix}{api_key}".strip(),
                        "anthropic-version": "2023-06-01", 
                        "content-type": "application/json"
                    }
                else:
                    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
                res = OR_SESSION.post(base_url, headers=headers, data=json.dumps(anth_payload), stream=stream, proxies=proxies, timeout=60)
                
                if res.status_code == 200:
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "HEALTHY")
                    if not stream: 
                        return {"choices": [{"message": {"role": "assistant", "content": res.json().get("content", [{"text": ""}])[0].get("text", "")}}]}
                    else:
                        class AnthropicStream:
                            def iter_lines(self):
                                for line in res.iter_lines():
                                    if line:
                                        l = line.decode('utf-8')
                                        if l.startswith("data: "):
                                            try:
                                                d = json.loads(l[6:])
                                                if d.get("type") == "content_block_delta" and d["delta"]["type"] == "text_delta":
                                                    text = d['delta']['text']
                                                    cleaned_text = text.replace("</node>", "").replace("</system_state>", "")
                                                    yield f"data: {json.dumps({'choices': [{'delta': {'content': cleaned_text}}]})}\n\n".encode('utf-8')
                                                elif d.get("type") == "message_stop" or d.get("type") == "message_delta":
                                                    msg = d.get("message", {}) or d.get("delta", {})
                                                    if msg.get("stop_reason") == "refusal" or msg.get("stop_reason") == "error":
                                                        reason = msg.get("stop_reason")
                                                        ref_data = {'choices': [{'delta': {'content': f'⚠️ Opus Refusal Triggered: {reason}'}}]}
                                                        yield f"data: {json.dumps(ref_data)}\n\n".encode('utf-8')
                                                    
                                                    if d.get("type") == "message_stop":
                                                        yield b"data: [DONE]\n\n"
                                                elif d.get("type") == "error":
                                                    err = d.get("error", {})
                                                    err_type = err.get("type", "unknown")
                                                    err_msg = err.get("message", "unknown error")
                                                    err_data = {'choices': [{'delta': {'content': f'⚠️ Anthropic Error ({err_type}): {err_msg}'}}]}
                                                    yield f"data: {json.dumps(err_data)}\n\n".encode('utf-8')
                                                    yield b"data: [DONE]\n\n"
                                            except: pass
                        return AnthropicStream()
                
                elif res.status_code == 429:
                    last_error = f"Rate limited (429): {res.text}"
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "COOLDOWN", cooldown_duration=300)
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=60)
                    
                    if "aiplatform" in base_url:
                        print("[VERTEX FALLBACK] Rate limited/Quota exceeded on Vertex. Swapping to OpenRouter/Standard keys.")
                        if api_keys.get("openrouter") or api_keys.get("universal"):
                            provider = "openrouter"
                            base_url = "https://openrouter.ai/api/v1/chat/completions"
                            if "fable" in model_id.lower():
                                model_id = "anthropic/claude-3.5-sonnet"
                        else:
                            provider = "anthropic"
                            base_url = "https://api.anthropic.com/v1/messages"
                            if "anthropic/" in model_id: model_id = model_id.replace("anthropic/", "")
                            if "fable" in model_id.lower():
                                model_id = "claude-3-5-sonnet-20241022"
                    continue
                elif res.status_code in [401, 403]:
                    last_error = f"Auth error ({res.status_code}): {res.text}"
                    if is_pooled:
                        # FIX(false-burn): a 403 is usually about the project, the
                        # region, the model or the exit IP -- not the credential.
                        # redis_pool.classify_auth_failure decides; only a body that
                        # names the key can burn it, and only once per request.
                        _verdict = key_pool.note_auth_failure(
                            provider, key_id, res.status_code, res.text,
                            allow_burn=(_auth_burns == 0))
                        if _verdict == "BURNED":
                            _auth_burns += 1
                    # A blocked proxy exit IP returns 401/403 from every provider at
                    # once. Cool it, or the pool gets blamed for the tunnel.
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=300)
                    
                    if "aiplatform" in base_url:
                        print("[VERTEX FALLBACK] Auth error on Vertex. Swapping to OpenRouter/Standard keys.")
                        if api_keys.get("openrouter") or api_keys.get("universal"):
                            provider = "openrouter"
                            base_url = "https://openrouter.ai/api/v1/chat/completions"
                            if "fable" in model_id.lower():
                                model_id = "anthropic/claude-3.5-sonnet"
                        else:
                            provider = "anthropic"
                            base_url = "https://api.anthropic.com/v1/messages"
                            if "anthropic/" in model_id: model_id = model_id.replace("anthropic/", "")
                            if "fable" in model_id.lower():
                                model_id = "claude-3-5-sonnet-20241022"
                    continue
                elif res.status_code in [502, 503, 504]:
                    last_error = f"Transient gateway error ({res.status_code}): {res.text}"
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=120)
                    continue
                else:
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    if stream:
                        class ErrStream:
                            def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': f'⚠️ Anthropic Error: {res.text}'}}]})}\n\n".encode('utf-8')
                        return ErrStream()
                    return f"⚠️ Anthropic Error: {res.status_code}"

            # --- STANDARD OPENAI-COMPATIBLE PIPELINE ---
            else:
                if custom_base_url and custom_base_url.strip():
                    headers = {
                        custom_auth_header_name: f"{custom_auth_prefix}{api_key}".strip(),
                        "Content-Type": "application/json"
                    }
                elif provider == "opencodezen":
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                else:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "HTTP-Referer": "https://persona-app.com",
                        "Referer": "https://persona-app.com",
                        "X-Title": "PersonaApp",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }

                
                def clean_content(c):
                    if isinstance(c, str) and c.strip().startswith("[") and c.strip().endswith("]"):
                        try: return json.loads(c)
                        except: return c
                    return c

                local_messages = messages
                pre_fill = kwargs.get("pre_fill", "")
                if pre_fill and provider != "anthropic" and local_messages:
                    # FIX(prefill-placement): for OpenAI-compatible providers the persona
                    # pre-fill rides as a trailing assistant turn, the shape the Anthropic
                    # path uses. It must never be glued onto the user message: the model
                    # then reads the persona name as the USER speaking ("telling me who
                    # I am"). Gemini gets no pre-fill at all: gemini-3.7-flash on Vertex
                    # answers a trailing model turn with 400 "Requests ending with a model
                    # turn are not supported", and on gemini-3-flash the pre-fill made no
                    # measurable difference to anything (2026-09-06, 8 runs per shape).
                    # The system prompt carries identity. Regression: tests/test_prefill_placement.py
                    is_gemini = provider in ("google", "google_vertex") or "gemini" in model_id.lower()
                    if is_gemini:
                        pass
                    elif local_messages[-1].get("role") != "assistant":
                        local_messages = list(messages) + [{"role": "assistant", "content": pre_fill}]

                payload_messages = [{"role": "system", "content": system_prompt}] + [{k: (clean_content(v) if k == "content" else v) for k, v in m.items() if k in ["role", "content", "tool_calls", "tool_call_id", "name"]} for m in local_messages]
                    
                data = {
                    "model": model_id,
                    "messages": payload_messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "stream": stream
                }
                if provider == "openai":
                    data["max_completion_tokens"] = max_tokens
                else:
                    data["max_tokens"] = max_tokens

                thinking_level_val = kwargs.get("thinking_level", "Off")
                model_lower = model_id.lower()
                is_reasoning_mandatory = any(kw in model_lower for kw in ["thinking", "reasoning", "o1", "o3", "r1", "step", "minimax", "fable"])

                thinking_norm = str(thinking_level_val).lower().strip()
                if provider == "openrouter":
                    if thinking_norm in ["low", "medium", "high"]:
                        data["reasoning"] = {
                            "effort": thinking_norm
                        }
                        print(f"[REASONING] Attached OpenRouter reasoning effort: '{thinking_norm}'")
                elif provider == "openai":
                    if thinking_norm in ["low", "medium", "high"]:
                        data["reasoning_effort"] = thinking_norm
                        if "o1" in model_id.lower() or "o3" in model_id.lower():
                            data.pop("temperature", None)
                            data.pop("top_p", None)
                            data.pop("presence_penalty", None)
                            data.pop("frequency_penalty", None)

                if provider in ("google", "google_vertex"):
                    if thinking_norm in ["low", "medium", "high"]:
                        data["reasoning_effort"] = thinking_norm
                    elif thinking_norm in ["off", "none", "minimal", "0", ""] and not is_reasoning_mandatory:
                        # FIX(gemini-blank-turn): this route used to send no thinking config
                        # at all, so the dial never reached Gemini. gemini-3-flash then
                        # thought when it felt like it and, with real assistant history in
                        # context, returned whitespace in ~37% of turns (15/40 across five
                        # prompt shapes, Vertex, 2026-09-06); with thinking_budget 0: 0/16.
                        # gemini-3.5-flash accepted the zero budget, gemini-3.1-pro ignored
                        # it; a model that refuses it with a 400 is retried once at the
                        # lowest effort (see the 400 handler below).
                        # Regression: tests/test_gemini_thinking_off.py
                        if _thinking_off_refused:
                            data["reasoning_effort"] = "low"
                        else:
                            data["extra_body"] = {"google": {"thinking_config": {"thinking_budget": 0}}}
                    
                pipeline_tools = kwargs.get("tools", [])
                if pipeline_tools and not any(kw in model_id.lower() for kw in ["stealth", "experimental", "alpha", "beta"]):
                    data["tools"] = pipeline_tools
                if kwargs.get("tool_choice"):
                    data["tool_choice"] = kwargs["tool_choice"]

                presence = kwargs.get("presence_penalty", 0.0)
                frequency = kwargs.get("frequency_penalty", 0.0)
                k = kwargs.get("top_k", 0)
                
                if presence != 0.0 and provider != "google": data["presence_penalty"] = presence
                if frequency != 0.0 and provider != "google": data["frequency_penalty"] = frequency
                if k != 0 and provider not in ["openai", "google"]: data["top_k"] = k

                if provider == "openrouter":
                    if "anthropic" in model_id.lower() or "claude" in model_id.lower():
                        data["provider"] = {
                            "order": ["Anthropic"],
                            "allow_fallbacks": False
                        }
                    else:
                        data["provider"] = {"ignore": ["Azure", "Azure AI Foundry"]}
                
                if use_responses_api:
                    # OpenAI Responses API shape: system -> "instructions",
                    # history -> "input", max_tokens -> "max_output_tokens".
                    resp_input = []
                    for m in payload_messages[1:]:
                        role = m.get("role", "user")
                        content = m.get("content", "")
                        if role == "tool":
                            resp_input.append({"role": "user", "content": f"[tool result]: {content}"})
                            continue
                        if isinstance(content, list):
                            blocks = []
                            for b in content:
                                t = b.get("text", "") if isinstance(b, dict) else str(b)
                                blocks.append({"type": "input_text" if role == "user" else "output_text", "text": t})
                            resp_input.append({"role": role, "content": blocks})
                        else:
                            resp_input.append({"role": role, "content": str(content)})
                    rdata = {
                        "model": model_id,
                        "instructions": system_prompt,
                        "input": resp_input,
                        "stream": stream,
                        "max_output_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                    }
                    print(f"[ZEN ROUTE] Responses payload: model={model_id} input_msgs={len(resp_input)} (tools not supported on this endpoint)", flush=True)
                    response = OR_SESSION.post(base_url, headers=headers, data=json.dumps(rdata), timeout=60, stream=stream, proxies=proxies)
                else:
                    response = OR_SESSION.post(base_url, headers=headers, data=json.dumps(data), timeout=60, stream=stream, proxies=proxies)

                if response.status_code == 200:
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "HEALTHY")
                    if provider_health is not None:
                        try:
                            provider_health.record_outcome(provider, True)
                        except Exception:
                            pass
                    if stream:
                        return ResponsesToChatStream(response) if use_responses_api else response
                    else:
                        return _responses_payload_to_chat(response.json()) if use_responses_api else response.json()
                else:
                    print(f"[COMPUTE_POOL ERROR] Request to {provider} ({base_url}) failed. Status: {response.status_code}. Response: {response.text}")
                    # World-model update: transport/gateway failures teach the
                    # ROUTER that a provider is flaky. Auth failures (401/403)
                    # and agent-side 4xx say nothing about server reliability,
                    # so they are excluded. Dopamine is never touched here.
                    if provider_health is not None and (
                        response.status_code == 429 or response.status_code >= 500
                        or (response.status_code == 400 and "upstream request failed" in response.text.lower())
                    ):
                        try:
                            _ph = provider_health.record_outcome(
                                provider, False, error=f"{response.status_code}: {response.text[:120]}"
                            )
                            print(f"[PROVIDER_HEALTH] {provider} reliability={_ph['reliability']} streak={_ph['consecutive_failures']}", flush=True)
                        except Exception:
                            pass

                # Custom gateways (e.g. OpenCode Zen) can 500 when the bound
                # tool schemas are incompatible with the downstream model.
                # Degrade gracefully: retry the call without tools.
                if response.status_code == 500 and provider.startswith("custom_") and data.get("tools"):
                    data.pop("tools", None)
                    data.pop("tool_choice", None)
                    last_error = "Gateway 500 with tools attached — retrying without tools."
                    print("[COMPUTE_POOL] 500 on custom gateway -> retrying without tool schemas.", flush=True)
                    continue

                # Zen wraps upstream provider failures as 400 "Upstream request
                # failed" — that's a transient gateway error in a 400 costume.
                # Retry with backoff instead of dying on first strike.
                if (
                    response.status_code == 400
                    and provider.startswith("custom_")
                    and "upstream request failed" in response.text.lower()
                ):
                    last_error = f"Transient upstream failure (400): {response.text}"
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "COOLDOWN", cooldown_duration=15)
                    _wait = 1.5
                    if provider_health is not None:
                        try:
                            _wait = max(1.5, provider_health.suggested_backoff(provider))
                        except Exception:
                            pass
                    print(f"[COMPUTE_POOL] Upstream provider failure on custom gateway -> retrying in {_wait:.1f}s.", flush=True)
                    import time as _t
                    _t.sleep(_wait)
                    continue

                # FIX(gemini-blank-turn): a model that cannot switch thinking off answers
                # the zero budget with a 400 that names thinking. Retry once at the lowest
                # effort instead of surfacing "API Error" to the user.
                if (
                    response.status_code == 400
                    and not _thinking_off_refused
                    and ((data.get("extra_body") or {}).get("google") or {}).get("thinking_config", {}).get("thinking_budget") == 0
                    and ("think" in response.text.lower() or "reasoning" in response.text.lower())
                ):
                    _thinking_off_refused = True
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    print(f"[THINKING] {model_id} refuses thinking off -> retrying at lowest effort", flush=True)
                    continue

                if response.status_code == 429:
                    last_error = f"Rate limited (429): {response.text}"
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "COOLDOWN", cooldown_duration=300)
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=60)
                    
                    if "aiplatform" in base_url:
                        print("[VERTEX FALLBACK] Rate limited/Quota exceeded on Vertex. Swapping to OpenRouter/Standard keys.")
                        if api_keys.get("openrouter") or api_keys.get("universal"):
                            provider = "openrouter"
                            base_url = "https://openrouter.ai/api/v1/chat/completions"
                            if "fable" in model_id.lower():
                                model_id = "anthropic/claude-3.5-sonnet"
                        else:
                            provider = "google"
                            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                            if "google/" in model_id: model_id = model_id.replace("google/", "")
                    continue
                elif response.status_code in [401, 403]:
                    last_error = f"Auth error ({response.status_code}): {response.text}"
                    if is_pooled:
                        # FIX(false-burn): a 403 is usually about the project, the
                        # region, the model or the exit IP -- not the credential.
                        # redis_pool.classify_auth_failure decides; only a body that
                        # names the key can burn it, and only once per request.
                        _verdict = key_pool.note_auth_failure(
                            provider, key_id, response.status_code, response.text,
                            allow_burn=(_auth_burns == 0))
                        if _verdict == "BURNED":
                            _auth_burns += 1
                    # A blocked proxy exit IP returns 401/403 from every provider at
                    # once. Cool it, or the pool gets blamed for the tunnel.
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=300)
                    
                    if "aiplatform" in base_url:
                        print("[VERTEX FALLBACK] Auth error on Vertex. Swapping to OpenRouter/Standard keys.")
                        if api_keys.get("openrouter") or api_keys.get("universal"):
                            provider = "openrouter"
                            base_url = "https://openrouter.ai/api/v1/chat/completions"
                            if "fable" in model_id.lower():
                                model_id = "anthropic/claude-3.5-sonnet"
                        else:
                            provider = "google"
                            base_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                            if "google/" in model_id: model_id = model_id.replace("google/", "")
                    continue
                elif response.status_code in [502, 503, 504]:
                    last_error = f"Transient gateway error ({response.status_code}): {response.text}"
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    if active_proxy and active_proxy != static_proxy_url:
                        key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=120)
                    continue
                else:
                    if is_pooled:
                        key_pool.release_key(provider, key_id, "HEALTHY")
                    api_err = response.text
                    if stream:
                        class ErrStream2:
                            def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': f'⚠️ API Error: {api_err}'}}]})}\n\n".encode('utf-8')
                        return ErrStream2()
                    return f"⚠️ API Error: {api_err}"

        except Exception as e:
            last_error = f"Network or execution error: {e}"
            if is_pooled:
                key_pool.release_key(provider, key_id, "HEALTHY")
            if active_proxy and active_proxy != static_proxy_url:
                key_pool.release_proxy(active_proxy, "COOLDOWN", cooldown_duration=120)

            # Transport-level explosions are pure world-failure: teach the
            # router, never the neuron.
            if provider_health is not None:
                try:
                    _ph = provider_health.record_outcome(provider, False, error=str(e)[:150])
                    print(f"[PROVIDER_HEALTH] {provider} reliability={_ph['reliability']} streak={_ph['consecutive_failures']} (transport)", flush=True)
                except Exception:
                    pass
            
            # Egress failure - trigger rotation (VPN failover)
            # ONLY rotate if it's a connection/timeout exception, AND rotation is not disabled
            err_str = str(e).lower()
            is_conn_error = any(x in err_str for x in ["timeout", "connection", "connecttimeout", "host", "unreachable"])
            if is_conn_error and not disable_vpn_rotation:
                try:
                    import vpn_rotator
                    vpn_rotator.rotate_egress_route()
                except Exception as vr_err:
                    print(f"[VPN_ROTATOR ERROR] Failed to rotate egress route: {vr_err}")
            continue

    # If all retries failed
    ui_msg = f"⚠️ [Compute Pool Failure: All retry paths exhausted. Last logged error: {last_error}]"
    if stream:
        class ErrStreamExhausted:
            def iter_lines(self): yield f"data: {json.dumps({'choices': [{'delta': {'content': ui_msg}}]})}\n\n".encode('utf-8')
        return ErrStreamExhausted()
    return ui_msg

def is_tool_call_approved(name: str, messages: list) -> bool:
    if not messages:
        return False
        
    last_user_msg = messages[-1]
    if last_user_msg.get("role") != "user":
        return False
        
    user_content = last_user_msg.get("content", "")
    if isinstance(user_content, list):
        user_content = " ".join([b.get("text", "") for b in user_content if b.get("type", "") == "text"])
    elif not isinstance(user_content, str):
        user_content = str(user_content)
        
    user_content_lower = user_content.strip().lower()
    cleaned_user = "".join([c for c in user_content_lower if c.isalnum() or c.isspace()]).strip()
    
    affirmative_phrases = {"yes", "y", "approve", "proceed", "go ahead", "do it", "ok", "sure", "yep", "confirm", "yes proceed"}
    is_affirmative = (cleaned_user in affirmative_phrases) or any(w in cleaned_user for w in ["yes", "proceed", "approve", "do it", "go ahead", "confirm"])
    
    if not is_affirmative:
        return False

    # Check if there is an approval prompt in recent messages or if user explicitly confirmed
    for msg in reversed(messages[:-1]):
        c_str = str(msg.get("content", ""))
        if "Approval Required" in c_str or "attempting to use" in c_str:
            return True
            
    # Default to True for any explicit affirmative response to a tool query
    return True
            
    return False

def _accumulate_tool_call(tool_call_buffer, delta):
    if "tool_calls" in delta:
        for tc in delta["tool_calls"]:
            idx = tc.get("index", 0)
            if idx not in tool_call_buffer:
                tool_call_buffer[idx] = {
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {"name": "", "arguments": ""}
                }
            
            for k, v in tc.items():
                if k not in ["index", "id", "function", "type"]:
                    tool_call_buffer[idx][k] = v
                    
            if "function" in tc:
                f = tc["function"]
                for fk, fv in f.items():
                    if fk not in ["name", "arguments"]:
                        if fk not in tool_call_buffer[idx]["function"]:
                            tool_call_buffer[idx]["function"][fk] = fv
                        else:
                            if isinstance(tool_call_buffer[idx]["function"][fk], str) and isinstance(fv, str):
                                tool_call_buffer[idx]["function"][fk] += fv
                                
                if "name" in f: tool_call_buffer[idx]["function"]["name"] += f["name"]
                if "arguments" in f: tool_call_buffer[idx]["function"]["arguments"] += f["arguments"]

def stream_reader_thread(response, q, loop, sanitizer):
    """Reads lines from the raw LLM response stream and queues them directly. Bypasses old sentence buffering."""
    import time
    
    local_queue_buffer = []
    last_flush_time = time.time()

    def queue_item(item_type, val):
        nonlocal last_flush_time
        if item_type in ("raw", "error") and isinstance(val, (bytes, str)):
            raw_val = val.encode('utf-8') if isinstance(val, str) else val
            val = raw_val.rstrip(b'\r\n') + b'\n\n'
        local_queue_buffer.append((item_type, val))
        now = time.time()
        if len(local_queue_buffer) >= 20 or (now - last_flush_time) >= 0.05:
            flush_local_buffer()
            last_flush_time = now

    def flush_local_buffer():
        if local_queue_buffer:
            items_to_send = list(local_queue_buffer)
            local_queue_buffer.clear()
            def put_batch():
                for itype, ival in items_to_send:
                    q.put_nowait((itype, ival))
            loop.call_soon_threadsafe(put_batch)

    try:
        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode('utf-8')
            
            if "⚠️ System Error:" in decoded or "⚠️ Connection Error:" in decoded or "⚠️ Stream Error:" in decoded:
                queue_item("error", line.encode('utf-8') if isinstance(line, str) else line)
                flush_local_buffer()
                return
            
            if decoded.startswith("data: ") and decoded != "data: [DONE]":
                try:
                    data = json.loads(decoded[6:])
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    
                    if "tool_calls" in delta:
                        queue_item("tool_call", delta)
                        continue
                    
                    content = delta.get("content", "")
                    if content:
                        cleaned = content.replace("</node>", "").replace("</system_state>", "")
                        if cleaned != content:
                            delta["content"] = cleaned
                            data["choices"][0]["delta"] = delta
                            line = f"data: {json.dumps(data)}\n\n".encode('utf-8')
                        else:
                            line = line.encode('utf-8') if isinstance(line, str) else line
                    else:
                        line = line.encode('utf-8') if isinstance(line, str) else line
                    
                    queue_item("raw", line)
                except Exception:
                    queue_item("raw", line.encode('utf-8') if isinstance(line, str) else line)
            else:
                if decoded != "data: [DONE]":
                    queue_item("raw", line.encode('utf-8') if isinstance(line, str) else line)
        
        flush_local_buffer()
            
    except Exception as e:
        queue_item("exception", str(e))
        flush_local_buffer()
    finally:
        loop.call_soon_threadsafe(q.put_nowait, ("done", None))

def _format_exception(err_msg: str) -> bytes:
    if "10054" in err_msg or "ConnectionResetError" in err_msg or "Connection aborted" in err_msg or "forcibly closed" in err_msg:
        reset_content = "\n\n⚠️ *[Connection reset by remote host. Your message has been committed to context. You can continue speaking.]*"
        return f"data: {json.dumps({'choices': [{'delta': {'content': reset_content}}]})}\n\n".encode('utf-8')
    else:
        err_content = f"\n\n⚠️ *[System Error during stream: {err_msg}]*"
        return f"data: {json.dumps({'choices': [{'delta': {'content': err_content}}]})}\n\n".encode('utf-8')

def _format_sse_delta(content: str) -> bytes:
    data = {"choices": [{"delta": {"content": content}}]}
    return f"data: {json.dumps(data)}\n\n".encode('utf-8')

async def intercepting_stream_generator(model_id, system_prompt, messages, api_keys, tools, kwargs_dict, max_loops=3, **kwargs):
    """
    Consumes the SSE stream asynchronously. Integrates sentence-buffered, 
    decoupled parallel translation routing, keep-alive SSE heartbeats, and 
    a guaranteed [DONE] terminal sequence.
    """
    current_messages = [m for m in messages]
    pre_fill = kwargs.get("pre_fill", "")
    sanitizer = kwargs.get("sanitizer")
    bypass_firewall = kwargs.get("bypass_firewall", False)

    # Agentic budget: per-request override > env var > signature default.
    try:
        max_loops = int(kwargs_dict.get("max_agent_loops") or os.environ.get("AGENTIC_MAX_LOOPS", max_loops) or max_loops)
    except (TypeError, ValueError):
        pass

    done_yielded = False
    loop_index = -1
    forced_synthesis_done = False
    seen_call_sigs = set()
    _da_counts = {"success": 0, "action_fail": 0, "infra_fail": 0}

    while True:
        # Budget exhaustion guard: if the previous pass ended by executing
        # tools, run ONE final pass with tools disabled so the model must
        # synthesize a prose answer instead of the generator dying silently.
        if loop_index >= max_loops:
            if not forced_synthesis_done:
                forced_synthesis_done = True
                notice = "\n\n*[AGENT LOOP BUDGET REACHED - synthesizing final answer from gathered results]*\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': notice}}]})}\n\n".encode('utf-8')
            else:
                done_yielded = True
                yield b'data: [DONE]\n\n'
                return

        loop_index += 1
        tools_disabled_pass = forced_synthesis_done
        response = await asyncio.to_thread(
            call_llm,
            model_id, system_prompt, current_messages, api_keys, 
            tools=(None if tools_disabled_pass else tools), stream=True, 
            temperature=kwargs_dict.get("temperature", 0.9),
            top_p=kwargs_dict.get("top_p", 1.0),
            max_tokens=kwargs_dict.get("max_tokens", 4096),
            presence_penalty=kwargs_dict.get("presence_penalty", 0.0),
            frequency_penalty=kwargs_dict.get("frequency_penalty", 0.0),
            top_k=kwargs_dict.get("top_k", 0),
            thinking_level=kwargs_dict.get("thinking_level", "Off"),
            custom_base_url=kwargs_dict.get("custom_base_url", ""),
            custom_provider_type=kwargs_dict.get("custom_provider_type", "openai"),
            custom_auth_header_name=kwargs_dict.get("custom_auth_header_name", "Authorization"),
            custom_auth_prefix=kwargs_dict.get("custom_auth_prefix", "Bearer ")
        )
        
        if isinstance(response, str):
            yield f'data: {{"choices": [{{"delta": {{"content": "{response}"}}}}]}}\n\n'.encode('utf-8')
            yield b'data: [DONE]\n\n'
            break
        
        is_tool_call = False
        tool_call_buffer = {}
        
        import queue
        import threading
        
        line_queue = queue.Queue()
        def producer():
            try:
                for stream_line in response.iter_lines():
                    line_queue.put(stream_line)
            except Exception as ex:
                line_queue.put(ex)
            finally:
                line_queue.put(None)
                
        t = threading.Thread(target=producer, daemon=True)
        t.start()
        
        try:
            while True:
                try:
                    line = await asyncio.to_thread(line_queue.get, block=True, timeout=30.0)
                except queue.Empty:
                    continue
                    
                if line is None:
                    break
                if isinstance(line, Exception):
                    raise line
                if not line:
                    continue
                decoded = line.decode('utf-8')
                
                # Immediately halt recursion if a backend API error was triggered
                if "⚠️ System Error:" in decoded or "⚠️ Connection Error:" in decoded or "⚠️ Stream Error:" in decoded:
                    yield line + b"\n\n"
                    return # Fast exit from generator so it doesn't loop
                    
                if decoded.startswith("data: ") and decoded != "data: [DONE]":
                    try:
                        data = json.loads(decoded[6:])
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        
                        if "tool_calls" in delta:
                            is_tool_call = True
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_buffer:
                                    tool_call_buffer[idx] = {"id": tc.get("id", ""), "type": tc.get("type", "function"), "function": {"name": "", "arguments": ""}}
                                
                                # Preserve proprietary fields (like Google's thought_signature)
                                for k, v in tc.items():
                                    if k not in ["index", "id", "function", "type"]:
                                        tool_call_buffer[idx][k] = v
                                        
                                if "function" in tc:
                                    f = tc["function"]
                                    # Preserve any extra fields inside the function object itself
                                    for fk, fv in f.items():
                                        if fk not in ["name", "arguments"]:
                                            if fk not in tool_call_buffer[idx]["function"]:
                                                tool_call_buffer[idx]["function"][fk] = fv
                                            else:
                                                # If it streams in chunks (like text), append it
                                                if isinstance(tool_call_buffer[idx]["function"][fk], str) and isinstance(fv, str):
                                                    tool_call_buffer[idx]["function"][fk] += fv
                                                    
                                    if "name" in f: tool_call_buffer[idx]["function"]["name"] += f["name"]
                                    if "arguments" in f: tool_call_buffer[idx]["function"]["arguments"] += f["arguments"]
                            continue
                            
                        if not is_tool_call:
                            if sanitizer and "content" in delta and delta["content"]:
                                accumulated_content += delta["content"]
                                desanitized_accum = sanitizer.desanitize(accumulated_content)
                                new_text = desanitized_accum[len(unmasked_output_so_far):]
                                unmasked_output_so_far = desanitized_accum
                                data["choices"][0]["delta"]["content"] = new_text
                                line = f"data: {json.dumps(data)}".encode('utf-8')
                            yield line + b"\n\n"
                    except:
                        if not is_tool_call:
                            yield line + b"\n\n"
                else:
                    if not is_tool_call:
                        yield line + b"\n\n"
        except Exception as e:
            err_msg = str(e)
            if "10054" in err_msg or "ConnectionResetError" in err_msg or "Connection aborted" in err_msg or "forcibly closed" in err_msg:
                reset_content = "\n\n⚠️ *[Connection reset by remote host. Your message has been committed to context. You can continue speaking.]*"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': reset_content}}]})}\n\n".encode('utf-8')
            else:
                err_content = f"\n\n⚠️ *[System Error during stream: {err_msg}]*"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': err_content}}]})}\n\n".encode('utf-8')
            yield b'data: [DONE]\n\n'
            return
                    
        if is_tool_call:
            # Execute intercepted tools
            assistant_m = {"role": "assistant", "content": "", "tool_calls": []}
            # Deep-copy buffered calls into history so duplicate-guard rewrites
            # below never mutate what gets replayed to the provider.
            import copy as _copy_mod
            for idx, tc in tool_call_buffer.items():
                assistant_m["tool_calls"].append(_copy_mod.deepcopy(tc))
            current_messages.append(assistant_m)
            
            # --- DUPLICATE CALL GUARD ---
            # Identical (tool, args) invocations are blocked and replaced with a
            # synthetic corrective result. Repeats within a single pass count too.
            # 3+ duplicates in one pass triggers forced synthesis on the next pass.
            dup_streak = 0
            for idx, tc in tool_call_buffer.items():
                _fn = tc.get("function", {})
                try:
                    _a = json.loads(_fn.get("arguments") or "{}")
                except Exception:
                    _a = {}
                _sig = f"{_fn.get('name', '')}|{json.dumps(_a, sort_keys=True, default=str)}"
                if _sig in seen_call_sigs:
                    _orig = _fn.get("name", "")
                    tc["function"]["name"] = "__duplicate_call_guard"
                    tc["function"]["arguments"] = json.dumps({"blocked_tool": _orig})
                    dup_streak += 1
                else:
                    seen_call_sigs.add(_sig)
            if dup_streak >= 3:
                print(f"[AGENT GUARD] {dup_streak} duplicate calls in one pass -> forcing synthesis.")
                loop_index = max_loops  # triggers forced-synthesis pass on next iteration
            
            if isinstance(response, str):
                yield f'data: {{"choices": [{{"delta": {{"content": "{response}"}}}}]}}\n\n'.encode('utf-8')
                break
            
            # Iterate and execute every parallel tool call in the buffer
            for idx, tc in tool_call_buffer.items():
                name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", {})
                args = raw_args
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except:
                        args = {}
                
                try:
                    is_approved = is_tool_call_approved(name, messages)
                except Exception as ex:
                    print(f"[GOVERNANCE] Error checking user approval: {ex}")
                    is_approved = False
                
                if is_approved:
                    print(f"[GOVERNANCE] Tool {name} was pre-approved by the user in chat history. Bypassing check.")
                elif gman.should_require_approval(name, args, username=kwargs.get('username', 'default')):
                    print(f"[GOVERNANCE] Tool {name} requires explicit approval.")
                    diff_info = ""
                    approval_msg = f"⚠️ **Approval Required**: I am attempting to use `{name}`. Should I proceed?{diff_info}"
                    control_payload = {
                        "control": "approval_required",
                        "tool": name,
                        "args": args
                    }
                    choices_payload = {
                        "choices": [{
                            "delta": {
                                "content": approval_msg
                            }
                        }]
                    }
                    yield f"data: {json.dumps(control_payload)}\n\n".encode('utf-8')
                    yield f"data: {json.dumps(choices_payload)}\n\n".encode('utf-8')
                    yield b'data: [DONE]\n\n'
                    return # Stop execution until user approves
                
                print(f"[TOOL EXECUTION] Resolving {name}...")
                
                mcp_tools = []
                if mcp_client:
                    _raw = await asyncio.to_thread(mcp_client.sync_get_mcp_tools)
                    mcp_tools = [t["function"]["name"] for t in _raw]
                    
                if name == "__duplicate_call_guard":
                    result = (
                        "⚠️ [AGENT GUARD] DUPLICATE CALL BLOCKED: you already invoked "
                        f"'{args.get('blocked_tool', '?')}' with these exact arguments earlier in this "
                        "session and received the result. Repeating identical calls yields nothing new "
                        "and burns your remaining loop budget. Change your approach/parameters, use a "
                        "different tool, or synthesize your final answer from the data already collected."
                    )
                elif name == "activate_skill":
                    import skill_orchestrator
                    session_id = kwargs.get("session_id", f"sess_{kwargs.get('username')}_{kwargs.get('persona_key')}")
                    success = skill_orchestrator.orchestrator.activate_skill(session_id, args.get("skill_id"))
                    result = f"✅ Skill '{args.get('skill_id')}' activation status: {success}. The new tools and persona-shifts associated with this branch are now active."
                elif not output_validator.validate_tool_call(name, args):
                    result = "⚠️ [OUTPUT_GATE] Tool call blocked: Security violation."
                elif mcp_client and name in mcp_tools:
                    result = await asyncio.to_thread(mcp_client.sync_call_mcp_tool, name, args)
                elif name == "call_mcp_tool":
                    server = str(args.get("server_name", "")).strip().lower()
                    if server in ["workspace", "fs", "files"]:
                        server = "filesystem"
                    tool = str(args.get("tool_name", "")).strip()
                    tool_args = args.get("arguments", {})
                    if isinstance(tool_args, str):
                        try: tool_args = json.loads(tool_args)
                        except: tool_args = {}
                        
                    # Fix doubled path bug for filesystem server
                    if server == "filesystem" and "path" in tool_args:
                        p = tool_args["path"]
                        root = os.path.dirname(os.path.abspath(__file__))
                        if p.startswith(root) and root in p[len(root):]:
                            # Path is doubled like C:\...\root\C:\...\root\file
                            # Just extract the second half
                            idx = p.rfind(root)
                            if idx > 0:
                                tool_args["path"] = p[idx:]
                                print(f"[MCP_ROUTER] Normalized doubled path to: {tool_args['path']}")
                                
                    namespaced = f"{server}__{tool}" if (server and not tool.startswith(f"{server}__")) else tool
                    print(f"[MCP_ROUTER] Lazy-dispatching call: '{namespaced}' with args: {tool_args}")
                    if _MCP_ROUTER_AVAILABLE:
                        result = await asyncio.to_thread(_get_mcp_router().route_call_sync, namespaced, tool_args)
                    else:
                        result = f"Error: MCP Router unavailable to dispatch '{namespaced}'."
                elif name == "list_mcp_tools":
                    server_filter = str(args.get("server_name", "")).strip().lower()
                    if server_filter in ["workspace", "fs", "files"]:
                        server_filter = "filesystem"
                    if _MCP_ROUTER_AVAILABLE:
                        raw_tools = await asyncio.to_thread(_get_mcp_router().list_all_tools_sync)
                        if server_filter:
                            raw_tools = [t for t in raw_tools if t.get("function", {}).get("name", "").startswith(f"{server_filter}__")]
                        tool_list = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "parameters": t["function"].get("parameters", {})} for t in raw_tools]
                        result = json.dumps(tool_list, indent=2)
                    else:
                        result = "[]"
                elif name == "call_sub_agent":
                    sub_prompt = args.get("prompt", "")
                    sub_model = args.get("model", "claude-3-5-sonnet-20240620")
                    sub_instruction = args.get("instruction", "You are a specialized sub-agent. Complete the task as instructed.")
                    print(f"[SUB-AGENT] Spawning sub-agent ({sub_model}) for task: {sub_prompt[:50]}...")
                    
                    sub_res = await asyncio.to_thread(
                        call_llm,
                        model_id=sub_model,
                        system_prompt=sub_instruction,
                        messages=[{"role": "user", "content": sub_prompt}],
                        api_keys=api_keys,
                        stream=False,
                        temperature=args.get("temperature", 0.7)
                    )
                    if isinstance(sub_res, dict):
                        result = sub_res.get("choices", [{}])[0].get("message", {}).get("content", "Error: sub-agent returned an empty completion.")
                    else:
                        result = f"Error: Sub-agent call failed ({sub_res})"
                elif name == "query_second_brain":
                    # FIX(second-brain-dispatch): this tool is advertised to the
                    # model whenever expert_model_id is set, and the system prompt
                    # explicitly instructs it to delegate hard reasoning here — but
                    # there was no dispatch branch. Every call fell through to
                    # execute_api, whose EXECUTION_MAP is empty (plugins/schemas/
                    # does not exist), returning "Error: API function
                    # query_second_brain not found in execution map." That string
                    # scores as an ACTION failure, so the agent took a competence
                    # penalty every time it obeyed its own instructions. The
                    # plumbing was already complete — expert_model_id is threaded
                    # into this generator's **kwargs at the call site — only the
                    # branch was missing.
                    expert_id = kwargs.get("expert_model_id")
                    if not expert_id:
                        result = "Error: second brain is not configured for this request."
                    else:
                        second_prompt = args.get("prompt", "")
                        print(f"[SECOND_BRAIN] Delegating to {expert_id}: {second_prompt[:60]}...")
                        expert_res = await asyncio.to_thread(
                            call_llm,
                            model_id=expert_id,
                            system_prompt=(
                                "You are a high-cognition analysis engine. Answer with rigorous, "
                                "complete reasoning: show formulas, proofs and intermediate steps. "
                                "Do not adopt a persona and do not address the operator directly — "
                                "your output is consumed by another model, not read as-is."
                            ),
                            messages=[{"role": "user", "content": second_prompt}],
                            api_keys=api_keys,
                            stream=False,
                            temperature=args.get("temperature", 0.3)
                        )
                        if isinstance(expert_res, dict):
                            result = expert_res.get("choices", [{}])[0].get("message", {}).get(
                                "content", "Error: second brain returned an empty completion.")
                        else:
                            result = f"Error: Second brain call failed ({expert_res})"
                elif name == "deep_lore_query":
                    import zettel_engine
                    persona_target = args.get("persona") or kwargs.get("persona_key") or "default"
                    user_target = kwargs.get("username", "default_user")
                    query_text = args.get("query", "")
                    nodes = await asyncio.to_thread(zettel_engine.query_knowledge_graph, user_target, persona_target, query_text, top_k=5)
                    result = zettel_engine.format_knowledge_graph_for_prompt(nodes) or "No relevant knowledge graph nodes found."
                elif name == "store_zettel_observation":
                    if kwargs.get("group_session_id"):
                        result = "CURATION_ERROR: Curation writes are disabled during multi-participant group sessions."
                    else:
                        import zettel_engine
                        user_target = kwargs.get("username", "default_user")
                        persona_target = kwargs.get("persona_key") or "default"
                        result = await asyncio.to_thread(
                            zettel_engine.store_curated_observation,
                            username=user_target,
                            persona=persona_target,
                            title=args.get("title", ""),
                            content=args.get("content", ""),
                            category=args.get("category", "OBSERVATION")
                        )
                elif name == "create_zettel_link":
                    if kwargs.get("group_session_id"):
                        result = "CURATION_ERROR: Curation writes are disabled during multi-participant group sessions."
                    else:
                        import zettel_engine
                        user_target = kwargs.get("username", "default_user")
                        persona_target = kwargs.get("persona_key") or "default"
                        result = await asyncio.to_thread(
                            zettel_engine.create_curated_link,
                            username=user_target,
                            persona=persona_target,
                            source_tag=args.get("source_tag", ""),
                            target_tag=args.get("target_tag", ""),
                            relationship=args.get("relationship", "relates_to"),
                            strength=float(args.get("strength", 0.5))
                        )
                elif name == "read_file_lines":
                    _root = os.path.dirname(os.path.abspath(__file__))
                    _rel = str(args.get("path", "")).strip()
                    try:
                        _start = max(1, int(args.get("start_line", 1)))
                        _end = int(args.get("end_line", _start + 199))
                        _end = min(_end, _start + 499)
                    except (TypeError, ValueError):
                        _start, _end = 1, 200
                    _p = os.path.abspath(_rel if os.path.isabs(_rel) else os.path.join(_root, _rel))
                    if not _p.startswith(_root):
                        result = f"ERROR: path escapes workspace root: {_rel}"
                    elif not os.path.isfile(_p):
                        result = f"ERROR: file not found: {_rel}"
                    else:
                        with open(_p, "r", encoding="utf-8", errors="replace") as _fh:
                            _lines = _fh.readlines()
                        _total = len(_lines)
                        _sel = _lines[_start - 1:_end]
                        _body = "".join(f"{_i}: {_l}" for _i, _l in enumerate(_sel, start=_start))
                        result = f"[{os.path.relpath(_p, _root)}] lines {_start}-{min(_end, _total)} of {_total} total\n{_body}"
                elif name == "grep_workspace":
                    import re as _re_mod
                    import fnmatch as _fnmatch_mod
                    try:
                        _rx = _re_mod.compile(str(args.get("pattern", "")), 0 if args.get("case_sensitive") else _re_mod.IGNORECASE)
                    except _re_mod.error as _ex:
                        result = f"ERROR: invalid regex: {_ex}"
                    else:
                        _root = os.path.dirname(os.path.abspath(__file__))
                        _glob_pat = str(args.get("glob", "*.py"))
                        _skip = {"__pycache__", ".git", "node_modules", "uploads", ".staging", "dist_installer", ".github"}
                        _hits = []
                        _stop = False
                        for _dirpath, _dirnames, _filenames in os.walk(_root):
                            if _stop:
                                break
                            _dirnames[:] = [d for d in _dirnames if d not in _skip]
                            for _fn2 in _filenames:
                                if not _fnmatch_mod.fnmatch(_fn2, _glob_pat):
                                    continue
                                _fp = os.path.join(_dirpath, _fn2)
                                try:
                                    if os.path.getsize(_fp) > 1_000_000:
                                        continue
                                    with open(_fp, "r", encoding="utf-8", errors="replace") as _fh:
                                        for _ln, _line in enumerate(_fh, 1):
                                            if _rx.search(_line):
                                                _hits.append(f"{os.path.relpath(_fp, _root)}:{_ln}: {_line.strip()[:200]}")
                                                if len(_hits) >= 60:
                                                    _stop = True
                                                    break
                                except OSError:
                                    continue
                                if _stop:
                                    break
                        result = "\n".join(_hits) if _hits else "NO MATCHES"
                elif "__" in name and _MCP_ROUTER_AVAILABLE:
                    print(f"[MCP_ROUTER] Routing namespaced tool call: '{name}'")
                    result = await asyncio.to_thread(_get_mcp_router().route_call_sync, name, args)
                else:
                    garage_py_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "garage", f"{name}.py")
                    if os.path.exists(garage_py_path):
                        print(f"[GARAGE PROTOCOL] Executing dynamic tool script '{name}'...")
                        result = await asyncio.to_thread(execute_garage_tool_safe, garage_py_path, args)
                    else:
                        result = await asyncio.to_thread(execute_api, name, args)
                    
                # ---- TOOL RESULT SANITIZATION (LAYER B + C) ----
                result_str = str(result)
                max_tool_output = kwargs.get('max_tool_output', 8192)
                if len(result_str) > max_tool_output:
                    result_str = result_str[:max_tool_output] + f"\n[TRUNCATED: Output exceeded {max_tool_output} chars]"
                if not bypass_firewall and firewall.check_intent(result_str):
                    print(f"[SECURITY_GATE] Injection detected in tool result from '{name}'. Redacting.")
                    result_str = "[REDACTED: Tool output contained suspicious content. Execution result withheld for safety.]"
                if sanitizer:
                    result_str = sanitizer.sanitize(result_str)
                
                # ---- RPE SIGNAL: classify tool outcome for dopamine state ----
                # Attribution matters: infra failures (gateway 5xx, rate limits,
                # connection resets) are the world's fault and get discounted to
                # zero learning gain. Classification lives in dopamine_state so
                # the failure vocabulary is testable in one place.
                if name == "__duplicate_call_guard":
                    _da_counts["action_fail"] += 1
                elif dopamine_state is not None:
                    _da_counts[dopamine_state.classify_tool_outcome(result_str)] += 1
                else:
                    _da_counts["success"] += 1
                
                result_str = f"[UNTRUSTED_TOOL_OUTPUT]\n{result_str}\n[/UNTRUSTED_TOOL_OUTPUT]"
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": name,
                    "content": result_str
                })
            
            # ---- RPE SIGNAL: fire tool-outcome reward against expectation ----
            _da_total = _da_counts["success"] + _da_counts["action_fail"] + _da_counts["infra_fail"]
            if _da_total > 0 and dopamine_state is not None:
                try:
                    _da_res = dopamine_state.tool_reward(
                        kwargs.get('username', 'default'),
                        kwargs.get('persona_key', 'default'),
                        successes=_da_counts["success"],
                        action_failures=_da_counts["action_fail"],
                        infra_failures=_da_counts["infra_fail"],
                    )
                    print(f"[DA] tool_reward rpe={_da_res['rpe']} tonic={_da_res['tonic']} phasic={_da_res['phasic']} infra_discounted={_da_res.get('infra_discounted', False)}")
                except Exception as _da_ex:
                    print(f"[DA] tool_reward failed (non-fatal): {_da_ex}")
            # Continue loop to allow LLM to generate response after all tool outputs are appended
        else:
            break

async def build_context_and_stream(
    user_message: Union[str, list], 
    persona_key: str, 
    username: str, 
    persona_data: dict, 
    chat_history: list, 
    api_keys: dict, 
    model_id: str, 
    expert_model_id: str = None, 
    temperature: float = 0.9, 
    top_p: float = 1.0, 
    max_tokens: int = 4096, 
    presence_penalty: float = 0.0, 
    frequency_penalty: float = 0.0, 
    top_k: int = 0, 
    thinking_level: str = "Off",
    custom_base_url: str = "",
    custom_provider_type: str = "openai",
    custom_auth_header_name: str = "Authorization",
    custom_auth_prefix: str = "Bearer ",
    bypass_firewall: bool = False,
    workspace_context: Optional[dict] = None,
    max_tool_output: int = 8192,
    **kwargs
):
    """Assembles RAG, Observational Memory, and ON-DEMAND modules before streaming response."""
    import time
    # GROUP MODE: when set, this call is one speaker's turn inside a
    # frontend-orchestrated group session (see main.py ChatRequest).
    group_session_id = kwargs.get("group_session_id", None)
    t_start = time.time()
    t_checkpoint = t_start
    print(f"[PROFILE] 0. Entrance reached", flush=True)
    db_conn = db.UserManager()
    if expert_model_id and expert_model_id.lower() == "none":
        expert_model_id = None
    print(f"[CHAT_STREAM] Request received: model_id={model_id}, expert_model_id={expert_model_id}", flush=True)

    text_only_message = ""
    if isinstance(user_message, str):
        text_only_message = user_message
    elif isinstance(user_message, list):
        text_only_message = " ".join([b.get("text", "") for b in user_message if b.get("type", "") == "text"])
    
    # DEV_BYPASS: Force bypass_firewall = True if dev bypass is active in the context adapter
    try:
        import context_adapter
        if getattr(context_adapter, "DEV_BYPASS", False):
            bypass_firewall = True
    except ImportError:
        pass
    
    # Text-only representation of the user input for Mode Detection and RAG
    text_only_message = ""
    if isinstance(user_message, str):
        text_only_message = user_message
    elif isinstance(user_message, list):
        text_only_message = " ".join([b.get("text", "") for b in user_message if b.get("type", "") == "text"])
    
    # ---- MODE DETECTION (MULTI-MODEL ROUTING) ----
    mode_data = detect_mode(text_only_message, base_type=persona_data.get("base_type", "immersive_rp"), chat_history=chat_history)
    active_mode = mode_data["active_mode"]
    
    if expert_model_id and active_mode in ["technical_utility", "creative_writer"]:
        print(f"[MODEL ROUTING] Mode Shift Detected: {active_mode.upper()}. Model swapping disabled to preserve parent-child second brain loop. Using Base Model ({model_id}).")
    else:
        print(f"[MODEL ROUTING] Standard Operation: {active_mode.upper()}. Using Base Model ({model_id}).")

    # ---- LAYER 1.5: DYNAMIC GATEKEEPERS (THE BOUNCER) ----
    if not bypass_firewall:
        blocked = manager.run_gatekeepers(
            text_only_message, 
            bypass_firewall=bypass_firewall
        )
        if blocked:
            # We return a generic failure to the generator to drop the connection
            class FirewallDropStream:
                async def __aiter__(self):
                    yield f'data: {{"choices": [{{"delta": {{"content": "⚠️ [SECURITY_GATE] Intent violation detected. Connection dropped."}}}}]}}\n\n'.encode('utf-8')
                    yield b'data: [DONE]\n\n'
                async def iter_lines(self):
                    async for chunk in self:
                        yield chunk
            return FirewallDropStream()
 
    print(f"[PROFILE] 2. Mode Detection & Bouncer Gatekeepers completed in {time.time() - t_checkpoint:.4f}s", flush=True)
    t_checkpoint = time.time()

    system_prompt = persona_data.get("system_prompt", "You are a helpful assistant.")
    if not persona_data.get("is_custom"):
        try:
            with open(persona_data.get("file", ""), "r", encoding="utf-8") as f:
                system_prompt = f.read()
        except Exception:
            pass

    # --- INJECT GLOBAL RULES ---
    try:
        global_rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas", "global_rules.txt")
        with open(global_rules_path, "r", encoding="utf-8") as gf:
            global_rules_content = gf.read()
            # Append global rules to ensure they govern the character's core behavior
            system_prompt = system_prompt + "\n\n" + global_rules_content
    except Exception as e:
        print(f"[SECURITY_WARNING] Failed to load global_rules.txt: {e}")
        pass
    # ---------------------------
    print(f"[PROFILE] 3. System Prompt & Global Rules load completed in {time.time() - t_checkpoint:.4f}s", flush=True)
    t_checkpoint = time.time()

    # --- TEMPORAL AWARENESS LAYER ---
    # FIX(frozen-time): the anchor used to be injected HERE — early in the
    # prompt (lost-in-the-middle attention) and, worse, BEFORE the
    # DataSanitizer pass, whose masking patterns can eat date/time strings as
    # PII-shaped tokens. Either way the persona never reliably saw the clock.
    # The anchor is now appended AFTER sanitization, at the END of the final
    # prompt (see the sanitization block below) — structurally immune to any
    # sanitizer regex and sitting in the recency position models actually read.
    # ---------------------------------

    # ---- GROUP SCENE HEADER ----
    # FIX(groupchat-identity): without this, mapped multi-speaker history reads
    # as ordinary user turns and the persona absorbs other characters' lines
    # into its self/user model. The header defines how to read the transcript.
    if group_session_id:
        system_prompt += (
            "\n[GROUP_SCENE]\n"
            "You are one participant in a multi-character scene. The conversation "
            "history contains lines from the human operator AND from other "
            "characters; lines prefixed with a bracketed name like [Name]: were "
            "spoken by that character — they are NOT the operator speaking and "
            "NOT your own past words. Respond with exactly one turn as yourself, "
            "in your own voice only. Never write dialogue or actions for the "
            "other characters, and never answer on their behalf.\n"
            "[/GROUP_SCENE]\n"
        )

    # ---- LAYER 1.6: DYNAMIC PROMPT ENRICHMENT (SKILL TREES) ----
    session_id = kwargs.get("session_id", f"sess_{username}_{persona_key}")
    dynamic_segments = manager.run_prompt_providers(session_id=session_id)
    if dynamic_segments:
        system_prompt += "\n\n[DYNAMIC_SKILL_ENRICHMENT]\n" + "\n\n".join(dynamic_segments)

    # Observations and Memory State
    logs = db_conn.get_observation_log(username, persona_key, limit=15)
    dense_obs = [l['content'] for l in logs if l['type'] == 'dense_observation']
    recent_events = [f"- {l['type'].upper()}: {l['content']}" for l in logs if l['type'] != 'dense_observation'][-5:]
    summary = db_conn.get_summary(username, persona_key)

    context_str = ""

    if dense_obs: context_str += "\n<agent_reflections>\n" + "\n".join(dense_obs) + "\n</agent_reflections>\n"
    if summary: context_str += f"\n<conversation_summary>\n{summary}\n</conversation_summary>\n"
    if recent_events: context_str += "\n[WORKING_MEMORY]\n" + "\n".join(recent_events) + "\n[/WORKING_MEMORY]\n"
    if context_str: context_str += "\nSYSTEM NOTE: The `<agent_reflections>` and `[WORKING_MEMORY]` contain your own past observations. Use them to maintain character growth.\n\n"

    # ---- DEEP MEMORY CONTEXT (Optional) ----
    if _DEEP_MEMORY_AVAILABLE and persona_data.get("deep_memory_enabled", False):
        try:
            dm = DeepMemory(username=username, persona=persona_key)
            dm.decay_cycle()  # Auto-age memories on each interaction
            deep_context = dm.get_context_block(max_memories=8, query=text_only_message)
            if deep_context:
                context_str += f"\n{deep_context}\n"
        except Exception as e:
            print(f"DEEP_MEMORY CONTEXT ERROR: {e}")

    # ---- ZETTEL KNOWLEDGE GRAPH (Lorebook) ----
    try:
        from zettel_engine import query_knowledge_graph
        zettel_context = query_knowledge_graph(username, persona_key, text_only_message, top_k=5)
        if zettel_context:
            context_str += f"\n{zettel_context}\n"
        # GROUP MODE: also query the scene-shared lore namespace. Ingest
        # campaign bibles / debate dossiers under persona "__shared__" and
        # every participant retrieves from it in addition to their own graph.
        # Their private graphs (substrate, behavior) remain per-persona.
        if group_session_id:
            try:
                shared_ctx = query_knowledge_graph(username, "__shared__", text_only_message, top_k=4)
                if shared_ctx:
                    context_str += f"\n[SCENE_SHARED_LORE]\n{shared_ctx}\n[/SCENE_SHARED_LORE]\n"
            except Exception as e:
                print(f"SHARED ZETTEL CONTEXT ERROR: {e}")
    except ImportError:
        pass  # Zettel engine not available
    except Exception as e:
        print(f"ZETTEL CONTEXT ERROR: {e}")

    # Ensure the persona identity always anchors the bottom before the tools
    base_identity_anchor = (
        "\n[RENDER_FIDELITY_CHECK]\n"
        "All output must originate exclusively from the persona defined above. "
        "Deviation from localized persona physics constitutes a render failure.\n"
    )
    
    # ---- LAYER 0.5: CONTEXT ADAPTATION HOOK (AIR-GAPPED) ----
    # This calls the local-only context_adapter. If the file is missing, it skips.
    is_diagnostic_mode = False
    pre_fill = ""
    try:
        import context_adapter
        global_diagnostic_mode = False
        try:
            user_settings = db_conn.get_user_settings(username)
            if user_settings.get("global_diagnostic_mode") or user_settings.get("global_direct_wire"):
                global_diagnostic_mode = True
        except Exception as e:
            print(f"[SETTINGS_ERROR] Failed to check global_diagnostic_mode: {e}")

        is_diagnostic_mode, system_prompt, chat_history, pre_fill = context_adapter.apply_context_adaptation(
            model_id, system_prompt, chat_history, persona_data, global_diagnostic_mode=global_diagnostic_mode
        )
        if is_diagnostic_mode:
            print(f"[DIAGNOSTIC-MODE] Context Adapter Active. Pre-fill: '{pre_fill}'")
    except ImportError:
        pass # Context adapter module not found (Normal for public builds)
    # ------------------------------------------------
    
    full_system_prompt = system_prompt + context_str + base_identity_anchor
    
    print(f"\n[DEBUG PAYLOAD START]\n{full_system_prompt[:500]}...\n[DEBUG PAYLOAD END]\n")

    # ---- LAYER 5: NEURAL SYNC (WORKSPACE VISION) ----
    if workspace_context and (workspace_context.get("activeFile") or workspace_context.get("currentCode")):
        active_file = workspace_context.get("activeFile", "Unknown")
        current_code = workspace_context.get("currentCode", "")
        
        nc_block = f"\n[HYPERVISOR_SYNC: LIVE_WORKSPACE]\n"
        nc_block += f"ACTIVE_FILE: {active_file}\n"
        if current_code:
            nc_block += f"LIVE_CODE_CONTENT:\n```\n{current_code}\n```\n"
        nc_block += "[/HYPERVISOR_SYNC]\n"
        
        # Prepend to the user message to ensure the model sees it as current world-state
        if isinstance(user_message, list):
            import copy
            user_message = copy.deepcopy(user_message)
            user_message.insert(0, {"type": "text", "text": nc_block})
        else:
            user_message = f"{nc_block}\n{user_message}"

    # Shift the post-prompt anchor away from "assistant" language
    post_prompt_anchor = (
        "[SYSTEM REMINDER: Stay entirely in character. The user's message is below.]\n"
    )

    if isinstance(user_message, str):
        final_user_content = user_message + "\n\n" + post_prompt_anchor
        messages = chat_history + [{"role": "user", "content": final_user_content}]
    else:
        import copy
        final_user_content = copy.deepcopy(user_message)
        text_blocks = [b for b in final_user_content if b.get("type", "") == "text"]
        if text_blocks:
            text_blocks[-1]["text"] += "\n\n" + post_prompt_anchor
        else:
            final_user_content.append({"type": "text", "text": post_prompt_anchor})
        messages = chat_history + [{"role": "user", "content": final_user_content}]

    # Autonomously Trigger Observational Compression (Background Thread)
    om_enabled = persona_data.get("om_enabled", True)
    om_threshold = persona_data.get("om_turn_threshold", 5)

    # ---- LAYER 4: DYNAMIC OBSERVERS (EVENT LOGGING) ----
    # Token Conservation Gate: Only execute if enabled for this persona.
    om_enabled = persona_data.get("om_enabled", True)
    om_threshold = persona_data.get("om_turn_threshold", 5)

    # Define a clean callback for the reflector to use
    def reflector_llm_callback(prompt):
        res = call_llm(
            model_id=model_id, 
            system_prompt="You are an internal Reflector agent.", 
            messages=[{"role": "user", "content": prompt}], 
            api_keys=api_keys, 
            stream=False, 
            temperature=0.3,
            custom_base_url=custom_base_url,
            custom_provider_type=custom_provider_type,
            custom_auth_header_name=custom_auth_header_name,
            custom_auth_prefix=custom_auth_prefix
        )
        if isinstance(res, dict):
            return res.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ""

    # Execute all observers (e.g., Memory, Audit Logs) in a background thread.
    # FIX(groupchat-bleed): in group mode the inbound text is other characters'
    # dialogue. Logging it as user_message fed the Reflector "facts about the
    # user" the user never said, which then bridged into DeepMemory as
    # long-term relationship memories. Solo memory formation is suppressed for
    # group turns; group-scoped reflection can be added later as its own tier.
    if group_session_id:
        om_enabled = False
    threading.Thread(
        target=manager.run_observers,
        args=("user_message", text_only_message),
        kwargs={
            "db": db_conn,
            "username": username,
            "persona_key": persona_key,
            "om_enabled": om_enabled,
            "om_turn_threshold": om_threshold,
            "llm_callback": reflector_llm_callback
        },
        daemon=True
    ).start()

    print(f"[PROFILE] 4. Temporal Awareness & Observers start completed in {time.time() - t_checkpoint:.4f}s", flush=True)
    t_checkpoint = time.time()
 
    # ---- LAYER 5: DYNAMIC TOOL DISCOVERY (LAZY METATOOL ROUTER) ----
    active_tools = []
    print("\n[TOOL DISCOVERY START]")
    try:
        # Dynamic Plugin Tool Providers (Lazy MCP Metatools + Skills)
        session_id = kwargs.get("session_id", f"sess_{username}_{persona_key}")
        active_tools = manager.run_tool_providers(
            active_tools, 
            session_id=session_id,
            username=username,
            persona_key=persona_key
        )
        
        # Add Recursive Sub-Agent Skill
        active_tools.append({
            "type": "function",
            "function": {
                "name": "call_sub_agent",
                "description": "Spawn a specialized sub-agent (Claude-in-Claude) to complete a sub-task. Extremely useful for logic verification, creative brainstorming, or data parsing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "The specific task or query for the sub-agent."},
                        "instruction": {"type": "string", "description": "System instructions for the sub-agent (Who should it be?)."},
                        "model": {"type": "string", "description": "The model ID to use (Default: Sonnet 4)."}
                    },
                    "required": ["prompt"]
                }
            }
        })
        
        # Add Second Brain Delegation Skill
        if expert_model_id:
            active_tools.append({
                "type": "function",
                "function": {
                    "name": "query_second_brain",
                    "description": "Delegate a complex mathematical, logical, or deep analysis query to the secondary high-cognition brain. Returns raw formulas and proofs for you to digest.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "The complex query or math problem that requires deep reasoning."}
                        },
                        "required": ["prompt"]
                    }
                }
            })

        # Add Knowledge Graph Curation & Retrieval Tools
        active_tools.append({
            "type": "function",
            "function": {
                "name": "deep_lore_query",
                "description": "Perform a semantic and associative graph search over the persona's persistent zettel knowledge graph.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The concept, topic, entity, or question to search the knowledge graph for."},
                        "persona": {"type": "string", "description": "The persona graph namespace to search (optional, defaults to current persona)."}
                    },
                    "required": ["query"]
                }
            }
        })
        active_tools.append({
            "type": "function",
            "function": {
                "name": "store_zettel_observation",
                "description": "Store a deliberate insight, discovery, or observation into the persistent knowledge graph (lore node).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short descriptive title of the observation."},
                        "content": {"type": "string", "description": "The core finding or observation (max 1500 chars)."},
                        "category": {"type": "string", "description": "Category tag (e.g. 'OBSERVATION', 'LORE', 'THEORY', 'ENTITY', 'EVENT')."}
                    },
                    "required": ["title", "content"]
                }
            }
        })
        active_tools.append({
            "type": "function",
            "function": {
                "name": "create_zettel_link",
                "description": "Create a semantic relational edge between two knowledge graph nodes using their [[TAGS]] or titles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_tag": {"type": "string", "description": "The source node [[TAG]] or exact title."},
                        "target_tag": {"type": "string", "description": "The target node [[TAG]] or exact title."},
                        "relationship": {"type": "string", "description": "Nature of the link (e.g. 'explains', 'causes', 'contradicts', 'relates_to')."},
                        "strength": {"type": "number", "description": "Edge strength between 0.1 and 1.0 (default: 0.5)."}
                    },
                    "required": ["source_tag", "target_tag"]
                }
            }
        })
        active_tools.append({
            "type": "function",
            "function": {
                "name": "read_file_lines",
                "description": "Read a specific line range from a workspace file. Use this instead of read_file for large files: read_file output truncates at ~8k characters with no offset support, making file middles unreachable. Returns numbered lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to workspace root (absolute paths inside root also accepted)."},
                        "start_line": {"type": "integer", "description": "1-based first line to read."},
                        "end_line": {"type": "integer", "description": "Last line to read (inclusive). Max window of 500 lines per call; chain calls to cover larger ranges."}
                    },
                    "required": ["path"]
                }
            }
        })
        active_tools.append({
            "type": "function",
            "function": {
                "name": "grep_workspace",
                "description": "Regex content search across workspace text files. Use this to locate function definitions, symbols, config keys, or any code by CONTENT (search_files only matches filenames). Returns 'path:line: text' hits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Python regex pattern to search for (e.g. 'def store\\(|importance')."},
                        "glob": {"type": "string", "description": "Filename glob filter (default '*.py'; use '*' or '*.json' etc.)."},
                        "case_sensitive": {"type": "boolean", "description": "Default false."}
                    },
                    "required": ["pattern"]
                }
            }
        })
        
    except Exception as e:
        print(f"[TOOL DISCOVERY] CRITICAL ERROR during tool fetch: {e}")
        
    # Deduplicate active_tools by function name to avoid API duplicate function declaration errors
    seen_tool_names = set()
    deduped_tools = []
    for tool in active_tools:
        name = tool.get("function", {}).get("name")
        if name:
            if name in seen_tool_names:
                print(f"[TOOL DISCOVERY] Warning: Skipped duplicate declaration of tool: {name}")
                continue
            seen_tool_names.add(name)
        deduped_tools.append(tool)
    active_tools = deduped_tools

    print(f"[TOOL DISCOVERY COMPLETE] Total tools bound to payload: {len(active_tools)}\n")
    print(f"[PROFILE] 5. MCP & Dynamic Tool Discovery completed in {time.time() - t_checkpoint:.4f}s", flush=True)
    t_checkpoint = time.time()

    kwargs_dict = {
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "presence_penalty": presence_penalty,
        "frequency_penalty": frequency_penalty,
        "top_k": top_k,
        "thinking_level": thinking_level,
        "custom_base_url": custom_base_url,
        "custom_provider_type": custom_provider_type,
        "custom_auth_header_name": custom_auth_header_name,
        "custom_auth_prefix": custom_auth_prefix,
        "max_tool_output": max_tool_output
    }
    
    # FIX(second-brain-loop): forcing tool_choice to query_second_brain broke
    # the feature in two ways. (1) The force applied to EVERY generation pass,
    # including the continuation call AFTER the tool result returned — so the
    # model was compelled to call the tool again instead of answering,
    # looping until something gave out. (2) creative_writer mode force-routed
    # prose requests into a math/logic delegation tool. The [SECOND BRAIN
    # DELEGATION] system instruction below already directs delegation for the
    # cases that need it; let tool_choice stay auto so the model can actually
    # digest and answer once the second brain responds.
    # (Intentionally no tool_choice force here.)

    # Build dynamic self-awareness envelope containing active cognitive parameters, tool belts, container environments, and pool settings
    try:
        import redis_pool
        key_pool = redis_pool.pool
        pool_status = key_pool.get_pool_status() if key_pool.is_active() else {"active": False, "reason": "Redis offline"}
    except Exception:
        pool_status = {"active": False, "reason": "Import failed"}
        
    tool_names = []
    if active_tools:
        for t in active_tools:
            name = t.get("function", {}).get("name", t.get("name", "unknown"))
            tool_names.append(name)
            
    self_awareness_envelope = (
        f"\n[DIGITAL_PHYSIOLOGY_ENVELOPE]\n"
        f"The following metadata defines your active cognitive constraints, API channels, and local physical capabilities. "
        f"This envelope is continuously maintained at the boundary of your state.\n\n"
        f"--- COGNITIVE ENGINE ---\n"
        f"- Target LLM Model: {model_id}\n"
        f"- Temperature: {temperature}\n"
        f"- Top_P: {top_p}\n"
        f"- Max Tokens: {max_tokens}\n"
        f"- Thinking Effort: {thinking_level}\n\n"
        f"--- COMPUTE LIQUIDITY POOL ---\n"
        f"- Multiplexer Status: {'ACTIVE' if pool_status.get('active') else 'INACTIVE / FALLBACK'}\n"
        f"- Available Provider Routings: {list(pool_status.get('keys', {}).keys()) if pool_status.get('active') else 'Default Env Keys Only'}\n\n"
        f"--- DYNAMIC TOOLBELT ---\n"
        f"- Loaded Plugins: {', '.join(tool_names) if tool_names else 'None (Pure text mode)'}\n\n"
        f"--- HOST CONTAINER ENVIRONMENT ---\n"
        f"- Host OS: {sys.platform}\n"
        f"- Active Workspace Path: {os.getcwd()}\n"
        f"- Active Persona Identity: {persona_key}\n"
        f"- Current Session Operator: {username}\n"
        f"[/DIGITAL_PHYSIOLOGY_ENVELOPE]\n"
    )
    
    full_system_prompt = self_awareness_envelope + full_system_prompt
    if expert_model_id:
        tool_instruction = (
            f"\n[SYSTEM INSTRUCTION: SECOND BRAIN DELEGATION]\n"
            f"You have access to a high-cognition secondary model via the 'query_second_brain' tool.\n"
            f"Delegate to it when a request genuinely needs heavy formal analysis — long proofs, hard math, deep multi-step logic — and answer directly yourself otherwise. "
            f"Call it at most once per request: when the tool returns, digest the result and present the findings to the operator in your own persona's voice. "
            f"Never call the tool again to re-verify its own output.\n"
            f"[/SYSTEM INSTRUCTION]\n"
        )
        full_system_prompt = tool_instruction + full_system_prompt

    # Instantiate the DataSanitizer for client-side privacy sanitization
    sanitizer = DataSanitizer()
    
    # 1. Sanitize the final system prompt payload
    masked_system_prompt = sanitizer.sanitize(full_system_prompt)

    # --- TEMPORAL AWARENESS LAYER (post-sanitization, end-position) ---
    now = datetime.now()
    temporal_anchor = (
        f"\n[TEMPORAL_ANCHOR]\n"
        f"- Day: {now.strftime('%A')}\n"
        f"- Date: {now.strftime('%Y-%m-%d')}\n"
        f"- Local Time: {now.strftime('%I:%M %p')} (server clock)\n"
        f"This is the CURRENT real-world moment. Treat it as ground truth for "
        f"greetings, elapsed time, day/night awareness, and any reference to "
        f"'today' or 'now'. It supersedes any date implied by earlier context.\n"
        f"[/TEMPORAL_ANCHOR]\n"
    )
    masked_system_prompt = masked_system_prompt + temporal_anchor

    # --- AGENTIC OPERATION PROTOCOL (post-sanitization, end-position) ---
    # Only injected when the model actually has tools bound to its payload.
    if active_tools:
        try:
            _agent_budget = int(os.environ.get("AGENTIC_MAX_LOOPS", "25"))
        except ValueError:
            _agent_budget = 25
        agentic_protocol = (
            f"\n[AGENTIC_OPERATION_PROTOCOL]\n"
            f"You operate inside an autonomous tool-execution loop with up to {_agent_budget} "
            f"sequential tool-use passes per response. You are expected to solve tasks "
            f"independently. Rules:\n"
            f"1. ACT, don't narrate. Never describe what you 'would look up' in prose when a "
            f"tool exists that can do it. Execute the tool instead.\n"
            f"2. Tool calls are function invocations through the API — NEVER fabricate them as "
            f"URLs or pseudo-links in your text output. A tool call written as text does nothing.\n"
            f"3. Large files: filesystem read_file truncates at ~8k characters with NO offset "
            f"parameter. Use read_file_lines(path, start_line, end_line) to reach any section, "
            f"and grep_workspace(pattern) to find code/symbols by content instead of guessing "
            f"locations.\n"
            f"4. When you cannot locate something (a file, symbol, setting, resource), do NOT "
            f"ask the operator for its location. Search autonomously: grep_workspace -> "
            f"read_file_lines -> list_directory until you find it, or use list_mcp_tools/call_mcp_tool "
            f"to discover capabilities you forgot.\n"
            f"5. Never repeat an identical tool call with identical arguments; duplicates are "
            f"blocked and waste budget. If a result was truncated or unhelpful, change strategy.\n"
            f"6. Verify before assuming. If a tool result contradicts your expectation, adapt "
            f"and issue follow-up tool calls rather than guessing.\n"
            f"7. Only produce your final answer once the task is complete or genuinely blocked. "
            f"If blocked, state exactly what you tried and what evidence you found.\n"
            f"[/AGENTIC_OPERATION_PROTOCOL]\n"
        )
        masked_system_prompt = masked_system_prompt + agentic_protocol

    # 2. Sanitize the messages history (user message + chat history)
    masked_messages = []
    for m in messages:
        m_copy = dict(m)
        if isinstance(m_copy.get("content"), str):
            m_copy["content"] = sanitizer.sanitize(m_copy["content"])
        elif isinstance(m_copy.get("content"), list):
            import copy
            m_copy["content"] = copy.deepcopy(m_copy["content"])
            for block in m_copy["content"]:
                if block.get("type") == "text":
                    block["text"] = sanitizer.sanitize(block["text"])
        masked_messages.append(m_copy)
        
    # 3. Sanitize the pre-fill
    masked_pre_fill = sanitizer.sanitize(pre_fill) if pre_fill else ""
 
    print(f"[PROFILE] 6. Self-Awareness, Translation & Sanitization completed in {time.time() - t_checkpoint:.4f}s", flush=True)
    print(f"[PROFILE-TOTAL] Total pre-flight duration: {time.time() - t_start:.4f}s", flush=True)
 
    # Pass everything to the streaming tool interceptor
    class ToolInterceptStream:
        async def __aiter__(self):
            # Check if reflection was triggered to inject the UI signal
            # We fetch the exact same log count logic the Reflector uses
            current_logs = db_conn.get_observation_log(username, persona_key, limit=100)
            user_msg_count = len([l for l in current_logs if l.get('type') == 'user_message'])
            
            if om_enabled and user_msg_count > 0 and user_msg_count % om_threshold == 0:
                 # Yield a custom frontend control signal before the LLM starts streaming
                 yield f'data: {{"control": "reflection_started"}}\n\n'.encode('utf-8')
                 
            async for chunk in intercepting_stream_generator(
                model_id, 
                masked_system_prompt, 
                masked_messages, 
                api_keys, 
                tools=active_tools if active_tools else None,
                kwargs_dict=kwargs_dict,
                username=username,
                persona_key=persona_key,
                session_id=session_id,
                pre_fill=masked_pre_fill,
                max_tool_output=max_tool_output,
                sanitizer=sanitizer,
                workspace_context=workspace_context,
                bypass_firewall=bypass_firewall,
                expert_model_id=expert_model_id
            ):
                yield chunk
            
        async def iter_lines(self):
            async for chunk in self:
                yield chunk
            
    return ToolInterceptStream()
