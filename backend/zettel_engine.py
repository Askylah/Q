"""
Auto-Zettel Knowledge Graph Engine
Developed for PersonaApp by askylah_

Surface: Simple lorebook ("add lore about your character")
Backend: Automatic chunking, embedding, entity extraction, 
         auto-linking into a knowledge graph via flash LLM pass.

Write Path (once per entry creation):
    User creates lore → chunk_text() → embed chunks → 
    LLM flash extracts entities + [[CATEGORY-NAME-###]] links →
    Store nodes + edges in SQLite → Cross-link against existing nodes

Read Path (every message, zero LLM cost):
    User sends message → embed query → vector search + FTS5 keyword search →
    Reciprocal Rank Fusion → 1-hop graph expansion → inject subgraph into context
"""

import json
import uuid
import re
import os
import functools
import threading
import numpy as np
from collections import OrderedDict
from sklearn.metrics.pairwise import cosine_similarity
from embedding_model import get_shared_model
import database as db
import pickle
import redis_client

# Contiguous matrix cache per (username, persona) to prevent loading binary vectors from SQLite on every turn.
# Thread-safe global lock to protect memory reads/writes.
# FIX(cache-growth): OrderedDict + hard cap = LRU. Previously only same-user
# personas were evicted on switch, so the process cache grew unboundedly
# across users (only Redis had a TTL).
_EMBEDDING_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
MAX_CACHED_PERSONAS = 50

# FIX(per-message-io): path -> (mtime, sha256). The compile check runs on every
# message; this lets it skip re-hashing files whose mtime hasn't changed.
_FILE_HASH_CACHE = {}

# FIX(per-message-regex): (username, persona) -> {"signature": frozenset(node pks),
# "patterns": [(compiled_regex, node_pk)]}. Trigger regexes are compiled once per
# deterministic-node-set instead of re-parsed + re-compiled for every message.
_TRIGGER_REGEX_CACHE = {}

def evict_other_personas_from_cache(username: str, active_persona: str):
    """DEPRECATED no-op, retained for import compatibility.

    FIX(groupchat-thrash): group chat invokes each speaker through the solo
    /chat/{persona}/stream path, so with N personas in a room this eviction
    fired on EVERY speaker change and every turn was a cold cache rebuild
    from SQLite. The LRU cap in _cache_put now owns memory pressure; multiple
    personas may be warm simultaneously by design."""
    return
    # (previous body removed)
    other_keys = [k for k in _EMBEDDING_CACHE.keys() if k[0] == username and k[1] != active_persona]
    for k in other_keys:
        del _EMBEDDING_CACHE[k]
        print(f"[ZETTEL CACHE] Evicted persona '{k[1]}' cache for user '{username}' on persona switch.")

def _cache_put(cache_key: tuple, cache_data: dict):
    """Insert/refresh a cache entry and enforce the LRU cap. Assumes caller holds _CACHE_LOCK."""
    _EMBEDDING_CACHE[cache_key] = cache_data
    _EMBEDDING_CACHE.move_to_end(cache_key)
    while len(_EMBEDDING_CACHE) > MAX_CACHED_PERSONAS:
        evicted_key, _ = _EMBEDDING_CACHE.popitem(last=False)
        print(f"[ZETTEL CACHE] LRU-evicted '{evicted_key[1]}' (user '{evicted_key[0]}')")

def invalidate_zettel_cache(username: str, persona: str):
    """
    FIX(stale-vectors): Drop the in-process AND Redis caches for a persona.

    MUST be called after ANY node deletion (behavioral file recompile, lore
    entry deletion). bulk_append_to_zettel_cache can only ADD rows — deletes
    previously left stale vectors in the matrix forever, growing it on every
    recompile and letting dead nodes outrank live ones in the top-k slice.
    The next query_knowledge_graph call rebuilds from SQLite.
    """
    cache_key = (username, persona)
    with _CACHE_LOCK:
        _EMBEDDING_CACHE.pop(cache_key, None)
        _TRIGGER_REGEX_CACHE.pop(cache_key, None)
    if redis_client.is_active():
        redis_key = f"zettel:cache:{username}:{persona}"
        try:
            # redis_client's delete helper name may vary; degrade gracefully.
            if hasattr(redis_client, "delete_val"):
                redis_client.delete_val(redis_key)
            elif hasattr(redis_client, "delete"):
                redis_client.delete(redis_key)
            else:
                # No delete helper exposed — overwrite with a 1s TTL tombstone.
                redis_client.set_val(redis_key, pickle.dumps(None), ex=1)
        except Exception as e:
            print(f"[REDIS ERROR] Failed to invalidate cache: {e}")
    print(f"[ZETTEL CACHE] Invalidated '{persona}' for user '{username}' (node deletion)")

def load_zettel_cache(username: str, persona: str, all_nodes: list) -> dict:
    """Loads all nodes for the given persona into _EMBEDDING_CACHE under thread lock."""
    cache_key = (username, persona)
    
    nodes_with_embeddings = [n for n in all_nodes if n.get("embedding")]
    if not nodes_with_embeddings:
        return None
        
    node_ids = [n["id"] for n in nodes_with_embeddings]
    node_tags = [n["node_id"] for n in nodes_with_embeddings]
    
    embeddings = np.array([
        np.frombuffer(n["embedding"], dtype=np.float32)
        for n in nodes_with_embeddings
    ], dtype=np.float32)
    
    cache_data = {
        "node_ids": node_ids,
        "node_tags": node_tags,
        "embeddings": embeddings
    }
    
    if redis_client.is_active():
        redis_key = f"zettel:cache:{username}:{persona}"
        try:
            redis_client.set_val(redis_key, pickle.dumps(cache_data), ex=3600)
            print(f"[ZETTEL CACHE] Caching nodes to Redis for '{persona}' (Count: {len(node_ids)})")
        except Exception as e:
            print(f"[REDIS ERROR] Failed to cache data in Redis: {e}")
            
    with _CACHE_LOCK:
        _cache_put(cache_key, cache_data)
        return _EMBEDDING_CACHE[cache_key]

def bulk_append_to_zettel_cache(username: str, persona: str, new_nodes: list):
    """Appends multiple new nodes to the cache in a single contiguous allocation pass."""
    if not new_nodes:
        return
        
    cache_key = (username, persona)
    redis_key = f"zettel:cache:{username}:{persona}"
    
    with _CACHE_LOCK:
        cache = None
        if cache_key in _EMBEDDING_CACHE:
            cache = _EMBEDDING_CACHE[cache_key]
        elif redis_client.is_active():
            redis_data = redis_client.get(redis_key)
            if redis_data:
                try:
                    cache = pickle.loads(redis_data)
                except Exception as e:
                    print(f"[REDIS ERROR] Failed to deserialize cache: {e}")
                    
        if cache:
            new_ids = [n["id"] for n in new_nodes]
            new_tags = [n["tag"] for n in new_nodes]
            new_vecs = np.array([n["embedding"] for n in new_nodes], dtype=np.float32)
            
            cache["node_ids"].extend(new_ids)
            cache["node_tags"].extend(new_tags)
            cache["embeddings"] = np.vstack([cache["embeddings"], new_vecs])
            
            _EMBEDDING_CACHE[cache_key] = cache
            
            if redis_client.is_active():
                try:
                    redis_client.set_val(redis_key, pickle.dumps(cache), ex=3600)
                except Exception as e:
                    print(f"[REDIS ERROR] Failed to write updated cache to Redis: {e}")

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

# Max tokens per atomic chunk (~4 chars per token)
MAX_CHUNK_TOKENS = 256
MAX_CHUNK_CHARS = MAX_CHUNK_TOKENS * 4  # ~1024

# Minimum similarity for auto-linking new nodes to existing graph (raised from 0.50 to prevent hairballs)
AUTO_LINK_SIMILARITY_THRESHOLD = 0.75

def get_strength_label(strength: float) -> str:
    """Map numerical link strength to categorical label."""
    if strength >= 0.85:
        return "core"
    elif strength >= 0.70:
        return "related"
    else:
        return "incidental"

# Valid entity categories for the LLM flash pass
VALID_CATEGORIES = frozenset([
    "CHAR", "LOC", "EVT", "FAC", "ITEM", 
    "CONCEPT", "REL", "HIST", "ABILITY", "ORG",
    "OBSERVATION", "DISCOVERY", "THEORY", "LORE"
])

# Flash model for entity extraction (cheap + fast)
FLASH_MODEL = "google/gemini-3-flash-preview"

# Hyperscaling: Max chunks to process in a single LLM pass
CHUNKS_PER_BATCH = 15

# FIX(batch-boundary): overlap consecutive extraction batches so relationships
# spanning a batch boundary (e.g. chunk 14 ↔ chunk 16) can still be proposed.
# Without overlap the LLM physically cannot link chunks it never sees together.
BATCH_OVERLAP = 3

# FIX(flat-links): LLM-extracted relationships carry an actual typed relation
# (caused_by, member_of, ...) — they are the highest-signal edges in the graph.
# At the old hardcoded 0.8 they could never be labeled "core" (>= 0.85), so the
# most meaningful links ranked below strong-but-generic embedding auto-links.
LLM_LINK_STRENGTH = 0.85


# ═══════════════════════════════════════════════════════════
# TEXT CHUNKING
# ═══════════════════════════════════════════════════════════

def chunk_text(raw_content: str, max_chars: int = MAX_CHUNK_CHARS) -> list:
    """
    Split raw lore text into atomic chunks.
    
    Strategy:
    1. Split by double-newline (paragraphs)
    2. If a paragraph exceeds max_chars, split by sentence
    3. If a single sentence exceeds max_chars, hard-split at max_chars
    
    Returns list of text chunks, each ≤ max_chars.
    """
    if not raw_content or not raw_content.strip():
        return []
    
    paragraphs = re.split(r'\n\s*\n', raw_content.strip())
    chunks = []
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            # Split by sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', para)
            current_chunk = ""
            
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                
                if len(sentence) > max_chars:
                    # Hard split oversized sentences
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = ""
                    for i in range(0, len(sentence), max_chars):
                        chunks.append(sentence[i:i + max_chars].strip())
                elif len(current_chunk) + len(sentence) + 1 <= max_chars:
                    current_chunk += (" " if current_chunk else "") + sentence
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
            
            if current_chunk:
                chunks.append(current_chunk.strip())
    
    # Filter out empty chunks
    # FIX(silent-drop): previously chunks ≤10 chars vanished without a trace —
    # a very short lore entry could be silently un-indexed. Log it so the
    # caller/UI can surface "entry too short to index" to the author.
    kept = [c for c in chunks if c and len(c.strip()) > 10]
    dropped = len(chunks) - len(kept)
    if dropped:
        print(f"[ZETTEL] chunk_text: dropped {dropped} chunk(s) ≤ 10 chars"
              + (" — ENTIRE ENTRY dropped, surface this to the author" if not kept else ""))
    return kept


# ═══════════════════════════════════════════════════════════
# ENTITY EXTRACTION (Flash LLM Pass)
# ═══════════════════════════════════════════════════════════

ENTITY_EXTRACTION_PROMPT = """You are a knowledge graph entity extractor for a fictional world/character lorebook.

Given the following lore text chunks, extract structured entities and relationships.

CHUNKS:
{chunks_text}

For each chunk, output a JSON object with:
- "chunk_index": the 0-based index of the chunk
- "category": one of CHAR, LOC, EVT, FAC, ITEM, CONCEPT, REL, HIST, ABILITY, ORG
- "title": a short descriptive title for this node (2-5 words)
- "entities": list of named entities mentioned (people, places, things)

Then, output a "relationships" array listing connections BETWEEN chunks:
- "source_index": chunk index
- "target_index": chunk index  
- "relationship": a short label (e.g. "member_of", "located_in", "caused_by", "knows", "wields", "born_in")

OUTPUT FORMAT (strict JSON, no markdown):
{{
    "nodes": [
        {{"chunk_index": 0, "category": "CHAR", "title": "Kael's Origin", "entities": ["Kael", "The Citadel"]}},
        ...
    ],
    "relationships": [
        {{"source_index": 0, "target_index": 1, "relationship": "located_in"}},
        ...
    ]
}}

RULES:
- Every chunk MUST have exactly one node entry
- Categories must be from: CHAR, LOC, EVT, FAC, ITEM, CONCEPT, REL, HIST, ABILITY, ORG
- Keep titles short and descriptive
- Only include relationships where a clear connection exists
- Output ONLY the JSON object, nothing else"""


