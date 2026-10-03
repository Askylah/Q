from fastapi import FastAPI, HTTPException, File, UploadFile, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, HTMLResponse
from pydantic import BaseModel
import asyncio
import database as db
import provider_registry
import oauth_engine
import json
import os
import shutil
import uvicorn
from dotenv import load_dotenv
import magic
from typing import Union, List, Dict, Any, Optional
import threading
import workspace_engine as workspace
from workspace_engine import SafeWorkspace, SecurityViolation

from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

load_dotenv()

app = FastAPI(title="PersonaApp API")

@app.on_event("startup")
def configure_executor():
    import concurrent.futures
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=100))
    print("[SYSTEM] ThreadPoolExecutor max_workers set to 100", flush=True)

@app.on_event("startup")
def backup_database_on_start():
    """Verified point-in-time backup before the daemon starts writing.

    The 6-hour floor matters here: uvicorn runs with reload=True (see __main__
    below), so startup hooks re-fire on every source edit. Without the floor this
    would mint a fresh 7MB file on every file save.
    """
    try:
        import app_paths as _paths
        import database as _db
        print(f"[SYSTEM] Storage: {_paths.DB_PATH}", flush=True)
        _db.backup_database(retain=10, min_interval_hours=6.0)
    except Exception as exc:
        print(f"[SYSTEM] Startup backup skipped: {exc}", flush=True)


@app.on_event("startup")
def reap_observations_on_start():
    """Age off daemon-authored observation rows before the daemon starts writing.

    Ordering is deliberate and this hook must stay between the two neighbours it
    has: it runs AFTER backup_database_on_start, so a verified full snapshot of
    the database already exists on disk before anything is deleted, and BEFORE
    start_consciousness_daemon, so the persona's working-memory window is clean
    at the moment the daemon begins emitting into it.

    The reaper keeps its own interval floor (filesystem mtime of a stamp file in
    backups/), so re-firing this hook on every uvicorn source-edit reload is free.
    """
    try:
        import database as _db
        _db.reap_observations()
    except Exception as exc:
        print(f"[SYSTEM] Startup reap skipped: {exc}", flush=True)


@app.on_event("startup")
def verify_governance_registry():
    import sys
    import os
    plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
    if plugins_dir not in sys.path:
        sys.path.append(plugins_dir)
        
    import mcp_router
    from governance_manager import validate_registry_coverage
    from api_parser import load_universal_schemas
    
    # 1. Get all MCP tools
    router = mcp_router.get_router()
    mcp_tools = [t['function']['name'] for t in router.list_all_tools_sync()]
    
    # 2. Get universal tools from api_parser
    universal_tools = [t['function']['name'] for t in load_universal_schemas()]
    
    # 3. Add static tools
    static_tools = ["call_sub_agent", "query_second_brain"]
    
    # 4. Get dynamic garage tools
    garage_tools = []
    garage_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "garage")
    if os.path.exists(garage_dir):
        for f_name in os.listdir(garage_dir):
            if f_name.endswith(".json"):
                garage_tools.append(f_name[:-5])
                
    # Combine all known tools
    all_known_tools = set(mcp_tools + universal_tools + static_tools + garage_tools)
    
    print(f"[SYSTEM] Validating governance registry coverage for {len(all_known_tools)} tools...", flush=True)
    validate_registry_coverage(all_known_tools)
    print("[SYSTEM] Governance registry coverage validated successfully. No unclassified tools found.", flush=True)

@app.on_event("startup")

def start_consciousness_daemon():
    def run_daemon():
        try:
            import subprocess
            import sys
            worker_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stream_worker.py")
            if os.path.exists(worker_path):
                print(f"[SYSTEM] Spawning Consciousness Daemon background process: {worker_path}", flush=True)
                subprocess.Popen([sys.executable, worker_path])
            else:
                print(f"[SYSTEM ERROR] stream_worker.py not found at {worker_path}", flush=True)
        except Exception as e:
            print(f"[SYSTEM ERROR] Failed to spawn Consciousness Daemon: {e}", flush=True)
            
    threading.Thread(target=run_daemon, daemon=True).start()

