from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import database as db
import json
import os
import shutil
from fastapi.responses import StreamingResponse
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    active_api_keys: dict = {}
    custom_base_url: str = ""
    custom_provider_type: str = "openai"
    custom_auth_header_name: str = "Authorization"
    custom_auth_prefix: str = "Bearer "
    workspace_context: Optional[Dict[str, Any]] = None

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

class LoreEntryPayload(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = "default_user"
    title: str
    content: str
    active_api_keys: dict = {}

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
@app.get("/")
def read_root():
    return {"status": "The Freakshow Backend is ALIVE on Port 8000.", "cage": "Cage for a God [STABILIZED]"}

@app.get("/personas")
def get_personas(username: str = "default_user"):
    """Retrieve all global and custom personas for a user."""
    return load_personas_logic(username)

@app.post("/personas")
def create_persona(payload: PersonaCreatePayload):
    """Creates a new custom persona in the SQLite database."""
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
async def delete_persona(persona_key: str, username: str = "default_user"):
    """Deletes a custom persona from the database."""
    user_manager = db.UserManager()
    success = user_manager.delete_custom_persona(username, persona_key)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to delete persona")
    return {"status": "success", "message": f"Persona {persona_key} deleted"}


UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
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
def get_chat_history(persona_key: str, username: str = "default_user", limit: int = 50):
    """Retrieve chat history for a specific persona."""
    db_conn = db.UserManager()
    history = db_conn.get_chat_history(username, persona_key, limit)
    return {"history": history}

@app.post("/chat/{persona_key}")
def save_chat_message(persona_key: str, msg: MessagePayload):
    """Save a simulated user or assistant message to the database."""
    db_conn = db.UserManager()
    
    content_str = json.dumps(msg.content) if isinstance(msg.content, list) else msg.content
    
    success = db_conn.save_message(msg.username, persona_key, msg.role, content_str)
    if not success:
        raise HTTPException(status_code=500, detail="Database insertion failed.")
    return {"status": "success", "message": "Message committed to SQLite."}

@app.post("/chat/{persona_key}/clear")
def clear_chat(persona_key: str, username: str = "default_user"):
    db_conn = db.UserManager()
    db_conn.clear_chat_history(username, persona_key)
    return {"status": "success", "message": "Chat history cleared."}

@app.delete("/chat/message/{message_id}")
def delete_chat_message(message_id: int):
    """Delete a specific message from a chat."""
    db_conn = db.UserManager()
    success = db_conn.delete_message(message_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete message.")
    return {"status": "success"}

@app.post("/groupchat/{session_id}")
def save_group_chat_message(session_id: str, msg: GroupMessagePayload):
    """Save a message to the group conversation history."""
    db_conn = db.UserManager()
    success = db_conn.save_group_message(
        session_id, msg.username, msg.persona_key, msg.persona_name, 
        msg.persona_avatar, msg.role, msg.content, msg.is_observer
    )
    if not success:
        raise HTTPException(status_code=500, detail="Database insertion failed.")
    return {"status": "success"}

@app.get("/groupchat/{session_id}")
def get_group_chat_history(session_id: str, username: str = "default_user", limit: int = 100):
    """Retrieve group chat history."""
    db_conn = db.UserManager()
    history = db_conn.get_group_history(session_id, username, limit)
    return {"history": history}

@app.delete("/groupchat/message/{message_id}")
def delete_group_chat_message(message_id: int):
    """Delete a specific message from a group chat."""
    db_conn = db.UserManager()
    success = db_conn.delete_group_message(message_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete group message.")
    return {"status": "success"}

@app.post("/groupchat/{session_id}/clear")
def clear_group_chat_history(session_id: str, username: str = "default_user"):
    """Clear group chat history."""
    db_conn = db.UserManager()
    success = db_conn.clear_group_history(session_id, username)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear group history.")
    return {"status": "success", "message": "Group chat history cleared."}

@app.post("/personas/{persona_key}/wipe")
def wipe_memory(persona_key: str, username: str = "default_user"):
    """Wipe all semantic memories, summaries, observations, and Zettel data."""
    db_conn = db.UserManager()
    
    # 1. Clear SQLite tables (Memories, Summaries, Observations, Zettel Graph)
    success = db_conn.wipe_memories(username, persona_key)
    
    # 2. Clear RAG Vector Store (Pickle storage)
    try:
        from llm_engine import get_rag_engine
        rag = get_rag_engine()
        rag.clear_persona_knowledge(persona_key, username)
    except Exception as e:
        print(f"[WIPE ERROR] Failed to clear RAG store: {e}")
        # We don't fail the whole request if RAG fails, but we log it.

    if success:
        return {"status": "success", "message": "Memories wiped."}
    raise HTTPException(status_code=500, detail="Failed to wipe memories.")

@app.post("/chat/{persona_key}/stream")
def stream_chat(persona_key: str, req: StreamRequest):
    """Generates a streaming response for the given persona and saves the transaction."""
    personas = load_personas_logic(req.username)
    
    if persona_key not in personas:
        # Fallback to case-insensitive match
        matched_key = next((k for k in personas if k.lower() == persona_key.lower()), None)
        if matched_key:
            persona_key = matched_key
        else:
            raise HTTPException(status_code=404, detail=f"Persona '{persona_key}' not found. Available keys for this user: {list(personas.keys())}")
    
    persona_data = personas[persona_key]
    
    # Load API keys from the server's .env file as fallbacks
    api_keys = {
        "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", ""),
        "xai": os.getenv("XAI_API_KEY", ""),
        "featherless": os.getenv("FEATHERLESS_API_KEY", ""),
        "perplexity": os.getenv("PERPLEXITY_API_KEY", "")
    }
    
    # Override with any keys provided from the UI
    for provider, key in req.active_api_keys.items():
        if key:
            api_keys[provider] = key

    # Immediately save the User's message so it doesn't fade on UI reload
    content_str = json.dumps(req.message) if isinstance(req.message, list) else req.message
    db_conn = db.UserManager()
    db_conn.save_message(req.username, persona_key, "user", content_str)
    
    stream_obj = build_context_and_stream(
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
        workspace_context=req.workspace_context
    )
    
    if isinstance(stream_obj, str):
        # Handle string error returns directly
        def err_generator():
            yield f'data: {{"choices": [{{"delta": {{"content": "{stream_obj}"}}}}]}}\n\n'
        return StreamingResponse(err_generator(), media_type="text/event-stream")
        
    def sse_generator():
        # stream_obj is an intercepting generator yielding bytes
        for chunk in stream_obj:
            if chunk:
                yield chunk
                
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

# ============================================================
# LORE (ZETTEL KNOWLEDGE GRAPH) ENDPOINTS
# ============================================================
from zettel_engine import process_entry as zettel_process_entry

@app.post("/personas/{persona_key}/lore")
def create_lore_entry(persona_key: str, payload: LoreEntryPayload):
    """Create a lore entry for a persona. Triggers async background processing."""
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
    api_keys = {
        "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", ""),
        "universal": os.getenv("OPENROUTER_API_KEY", ""),
    }
    for provider, key in payload.active_api_keys.items():
        if key:
            api_keys[provider] = key
    
    # Process in background thread (chunking + embedding + LLM entity extraction)
    def _bg_process():
        try:
            zettel_process_entry(payload.username, persona_key, entry_id, api_keys)
        except Exception as e:
            print(f"[ZETTEL BG ERROR] {e}")
    
    threading.Thread(target=_bg_process, daemon=True).start()
    
    return {"status": "success", "entry_id": entry_id, "message": "Lore entry created. Processing in background."}

@app.get("/personas/{persona_key}/lore")
def get_lore_entries(persona_key: str, username: str = "default_user"):
    """List all lore entries for a persona."""
    db_conn = db.UserManager()
    entries = db_conn.get_zettel_entries(username, persona_key)
    return {"entries": entries}

@app.put("/personas/{persona_key}/lore/{entry_id}")
def update_lore_entry(persona_key: str, entry_id: str, payload: LoreEntryPayload):
    """Update a lore entry and re-trigger processing."""
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
    
    api_keys = {
        "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "google": os.getenv("GOOGLE_API_KEY", ""),
        "universal": os.getenv("OPENROUTER_API_KEY", ""),
    }
    for provider, key in payload.active_api_keys.items():
        if key:
            api_keys[provider] = key
    
    def _bg_process():
        try:
            zettel_process_entry(payload.username, persona_key, entry_id, api_keys)
        except Exception as e:
            print(f"[ZETTEL BG ERROR] {e}")
    
    threading.Thread(target=_bg_process, daemon=True).start()
    
    return {"status": "success", "message": "Lore entry updated. Re-processing in background."}

@app.delete("/personas/{persona_key}/lore/{entry_id}")
def delete_lore_entry(persona_key: str, entry_id: str, username: str = "default_user"):
    """Delete a lore entry and cascade-remove its nodes + links."""
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
def get_workspace_tree(path: str = "."):
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
def get_workspace_file(path: str):
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
def save_workspace_file(data: WorkspaceSave):
    root = os.path.dirname(os.path.abspath(data.path))
    try:
        ws = SafeWorkspace(root)
        return ws.save_file_content(data.path, data.content)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

class WorkspaceCreate(BaseModel):
    path: str
    item_type: str = "file"

@app.post("/workspace/create")
def create_workspace_item(data: WorkspaceCreate):
    # Root is the parent of the item being created
    parent = os.path.dirname(os.path.abspath(data.path))
    try:
        ws = SafeWorkspace(parent)
        return ws.create_item(data.path, data.item_type)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

@app.delete("/workspace/delete")
def delete_workspace_item(path: str):
    parent = os.path.dirname(os.path.abspath(path))
    try:
        ws = SafeWorkspace(parent)
        return ws.delete_item(path)
    except SecurityViolation as e:
        raise HTTPException(status_code=403, detail=f"Security Violation: {str(e)}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