def _extract_entities_via_llm(chunks: list, api_keys: dict, model_id: str = None, base_index: int = 0) -> dict:
    """
    Call a flash LLM to extract entities and relationships from chunks.
    Returns parsed JSON or a fallback structure if the call fails.
    """
    from llm_engine import call_llm
    
    if not model_id:
        model_id = FLASH_MODEL
    
    # Build the chunks text with relative offsets
    chunks_text = "\n\n".join([f"[Chunk {base_index + i}]: {c}" for i, c in enumerate(chunks)])
    prompt = ENTITY_EXTRACTION_PROMPT.format(chunks_text=chunks_text)
    
    fallback = {"nodes": [], "relationships": []}
    
    try:
        response = call_llm(
            model_id=model_id,
            system_prompt="You are a precise JSON entity extractor. Output only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            api_keys=api_keys,
            stream=False,
            temperature=0.1,
            max_tokens=2048
        )
        
        # If response is a string (error prefix from call_llm), log and return fallback
        if isinstance(response, str):
            print(f"[ZETTEL] LLM Error response: {response}")
            return fallback

        if isinstance(response, dict) and "choices" in response:
            content = response["choices"][0].get("message", {}).get("content", "")
            # Strip markdown code fences if present
            content = re.sub(r'^```(?:json)?\s*', '', content.strip())
            content = re.sub(r'\s*```$', '', content.strip())
            
            parsed = json.loads(content)
            return parsed
        else:
            print(f"[ZETTEL] LLM entity extraction returned non-dict: {type(response)}")
            return None
            
    except json.JSONDecodeError as e:
        print(f"[ZETTEL] Failed to parse LLM entity JSON: {e}")
        return None
    except Exception as e:
        print(f"[ZETTEL] LLM entity extraction error: {e}")
        return None


def _generate_node_id(category: str, title: str, existing_ids: set) -> str:
    """Generate a unique [[CATEGORY-NAME-###]] tag for a node."""
    cat = category.upper() if category in VALID_CATEGORIES else "CONCEPT"
    # Sanitize title to create a short slug
    slug = re.sub(r'[^A-Za-z0-9]+', '-', title.strip()).strip('-').upper()
    if len(slug) > 20:
        slug = slug[:20].rstrip('-')
    
    # Find next available number
    counter = 1
    while True:
        node_id = f"[[{cat}-{slug}-{counter:03d}]]"
        if node_id not in existing_ids:
            existing_ids.add(node_id)
            return node_id
        counter += 1


# ═══════════════════════════════════════════════════════════
# HANDWRITTEN LORE (on-demand-format modules pasted into the lorebook)
# ═══════════════════════════════════════════════════════════

# Lesion: ZETTEL_LORE_HANDWRITTEN_OFF=1 sends every lore entry down the old
# auto-split + LLM path, exactly as before 2026-10-04.
LORE_HANDWRITTEN_OFF_ENV = "ZETTEL_LORE_HANDWRITTEN_OFF"


def _env_flag(name: str) -> bool:
    """Lesion flags are read per call, so a test (or an operator with a
    debugger) can flip one without reloading the module."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _bare_tag(tag: str) -> str:
    """Comparable node_id: lore tags carry [[brackets]], module IDs do not;
    case is not a distinction anyone means."""
    return (tag or "").strip().strip("[]").strip().upper()


def _import_handwritten_entry(db_conn, model, username: str, persona: str, entry_id: str,
                              entry_title: str, raw_content: str, modules: list, parse_stats: dict) -> str:
    """Store each parsed module as one behavioral node, the way
    compile_behavioral_zettels stores on-demand files: node_id = the module's
    own ID, its title, content `TRIGGERS: ...` + body, DETERMINISTIC, embedding
    of the body. source_entry_id = the lore entry. Returns the import note.

    An ID this user+persona already has from another source (an on-demand file
    or another lore entry) is SKIPPED and reported -- the existing module stays.
    Two modules with one ID was never a thing the graph could represent: typed
    links resolve a tag to one node, and a silent second copy is how the "KB"
    entry ended up duplicating Rick_kb.txt."""
    import sqlite3
    import hashlib
    name = f"lore entry '{entry_title}'"

    conn = sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    # Re-processing replaces the entry's own nodes (update_zettel_entry already
    # deleted them; this keeps a retry idempotent). Edges go with their nodes.
    c.execute("""DELETE FROM zettel_links
                 WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)
                    OR target_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)""",
              (entry_id, entry_id))
    c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)",
              (entry_id,))
    c.execute("DELETE FROM zettel_nodes WHERE source_entry_id=?", (entry_id,))
    conn.commit()
    c.execute("""SELECT node_id, source_entry_id FROM zettel_nodes
                 WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?""", (username, persona))
    owner = {}
    for tag, src in c.fetchall():
        owner.setdefault(_bare_tag(tag), src)
    c.execute("""SELECT id, title FROM zettel_entries
                 WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?""", (username, persona))
    entry_titles = dict(c.fetchall())
    conn.close()

    def source_label(src):
        if src in entry_titles:
            return f"lore entry '{entry_titles[src]}'"
        if src and ("/" in src or "\\" in src):
            return os.path.basename(src)
        return src or "an unknown source"

    kept, dup_self, failed = [], [], []
    dup_other = OrderedDict()          # source label -> [IDs]
    seen = set()
    for mod in modules:
        key = _bare_tag(mod.id)
        if key in seen:
            dup_self.append(mod.id)
            print(f"[ZETTEL LORE] !!! DUPLICATE ID {mod.id} appears twice in {name}; "
                  f"the first is kept, this one is SKIPPED.")
            continue
        seen.add(key)
        if key in owner:
            label = source_label(owner[key])
            dup_other.setdefault(label, []).append(mod.id)
            print(f"[ZETTEL LORE] !!! DUPLICATE ID {mod.id} in {name} is already defined by {label} "
                  f"for {username}/{persona}; this module is SKIPPED and the existing one stays.")
            continue
        kept.append(mod)

    vecs = []
    if model and kept:
        try:
            vecs = list(model.encode([m.content for m in kept], convert_to_numpy=True))
        except Exception as e:
            print(f"[ZETTEL LORE ERROR] embedding failed for {name}: {e}; modules stored without vectors")
            vecs = []
    content_hash = (hashlib.sha256((raw_content or "").encode("utf-8")).hexdigest()
                    + f":p{ON_DEMAND_PARSER_VERSION}:lore")
    written = []
    for i, mod in enumerate(kept):
        blob = np.asarray(vecs[i], dtype=np.float32).tobytes() if i < len(vecs) else None
        ok = db_conn.add_zettel_node(
            node_id_pk=str(uuid.uuid4()),
            username=username,
            persona=persona,
            node_id_tag=mod.id,
            title=mod.title,
            content=f"TRIGGERS: {','.join(mod.triggers)}\n\n{mod.content}",
            category="CONCEPT",
            embedding_blob=blob,
            source_entry_id=entry_id,
            node_class='behavioral',
            trigger_type='DETERMINISTIC',
            content_hash=content_hash,
            trigger_match=mod.match
        )
        (written if ok else failed).append(mod)

    # Nodes changed under the caches: drop the vector matrix AND the trigger
    # cache (invalidate_zettel_cache does both, plus Redis), then give the new
    # modules their typed edges -- in both directions, since a file module can
    # link INTO a lore module as well.
    invalidate_zettel_cache(username, persona)
    try:
        rebuild_typed_links(username, persona, load_persona_on_demand_files(persona))
        _TYPED_LINKS_OK.add((username.lower(), persona.lower()))
    except Exception as e:
        print(f"[ZETTEL LORE] typed-link rebuild failed (non-fatal): {e}")

    conn = sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT count(*) FROM zettel_links l JOIN zettel_nodes n ON n.id = l.source_node_id
                 WHERE n.source_entry_id=? AND l.relationship=?""", (entry_id, TYPED_LINK_RELATIONSHIP))
    edges = c.fetchone()[0]
    c.execute("""SELECT node_id FROM zettel_nodes
                 WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?""", (username, persona))
    known = {_bare_tag(r[0]) for r in c.fetchall()}
    conn.close()
    unresolved = []
    for mod in written:
        for l in mod.links:
            if l and _bare_tag(l) not in known and l not in unresolved:
                unresolved.append(l)

    parts = [f"handwritten: {len(written)} module(s) imported, {edges} typed link(s)"]
    exact_only = [m.id for m in written if m.match == "exact"]
    if exact_only:
        parts.append(f"{len(exact_only)} exact-trigger-only (Match: exact): {', '.join(exact_only)}")
    for label, ids in dup_other.items():
        parts.append(f"skipped {len(ids)} duplicate ID(s) already defined by {label}: {', '.join(ids)}")
    if dup_self:
        parts.append(f"skipped {len(dup_self)} ID(s) repeated inside this entry: {', '.join(dup_self)}")
    if failed:
        parts.append(f"FAILED to store {len(failed)}: {', '.join(m.id for m in failed)}")
    if parse_stats.get("no_body"):
        parts.append(f"skipped {len(parse_stats['no_body'])} header(s) with no body: "
                     f"{', '.join(parse_stats['no_body'])}")
    if parse_stats.get("preamble_chars"):
        parts.append(f"ignored {parse_stats['preamble_chars']} chars before the first header")
    if parse_stats.get("orphan_chars"):
        parts.append(f"ignored {parse_stats['orphan_chars']} chars of body text with no header "
                     f"(a '---' inside a module body?)")
    if unresolved:
        parts.append(f"unresolved links: {', '.join('[[' + u + ']]' for u in unresolved)}")
    if kept and not vecs:
        parts.append("stored without vectors (no embedding model); triggers and keyword search still work")
    note = "; ".join(parts)
    print(f"[ZETTEL LORE] {name}: {note}")
    if parse_stats.get("preamble_chars") or parse_stats.get("orphan_chars"):
        print(f"[ZETTEL LORE] {name}: text outside any module was NOT imported (see note above).")
    return note


# ═══════════════════════════════════════════════════════════
# ENTRY PROCESSING (Write Path)
# ═══════════════════════════════════════════════════════════