# ============================================================
# LAYER 3: THE BLACKHOLE (GHOST MODE)
# ============================================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """
    If a payload has even ONE unexpected key, we don't just reject it—we blackhole it.
    No error message. No '400 Bad Request'. We just drop the connection.
    - Rick
    """
    return Response(content=None, status_code=444) # Nginx-style 'No Response' status

# --- WALL NUMBER TWO: THE CORS DIVORCE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CRYPTOGRAPHIC PROFILE AUTHENTICATION ---
import hashlib

class RegisterPayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str
    secret_key: str

class VerifyPayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str
    secret_key: str

async def get_current_user(
    x_profile_username: Optional[str] = Header(None, alias="X-Profile-Username"),
    x_profile_key: Optional[str] = Header(None, alias="X-Profile-Key")
):
    if not x_profile_username or not x_profile_key:
        raise HTTPException(status_code=401, detail="Authentication credentials missing.")
    db_conn = db.UserManager()
    clean_username = x_profile_username.strip()
    clean_key = x_profile_key.strip()
    if not db_conn.verify_profile(clean_username, clean_key):
        raise HTTPException(status_code=401, detail="Invalid profile key or username.")
    return clean_username

# --- MODELS (Hardened) ---
class MessagePayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    role: str
    content: Union[str, List[Dict[str, Any]]]

class GroupMessagePayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    persona_key: str
    persona_name: str
    persona_avatar: str
    role: str
    content: str
    is_observer: bool = False
    
class StreamRequest(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    message: Union[str, List[Dict[str, Any]]]
    chat_history: list = []
    target_model_id: str = "google/gemini-3-flash-preview"
    expert_model_id: str = "google/gemini-3.1-pro-preview"
    temperature: float = 0.9
    top_p: float = 1.0
    top_k: int = 0
    max_tokens: int = 4096
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    thinking_level: str = "Off"
    bypass_firewall: bool = False
    # GROUP MODE CONTRACT: set by the frontend when this invocation is one
    # speaker's turn inside a group session. The backend then (a) does NOT
    # write the inbound message into the persona's SOLO history — the
    # frontend owns group persistence via POST /groupchat/{session_id} —
    # and (b) llm_engine suppresses solo memory formation and injects a
    # scene header (see build_context_and_stream).
    group_session_id: str = None
    active_api_keys: dict = {}
    custom_base_url: str = ""
    custom_provider_type: str = "openai"
    custom_auth_header_name: str = "Authorization"
    custom_auth_prefix: str = "Bearer "
    workspace_context: Optional[Dict[str, Any]] = None
    max_tool_output: int = 8192

class PersonaCreatePayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    original_key: str = ""
    key: str = ""
    name: str
    avatar: str
    tagline: str
    system_prompt: str
    on_demand_file: str = ""
    on_demand_files: list[str] = []
    access_code: str = ""
    om_enabled: bool = True
    om_turn_threshold: int = 5
    deep_memory_enabled: bool = False
    direct_wire: bool = False

class LoreEntryPayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    title: str
    content: str
    active_api_keys: dict = {}

class SettingsPayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    review_policy: str = "ask"
    auto_execute_terminal: int = 0
    active_persona_key: str = ""
    security_level: str = "strict"
    global_direct_wire: int = 1
    daemon_nli_model: Optional[str] = None
    daemon_monologue_model: Optional[str] = None

# --- CORE LOGIC (Decoupled from Streamlit) ---
def load_personas_logic(username: str = None):
    personas = {}
    try:
        # Resolve path to ensure it finds personas.json even if run from parent
        p_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas.json")
        with open(p_path, "r", encoding="utf-8") as f:
            personas = json.load(f)
            
        # Hydrate built-in personas with their text files
        base_dir = os.path.dirname(os.path.abspath(__file__))
        for key, p in personas.items():
            if "file" in p:
                full_path = p["file"] if os.path.isabs(p["file"]) else os.path.join(base_dir, p["file"])
                try:
                    with open(full_path, "r", encoding="utf-8") as prompt_file:
                        p["system_prompt"] = prompt_file.read()
                except Exception:
                    p["system_prompt"] = ""
            
            # Ensure all personas have memory flags (for built-in personas)
            p.setdefault("om_enabled", True)
            p.setdefault("om_turn_threshold", 5)
            p.setdefault("deep_memory_enabled", False)
    except Exception as e:
        print(f"DEBUG: Failed to load personas.json: {e}")
        pass
        
    if username:
        db_conn = db.UserManager()
        custom = db_conn.get_custom_personas(username)
        # Merge dictionaries
        personas = {**personas, **custom}
        
    return personas

from llm_engine import build_context_and_stream

# --- ENDPOINTS ---
@app.get("/health")
def read_root():
    return {"status": "Q Backend is ALIVE on Port 8000.", "cage": "Cage for a God [STABILIZED]"}

@app.post("/auth/register")
def register_profile(payload: RegisterPayload):
    """Registers a profile on the server using a SHA-256 hashed secret key."""
    db_conn = db.UserManager()
    success, message = db_conn.register_profile(payload.username.strip(), payload.secret_key.strip())
    if not success:
         raise HTTPException(status_code=400, detail=message)
    return {"status": "success", "message": message}

@app.post("/auth/verify")
def verify_profile(payload: VerifyPayload):
    """Verifies that a secret key matches the username's hashed key."""
    db_conn = db.UserManager()
    valid = db_conn.verify_profile(payload.username.strip(), payload.secret_key.strip())
    if not valid:
         raise HTTPException(status_code=401, detail="Invalid username or secret key.")
    return {"status": "success", "message": "Attuned successfully."}

