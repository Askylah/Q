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
    
    # 2. Chunk
    chunks = chunk_text(raw_content)
    if not chunks:
        print(f"[ZETTEL] No chunks generated for entry {entry_id}")
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
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas.json")
    if not os.path.exists(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        persona_data = data.get(persona_key.lower(), {})
        files = persona_data.get("on_demand_files", [])
        if not files and persona_data.get("on_demand_file"):
            files = [persona_data.get("on_demand_file")]
            
        base_dir = os.path.dirname(os.path.abspath(__file__))
        resolved = []
        for p in files:
            if p:
                if not os.path.isabs(p):
                    p = os.path.join(base_dir, p)
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
    __slots__ = ("id", "title", "type", "links", "triggers", "content", "priority", "token_estimate", "regex")

    def __init__(self, id: str, title: str, type: str, links: list,
                 triggers: list, content: str, priority: str = "NORMAL"):
        self.id = id
        self.title = title
        self.type = type
        self.links = links
        self.triggers = [t.strip().lower() for t in triggers if t.strip()]
        self.content = content.strip()
        self.priority = priority
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

def parse_on_demand_file(filepath: str) -> list:
    """Parse a Markdown/text file containing multiple YAML-header modules."""
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except Exception:
        return []

    modules = []
    blocks = re.split(r"^---$", raw_content, flags=re.MULTILINE)
    
    current_header = None
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        if "ID:" in block and "Title:" in block:
            current_header = _parse_header(block)
        elif current_header:
            new_module = OnDemandModule(
                id=current_header.get("ID", "UNKNOWN"),
                title=current_header.get("Title", "Untitled"),
                type=current_header.get("Type", "ON_DEMAND"),
                links=current_header.get("Links", []),
                triggers=current_header.get("Triggers", []),
                content=block,
                priority=current_header.get("Priority", "NORMAL")
            )
            modules.append(new_module)
            current_header = None

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
                content_hash=file_hash
            )
            
        any_recompiled = True

    # FIX(stale-vectors): a recompile DELETES the file's previous nodes, but
    # bulk_append could only add — the deleted nodes' vectors stayed in the
    # in-process/Redis matrix forever. Invalidate instead; the caller
    # (query_knowledge_graph) rebuilds the cache from SQLite on this same turn.
    if any_recompiled:
        invalidate_zettel_cache(username, persona)


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
        patterns = []
        for node in det_nodes:
            content = node.get("content", "")
            # FIX(defensive-unpack): the old `header, _ = content.split("\n\n", 1)`
            # raised ValueError on any behavioral node without a blank line.
            # The compiler guarantees the format today, but guard anyway.
            if not content.startswith("TRIGGERS:") or "\n\n" not in content:
                continue
            header = content.split("\n\n", 1)[0]
            for t in (t.strip().lower() for t in header[len("TRIGGERS:"):].split(",")):
                if not t:
                    continue
                # FIX(word-boundary): \b misbehaves when a trigger starts/ends
                # with a non-word char ("!roll", "..."). Use whitespace
                # lookarounds on those edges instead.
                left = r"\b" if (t[0].isalnum() or t[0] == "_") else r"(?<!\S)"
                right = r"\b" if (t[-1].isalnum() or t[-1] == "_") else r"(?!\S)"
                patterns.append((re.compile(left + re.escape(t) + right), node["id"]))
        trig_cache = {"signature": signature, "patterns": patterns}
        _TRIGGER_REGEX_CACHE[cache_key] = trig_cache

    behav_seeds = []
    seen_behav_ids = set()
    query_lower = query_text.lower()
    for pattern, node_pk in trig_cache["patterns"]:
        if node_pk in seen_behav_ids:
            continue
        if pattern.search(query_lower):
            seen_behav_ids.add(node_pk)
            behav_seeds.append(node_lookup[node_pk])

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
    # below is a sanity limit against trigger-word pileups, not a floor.
    MAX_DETERMINISTIC = 3
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