def process_entry(username: str, persona: str, entry_id: str, api_keys: dict, model_id: str = None):
    """
    Full write-path pipeline for a single lore entry.
    
    1. Load raw entry from DB
    2. Chunk the text
    3. Embed each chunk
    4. Call flash LLM for entity extraction
    5. Store nodes + links in DB
    6. Auto-link against existing graph via embedding similarity
    7. Mark entry as processed
    
    This runs in a background thread — zero blocking on the API response.
    """
    db_conn = db.UserManager()
    model = get_shared_model()
    
    # 1. Load the entry
    entries = db_conn.get_zettel_entries(username, persona)
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        print(f"[ZETTEL] Entry {entry_id} not found")
        return
    
    raw_content = entry["content"]
    entry_title = entry["title"]

    # 1b. Handwritten modules stay whole (2026-10-04). This endpoint used to
    # chunk EVERY entry by paragraph and let an LLM file the chunks under
    # fiction categories with invented IDs -- Rick_kb.txt pasted as lore entry
    # "KB" became 229 nodes like [[ABILITY-MULTIVERSAL-DEBUGGIN-001]], headers
    # severed from bodies, no triggers. Text in the on-demand format (ID: /
    # Title: / Links: / Triggers: headers) now becomes one behavioral node per
    # module, exactly as an on_demand_files entry would. Anything else still
    # takes the auto-split + LLM path below. Lesion: ZETTEL_LORE_HANDWRITTEN_OFF=1.
    if not _env_flag(LORE_HANDWRITTEN_OFF_ENV):
        parse_stats = {}
        modules = parse_on_demand_text(raw_content or "", f"lore entry '{entry_title}'", stats=parse_stats)
        if modules:
            print(f"[ZETTEL] Entry '{entry_title}': handwritten format, {len(modules)} module(s) -- "
                  f"kept intact (own IDs, links, triggers); no chunking, no LLM")
            try:
                note = _import_handwritten_entry(db_conn, model, username, persona, entry_id,
                                                 entry_title, raw_content, modules, parse_stats)
            except Exception as e:
                import traceback
                traceback.print_exc()
                note = f"handwritten import FAILED: {e}"
                print(f"[ZETTEL LORE ERROR] '{entry_title}': {note}")
            # Processed either way: a failed import must not leave the UI's lore
            # poll spinning forever. The note says what happened.
            db_conn.set_zettel_entry_import_note(entry_id, note)
            db_conn.mark_zettel_entry_processed(entry_id)
            return

    # 2. Chunk
    chunks = chunk_text(raw_content)
    if not chunks:
        print(f"[ZETTEL] No chunks generated for entry {entry_id}")
        db_conn.set_zettel_entry_import_note(entry_id, "auto-split: nothing to index (every chunk was 10 chars or less)")
        db_conn.mark_zettel_entry_processed(entry_id)
        return
    
    print(f"[ZETTEL] Processing entry '{entry_title}': {len(chunks)} chunks")
    
    # 3. Embed all chunks
    embeddings = []
    if model:
        vecs = model.encode(chunks, convert_to_numpy=True)
        embeddings = [v for v in vecs]
    
    # 4. LLM entity extraction (Batched for Hyperscaling)
    all_nodes = []
    all_relationships = []
    
    if api_keys:
        # FIX(batch-boundary): stride = batch size minus overlap, so each batch
        # re-sees the tail of the previous one and can propose cross-boundary
        # relationships. Overlapped chunks produce duplicate node entries and
        # possibly duplicate relationships — dedup keeps the first occurrence.
        step = max(1, CHUNKS_PER_BATCH - BATCH_OVERLAP)
        seen_node_indices = set()
        seen_rel_keys = set()

        for i in range(0, len(chunks), step):
            batch = chunks[i : i + CHUNKS_PER_BATCH]
            print(f"[ZETTEL]   Extraction Wave: Chunks {i} to {i + len(batch) - 1}")
            batch_data = _extract_entities_via_llm(batch, api_keys, model_id, base_index=i)

            if batch_data:
                for n in batch_data.get("nodes", []):
                    ci = n.get("chunk_index")
                    if ci not in seen_node_indices:
                        seen_node_indices.add(ci)
                        all_nodes.append(n)
                for r in batch_data.get("relationships", []):
                    key = (r.get("source_index"), r.get("target_index"), r.get("relationship"))
                    if key not in seen_rel_keys:
                        seen_rel_keys.add(key)
                        all_relationships.append(r)

            # This batch reached the final chunk — striding further would only
            # re-process the same tail window repeatedly.
            if i + CHUNKS_PER_BATCH >= len(chunks):
                break

    llm_data = {"nodes": all_nodes, "relationships": all_relationships}
    
    # 5. Build and store nodes
    existing_nodes = db_conn.get_zettel_nodes_for_persona(username, persona)
    existing_node_ids = {n["node_id"] for n in existing_nodes}
    
    created_node_ids = []  # Maps chunk_index → db primary key
    new_cache_nodes = []   # Buffers nodes for bulk cache append
    
    for i, chunk in enumerate(chunks):
        pk = str(uuid.uuid4())
        
        # Extract metadata from LLM or use defaults
        category = "CONCEPT"
        title = f"{entry_title} ({i+1})"
        
        if llm_data and "nodes" in llm_data:
            node_info = next((n for n in llm_data["nodes"] if n.get("chunk_index") == i), None)
            if node_info:
                cat = node_info.get("category", "CONCEPT").upper()
                category = cat if cat in VALID_CATEGORIES else "CONCEPT"
                title = node_info.get("title", title)
        
        # Generate [[CATEGORY-NAME-###]] tag
        node_id_tag = _generate_node_id(category, title, existing_node_ids)
        
        # Embedding blob
        embedding_blob = embeddings[i].tobytes() if i < len(embeddings) else None
        
        db_conn.add_zettel_node(
            node_id_pk=pk,
            username=username,
            persona=persona,
            node_id_tag=node_id_tag,
            title=title,
            content=chunk,
            category=category,
            embedding_blob=embedding_blob,
            source_entry_id=entry_id
        )
        
        # Buffer for bulk append to in-memory cache directly
        if model and i < len(embeddings):
            new_cache_nodes.append({
                "id": pk,
                "tag": node_id_tag,
                "embedding": embeddings[i]
            })
            
        created_node_ids.append(pk)
        print(f"[ZETTEL]   Node: {node_id_tag} → {title}")
        
    # Bulk load into the cache in one contiguous memory allocation
    bulk_append_to_zettel_cache(username, persona, new_cache_nodes)
    
    # 6a. Store intra-entry relationships from LLM
    if llm_data and "relationships" in llm_data:
        for rel in llm_data["relationships"]:
            src_idx = rel.get("source_index", -1)
            tgt_idx = rel.get("target_index", -1)
            
            if 0 <= src_idx < len(created_node_ids) and 0 <= tgt_idx < len(created_node_ids):
                link_id = str(uuid.uuid4())
                db_conn.add_zettel_link(
                    link_id=link_id,
                    source_node_id=created_node_ids[src_idx],
                    target_node_id=created_node_ids[tgt_idx],
                    relationship=rel.get("relationship", "related_to"),
                    strength=LLM_LINK_STRENGTH,
                    label=get_strength_label(LLM_LINK_STRENGTH)
                )
                print(f"[ZETTEL]   Link: {src_idx} --[{rel.get('relationship')}]--> {tgt_idx}")
    
    # 6b. Auto-link against EXISTING nodes via embedding similarity
    if model and embeddings and existing_nodes:
        existing_with_embeddings = [n for n in existing_nodes if n.get("embedding")]
        
        if existing_with_embeddings:
            existing_vecs = np.array([
                np.frombuffer(n["embedding"], dtype=np.float32)
                for n in existing_with_embeddings
            ])
            
            for i, new_vec in enumerate(embeddings):
                new_vec_2d = new_vec.reshape(1, -1)
                sims = cosine_similarity(new_vec_2d, existing_vecs).flatten()
                
                # Link to existing nodes above threshold
                top_indices = np.argsort(sims)[::-1]
                link_count = 0
                
                for idx in top_indices:
                    if sims[idx] < AUTO_LINK_SIMILARITY_THRESHOLD:
                        break
                    if link_count >= 3:  # Max 3 cross-links per new node
                        break
                    
                    existing_node = existing_with_embeddings[idx]
                    link_id = str(uuid.uuid4())
                    db_conn.add_zettel_link(
                        link_id=link_id,
                        source_node_id=created_node_ids[i],
                        target_node_id=existing_node["id"],
                        relationship="related_to",
                        strength=round(float(sims[idx]), 3),
                        label=get_strength_label(sims[idx])
                    )
                    link_count += 1
                    print(f"[ZETTEL]   Cross-link: new[{i}] → {existing_node['node_id']} (sim: {sims[idx]:.2f})")
    
    # 7. Mark entry as processed
    llm_tagged = len({n.get("chunk_index") for n in all_nodes if isinstance(n, dict)})
    db_conn.set_zettel_entry_import_note(
        entry_id, f"auto-split: {len(created_node_ids)} chunk(s) as lore nodes; "
                  f"LLM categorised {min(llm_tagged, len(created_node_ids))} of them")
    db_conn.mark_zettel_entry_processed(entry_id)
    print(f"[ZETTEL] ✅ Entry '{entry_title}' fully processed: {len(created_node_ids)} nodes")


# ═══════════════════════════════════════════════════════════
# HYBRID RETRIEVAL (Read Path)
# ═══════════════════════════════════════════════════════════

def _reciprocal_rank_fusion(ranked_lists: list, k: int = 60) -> list:
    """
    Merge multiple ranked result lists using Reciprocal Rank Fusion.
    Each list is a list of (node_id, score) tuples.
    Returns a single merged list sorted by fused score.
    """
    scores = {}
    
    for ranked_list in ranked_lists:
        for rank, (node_id, _) in enumerate(ranked_list):
            if node_id not in scores:
                scores[node_id] = 0.0
            scores[node_id] += 1.0 / (k + rank + 1)
    
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused


def _truncate_at_sentence(text: str, max_chars: int = 200) -> str:
    """
    FIX(mid-word-cut): truncate at the last sentence boundary inside the
    window (falling back to the last word boundary). content[:200] used to
    cut mid-word, and a dangling fragment in injected context invites the
    downstream model to invent its completion.
    """
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    sentence_ends = list(re.finditer(r'[.!?](?:\s|$)', window))
    if sentence_ends:
        return window[:sentence_ends[-1].end()].strip()
    space = window.rfind(" ")
    if space > max_chars // 3:
        return window[:space].rstrip() + "…"
    return window.rstrip() + "…"