# --- CONNECT A PROVIDER ---
class ProviderConnectPayload(BaseModel):
    model_config = {"extra": "forbid"}
    api_key: str
    validate_key: bool = True

@app.get("/providers")
def list_providers(current_user: str = Depends(get_current_user)):
    """Connection status per provider. Never returns key material."""
    return provider_registry.list_status(current_user)

@app.post("/providers/{provider_id}/connect")
def connect_provider(provider_id: str, payload: ProviderConnectPayload, current_user: str = Depends(get_current_user)):
    """Validate (cheap live ping) and store an API key, encrypted at rest."""
    try:
        return provider_registry.connect(current_user, provider_id, payload.api_key, validate=payload.validate_key)
    except provider_registry.ProviderRejected as e:
        raise HTTPException(status_code=400, detail=str(e))
    except provider_registry.ProviderStoreLocked as e:
        # Fail closed: refuse rather than regenerate the master key and orphan stored credentials.
        raise HTTPException(status_code=503, detail=f"Credential store is locked: {e}")

@app.delete("/providers/{provider_id}")
def disconnect_provider(provider_id: str, current_user: str = Depends(get_current_user)):
    if provider_id not in provider_registry.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return {"status": "success", "removed": provider_registry.disconnect(current_user, provider_id)}

# --- OAUTH & EXTERNAL SESSIONS ---
@app.get("/oauth/overview")
def get_oauth_overview(current_user: str = Depends(get_current_user)):
    """Overview of detected external sessions and OAuth connection status."""
    return oauth_engine.get_oauth_status_overview(current_user)

@app.get("/oauth/{provider_id}/start")
def start_oauth(provider_id: str, request: Request, current_user: str = Depends(get_current_user)):
    """Initiates an interactive OAuth PKCE browser flow."""
    base_url = str(request.base_url)
    if provider_id == "openrouter":
        auth_url = oauth_engine.start_openrouter_oauth(current_user, base_url)
        return {"auth_url": auth_url, "provider": "openrouter"}
    else:
        raise HTTPException(status_code=400, detail=f"OAuth start not implemented for {provider_id}")

@app.get("/oauth/callback/openrouter")
def callback_openrouter(code: str, state: str):
    """Callback landing page for OpenRouter OAuth PKCE in popup window."""
    try:
        res = oauth_engine.finish_openrouter_oauth(code, state)
        html = f"""<!DOCTYPE html>
<html>
<head><title>OpenRouter Authorized</title></head>
<body style="background:#0a0a0a;color:#00cc66;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
  <div style="text-align:center;padding:24px;border:1px solid #1a0a2a;border-radius:8px;background:#050505;">
    <h2 style="color:#ff007f;margin-bottom:8px;">OpenRouter Connected!</h2>
    <p style="color:#00cc66;font-size:14px;">Authorization successful. Stored in your encrypted vault.</p>
    <p style="color:#888;font-size:12px;">Closing window...</p>
    <script>
      if (window.opener) {{
        window.opener.postMessage({{ type: 'oauth_complete', provider: 'openrouter', status: 'success' }}, '*');
        setTimeout(() => window.close(), 1200);
      }}
    </script>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)
    except Exception as e:
        err_html = f"""<!DOCTYPE html>
<html>
<head><title>Authorization Failed</title></head>
<body style="background:#0a0a0a;color:#ed4245;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
  <div style="text-align:center;padding:24px;border:1px solid #ed4245;border-radius:8px;background:#050505;">
    <h2>Authorization Failed</h2>
    <p style="font-size:13px;">{str(e)}</p>
  </div>
