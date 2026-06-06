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
from sklearn.metrics.pairwise import cosine_similarity
from rag_engine import get_shared_model
import database as db
import pickle
import redis_client

# Contiguous matrix cache per (username, persona) to prevent loading binary vectors from SQLite on every turn.
# Thread-safe global lock to protect memory reads/writes.
_EMBEDDING_CACHE = {}
_CACHE_LOCK = threading.Lock()

def evict_other_personas_from_cache(username: str, active_persona: str):
    """Evicts any cached persona data for this user other than the currently active persona. Assumes caller holds _CACHE_LOCK."""
    other_keys = [k for k in _EMBEDDING_CACHE.keys() if k[0] == username and k[1] != active_persona]
    for k in other_keys:
        del _EMBEDDING_CACHE[k]
        print(f"[ZETTEL CACHE] Evicted persona '{k[1]}' cache for user '{username}' on persona switch.")

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
        evict_other_personas_from_cache(username, persona)
        _EMBEDDING_CACHE[cache_key] = cache_data
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

# Valid entity categories for the LLM flash pass
VALID_CATEGORIES = frozenset([
    "CHAR", "LOC", "EVT", "FAC", "ITEM", 
    "CONCEPT", "REL", "HIST", "ABILITY", "ORG"
])

# Flash model for entity extraction (cheap + fast)
FLASH_MODEL = "google/gemini-3-flash-preview"

# Hyperscaling: Max chunks to process in a single LLM pass
CHUNKS_PER_BATCH = 15


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
    return [c for c in chunks if c and len(c.strip()) > 10]


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
        for i in range(0, len(chunks), CHUNKS_PER_BATCH):
            batch = chunks[i : i + CHUNKS_PER_BATCH]
            print(f"[ZETTEL]   Extraction Wave: Chunks {i} to {i + len(batch) - 1}")
            batch_data = _extract_entities_via_llm(batch, api_keys, model_id, base_index=i)
            
            if batch_data:
                if "nodes" in batch_data:
                    all_nodes.extend(batch_data["nodes"])
                if "relationships" in batch_data:
                    all_relationships.extend(batch_data["relationships"])
            
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
                    strength=0.8
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
                        strength=round(float(sims[idx]), 3)
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


def query_knowledge_graph(username: str, persona: str, query_text: str, top_k: int = 5) -> str:
    """
    Full read-path: hybrid vector + keyword search with graph expansion.
    
    Returns a formatted context string ready for injection into the LLM prompt.
    Zero LLM cost — pure math + DB queries.
    """
    db_conn = db.UserManager()
    model = get_shared_model()
    
    # Quick-exit if no nodes exist
    all_nodes = db_conn.get_zettel_nodes_for_persona(username, persona)
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
                with _CACHE_LOCK:
                    evict_other_personas_from_cache(username, persona)
                    _EMBEDDING_CACHE[cache_key] = cache
                print(f"[ZETTEL CACHE] Hot cache loaded from Redis for '{persona}'")
            except Exception as e:
                print(f"[REDIS ERROR] Failed to load cache from Redis: {e}")
                cache = None
                
    if cache is None:
        # load_zettel_cache internally handles the lock, eviction, loading, and writing to Redis/local cache
        cache = load_zettel_cache(username, persona, all_nodes)
    
    # ── VECTOR PATH ──
    vector_ranked = []
    if model and cache:
        query_vec = model.encode([query_text], convert_to_numpy=True).reshape(1, -1)
        
        # Lock during vector array reading to avoid background thread mutations
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
    
    # ── FUSION ──
    if not vector_ranked and not keyword_ranked:
        print("[ZETTEL] No results from either path — nothing to inject")
        return ""
    
    ranked_lists = []
    if vector_ranked:
        ranked_lists.append(vector_ranked)
    if keyword_ranked:
        ranked_lists.append(keyword_ranked)
    
    fused = _reciprocal_rank_fusion(ranked_lists)
    top_node_ids = [node_id for node_id, _ in fused[:top_k]]
    
    # ── GRAPH EXPANSION (1-hop) ──
    expanded_ids = set(top_node_ids)
    linked_nodes_data = []
    
    for node_id in top_node_ids:
        linked = db_conn.get_linked_nodes(node_id, depth=1)
        for ln in linked:
            # Prevent context explosion: only expand links with strength >= 0.70
            if ln["id"] not in expanded_ids and ln.get("strength", 0.5) >= 0.70:
                expanded_ids.add(ln["id"])
                linked_nodes_data.append(ln)
    
    # ── BUILD OUTPUT ──
    # Collect full node data for the top hits
    node_lookup = {n["id"]: n for n in all_nodes}
    
    primary_nodes = []
    for nid in top_node_ids:
        if nid in node_lookup:
            primary_nodes.append(node_lookup[nid])
    
    if not primary_nodes and not linked_nodes_data:
        return ""
    
    return format_zettel_context(primary_nodes, linked_nodes_data)


# ═══════════════════════════════════════════════════════════
# CONTEXT FORMATTING
# ═══════════════════════════════════════════════════════════

def format_zettel_context(primary_nodes: list, linked_nodes: list = None) -> str:
    """
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