def load_persona_on_demand_files(persona_key: str) -> list:
    import json
    from app_paths import APP_ROOT
    json_path = os.path.join(APP_ROOT, "personas.json")
    if not os.path.exists(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        persona_data = data.get(persona_key.lower(), {})
        files = persona_data.get("on_demand_files", [])
        if not files and persona_data.get("on_demand_file"):
            files = [persona_data.get("on_demand_file")]
            
        # FIX(backend-move): relative paths in personas.json are relative to the
        # project root ("personas/rick_ondemand.txt"). They used to be joined to
        # this file's directory, which WAS the root until the 2026-10-03 move into
        # backend/; after it every path pointed at backend/personas/..., which does
        # not exist, so no on-demand file was ever recompiled again (an edited
        # rick_ondemand.txt never reached the graph) and a typed-link rebuild would
        # have parsed nothing. Root first -- the same join the pre-move code made,
        # so the stored source_entry_id strings still match -- then backend/.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        resolved = []
        for p in files:
            if p:
                if not os.path.isabs(p):
                    root_p = os.path.join(APP_ROOT, p)
                    local_p = os.path.join(base_dir, p)
                    p = root_p if (os.path.exists(root_p) or not os.path.exists(local_p)) else local_p
                resolved.append(p)
        return resolved
    except Exception:
        return []

def get_file_sha256(filepath: str) -> str:
    import hashlib
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""

def get_file_hash_cached(filepath: str) -> str:
    """
    FIX(per-message-io): mtime-gated SHA-256. compile_behavioral_zettels runs on
    every message via query_knowledge_graph, so previously every on-demand file
    was fully re-hashed per turn. Only re-hash when mtime changes.
    """
    try:
        mtime = os.path.getmtime(filepath)
    except OSError:
        return ""
    cached = _FILE_HASH_CACHE.get(filepath)
    if cached and cached[0] == mtime:
        return cached[1]
    file_hash = get_file_sha256(filepath)
    if file_hash:
        _FILE_HASH_CACHE[filepath] = (mtime, file_hash)
    return file_hash

class OnDemandModule:
    """Single parsed ON_DEMAND module."""
    __slots__ = ("id", "title", "type", "links", "triggers", "content", "priority", "token_estimate", "regex",
                 "match")

    def __init__(self, id: str, title: str, type: str, links: list,
                 triggers: list, content: str, priority: str = "NORMAL", match: str = "all"):
        self.id = id
        self.title = title
        self.type = type
        self.links = links
        self.triggers = [t.strip().lower() for t in triggers if t.strip()]
        self.content = content.strip()
        self.priority = priority
        # "exact": only the exact trigger regex may fire this module; the
        # inflected and semantic trigger layers skip it (header `Match: exact`)
        self.match = match
        self.token_estimate = int(len(self.content) / 4)
        
        if self.triggers:
            sorted_triggers = sorted(self.triggers, key=len, reverse=True)
            self.regex = re.compile(rf"\b({'|'.join(re.escape(t) for t in sorted_triggers)})\b", re.IGNORECASE)
        else:
            self.regex = None

def _parse_header(header_text: str) -> dict:
    """Simple key-value parser for the YAML-like header."""
    header = {}
    lines = header_text.split("\n")
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        
        if key == "Links":
            links = []
            for item in value.split(","):
                item = item.strip()
                match = re.search(r"\[+([^\]]+)\]+", item)
                if match:
                    links.append(match.group(1))
            header[key] = links
        elif key == "Triggers":
            triggers = [t.strip() for t in value.split(",")]
            header[key] = triggers
        else:
            header[key] = value
            
    return header

# v3 (2026-10-04): `Match:` is a header key. Before it was one, a `Match:` line
# ENDED the header and became the first line of the body (the same way any
# unknown key line does), so every file must be re-parsed once.
ON_DEMAND_PARSER_VERSION = 3


_HEADER_KEYS = ("ID", "Title", "Type", "Links", "Triggers", "Priority", "Match")

# `Match:` header values. "all" (default) = exact, inflected and semantic
# trigger layers; "exact" = the exact `\b<trigger>\b` regex only. For modules
# that must stay rare -- Rick's WALL: SCAR/REL/WOUND/TRAUMA/FAIL/VULN/COPE fire
# on their exact words or not at all.
MATCH_MODES = ("all", "exact")


def _match_mode(value, module_id: str = "?", name: str = "?", quiet: bool = False) -> str:
    """Normalise a `Match:` header value; anything unknown is 'all', loudly."""
    v = (value or "all").strip().lower()
    if v in MATCH_MODES:
        return v
    if not quiet:
        print(f"[ZETTEL PARSER] {name}: module {module_id} has unknown Match value {value!r} "
              f"(expected one of {', '.join(MATCH_MODES)}); treating it as 'all'.")
    return "all"


def _is_header_key_line(line: str) -> bool:
    l = line.lstrip()
    return any(l.startswith(k + ":") for k in _HEADER_KEYS)


def _split_embedded_header(block: str, filepath: str = "", name: str = None, quiet: bool = False) -> list:
    """Classify a '---'-delimited block into [(kind, text)...], kind in
    {"header", "body"}, tolerating missing separators in either position:

      body ... ID: X / Title: ...        <- no '---' before the header
      ID: X / Title: ... / body ...      <- no '---' after the header
      body ... ID: X ... body ... ID: Y  <- chains of both

    A header starts at an `ID:` line whose next non-empty line is `Title:` and
    ends at the last consecutive known key line (ID/Title/Type/Links/Triggers/
    Priority). Text before it is body; text after it is fed back through this
    function, so a run of fused modules yields every one of them. Each
    recovery is logged so the author can put the separators back.

    `name` labels the log lines (default: the basename of `filepath`), so text
    that never lived in a file (a lorebook entry) can be parsed too. `quiet`
    silences the recovery lines for re-parses that already reported them."""
    lines = block.split("\n")
    n = len(lines)
    start = None
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("ID:"):
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and lines[j].lstrip().startswith("Title:"):
            start = i
            break
    if start is None:
        return [("body", block)]
    # header runs while lines are known keys (blank lines inside are tolerated)
    k = start
    last_key = start
    while k < n:
        if _is_header_key_line(lines[k]):
            last_key = k
            k += 1
        elif not lines[k].strip() and k + 1 < n and _is_header_key_line(lines[k + 1]):
            k += 1
        else:
            break
    header = "\n".join(lines[start:last_key + 1]).strip()
    before = "\n".join(lines[:start]).strip()
    after = "\n".join(lines[last_key + 1:]).strip()
    label = (name if name is not None else os.path.basename(filepath)) or "?"
    first = header.splitlines()[0][:40]
    if before and not quiet:
        print(f"[ZETTEL PARSER] {label}: recovered a header without a '---' before it ({first}); "
              f"put the separator back in the source file.")
    if after and not quiet:
        print(f"[ZETTEL PARSER] {label}: recovered a header without a '---' after it ({first}); "
              f"put the separator back in the source file.")
    out = []
    if before:
        out.append(("body", before))
    out.append(("header", header))
    if after:
        out.extend(_split_embedded_header(after, filepath, name, quiet))
    return out


def parse_on_demand_file(filepath: str) -> list:
    """Parse a Markdown/text file containing multiple YAML-header modules.
    Thin wrapper: the format lives in parse_on_demand_text, which the
    lorebook also uses for handwritten entries (2026-10-04)."""
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except Exception:
        return []

    return parse_on_demand_text(raw_content, os.path.basename(filepath))


def parse_on_demand_text(text: str, name: str = "?", stats: dict = None, quiet: bool = False) -> list:
    """Parse text containing multiple YAML-header modules -- the on-demand file
    format, which is also Sky's handwritten-lore format:

        ---
        ID: X-001 / Title: ... / Type: ... / Links: [[Y-001]] / Triggers: a, b
        ---
        body

    `name` labels the log lines. `stats`, if a dict is passed, receives what
    the parser set aside without saying so in its own log (files have always
    been silent about it, and their log stays byte-identical):
        preamble_chars  body text before the first header
        orphan_chars    body text after a module's body with no header of its
                        own (usually a '---' used as a rule INSIDE a body)
        no_body         IDs whose header had no body (these ARE logged)
    `quiet` suppresses every log line (re-parses of text already reported)."""
    # Files arrive newline-normalised (text mode); a lorebook paste may carry
    # CRLF, which would otherwise leave '\r' on every body line.
    raw_content = (text or "").replace("\r\n", "\n")
    modules = []
    # FIX(parser-eats-module): the old test for "is this block a header" was
    # `"ID:" in block and "Title:" in block`. When an author forgets the '---'
    # between a body and the next header, the two fuse into one block, the
    # fused block passes that test, and the module whose body it was is
    # silently dropped -- along with every module in the run until the next
    # clean separator. Rick_kb.txt lost 8 modules that way (TOOL-CHECK/SYS/
    # INFO/OPT, ARCH-DATA/ROBUST, SCAR-008/009) and rick_ondemand.txt 2
    # (RESEARCH-002/003), measured 2026-09-13. Now: a header is a block whose
    # FIRST line is `ID:`; a body block that contains an `ID:` line followed
    # by a `Title:` line is split there, the front half kept as the body it
    # was, the back half treated as the header it is. Recovery is logged so
    # the author can put the separator back.
    blocks = re.split(r"^---\s*$", raw_content, flags=re.MULTILINE)

    current_header = None
    preamble_chars = 0
    orphan_chars = 0
    no_body = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        for kind, part in _split_embedded_header(block, "", name=name, quiet=quiet):
            if kind == "header":
                if current_header is not None:
                    no_body.append(current_header.get('ID', '?'))
                    if not quiet:
                        print(f"[ZETTEL PARSER] {name}: module "
                              f"{current_header.get('ID', '?')} has no body (header followed by header); skipped.")
                current_header = _parse_header(part)
            elif current_header is not None:
                modules.append(OnDemandModule(
                    id=current_header.get("ID", "UNKNOWN"),
                    title=current_header.get("Title", "Untitled"),
                    type=current_header.get("Type", "ON_DEMAND"),
                    links=current_header.get("Links", []),
                    triggers=current_header.get("Triggers", []),
                    content=part,
                    priority=current_header.get("Priority", "NORMAL"),
                    match=_match_mode(current_header.get("Match"), current_header.get("ID", "?"),
                                      name, quiet)
                ))
                current_header = None
            # else: body text with no header -- preamble (before the first
            # module) or an orphan (after one), ignored; counted for `stats`
            elif modules or no_body:
                orphan_chars += len(part)
            else:
                preamble_chars += len(part)
    if current_header is not None:
        no_body.append(current_header.get('ID', '?'))
        if not quiet:
            print(f"[ZETTEL PARSER] {name}: module "
                  f"{current_header.get('ID', '?')} has no body (end of file); skipped.")

    if stats is not None:
        stats["preamble_chars"] = preamble_chars
        stats["orphan_chars"] = orphan_chars
        stats["no_body"] = no_body
    return modules

def compile_behavioral_zettels(username: str, persona: str, on_demand_paths: list):
    """
    Parses and compiles static behavioral zettel files into SQLite zettel_nodes.
    Uses SHA-256 content hashes to avoid re-embedding unchanged files.
    """
    import sqlite3
    
    db_conn = db.UserManager()
    model = get_shared_model()
    
    # 1. Get existing compiled behavioral nodes for this persona
    conn = sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT source_entry_id, content_hash
        FROM zettel_nodes
        WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND node_class='behavioral'
    """, (username, persona))
    rows = c.fetchall()
    conn.close()
    
    existing_hashes = {}
    for source, val in rows:
        if source:
            existing_hashes[source] = val

    any_recompiled = False

    for path in on_demand_paths:
        if not path or not os.path.exists(path):
            continue
            
        file_hash = get_file_hash_cached(path)
        if not file_hash:
            continue
            
        # The stored hash carries the parser version: a parser fix (2026-09-13,
        # missing-separator recovery) must recompile files whose bytes did not
        # change, or the modules it recovers never reach the graph.
        file_hash = f"{file_hash}:p{ON_DEMAND_PARSER_VERSION}"
        # If the file hasn't changed, skip compilation
        if path in existing_hashes and existing_hashes[path] == file_hash:
            continue
            
        print(f"[ZETTEL COMPILER] File '{os.path.basename(path)}' changed or not yet compiled. Parsing...")
        
        # Parse modules from the zettel file
        modules = parse_on_demand_file(path)
        if not modules:
            continue
            
        # Delete existing nodes from this source file
        conn = sqlite3.connect(db.DB_PATH)
        c = conn.cursor()
        # FIX(dangling-links): a recompile replaced this file's nodes with new
        # UUIDs but left every edge that pointed at the old ones. Rick had 85 of
        # 99 edges dangling by 2026-09-13. Edges go with their nodes.
        c.execute("""
            DELETE FROM zettel_links
            WHERE source_node_id IN (SELECT id FROM zettel_nodes
                                     WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?)
               OR target_node_id IN (SELECT id FROM zettel_nodes
                                     WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?)
        """, (username, persona, path, username, persona, path))
        c.execute("""
            DELETE FROM zettel_nodes
            WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=? AND source_entry_id=?
        """, (username, persona, path))
        c.execute("""
            DELETE FROM zettel_fts
            WHERE node_db_id NOT IN (SELECT id FROM zettel_nodes)
        """)
        conn.commit()
        conn.close()
        
        # Embed and insert the parsed modules
        for mod in modules:
            embedding_blob = None
            vec = None
            if model:
                try:
                    vec = model.encode([mod.content], convert_to_numpy=True).flatten()
                    embedding_blob = vec.tobytes()
                except Exception as e:
                    print(f"[ZETTEL COMPILER ERROR] Failed to embed module {mod.id}: {e}")
            
            # Serialize triggers into content
            serialized_content = f"TRIGGERS: {','.join(mod.triggers)}\n\n{mod.content}"
            node_id_pk = str(uuid.uuid4())
            
            db_conn.add_zettel_node(
                node_id_pk=node_id_pk,
                username=username,
                persona=persona,
                node_id_tag=mod.id,
                title=mod.title,
                content=serialized_content,
                category="CONCEPT",
                embedding_blob=embedding_blob,
                source_entry_id=path,
                node_class='behavioral',
                trigger_type='DETERMINISTIC',
                content_hash=file_hash,
                trigger_match=mod.match
            )
            
        any_recompiled = True

    # FIX(stale-vectors): a recompile DELETES the file's previous nodes, but
    # bulk_append could only add — the deleted nodes' vectors stayed in the
    # in-process/Redis matrix forever. Invalidate instead; the caller
    # (query_knowledge_graph) rebuilds the cache from SQLite on this same turn.
    if any_recompiled:
        invalidate_zettel_cache(username, persona)
    # FIX(typed-links): the header parser has always read `Links: [[A]], [[B]]`
    # into OnDemandModule.links, and this function never looked at it. Not one
    # typed link ever became an edge; query-time graph expansion had nothing to
    # walk and the daemon saw the whole KB as "isolated pockets". Rebuild after
    # any recompile, and once per process for a persona that has none yet.
    _ensure_typed_links(username, persona, on_demand_paths, force=any_recompiled)


TYPED_LINK_RELATIONSHIP = "links_to"
_TYPED_LINKS_OK = set()


def _ensure_typed_links(username: str, persona: str, on_demand_paths: list, force: bool = False):
    import sqlite3
    key = (username.lower(), persona.lower())
    if not force and key in _TYPED_LINKS_OK:
        return
    if not force:
        # Bootstrap: a persona compiled before this fix has behavioral nodes and
        # zero typed edges. One COUNT, once per process.
        try:
            conn = sqlite3.connect(db.DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT count(*) FROM zettel_links l JOIN zettel_nodes n ON n.id = l.source_node_id
                WHERE n.username COLLATE NOCASE=? AND n.persona COLLATE NOCASE=? AND l.relationship=?
            """, (username, persona, TYPED_LINK_RELATIONSHIP))
            have = c.fetchone()[0]
            conn.close()
        except Exception:
            have = 1  # unreadable -> do not thrash
        if have > 0:
            _TYPED_LINKS_OK.add(key)
            return
    try:
        rebuild_typed_links(username, persona, on_demand_paths)
    except Exception as e:
        print(f"[ZETTEL COMPILER] typed-link rebuild failed (non-fatal): {e}")
    _TYPED_LINKS_OK.add(key)