</body>
</html>"""
        return HTMLResponse(content=err_html, status_code=400)

@app.post("/oauth/import/{provider_id}")
def import_session(provider_id: str, current_user: str = Depends(get_current_user)):
    """Imports an active local CLI/IDE session (e.g. Claude Code or Antigravity Google)."""
    try:
        if provider_id == "anthropic":
            return oauth_engine.import_claude_code_session(current_user)
        elif provider_id == "google":
            return oauth_engine.import_google_session(current_user)
        else:
            raise HTTPException(status_code=400, detail=f"No local session importer for {provider_id}")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/personas")
def get_personas(username: str = "default_user", current_user: str = Depends(get_current_user)):
    """Retrieve all global and custom personas for a user."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    return load_personas_logic(username)

@app.post("/personas")
def create_persona(payload: PersonaCreatePayload, current_user: str = Depends(get_current_user)):
    """Creates a new custom persona in the SQLite database."""
    if payload.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    
    # If editing an existing persona (original_key provided), we use that to anchor the UPDATE.
    # But if they change the name, we also want the ID to remain the same so history links don't break.
    anchor_key = payload.original_key if payload.original_key else payload.key
    
    success, result = db_conn.add_custom_persona(
        username=payload.username,
        old_key=anchor_key,
        new_key=payload.key,
        name=payload.name,
        avatar=payload.avatar,
        tagline=payload.tagline,
        system_prompt=payload.system_prompt,
        access_code=payload.access_code,
        on_demand_file=payload.on_demand_file,
        on_demand_files=payload.on_demand_files,
        om_enabled=payload.om_enabled,
        om_turn_threshold=payload.om_turn_threshold,
        deep_memory_enabled=payload.deep_memory_enabled
    )
    if not success:
        raise HTTPException(status_code=400, detail=result)
    return {"status": "success", "message": "Persona created/updated"}