def rebuild_typed_links(username: str, persona: str, on_demand_paths: list) -> dict:
    """Parse every on-demand file for the persona AND every handwritten
    lorebook entry (2026-10-04), resolve each module's typed Links against the
    persona's node_id tags, replace the `links_to` edges of every source that
    was parsed with the result, and prune any edge whose end no longer exists.
    Returns {"edges": int, "unresolved": [tag...], "pruned": int,
    "lore_modules": int}. Pure SQLite, no model, no LLM.

    Edges are replaced per SOURCE, not per persona: a file that could not be
    read this time (missing, mid-save) keeps the edges it already had instead
    of losing every one of them. That was the failure waiting in the old
    wholesale delete -- with the 2026-10-03 path regression, one rebuild would
    have wiped all of Rick's typed edges."""
    import sqlite3, uuid
    wanted = {}
    sources = set()
    for path in on_demand_paths or []:
        if not path or not os.path.exists(path):
            continue
        mods = parse_on_demand_file(path)
        if mods:
            sources.add(path)
        for mod in mods:
            if mod.links:
                wanted.setdefault(mod.id, [])
                wanted[mod.id].extend(l for l in mod.links if l and l != mod.id)
    conn = sqlite3.connect(db.DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT node_id, id, source_entry_id, node_class FROM zettel_nodes
                 WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?""", (username, persona))
    tag_to_pk = {}
    entry_tag_to_pk = {}
    behavioral_sources = set()
    for tag, pk, src, ncls in c.fetchall():
        tag_to_pk.setdefault(tag, pk)
        # lore nodes carry their brackets in node_id; typed links do not
        tag_to_pk.setdefault(tag.strip("[]"), pk)
        entry_tag_to_pk.setdefault((src, tag), pk)
        if ncls == "behavioral":
            behavioral_sources.add(src)
    # Handwritten lorebook entries: an entry counts once it owns behavioral
    # nodes (process_entry's handwritten path). An entry whose text merely
    # LOOKS handwritten but went through the old auto-split path owns lore
    # nodes with invented IDs; its Links header describes nodes it does not
    # have, so it contributes nothing.
    lore_wanted = []
    if behavioral_sources:
        c.execute("""SELECT id, title, raw_content FROM zettel_entries
                     WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?""", (username, persona))
        for eid, etitle, raw in c.fetchall():
            if eid not in behavioral_sources:
                continue
            sources.add(eid)
            for mod in parse_on_demand_text(raw or "", f"lore entry '{etitle}'", quiet=True):
                if mod.links:
                    lore_wanted.append((eid, mod.id, [l for l in mod.links if l and l != mod.id]))
    # prune edges with a missing end (global: a dangling edge is garbage for everyone)
    c.execute("""DELETE FROM zettel_links
                 WHERE source_node_id NOT IN (SELECT id FROM zettel_nodes)
                    OR target_node_id NOT IN (SELECT id FROM zettel_nodes)""")
    pruned = c.rowcount
    # replace the typed edges of every source parsed above (its text is the source of truth)
    for src in sources:
        c.execute("""DELETE FROM zettel_links
                     WHERE relationship=? AND source_node_id IN
                           (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
                                                          AND source_entry_id=?)""",
                  (TYPED_LINK_RELATIONSHIP, username, persona, src))
    edges = 0
    unresolved = set()
    seen = set()
    ts = str(__import__("datetime").datetime.now())
    # file modules resolve their source tag persona-wide (unchanged); a lore
    # module resolves it only among its own entry's nodes, so a module skipped
    # as a duplicate never grafts its Links onto the copy that won
    resolved_wanted = [(tag_to_pk.get(s), t) for s, t in wanted.items()]
    resolved_wanted += [(entry_tag_to_pk.get((eid, s)), t) for eid, s, t in lore_wanted]
    for src_pk, targets in resolved_wanted:
        if not src_pk:
            continue
        for tgt_tag in targets:
            tgt_pk = tag_to_pk.get(tgt_tag)
            if not tgt_pk:
                unresolved.add(tgt_tag)
                continue
            if (src_pk, tgt_pk) in seen:
                continue
            seen.add((src_pk, tgt_pk))
            c.execute("""INSERT OR IGNORE INTO zettel_links (id, source_node_id, target_node_id, relationship, strength, created_at, label)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (str(uuid.uuid4()), src_pk, tgt_pk, TYPED_LINK_RELATIONSHIP, 1.0, ts, "core"))
            edges += 1
    conn.commit()
    conn.close()
    print(f"[ZETTEL COMPILER] typed links for {username}/{persona}: {edges} edge(s) from "
          f"{len(wanted)} module(s)"
          + (f" + {len(lore_wanted)} handwritten lore module(s)" if lore_wanted else "")
          + f"; {len(unresolved)} unresolved tag(s); {pruned} dangling edge(s) pruned"
          + (f" | unresolved: {sorted(unresolved)[:8]}" if unresolved else ""), flush=True)
    return {"edges": edges, "unresolved": sorted(unresolved), "pruned": pruned,
            "lore_modules": len(lore_wanted)}


# ═══════════════════════════════════════════════════════════
# TRIGGER MATCHING (Phase 0 of the read path)
# ═══════════════════════════════════════════════════════════
#
# Three layers, strictly ordered, sharing the MAX_DETERMINISTIC cap:
#   1. exact     the original `\b<trigger>\b` regex on the lowercased query.
#                Unchanged and always first: anything that fired before still
#                fires, ahead of everything below.
#   2. inflected query and trigger tokens normalised by a small explicit suffix
#                stripper; multi-word triggers match as token sequences. Fixes
#                "debug" vs "debugging", "bugs" vs "bug", "errors" vs "error".
#                Lesion: ZETTEL_TRIGGER_INFLECT_OFF=1.
#   3. semantic  each module's trigger phrases are embedded once (shared
#                MiniLM, cached); the query vector the vector path already
#                computes is compared against them and the result blended with
#                that path's own query-vs-body similarity. A module at or above
#                SEMANTIC_TRIGGER_THRESHOLD becomes a trigger seed AFTER exact
#                and inflected hits. The same score gates layer 2 (see
#                INFLECT_SEMANTIC_GATE). Lesion: ZETTEL_TRIGGER_SEMANTIC_OFF=1.
#
# Measured 2026-10-04 on Rick's two files, 18 realistic prompts: the exact
# regex fired nothing on 10 of 18 ("can you debug this for me", "it throws
# errors on startup", "the fix didn't work"...), while the body-embedding
# vector path ranked the right modules top at 0.17-0.37, under its 0.40 floor.

TRIGGER_INFLECT_OFF_ENV = "ZETTEL_TRIGGER_INFLECT_OFF"
TRIGGER_SEMANTIC_OFF_ENV = "ZETTEL_TRIGGER_SEMANTIC_OFF"
TRIGGER_INFLECT_GATE_OFF_ENV = "ZETTEL_TRIGGER_INFLECT_GATE_OFF"

# All four constants below are PROPOSED (2026-10-04): picked from a labelled
# sweep on Rick's two files (labs/trigger_calibration.py -- positives: every
# module's own trigger phrases, 65 hand-written paraphrases that avoid the
# trigger words, the 18 audit prompts; negatives: 62 small-talk / family /
# philosophy / everyday lines). Synthetic data, not live chat. Re-measure.
#
# Semantic score of a module = BODY_WEIGHT * cos(query, module body embedding;
# the vector path's own number) + (1 - BODY_WEIGHT) * best cos(query, one of
# its trigger phrases). Youden J (recall on paraphrases+probes minus the
# negative fire rate) on that sweep: one card embedding 0.38 @ 0.26, best
# single phrase 0.22 @ 0.40-0.45, this 50/50 blend 0.51 @ 0.29-0.30. Phrases
# alone confuse senses ("security vulnerability" -> Rick's emotional
# VULN-* modules); the body says what the module is about.
SEMANTIC_TRIGGER_MODE = "phrase_max"       # or "card": one "Title: a, b, c" embedding
SEMANTIC_TRIGGER_BODY_WEIGHT = 0.5
# Re-picked 2026-10-04 (round 2) on the files as Sky's WALL rewrite left them
# (33 SCAR/REL/WOUND/TRAUMA/FAIL/VULN/COPE modules `Match: exact`), by the rule
# "best J among thresholds where the full pipeline fires outside the acceptable
# set on <= 4/62 negatives and a CODE/DEBUG/SEC/ARCH module on <= 1/62": 0.34
# (3/62 and 1/62, the old exact matcher's own counts on those files). 0.30 had
# the best unconstrained J but 8/62 and 3/62.
SEMANTIC_TRIGGER_THRESHOLD = 0.34
# An inflected-only hit must also clear this semantic score, when one exists.
# On the sweep every wanted inflected hit scored >= 0.30 ("debug" -> debugging
# 0.41-0.45, "bugs"/"errors" 0.30-0.35) and every unwanted one <= 0.24 ("what
# do you mean" -> meaning 0.22, "log in" -> logging 0.19, "my parents" ->
# parenting 0.16, "on a scale of" -> scaling 0.14). Lesion:
# ZETTEL_TRIGGER_INFLECT_GATE_OFF=1 (plain inflection, as first specified).
INFLECT_SEMANTIC_GATE = 0.25

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_INFLECT_MIN_LEN = 4        # shorter tokens ("bug", "ai", "cve") only match as-is...
_VOWELS = "aeiou"           # ...but "bugs" -> "bug" still reaches them

# (model, phrase) -> unit vector, shared by every user/persona (Sky's test
# accounts carry the same Rick modules). LRU so a long-running process cannot
# grow it forever.
_TRIGGER_VEC_CACHE = OrderedDict()
_TRIGGER_VEC_CACHE_MAX = 20000
_TRIGGER_VEC_LOCK = threading.Lock()


def _tokenize(text: str) -> list:
    text = (text or "").lower().replace("’", "'").replace("‘", "'")
    return _TOKEN_RE.findall(text)


def _is_cvc(s: str) -> bool:
    """consonant-vowel-consonant ending, last not w/x/y (Porter's *o): the
    stems that lost a silent e -- cod(e), cop(e), tim(e) -- but not fix, act."""
    return (len(s) >= 3 and s[-3] not in _VOWELS and s[-2] in _VOWELS
            and s[-1] not in _VOWELS and s[-1] not in "wxy")


def _verbal_bases(b: str) -> set:
    """-ing / -ed / -er -> candidate base(s). One suffix, explicit rules."""
    for suf in ("ing", "ed", "er"):
        if not b.endswith(suf):
            continue
        if suf == "ed" and b.endswith("eed"):
            return set()                  # need, speed, proceed: not a past tense
        stem = b[:-len(suf)]
        if len(stem) < 3 or not any(ch in _VOWELS + "y" for ch in stem):
            return set()                  # string, bring, used, red
        if (stem[-1] == stem[-2] and stem[-1] not in _VOWELS + "ylsz"
                and len(stem) - 1 >= (4 if suf == "er" else 3)):
            return {stem[:-1]}            # debugg -> debug, logg -> log (not fall, miss, buzz)
        if suf == "er":
            # agent/comparative -er only with a 4+ letter stem: tester -> test,
            # hacker -> hack; power, summer, matter keep their own form
            return {stem} if len(stem) >= 4 else set()
        if len(stem) == 3:
            return {stem + "e"} if _is_cvc(stem) else {stem}    # coding -> code; fixing -> fix
        return {stem, stem + "e"}         # caching -> cach | cache; testing -> test | teste
    return set()


@functools.lru_cache(maxsize=65536)
def _token_forms(tok: str) -> frozenset:
    """The token plus every base it could be an inflection of. Two tokens
    match when their form sets intersect, so "debug" meets "debugging"
    ({debugging, debug}) and "bugs" meets "bug". Explicit rules, no dictionary:
      possessive  rick's -> rick, users' -> users
      apostrophe  can't  -> cant (so the apostrophe-less spelling meets it)
      plural/3rd  bugs -> bug, crashes -> crash, vulnerabilities -> vulnerability
      -ied        tried -> try
      -ing/-ed/-er  see _verbal_bases (consonant undoubling, silent e)
    Different words CAN share a base ("meaning" and "mean" both reach mean).
    That is the price of this layer; the 2026-10-04 report measures it."""
    forms = {tok}
    t = tok.replace("’", "'")
    if t.endswith("'s"):
        t = t[:-2]
        forms.add(t)
    elif t.endswith("s'") and len(t) > 3:
        t = t[:-1]
        forms.add(t)
    if "'" in t:
        forms.add(t.replace("'", ""))
        return frozenset(f for f in forms if f)
    if len(t) < _INFLECT_MIN_LEN or not t.isalpha():
        return frozenset(f for f in forms if f)
    bases = {t}
    if t.endswith(("ies", "ied")) and len(t) > 4:
        bases.add(t[:-3] + "y")
    if t.endswith(("sses", "shes", "ches", "xes", "zes")):
        bases.add(t[:-2])
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        bases.add(t[:-1])
    for b in list(bases):
        bases |= _verbal_bases(b)
    forms |= bases
    return frozenset(f for f in forms if f)


def _node_triggers(node: dict):
    """Trigger phrases of a compiled behavioral node, or None if its content
    is not in the `TRIGGERS: a,b\\n\\nbody` shape."""
    content = node.get("content", "") or ""
    # FIX(defensive-unpack): the old `header, _ = content.split("\n\n", 1)`
    # raised ValueError on any behavioral node without a blank line.
    # The compiler guarantees the format today, but guard anyway.
    if not content.startswith("TRIGGERS:") or "\n\n" not in content:
        return None
    header = content.split("\n\n", 1)[0]
    return [t for t in (t.strip().lower() for t in header[len("TRIGGERS:"):].split(",")) if t]


def _build_trigger_cache(signature, det_nodes: list) -> dict:
    """Everything Phase 0 needs that depends only on the deterministic node
    set: the exact regexes (unchanged), the inflected token index, and the
    rows for the semantic trigger cards (embedded lazily, on first use).
    A node with trigger_match == 'exact' (header `Match: exact`) gets its
    regexes and nothing else: neither fuzzy layer can ever reach it."""
    patterns = []
    phrases = []          # (trigger, node_pk, order) in file order
    cards = []            # (node_pk, title, [triggers])
    for node in det_nodes:
        triggers = _node_triggers(node)
        if triggers is None:
            continue
        fuzzy = (node.get("trigger_match") or "all") != "exact"
        for t in triggers:
            # FIX(word-boundary): \b misbehaves when a trigger starts/ends
            # with a non-word char ("!roll", "..."). Use whitespace
            # lookarounds on those edges instead.
            left = r"\b" if (t[0].isalnum() or t[0] == "_") else r"(?<!\S)"
            right = r"\b" if (t[-1].isalnum() or t[-1] == "_") else r"(?!\S)"
            patterns.append((re.compile(left + re.escape(t) + right), node["id"]))
            if fuzzy:
                phrases.append((t, node["id"], len(patterns) - 1))
        if triggers and fuzzy:
            cards.append((node["id"], node.get("title", "") or "", triggers))
    index = {}
    for phrase, pk, order in phrases:
        toks = _tokenize(phrase)
        if not toks:
            continue
        seq = tuple(_token_forms(tok) for tok in toks)
        entry = (seq, pk, order)
        for f in seq[0]:
            index.setdefault(f, []).append(entry)
    return {"signature": signature, "patterns": patterns, "inflect_index": index,
            "cards": cards, "card_vecs": {}}


def _match_inflected(trig_cache: dict, query_text: str) -> list:
    """Node pks whose trigger matches the query as an inflection-tolerant
    token sequence, in trigger (file) order."""
    index = trig_cache.get("inflect_index") or {}
    if not index:
        return []
    qforms = [_token_forms(t) for t in _tokenize(query_text)]
    hits = {}
    for i, fs in enumerate(qforms):
        seen_entries = set()
        for f in fs:
            for entry in index.get(f, ()):
                if id(entry) in seen_entries:
                    continue
                seen_entries.add(id(entry))
                seq, pk, order = entry
                n = len(seq)
                if i + n > len(qforms):
                    continue
                if all(seq[j] & qforms[i + j] for j in range(1, n)):
                    if pk not in hits or order < hits[pk]:
                        hits[pk] = order
    return [pk for pk, _ in sorted(hits.items(), key=lambda kv: kv[1])]


def _embed_unit(model, texts: list) -> np.ndarray:
    """Unit vectors for `texts`, through the shared (model, text)->vector LRU.
    Keyed by model identity too: a vector from one model is noise to another."""
    out = [None] * len(texts)
    missing = []
    mkey = id(model)
    with _TRIGGER_VEC_LOCK:
        for i, t in enumerate(texts):
            v = _TRIGGER_VEC_CACHE.get((mkey, t))
            if v is not None:
                _TRIGGER_VEC_CACHE.move_to_end((mkey, t))
                out[i] = v
            else:
                missing.append(i)
    if missing:
        vecs = np.asarray(model.encode([texts[i] for i in missing], convert_to_numpy=True), dtype=np.float32)
        vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        with _TRIGGER_VEC_LOCK:
            for k, i in enumerate(missing):
                out[i] = vecs[k]
                _TRIGGER_VEC_CACHE[(mkey, texts[i])] = vecs[k]
            while len(_TRIGGER_VEC_CACHE) > _TRIGGER_VEC_CACHE_MAX:
                _TRIGGER_VEC_CACHE.popitem(last=False)
    if not out:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack(out)


def _trigger_card_texts(title: str, triggers: list, mode: str) -> list:
    if mode == "card":
        return [f"{title}: {', '.join(triggers)}" if title else ", ".join(triggers)]
    return list(triggers)                 # phrase_max


def _semantic_trigger_scores(trig_cache: dict, model, query_vec, mode: str = None,
                             body_sims: dict = None, body_weight: float = None) -> dict:
    """node_pk -> semantic trigger score of that module for this query.
    Without `body_sims`: the best similarity between the query and the
    module's trigger card(s). With it (node_pk -> cos to the module's body
    embedding, i.e. the vector path's own numbers): the PROPOSED blend
    body_weight * body + (1 - body_weight) * card; a module missing from
    `body_sims` counts its body as 0. Card vectors are built once per
    deterministic-node signature (the cache this lives in is dropped whenever
    the node set changes)."""
    mode = mode or SEMANTIC_TRIGGER_MODE
    cards = trig_cache.get("cards") or []
    if not cards or model is None or query_vec is None:
        return {}
    built = trig_cache["card_vecs"].get(mode)
    if built is None:
        owners, texts = [], []
        for pk, title, triggers in cards:
            for text in _trigger_card_texts(title, triggers, mode):
                owners.append(pk)
                texts.append(text)
        built = (owners, _embed_unit(model, texts))
        trig_cache["card_vecs"][mode] = built
    owners, mat = built
    if not owners:
        return {}
    q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
    q = q / max(float(np.linalg.norm(q)), 1e-12)
    sims = mat @ q
    scores = {}
    for pk, s in zip(owners, sims):
        s = float(s)
        if s > scores.get(pk, -2.0):
            scores[pk] = s
    if body_sims:
        w = SEMANTIC_TRIGGER_BODY_WEIGHT if body_weight is None else body_weight
        scores = {pk: w * float(body_sims.get(pk, 0.0)) + (1.0 - w) * s for pk, s in scores.items()}
    return scores


def soft_trigger_hits(trig_cache: dict, query_text: str, exclude: set, model=None, query_vec=None,
                      body_sims: dict = None, threshold: float = None, gate: float = None,
                      mode: str = None, report: dict = None) -> list:
    """Layers 2 and 3, in rank order, excluding `exclude` (the exact hits):
    [(node_pk, "inflected", score|None), ..., (node_pk, "semantic", score), ...].

    Semantic scores exist only with a model, a query vector AND the vector
    path's body similarities (`body_sims`); without them layer 3 is silent and
    layer 2 runs ungated. Each layer honours its lesion flag at call time;
    ZETTEL_TRIGGER_SEMANTIC_OFF=1 means no embeddings in trigger matching at
    all, so it also lifts the gate. `report`, if given, receives
    {"gated": [(pk, score)]} -- inflected hits the gate turned away."""
    out = []
    taken = set(exclude or ())
    scores = None
    if (not _env_flag(TRIGGER_SEMANTIC_OFF_ENV) and model is not None
            and query_vec is not None and body_sims):
        scores = _semantic_trigger_scores(trig_cache, model, query_vec, mode, body_sims=body_sims)
    gated = []
    if not _env_flag(TRIGGER_INFLECT_OFF_ENV):
        bar = INFLECT_SEMANTIC_GATE if gate is None else gate
        use_gate = scores is not None and not _env_flag(TRIGGER_INFLECT_GATE_OFF_ENV)
        for pk in _match_inflected(trig_cache, query_text):
            if pk in taken:
                continue
            s = scores.get(pk) if scores is not None else None
            if use_gate and (s is None or s < bar):
                gated.append((pk, s))
                continue
            taken.add(pk)
            out.append((pk, "inflected", s))
    if scores:
        thr = SEMANTIC_TRIGGER_THRESHOLD if threshold is None else threshold
        for pk, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            if s < thr:
                break
            if pk not in taken:
                taken.add(pk)
                out.append((pk, "semantic", s))
    if report is not None:
        report["gated"] = gated
    return out