@app.delete("/personas/{persona_key}")
async def delete_persona(persona_key: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    """Deletes a custom persona from the database."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    user_manager = db.UserManager()
    success = user_manager.delete_custom_persona(username, persona_key)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete persona")
    return {"status": "success", "message": f"Persona {persona_key} deleted"}


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), current_user: str = Depends(get_current_user)):
    """Accepts a file from the frontend UI safely via streamed chunks and verifies MIME type."""
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    # Read the first chunk to check the file signature mathematically using magic
    first_chunk = await file.read(2048)
    if not first_chunk:
        raise HTTPException(status_code=400, detail="Empty file detected.")
        
    mime_type = magic.from_buffer(first_chunk, mime=True)
    allowed_prefixes = ["image/", "text/", "application/pdf", "application/json", "application/msword", "application/vnd.openxmlformats-officedocument"]
    
    if not any(mime_type.startswith(p) for p in allowed_prefixes):
         raise HTTPException(status_code=400, detail=f"Disallowed file type detected by strict magic validation: {mime_type}")
         
    # Securely stream the rest of the chunks to the filesystem 1MB at a time
    with open(file_path, "wb") as buffer:
        buffer.write(first_chunk)
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            buffer.write(chunk)
            
    return {"path": file_path}

@app.get("/chat/{persona_key}")
def get_chat_history(persona_key: str, username: str = "default_user", limit: int = 50, current_user: str = Depends(get_current_user)):
    """Retrieve chat history for a specific persona."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    history = db_conn.get_chat_history(username, persona_key, limit)
    return {"history": history}

@app.post("/chat/{persona_key}")
def save_chat_message(persona_key: str, msg: MessagePayload, current_user: str = Depends(get_current_user)):
    """Save a simulated user or assistant message to the database."""
    if msg.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    
    content_str = json.dumps(msg.content) if isinstance(msg.content, list) else msg.content
    
    success = db_conn.save_message(msg.username, persona_key, msg.role, content_str)
    if not success:
        raise HTTPException(status_code=500, detail="Database insertion failed.")
    return {"status": "success", "message": "Message committed to SQLite."}

@app.post("/chat/{persona_key}/clear")
def clear_chat(persona_key: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    db_conn.clear_chat_history(username, persona_key)
    return {"status": "success", "message": "Chat history cleared."}

@app.delete("/chat/message/{message_id}")
def delete_chat_message(message_id: int, current_user: str = Depends(get_current_user)):
    """Delete a specific message from a chat."""
    db_conn = db.UserManager()
    owner = db_conn.get_message_username(message_id)
    if owner and owner != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    success = db_conn.delete_message(message_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete message.")
    return {"status": "success"}

@app.get("/groupchats/sessions/{username}")
async def get_user_group_sessions(username: str, current_user: str = Depends(get_current_user)):
    """Retrieve all unique group session IDs for a specific user."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    sessions = await asyncio.to_thread(db_conn.get_user_group_sessions, username)
    return {"sessions": sessions}

@app.post("/groupchat/{session_id}")
async def save_group_chat_message(session_id: str, msg: GroupMessagePayload, current_user: str = Depends(get_current_user)):
    """Save a message to the group conversation history."""
    if msg.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    success = await asyncio.to_thread(
        db_conn.save_group_message,
        session_id, msg.username, msg.persona_key, msg.persona_name, 
        msg.persona_avatar, msg.role, msg.content, msg.is_observer
    )
    if not success:
        raise HTTPException(status_code=500, detail="Database insertion failed.")
    return {"status": "success"}

@app.get("/groupchat/{session_id}")
def get_group_chat_history(session_id: str, username: str = "default_user", limit: int = 100, current_user: str = Depends(get_current_user)):
    """Retrieve group chat history."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    history = db_conn.get_group_history(session_id, username, limit)
    return {"history": history}

@app.delete("/groupchat/message/{message_id}")
def delete_group_chat_message(message_id: int, current_user: str = Depends(get_current_user)):
    """Delete a specific message from a group chat."""
    db_conn = db.UserManager()
    owner = db_conn.get_group_message_username(message_id)
    if owner and owner != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    success = db_conn.delete_group_message(message_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete group message.")
    return {"status": "success"}

@app.post("/groupchat/{session_id}/clear")
def clear_group_chat_history(session_id: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    """Clear group chat history."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    success = db_conn.clear_group_history(session_id, username)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear group history.")
    return {"status": "success", "message": "Group chat history cleared."}

@app.post("/personas/{persona_key}/wipe")
def wipe_memory(persona_key: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    """Wipe all semantic memories, summaries, observations, and Zettel data."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    
    # 1. Clear SQLite tables (Memories, Summaries, Observations, Zettel Graph)
    success = db_conn.wipe_memories(username, persona_key)
    
    # 2. Invalidate Zettel in-memory cache
    try:
        import zettel_engine
        zettel_engine.invalidate_zettel_cache(username, persona_key)
    except Exception as e:
        print(f"[WIPE ERROR] Failed to invalidate zettel cache: {e}")

    if success:
        return {"status": "success", "message": "Memories wiped."}
    raise HTTPException(status_code=500, detail="Failed to wipe memories.")

@app.post("/chat/{persona_key}/stream")
async def stream_chat(persona_key: str, req: StreamRequest, current_user: str = Depends(get_current_user)):
    """Generates a streaming response for the given persona and saves the transaction."""
    if req.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    personas = load_personas_logic(req.username)
    
    if persona_key not in personas:
        # Fallback to case-insensitive match
        matched_key = next((k for k in personas if k.lower() == persona_key.lower()), None)
        if matched_key:
            persona_key = matched_key
        else:
            raise HTTPException(status_code=404, detail=f"Persona '{persona_key}' not found. Available keys for this user: {list(personas.keys())}")
    
    persona_data = personas[persona_key]
    
    # Keys: server .env fallback < credentials connected via /providers < per-request override.
    api_keys = provider_registry.resolve_api_keys(current_user, req.active_api_keys)

    # Immediately save the User's message so it doesn't fade on UI reload.
    # FIX(groupchat-bleed): in group mode the "inbound message" is mapped group
    # history / another persona's line — writing it here polluted every
    # invoked persona's SOLO conversation history with group traffic.
    content_str = json.dumps(req.message) if isinstance(req.message, list) else req.message
    db_conn = db.UserManager()
    if not req.group_session_id:
        db_conn.save_message(req.username, persona_key, "user", content_str)
    
    stream_obj = await build_context_and_stream(
        user_message=req.message,
        persona_key=persona_key,
        username=req.username,
        persona_data=persona_data,
        chat_history=req.chat_history,
        api_keys=api_keys,
        model_id=req.target_model_id,
        expert_model_id=req.expert_model_id,
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
        presence_penalty=req.presence_penalty,
        frequency_penalty=req.frequency_penalty,
        top_k=req.top_k,
        thinking_level=req.thinking_level,
        custom_base_url=req.custom_base_url,
        custom_provider_type=req.custom_provider_type,
        custom_auth_header_name=req.custom_auth_header_name,
        custom_auth_prefix=req.custom_auth_prefix,
        bypass_firewall=req.bypass_firewall,
        workspace_context=req.workspace_context,
        max_tool_output=req.max_tool_output,
        group_session_id=req.group_session_id
    )
    
    if isinstance(stream_obj, str):
        # Handle string error returns directly
        async def err_generator():
            yield f'data: {{"choices": [{{"delta": {{"content": "{stream_obj}"}}}}]}}\n\n'
        return StreamingResponse(err_generator(), media_type="text/event-stream")
        
    async def sse_generator():
        # stream_obj is an async generator yielding bytes
        async for chunk in stream_obj:
            if chunk:
                yield chunk
                
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

# ============================================================
# LORE (ZETTEL KNOWLEDGE GRAPH) ENDPOINTS
# ============================================================
from zettel_engine import process_entry as zettel_process_entry

@app.post("/personas/{persona_key}/lore")
def create_lore_entry(persona_key: str, payload: LoreEntryPayload, current_user: str = Depends(get_current_user)):
    """Create a lore entry for a persona. Triggers async background processing."""
    if payload.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    entry_id = db_conn.add_zettel_entry(
        username=payload.username,
        persona=persona_key,
        title=payload.title,
        raw_content=payload.content
    )
    if not entry_id:
        raise HTTPException(status_code=500, detail="Failed to create lore entry")
    
    # Build API keys for the background processing thread
    api_keys = provider_registry.resolve_api_keys(current_user, payload.active_api_keys)
    # Preserved behaviour: lore processing's "universal" slot defaults to OpenRouter.
    if not api_keys.get("universal"):
        api_keys["universal"] = api_keys.get("openrouter", "")
    
    # Process in background thread (chunking + embedding + LLM entity extraction)
    def _bg_process():
        try:
            zettel_process_entry(payload.username, persona_key, entry_id, api_keys)
        except Exception as e:
            print(f"[ZETTEL BG ERROR] {e}")
    
    threading.Thread(target=_bg_process, daemon=True).start()
    
    return {"status": "success", "entry_id": entry_id, "message": "Lore entry created. Processing in background."}

@app.get("/personas/{persona_key}/lore")
def get_lore_entries(persona_key: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    """List all lore entries for a persona."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    entries = db_conn.get_zettel_entries(username, persona_key)
    return {"entries": entries}

@app.put("/personas/{persona_key}/lore/{entry_id}")
def update_lore_entry(persona_key: str, entry_id: str, payload: LoreEntryPayload, current_user: str = Depends(get_current_user)):
    """Update a lore entry and re-trigger processing."""
    if payload.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    success = db_conn.update_zettel_entry(
        username=payload.username,
        persona=persona_key,
        entry_id=entry_id,
        title=payload.title,
        raw_content=payload.content
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update lore entry")
    
    api_keys = provider_registry.resolve_api_keys(current_user, payload.active_api_keys)
    # Preserved behaviour: lore processing's "universal" slot defaults to OpenRouter.
    if not api_keys.get("universal"):
        api_keys["universal"] = api_keys.get("openrouter", "")
    
    def _bg_process():
        try:
            zettel_process_entry(payload.username, persona_key, entry_id, api_keys)
        except Exception as e:
            print(f"[ZETTEL BG ERROR] {e}")
    
    threading.Thread(target=_bg_process, daemon=True).start()
    
    return {"status": "success", "message": "Lore entry updated. Re-processing in background."}

@app.delete("/personas/{persona_key}/lore/{entry_id}")
def delete_lore_entry(persona_key: str, entry_id: str, username: str = "default_user", current_user: str = Depends(get_current_user)):
    """Delete a lore entry and cascade-remove its nodes + links."""
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    success = db_conn.delete_zettel_entry(username, persona_key, entry_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete lore entry")
    return {"status": "success", "message": "Lore entry deleted"}

# ============================================================
# WORKSPACE (LIVE FILESYSTEM) ENDPOINTS
# ============================================================

# Default workspace root: the application directory itself
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))

@app.get("/workspace/tree")
def get_workspace_tree(path: str = ".", current_user: str = Depends(get_current_user)):
    """Fetches the recursive file tree. Root jail is the requested path itself."""
    # Resolve the target path (absolute or relative to app root)
    if os.path.isabs(path):
        target = os.path.abspath(path)
    else:
        target = os.path.join(_APP_ROOT, path)
    
    if not os.path.isdir(target):
        raise HTTPException(status_code=404, detail="Path is not a directory.")
    
    try:
        # The root IS the target — the user can browse everything inside it
        ws = SafeWorkspace(target)
        return ws.get_file_tree(target)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

@app.get("/workspace/file")
def get_workspace_file(path: str, current_user: str = Depends(get_current_user)):
    """Fetches the raw content of a specific file."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Root is the file's parent directory — any path within parent is valid
    root = os.path.dirname(os.path.abspath(path))
    try:
        ws = SafeWorkspace(root)
        content = ws.read_file_content(path)
        return {"content": content}
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

class WorkspaceSave(BaseModel):
    path: str
    content: str

@app.post("/workspace/save")
def save_workspace_file(data: WorkspaceSave, current_user: str = Depends(get_current_user)):
    """Stages a file write for approval. Returns a diff and staging_id — does NOT write yet."""
    root = os.path.dirname(os.path.abspath(data.path))
    try:
        ws = SafeWorkspace(root)
        return ws.stage_write(data.path, data.content)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

class WorkspaceCreate(BaseModel):
    path: str
    item_type: str = "file"

@app.post("/workspace/create")
def create_workspace_item(data: WorkspaceCreate, current_user: str = Depends(get_current_user)):
    # Root is the parent of the item being created
    parent = os.path.dirname(os.path.abspath(data.path))
    try:
        ws = SafeWorkspace(parent)
        return ws.create_item(data.path, data.item_type)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

@app.delete("/workspace/delete")
def delete_workspace_item(path: str, current_user: str = Depends(get_current_user)):
    parent = os.path.dirname(os.path.abspath(path))
    try:
        ws = SafeWorkspace(parent)
        return ws.delete_item(path)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

# --- Staged Write Approval Endpoints ---

@app.post("/workspace/stage/{staging_id}/commit")
def commit_staged_write(staging_id: str, current_user: str = Depends(get_current_user)):
    """Approves a staged write. Backs up the current file (.bak) then commits the change."""
    ws = SafeWorkspace(_APP_ROOT)
    result = ws.commit_staged_write(staging_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result

@app.delete("/workspace/stage/{staging_id}")
def discard_staged_write(staging_id: str, current_user: str = Depends(get_current_user)):
    """Discards a pending staged write without modifying any real files."""
    ws = SafeWorkspace(_APP_ROOT)
    result = ws.discard_staged_write(staging_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result

@app.get("/workspace/staged")
def list_staged_writes(current_user: str = Depends(get_current_user)):
    """Lists all pending staged writes awaiting approval."""
    ws = SafeWorkspace(_APP_ROOT)
    return {"staged": ws.list_staged_writes()}

@app.get("/settings/{username}")
async def get_settings(username: str, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    return db_conn.get_user_settings(username)

class KeyPayload(BaseModel):
    model_config = {"extra": "forbid"}
    provider: str
    key_value: str
    key_id: Optional[str] = None
    proxy_url: Optional[str] = ""

@app.get("/settings/{username}/telemetry")
async def get_settings_telemetry(username: str, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    
    # 1. Redis pool status
    pool_status = {"active": False, "reason": "Redis module not loaded"}
    daemon_heartbeat = None
    try:
        from redis_pool import pool
        pool_status = pool.get_pool_status()
        
        # Check heartbeat
        import redis_client
        if redis_client.is_active():
            hb = redis_client.get("q:daemon:heartbeat")
            if hb:
                daemon_heartbeat = float(hb.decode('utf-8'))
    except Exception as e:
        pool_status = {"active": False, "error": str(e)}
        
    # 2. Database Stats & Observations (Dissonance log)
    node_count = 0
    link_count = 0
    recent_dissonances = []
    
    try:
        import sqlite3
        conn = sqlite3.connect(db.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM zettel_nodes WHERE username=?", (username,))
        node_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username=?)", (username,))
        link_count = c.fetchone()[0]
        
        # Recent observations (dissonances)
        c.execute("""
            SELECT id, persona, event_type, content, reflection_score, timestamp 
            FROM observations 
            WHERE username=? 
              AND event_type IN ('entropic_gap','semantic_contradiction','internal_reflection')
            ORDER BY id DESC LIMIT 10
        """, (username,))
        rows = c.fetchall()
        recent_dissonances = [
            {
                "id": r[0],
                "persona": r[1],
                "event_type": r[2],
                "content": r[3],
                "reflection_score": r[4],
                "timestamp": r[5]
            }
            for r in rows
        ]
        conn.close()
    except Exception as e:
        print(f"Error querying telemetry stats: {e}")
        
    return {
        "redis_pool": pool_status,
        "daemon_heartbeat": daemon_heartbeat,
        "zettel_stats": {
            "node_count": node_count,
            "link_count": link_count
        },
        "recent_dissonances": recent_dissonances
    }

@app.post("/settings/{username}/telemetry/keys")
async def add_pool_key(username: str, payload: KeyPayload, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        success = redis_pool.pool.add_key(payload.provider, payload.key_value, payload.key_id, payload.proxy_url)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/settings/{username}/telemetry/keys/{provider}/{key_id}")
async def delete_pool_key(username: str, provider: str, key_id: str, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        if not redis_pool.pool.is_active():
            raise HTTPException(status_code=400, detail="Redis pool is inactive")
        redis_key = f"q:pool:key:{provider}:{key_id}"
        redis_pool.pool.redis.delete(redis_key)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/settings/{username}/telemetry/keys/{provider}/{key_id}/reset")
async def reset_pool_key(username: str, provider: str, key_id: str, current_user: str = Depends(get_current_user)):
    """
    Return a COOLDOWN/BURNED key to HEALTHY. Exists because a burn used to be
    terminal and delete-and-re-add was the only way back.
    """
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        if not redis_pool.pool.is_active():
            raise HTTPException(status_code=400, detail="Redis pool is inactive")
        restored = redis_pool.pool.unburn_key(provider, key_id)
        if not restored:
            raise HTTPException(status_code=404, detail="No such key in the pool")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ProxyPayload(BaseModel):
    model_config = {"extra": "forbid"}
    url: str

@app.get("/settings/{username}/telemetry/proxies")
async def list_pool_proxies(username: str, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        if not redis_pool.pool.is_active():
            return {"proxies": []}
        keys = redis_pool.pool.redis.keys("q:pool:proxy:*")
        proxies_list = []
        for k in keys:
            data = redis_pool.pool.redis.hgetall(k)
            fields = {key.decode('utf-8'): val.decode('utf-8') for key, val in data.items()}
            k_str = k.decode('utf-8')
            proxy_id = k_str.split(":")[-1]
            proxies_list.append({
                "id": proxy_id,
                "url": fields.get("url", ""),
                "status": fields.get("status", "HEALTHY"),
                "failures": int(fields.get("failures", 0))
            })
        return {"proxies": proxies_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/settings/{username}/telemetry/proxies")
async def add_pool_proxy_endpoint(username: str, payload: ProxyPayload, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        success = redis_pool.pool.add_pool_proxy(payload.url)
        return {"success": success}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/settings/{username}/telemetry/proxies/{proxy_id}")
async def delete_pool_proxy_endpoint(username: str, proxy_id: str, current_user: str = Depends(get_current_user)):
    if username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    try:
        import redis_pool
        if not redis_pool.pool.is_active():
            raise HTTPException(status_code=400, detail="Redis pool is inactive")
        redis_key = f"q:pool:proxy:{proxy_id}"
        redis_pool.pool.redis.delete(redis_key)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/settings")
async def update_settings(payload: SettingsPayload, current_user: str = Depends(get_current_user)):
    if payload.username != current_user:
        raise HTTPException(status_code=403, detail="Username mismatch")
    db_conn = db.UserManager()
    # None = "field not provided" (the daemon model fields). Without this filter
    # every ordinary settings save would overwrite the stored daemon model with NULL.
    db_conn.update_user_settings(payload.username, {k: v for k, v in payload.model_dump().items() if v is not None})
    return {"status": "success"}

# --- STATIC FRONTEND SERVING (For "One-Click" Distributed Releases) ---
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vite-project", "dist")
if os.path.exists(FRONTEND_DIST):
    # Mount the assets directory specifically
    assets_dir = os.path.join(FRONTEND_DIST, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        
    # Catch-all route to serve the React index.html for frontend routing
    @app.get("/{catchall:path}")
    def serve_react_app(catchall: str):
        index_path = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Frontend build missing index.html")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, http="h11", reload=True, reload_excludes=["*labs*", "*labs\\*", "labs\\*", "*lab_exec*"])