def query_knowledge_graph(username: str, persona: str, query_text: str, top_k: int = 5) -> str:
    """
    Full read-path: hybrid vector + keyword search with graph expansion.
    
    Returns a formatted context string ready for injection into the LLM prompt.
    Zero LLM cost — pure math + DB queries.
    """
    # ── COMPILE BEHAVIORAL ZETTELS ON-DEMAND ──
    on_demand_paths = load_persona_on_demand_files(persona)
    if on_demand_paths:
        compile_behavioral_zettels(username, persona, on_demand_paths)

    db_conn = db.UserManager()
    model = get_shared_model()
    
    # Quick-exit if no nodes exist
    # FIX(per-message-io): the per-turn node fetch only feeds node_lookup and
    # the trigger scan — it does NOT need embedding blobs (that's what the
    # cache is for). Requires a companion change in database.py:
    #   get_zettel_nodes_for_persona(..., include_embeddings=True) that, when
    #   False, SELECTs every column EXCEPT the embedding blob.
    # Falls back gracefully until that lands.
    try:
        all_nodes = db_conn.get_zettel_nodes_for_persona(username, persona, include_embeddings=False)
        _nodes_have_blobs = False
    except TypeError:
        all_nodes = db_conn.get_zettel_nodes_for_persona(username, persona)
        _nodes_have_blobs = True
    print(f"[ZETTEL] Query ({username}/{persona}): '{query_text[:60]}' | Nodes in graph: {len(all_nodes)}")
    if not all_nodes:
        return ""
    
    # Ensure cache is active for this persona, reading under lock
    cache_key = (username, persona)
    redis_key = f"zettel:cache:{username}:{persona}"
    cache = None
    
    with _CACHE_LOCK:
        cache = _EMBEDDING_CACHE.get(cache_key)
        
    if cache is None and redis_client.is_active():
        redis_data = redis_client.get(redis_key)
        if redis_data:
            try:
                cache = pickle.loads(redis_data)
                # Invalidation may leave a None tombstone — treat it as a miss
                # and never store None into the local cache.
                if cache is not None:
                    with _CACHE_LOCK:
                        _cache_put(cache_key, cache)
                    print(f"[ZETTEL CACHE] Hot cache loaded from Redis for '{persona}'")
            except Exception as e:
                print(f"[REDIS ERROR] Failed to load cache from Redis: {e}")
                cache = None
                
    if cache is None:
        # Rebuild needs the blobs — re-fetch WITH embeddings only on this
        # (rare) path, if the fast fetch above excluded them.
        nodes_for_cache = all_nodes
        if not _nodes_have_blobs:
            nodes_for_cache = db_conn.get_zettel_nodes_for_persona(username, persona)
        # load_zettel_cache internally handles the lock, eviction, loading, and writing to Redis/local cache
        cache = load_zettel_cache(username, persona, nodes_for_cache)
    
    # ── VECTOR PATH ──
    vector_ranked = []
    query_vec = None   # reused by the semantic trigger layer in Phase 0...
    body_sims = {}     # ...with these: node pk -> cos(query, node body), every node
    if model and cache:
        query_vec = model.encode([query_text], convert_to_numpy=True).reshape(1, -1)
        
        # FIX(lock-contention): only snapshot references under the lock.
        # bulk_append replaces cache["embeddings"] via np.vstack (a NEW array)
        # rather than mutating in place, so the snapshot stays internally
        # consistent — no need to hold the lock through an O(N·d) matmul and
        # block every background writer.
        with _CACHE_LOCK:
            node_vecs = cache["embeddings"]
            node_ids = list(cache["node_ids"])
        sims = cosine_similarity(query_vec, node_vecs).flatten()
        body_sims = dict(zip(node_ids, sims.tolist()))

        ranked_indices = np.argsort(sims)[::-1]
        
        # Raised query relevance threshold from 0.15 to 0.40 to prevent hairballs
        for idx in ranked_indices:
            if sims[idx] > 0.40:
                vector_ranked.append((node_ids[idx], float(sims[idx])))
        print(f"[ZETTEL] Vector hits (sim>0.40): {len(vector_ranked)}")
    
    # ── KEYWORD PATH (FTS5) ──
    keyword_ranked = []
    try:
        fts_results = db_conn.search_zettel_fts(username, persona, query_text)
        for r in fts_results:
            # FTS5 rank is negative (lower = better), invert for fusion
            keyword_ranked.append((r["id"], -r.get("fts_rank", 0)))
        print(f"[ZETTEL] FTS5 hits: {len(keyword_ranked)}")
    except Exception as e:
        print(f"[ZETTEL] FTS5 search error (non-fatal): {e}")
    
    # ── Phase 0: SCAN DETERMINISTIC BEHAVIORAL TRIGGERS ──
    # FIX(per-message-regex): compile trigger patterns once per deterministic
    # node set and cache them, instead of parsing headers + compiling regexes
    # for every node on every message. The signature guard rebuilds the cache
    # whenever the deterministic node set changes (recompile, deletion).
    node_lookup = {n["id"]: n for n in all_nodes}
    det_nodes = [n for n in all_nodes
                 if n.get("node_class") == "behavioral" and n.get("trigger_type") == "DETERMINISTIC"]
    signature = frozenset(n["id"] for n in det_nodes)

    trig_cache = _TRIGGER_REGEX_CACHE.get(cache_key)
    if not trig_cache or trig_cache["signature"] != signature:
        # exact regexes + inflection index + semantic card rows, all keyed to
        # this node set (see _build_trigger_cache)
        trig_cache = _build_trigger_cache(signature, det_nodes)
        _TRIGGER_REGEX_CACHE[cache_key] = trig_cache

    # Sanity cap on trigger seeds per turn (see Phase 2). Exact hits fill it
    # first, exactly as before; inflected, then semantic hits get what is left.
    MAX_DETERMINISTIC = 3

    behav_seeds = []
    seen_behav_ids = set()
    query_lower = query_text.lower()
    for pattern, node_pk in trig_cache["patterns"]:
        if node_pk in seen_behav_ids:
            continue
        if pattern.search(query_lower):
            seen_behav_ids.add(node_pk)
            behav_seeds.append(node_lookup[node_pk])

    # Layers 2 + 3 (inflected, semantic). Only the hits that fit under the cap
    # become seeds; the rest stay eligible for ordinary vector/keyword ranking
    # below instead of being swallowed the way over-cap exact hits are.
    exact_count = len(behav_seeds)
    soft_report = {}
    try:
        soft = soft_trigger_hits(trig_cache, query_text, seen_behav_ids, model=model,
                                 query_vec=query_vec, body_sims=body_sims, report=soft_report)
    except Exception as e:
        print(f"[ZETTEL] soft trigger layers failed (non-fatal, exact triggers unaffected): {e}")
        soft = []
    if soft_report.get("gated"):
        print(f"[ZETTEL] inflected trigger hit(s) under the semantic gate "
              f"(INFLECT_SEMANTIC_GATE={INFLECT_SEMANTIC_GATE}), not seeded: "
              f"{[(node_lookup[pk].get('node_id', '?'), None if s is None else round(s, 2)) for pk, s in soft_report['gated'] if pk in node_lookup]}")
    room = max(0, MAX_DETERMINISTIC - exact_count)
    for node_pk, layer, score in soft[:room]:
        seen_behav_ids.add(node_pk)
        behav_seeds.append(node_lookup[node_pk])
    if behav_seeds:
        def _tag(n):
            return n.get("node_id", "?")
        fired = [_tag(n) for n in behav_seeds[:exact_count]]
        parts = [f"exact={fired}"] if fired else []
        for layer in ("inflected", "semantic"):
            got = [f"{_tag(node_lookup[pk])}" + (f" {s:.2f}" if s is not None else "")
                   for pk, lay, s in soft[:room] if lay == layer]
            if got:
                parts.append(f"{layer}={got}")
        print(f"[ZETTEL] Trigger seeds: " + " ".join(parts))
    if len(soft) > room:
        print(f"[ZETTEL] {len(soft) - room} inflected/semantic trigger hit(s) over the "
              f"MAX_DETERMINISTIC={MAX_DETERMINISTIC} cap left to vector/keyword ranking: "
              f"{[node_lookup[pk].get('node_id', '?') for pk, _, _ in soft[room:]]}")

    # ── Phase 1: RECIPROCAL RANK FUSION & PARTITIONING ──
    fused = []
    if vector_ranked or keyword_ranked:
        ranked_lists = []
        if vector_ranked:
            ranked_lists.append(vector_ranked)
        if keyword_ranked:
            ranked_lists.append(keyword_ranked)
        fused = _reciprocal_rank_fusion(ranked_lists)

    behav_ranked = []
    lore_ranked = []
    
    for nid, _ in fused:
        if nid not in node_lookup:
            continue
        node = node_lookup[nid]
        if node["id"] in seen_behav_ids:
            continue
        if node.get("node_class") == "behavioral":
            behav_ranked.append(node)
        else:
            lore_ranked.append(node)

    # ── Phase 2: DYNAMIC BUDGET GUARANTEES & SEED SELECTION ──
    # FIX(unused-param): top_k was accepted and silently ignored; it now IS the budget.
    TOTAL_BUDGET = max(1, top_k)
    BEHAV_FLOOR = 2
    # FIX(dropped-triggers): deterministic modules are author-mandated ("must
    # fire"), so they get guaranteed slots even above BEHAV_FLOOR — previously
    # a 3rd simultaneous trigger was silently cut by the floor of 2. The cap
    # (MAX_DETERMINISTIC = 3, now set in Phase 0 because the inflected and
    # semantic layers share it) is a sanity limit against trigger-word
    # pileups, not a floor.
    EXPAND_FANOUT = 2
    HARD_CEILING = 9
    LINK_FLOOR = 0.70
    EXCLUDE_LABELS = {"incidental"}

    deterministic_seeds = behav_seeds[:MAX_DETERMINISTIC]
    if len(behav_seeds) > MAX_DETERMINISTIC:
        print(f"[ZETTEL] WARNING: {len(behav_seeds) - MAX_DETERMINISTIC} deterministic "
              f"trigger(s) dropped by MAX_DETERMINISTIC={MAX_DETERMINISTIC} cap")

    # Complete behavioral seed selection (deterministic first, then RRF-ranked)
    behav_seeds_all = list(deterministic_seeds)
    for node in behav_ranked:
        if node["id"] not in seen_behav_ids:
            seen_behav_ids.add(node["id"])
            behav_seeds_all.append(node)

    # Lore seed selection
    lore_seeds_all = list(lore_ranked)

    # Deterministic hits are guaranteed; semantic behavioral hits get the floor.
    behav_target = max(min(BEHAV_FLOOR, len(behav_seeds_all)), len(deterministic_seeds))
    behav_target = min(behav_target, TOTAL_BUDGET)
    lore_target = min(TOTAL_BUDGET - behav_target, len(lore_seeds_all))

    # FIX(slack-bias): the old loop always fed lore first, so behavioral
    # candidates were starved of slack whenever ANY lore existed. Alternate.
    give_lore_next = True
    while (behav_target + lore_target < TOTAL_BUDGET):
        has_more_behav = behav_target < len(behav_seeds_all)
        has_more_lore = lore_target < len(lore_seeds_all)

        if not has_more_behav and not has_more_lore:
            break

        if give_lore_next and has_more_lore:
            lore_target += 1
        elif has_more_behav:
            behav_target += 1
        elif has_more_lore:
            lore_target += 1
        give_lore_next = not give_lore_next

    # Slice primary seeds to targets
    final_behav = behav_seeds_all[:behav_target]
    final_lore_seeds = lore_seeds_all[:lore_target]
    
    # Get primary IDs for expansion deduplication
    primary_behav_ids = {n["id"] for n in final_behav}
    primary_lore_ids = {n["id"] for n in final_lore_seeds}

    # ── Phase 3: CLASS-ISOLATED EXPANSION (Run on selected primary seeds only) ──
    # VERIFIED: db.get_linked_nodes traverses edges bidirectionally (matches
    # both source_node_id=? and target_node_id=?), so new→existing auto-links
    # expand in both directions. No stale-lore direction bias.
    def get_class_isolated_expansion(seeds, allowed_class, primary_ids):
        expand_nodes = []
        seen_expand_ids = set(primary_ids)
        
        for seed in seeds:
            linked = db_conn.get_linked_nodes(seed["id"], depth=1)
            # Sort links by strength descending for fanout capping
            linked.sort(key=lambda x: x.get("strength", 0.5), reverse=True)
            
            fanout_count = 0
            for ln in linked:
                strength = ln.get("strength", 0.5)
                label = ln.get("label", "related")
                target_class = ln.get("node_class", "lore")
                
                if strength >= LINK_FLOOR and label not in EXCLUDE_LABELS and target_class == allowed_class:
                    if ln["id"] not in seen_expand_ids:
                        if fanout_count < EXPAND_FANOUT:
                            seen_expand_ids.add(ln["id"])
                            expand_nodes.append(ln)
                            fanout_count += 1
        return expand_nodes

    behav_expand = get_class_isolated_expansion(final_behav, "behavioral", primary_behav_ids)
    lore_expand = get_class_isolated_expansion(final_lore_seeds, "lore", primary_lore_ids)

    # ── Phase 4: ASSEMBLY & HARD CEILING (Respect the 9 unique nodes ceiling) ──
    # FIX(dead-expansion): behav_expand was computed in Phase 3 and then thrown
    # away — behavioral neighbors never reached the context, and the DB calls
    # were wasted. Both expansion classes now share the remaining ceiling
    # slots, interleaved so neither class monopolizes the expansion budget.
    expand_slots = HARD_CEILING - len(final_behav) - len(final_lore_seeds)
    final_behav_expand = []
    final_lore_expand = []
    if expand_slots > 0:
        bi, li = 0, 0
        take_lore = True
        while len(final_behav_expand) + len(final_lore_expand) < expand_slots:
            has_b = bi < len(behav_expand)
            has_l = li < len(lore_expand)
            if not has_b and not has_l:
                break
            if take_lore and has_l:
                final_lore_expand.append(lore_expand[li]); li += 1
            elif has_b:
                final_behav_expand.append(behav_expand[bi]); bi += 1
            elif has_l:
                final_lore_expand.append(lore_expand[li]); li += 1
            take_lore = not take_lore

    # ── Phase 5: FORMAT OUTPUT ──
    behav_parts = []
    if final_behav:
        behav_parts.extend([
            "\n[ACTIVE BEHAVIORAL MODULES: Character Psychological Substrate & Lived Experience]",
            "Treat the following directives as high-priority behavioral modifiers for this turn only.",
            "They represent external fictional character psychology. Do NOT cite these modules.",
            "--------------------------------"
        ])
        for node in final_behav:
            node_id = node.get("node_id", "")
            title = node.get("title", "")
            content = node.get("content", "")
            # FIX(defensive-unpack): guard the split — a malformed module
            # without a blank line used to raise ValueError here.
            if content.startswith("TRIGGERS:") and "\n\n" in content:
                _, content = content.split("\n\n", 1)
            behav_parts.append(f"### MODULE: {node_id} ({title})")
            behav_parts.append(content)
            behav_parts.append("---")
        # FIX(dead-expansion): behavioral neighbors are now actually injected.
        if final_behav_expand:
            behav_parts.append("[LINKED_MODULES]")
            for ln in final_behav_expand:
                rel = ln.get("relationship", "related_to")
                ln_content = ln.get("content", "")
                if ln_content.startswith("TRIGGERS:") and "\n\n" in ln_content:
                    ln_content = ln_content.split("\n\n", 1)[1]
                behav_parts.append(f"  → ({rel}) {ln.get('node_id', '')}: {_truncate_at_sentence(ln_content, 200)}")
            behav_parts.append("[/LINKED_MODULES]")
        behav_parts.append("[/ACTIVE BEHAVIORAL MODULES]\n")

    lore_parts = []
    if final_lore_seeds or final_lore_expand:
        lore_parts.append("[KNOWLEDGE_GRAPH: Established Fictional Lore & Character Backstory]")
        for node in final_lore_seeds:
            node_id = node.get("node_id", "")
            title = node.get("title", "")
            content = node.get("content", "")
            category = node.get("category", "")
            lore_parts.append(f"--- {node_id} [{category}] {title} ---")
            lore_parts.append(content)
            
        if final_lore_expand:
            lore_parts.append("")
            lore_parts.append("[LINKED_CONTEXT]")
            # FIX(double-cap): the [:5] re-cap is gone — Phase 4's ceiling
            # allocation is now the single source of truth for expansion size.
            for ln in final_lore_expand:
                rel = ln.get("relationship", "related_to")
                node_id = ln.get("node_id", "")
                content = ln.get("content", "")
                # FIX(mid-word-cut): a dangling half-sentence invites the
                # downstream model to hallucinate its completion.
                lore_parts.append(f"  → ({rel}) {node_id}: {_truncate_at_sentence(content, 200)}")
            lore_parts.append("[/LINKED_CONTEXT]")
            
        lore_parts.append("[/KNOWLEDGE_GRAPH]")

    ret_parts = []
    if behav_parts:
        ret_parts.append("\n".join(behav_parts))
    if lore_parts:
        ret_parts.append("\n".join(lore_parts))
        
    return "\n".join(ret_parts)


# ═══════════════════════════════════════════════════════════
# CONTEXT FORMATTING
# ═══════════════════════════════════════════════════════════

def format_zettel_context(primary_nodes: list, linked_nodes: list = None) -> str:
    """
    LEGACY: query_knowledge_graph now formats its own output inline (Phase 5)
    and does NOT call this. If nothing else imports it, delete it — otherwise
    the two formats will silently drift apart. Kept temporarily for external
    callers.

    Format retrieved graph nodes into an LLM-injectable context block.
    
    The character's prompt already has a protocol for scanning [[ID]] tags
    and doing secondary lookups — this format is designed to work WITH that.
    """
    if not primary_nodes:
        return ""
    
    parts = ["[KNOWLEDGE_GRAPH]"]
    
    for node in primary_nodes:
        node_id = node.get("node_id", "")
        title = node.get("title", "")
        content = node.get("content", "")
        category = node.get("category", "")
        
        parts.append(f"--- {node_id} [{category}] {title} ---")
        parts.append(content)
    
    # Add linked/associated nodes (graph expansion results)
    if linked_nodes:
        parts.append("")
        parts.append("[LINKED_CONTEXT]")
        for ln in linked_nodes[:5]:  # Cap linked context
            rel = ln.get("relationship", "related_to")
            node_id = ln.get("node_id", "")
            content = ln.get("content", "")
            parts.append(f"  → ({rel}) {node_id}: {content[:200]}")
        parts.append("[/LINKED_CONTEXT]")
    
    parts.append("[/KNOWLEDGE_GRAPH]")
    
    return "\n".join(parts)


# ── Native Persona Curation & Graph Editing API ──────────────────────────────

def resolve_tag_to_pk(username: str, persona: str, query: str):
    """
    Resolves a human-readable tag (e.g., '[[CONCEPT-Citadel-001]]' or 'CONCEPT-Citadel-001')
    or an exact node title to its SQLite database primary key UUID.

    Fails loud on ambiguity: if multiple nodes match, returns candidate tags rather
    than guessing and producing a bad graph edge.

    Returns:
        tuple: (node_pk, node_tag, error_message)
    """
    clean_query = query.strip()
    if clean_query.startswith("[[") and clean_query.endswith("]]"):
        clean_query = clean_query[2:-2].strip()

    if not clean_query:
        return None, None, "INVALID_QUERY: Query string cannot be empty."

    db_manager = db.UserManager()
    nodes = db_manager.get_zettel_nodes_for_persona(username, persona, include_embeddings=False)

    matches = []
    for n in nodes:
        node_tag = n.get("node_id", "")
        title = n.get("title", "")
        if clean_query.lower() == node_tag.lower() or clean_query.lower() == title.lower():
            matches.append(n)

    if not matches:
        return None, None, f"NOT_FOUND: No node matching tag or title '{query}' found."

    if len(matches) > 1:
        match_summary = ", ".join([f"[[{m['node_id']}]] ('{m['title']}')" for m in matches])
        return None, None, f"AMBIGUOUS: Multiple nodes matched '{query}': {match_summary}. Please specify the exact [[TAG]]."

    target = matches[0]
    return target["id"], target["node_id"], None


def store_curated_observation(username: str, persona: str, title: str, content: str, category: str = "OBSERVATION") -> str:
    """
    Allows a persona to deliberately store a curated observation or note in its zettel graph.
    Hardcodes safety invariants (node_class='lore', trigger_type='PROBABILISTIC') and
    indexes both vector embedding and FTS5 mirror synchronously at write time.
    """
    # 1. Content length cap
    clean_content = content.strip()[:1500]
    clean_title = title.strip()[:120]
    clean_category = category.strip().upper()[:30] if category else "OBSERVATION"

    if not clean_content:
        return "ERROR: Observation content cannot be empty."
    if not clean_title:
        clean_title = f"Note: {clean_content[:30]}..."

    db_manager = db.UserManager()
    existing_nodes = db_manager.get_zettel_nodes_for_persona(username, persona, include_embeddings=False)
    existing_tags = {n.get("node_id") for n in existing_nodes if n.get("node_id")}

    node_id_tag = _generate_node_id(clean_category, clean_title, existing_tags)
    node_id_pk = str(uuid.uuid4())

    # 2. Synchronous Vector Embedding Generation
    shared_model = get_shared_model()
    if shared_model:
        try:
            embedding = shared_model.encode(clean_content, convert_to_numpy=True)
            embedding_blob = embedding.tobytes()
        except Exception as e:
            print(f"[ZETTEL CURATION] Embedding encoding failed: {e}")
            embedding = None
            embedding_blob = None
    else:
        embedding = None
        embedding_blob = None

    # 3. Write to SQLite and FTS5 (dual-indexed inside add_zettel_node)
    success = db_manager.add_zettel_node(
        node_id_pk=node_id_pk,
        username=username,
        persona=persona,
        node_id_tag=node_id_tag,
        title=clean_title,
        content=clean_content,
        category=clean_category,
        embedding_blob=embedding_blob,
        source_entry_id="source:persona_curation",
        node_class="lore",
        trigger_type="PROBABILISTIC"
    )

    if not success:
        return f"DATABASE_ERROR: Failed to commit node {node_id_tag} to SQLite."

    # 4. Incremental Matrix Append (Zero Full Invalidation)
    if shared_model and embedding is not None:
        bulk_append_to_zettel_cache(username, persona, [{
            "id": node_id_pk,
            "tag": node_id_tag,
            "embedding": embedding
        }])

    return f"SUCCESS: Stored observation node {node_id_tag} ('{clean_title}'). Node is indexed in knowledge graph."


def create_curated_link(username: str, persona: str, source_tag: str, target_tag: str, relationship: str = "relates_to", strength: float = 0.5) -> str:
    """
    Allows a persona to deliberately create a weighted relational edge between two zettel nodes.
    Resolves human-readable tags/titles to database primary keys and deduplicates edges.
    """
    src_pk, src_tag, src_err = resolve_tag_to_pk(username, persona, source_tag)
    if src_err:
        return f"LINK_ERROR (Source): {src_err}"

    tgt_pk, tgt_tag, tgt_err = resolve_tag_to_pk(username, persona, target_tag)
    if tgt_err:
        return f"LINK_ERROR (Target): {tgt_err}"

    if src_pk == tgt_pk:
        return "LINK_ERROR: Cannot link a node to itself."

    clean_rel = relationship.strip().lower()[:50] if relationship else "relates_to"
    bounded_strength = max(0.1, min(1.0, float(strength)))

    db_manager = db.UserManager()
    link_id = str(uuid.uuid4())
    success = db_manager.add_zettel_link(
        link_id=link_id,
        source_node_id=src_pk,
        target_node_id=tgt_pk,
        relationship=clean_rel,
        strength=bounded_strength,
        label="curated"
    )

    if not success:
        return f"DATABASE_ERROR: Failed to insert relational link between {src_tag} and {tgt_tag}."

    return f"SUCCESS: Created relational edge: {src_tag} --({clean_rel}, strength={bounded_strength:.2f})--> {tgt_tag}."

