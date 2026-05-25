"""
Pluggable Associative Memory Engine
Adapted from AoiNyte/emergence-kit (Nell & Hana, Feb 2026)

Provides enriched memory with:
- Multi-dimensional emotional scoring
- Auto-association (weighted connection graph)
- Importance-gated temporal decay
- Involuntary recall chains (depth-2)
- Temporal anchor (fuzzy time-since-last-interaction)

Usage:
    from memory_engine import DeepMemory
    dm = DeepMemory(db_conn, username, persona_key)
    dm.store("User loves horror films", memory_type="preference", domain="personal",
             emotions={"joy": 6}, tags=["preference"], importance=5)
    context = dm.get_context_block()
"""

import json
import uuid
import re
import sqlite3
import os
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from datetime import datetime, timezone, timedelta
from rag_engine import get_shared_model

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

# Association scan cap — only scan the N most recent active memories
ASSOCIATION_SCAN_CAP = 200

# Max associations per memory
MAX_ASSOCIATIONS = 8

# Association threshold — minimum score to form a connection
ASSOCIATION_THRESHOLD = 4

# Decay rates (importance points lost per 30-day cycle)
DECAY_RATE_NORMAL = 1.0
DECAY_RATE_SLOW = 0.5

# Tags that NEVER decay
PERMANENT_TAGS = frozenset([
    "permanent", "sacred", "milestone", "first",
    "birthday", "anniversary", "core-identity",
    "non-negotiable"
])

# Memory types that decay at half rate
SLOW_DECAY_TYPES = frozenset(["identity", "emotional", "relationship"])

# Valid memory types
VALID_TYPES = frozenset([
    "fact", "identity", "emotional", "relationship",
    "preference", "creative", "technical", "feedback"
])

# Valid domains
VALID_DOMAINS = frozenset([
    "personal", "technical", "creative",
    "relationship", "world", "meta"
])

# Stop words for keyword extraction
_STOP_WORDS = frozenset([
    "the", "a", "an", "is", "was", "were", "are", "been", "be", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "may", "might", "shall", "can", "need", "dare", "ought", "used",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "because", "but", "and", "or", "if", "while",
    "that", "this", "these", "those", "i", "me", "my", "myself", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "it", "its",
    "they", "them", "their", "what", "which", "who", "whom", "about",
    "also", "like", "even", "still", "already", "much", "many",
])


# ═══════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════

def _ensure_table():
    """Create the deep_memories table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS deep_memories (
            id TEXT PRIMARY KEY,
            username TEXT,
            persona TEXT,
            content TEXT,
            memory_type TEXT,
            domain TEXT,
            emotions TEXT,
            emotion_score INTEGER DEFAULT 0,
            importance INTEGER DEFAULT 5,
            tags TEXT,
            connections TEXT,
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT,
            active INTEGER DEFAULT 1,
            embedding BLOB,
            created_at TEXT
        )
    ''')
    
    # Migration for existing tables
    c.execute("PRAGMA table_info(deep_memories)")
    columns = [row[1] for row in c.fetchall()]
    if 'embedding' not in columns:
        c.execute("ALTER TABLE deep_memories ADD COLUMN embedding BLOB")
        
    conn.commit()
    conn.close()


# Run migration on import
_ensure_table()


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def _generate_id():
    return str(uuid.uuid4())


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _extract_keywords(text):
    """Extract significant words from text for content matching."""
    words = set()
    for word in text.lower().split():
        cleaned = ''.join(c for c in word if c.isalnum() or c == '-')
        if cleaned and len(cleaned) > 2 and cleaned not in _STOP_WORDS:
            words.add(cleaned)
    return words


def _fuzzy_time_delta(dt_str):
    """Convert a datetime string to a fuzzy human description."""
    if not dt_str:
        return "never (first conversation)"
    
    try:
        then = datetime.fromisoformat(dt_str)
        # Handle naive vs aware datetimes
        now = datetime.now(timezone.utc)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        
        delta = now - then
        minutes = delta.total_seconds() / 60
        hours = minutes / 60
        days = delta.days
        
        if minutes < 5:
            return "just now"
        elif minutes < 60:
            return f"about {int(minutes)} minutes ago"
        elif hours < 2:
            return "about an hour ago"
        elif hours < 6:
            return f"a few hours ago"
        elif hours < 24:
            return "earlier today"
        elif days == 1:
            return "yesterday"
        elif days < 4:
            return f"{days} days ago"
        elif days < 7:
            return "a few days ago"
        elif days < 14:
            return "about a week ago"
        elif days < 30:
            return f"about {days // 7} weeks ago"
        elif days < 60:
            return "about a month ago"
        elif days < 365:
            return f"about {days // 30} months ago"
        else:
            return f"over {days // 365} year(s) ago"
    except (ValueError, TypeError):
        return "unknown"


def _calculate_emotion_metrics(emotions):
    """Calculate derived metrics from emotion scores."""
    if not emotions:
        return {"emotion_score": 0, "auto_importance": 2}
    
    score = sum(emotions.values())
    
    if score >= 80:
        auto_imp = 10
    elif score >= 60:
        auto_imp = 9
    elif score >= 40:
        auto_imp = 8
    elif score >= 25:
        auto_imp = 6
    elif score >= 10:
        auto_imp = 4
    else:
        auto_imp = 2
    
    return {"emotion_score": score, "auto_importance": auto_imp}


# ═══════════════════════════════════════════════════════════
# DEEP MEMORY CLASS
# ═══════════════════════════════════════════════════════════

class DeepMemory:
    """
    Pluggable associative memory engine for a single persona.
    
    Initialize per-session:
        dm = DeepMemory(username="askylah", persona="rick")
    """
    
    def __init__(self, username: str, persona: str):
        self.username = username
        self.persona = persona
    
    # ─── CORE OPERATIONS ──────────────────────────────────
    
    def store(self, content: str, memory_type: str = "fact",
              domain: str = "personal", emotions: dict = None,
              tags: list = None, importance: int = None) -> dict:
        """
        Store an enriched memory and auto-associate it.
        
        Returns the created memory dict.
        """
        emotions = emotions or {}
        tags = tags or []
        metrics = _calculate_emotion_metrics(emotions)
        
        if importance is None:
            importance = metrics["auto_importance"]
        
        mem_id = _generate_id()
        now = _now_iso()
        
        memory = {
            "id": mem_id,
            "username": self.username,
            "persona": self.persona,
            "content": content[:500],  # cap content length
            "memory_type": memory_type if memory_type in VALID_TYPES else "fact",
            "domain": domain if domain in VALID_DOMAINS else "personal",
            "emotions": emotions,
            "emotion_score": metrics["emotion_score"],
            "importance": max(0, min(10, importance)),
            "tags": tags,
            "connections": [],
            "access_count": 0,
            "last_accessed": None,
            "active": 1,
            "created_at": now,
        }
        
        # Generate Semantic Embedding
        model = get_shared_model()
        embedding_blob = None
        if model:
            vec = model.encode([content], convert_to_numpy=True)
            embedding_blob = vec.tobytes()
        
        # Write to database
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO deep_memories 
            (id, username, persona, content, memory_type, domain, emotions,
             emotion_score, importance, tags, connections, access_count,
             last_accessed, active, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            memory["id"], self.username, self.persona,
            memory["content"], memory["memory_type"], memory["domain"],
            json.dumps(memory["emotions"]), memory["emotion_score"],
            memory["importance"], json.dumps(memory["tags"]),
            json.dumps(memory["connections"]), 0, None, 1, embedding_blob, now
        ))
        conn.commit()
        conn.close()
        
        # Auto-associate with existing memories
        self._auto_associate(memory)
        
        return memory
    
    def recall(self, query: str = None, limit: int = 10) -> list:
        """
        Retrieve memories with association chains.
        
        If query is provided, filters by keyword relevance.
        Always returns memories sorted by importance (descending).
        Includes depth-2 involuntary associations.
        """
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, content, memory_type, domain, emotions, emotion_score,
                   importance, tags, connections, access_count, last_accessed,
                   active, embedding, created_at
            FROM deep_memories
            WHERE username=? AND persona=? AND active=1
            ORDER BY importance DESC, created_at DESC
        """, (self.username, self.persona))
        rows = c.fetchall()
        conn.close()
        
        memories = [self._row_to_dict(r) for r in rows]
        
        model = get_shared_model()
        
        # Filter by semantic relevance if query provided
        if query and model:
            query_vec = model.encode([query], convert_to_numpy=True)
            scored = []
            
            for m in memories:
                if m["embedding"]:
                    mem_vec = np.frombuffer(m["embedding"], dtype=np.float32).reshape(1, -1)
                    sim = float(cosine_similarity(query_vec, mem_vec)[0][0])
                    
                    # Hybrid Score: (Sim * 10) + Importance + (Emotion Score / 10)
                    total_score = (sim * 10) + m["importance"] + (m["emotion_score"] / 10)
                    
                    if sim > 0.15: # threshold
                        scored.append((m, total_score))
            
            # --- SEMANTIC DEEP ECHO (Reactivation) ---
            c = conn.cursor()
            c.execute("""
                SELECT id, content, memory_type, domain, emotions, emotion_score,
                       importance, tags, connections, access_count, last_accessed,
                       active, embedding, created_at
                FROM deep_memories
                WHERE username=? AND persona=? AND active=0
                LIMIT 50
            """, (self.username, self.persona))
            archived_rows = c.fetchall()
            
            for row in archived_rows:
                am = self._row_to_dict(row)
                if am["embedding"]:
                    mem_vec = np.frombuffer(am["embedding"], dtype=np.float32).reshape(1, -1)
                    sim = float(cosine_similarity(query_vec, mem_vec)[0][0])
                    
                    # If high semantic overlap (Spark), reactivate
                    if sim >= 0.4:
                        print(f"[DEEP ECHO] Semantic reactivation: {am['id']} (sim: {sim:.2f})")
                        c.execute("""
                            UPDATE deep_memories 
                            SET active=1, importance=importance+2, last_accessed=? 
                            WHERE id=?
                        """, (_now_iso(), am["id"]))
                        am["active"] = 1
                        am["importance"] += 2
                        total_score = (sim * 10) + am["importance"] + (am["emotion_score"] / 10)
                        scored.append((am, total_score))
            conn.commit()
            
            scored.sort(key=lambda x: x[1], reverse=True)
            memories = [s[0] for s in scored[:limit]]
        elif query:
            # Fallback for when model is not available
            query_words = _extract_keywords(query)
            scored = []
            for m in memories:
                mem_words = _extract_keywords(m["content"])
                overlap = len(query_words & mem_words)
                if overlap > 0:
                    scored.append((m, overlap + m["importance"]))
            scored.sort(key=lambda x: x[1], reverse=True)
            memories = [s[0] for s in scored[:limit]]
        else:
            memories = memories[:limit]
        
        # Bump access count for recalled memories
        self._bump_access(memories)
        
        # Gather associations (depth-2)
        enriched = []
        seen_ids = {m["id"] for m in memories}
        
        for m in memories:
            associations = self._get_associations(m, depth=2, max_per_level=2)
            # Filter to unseen
            novel = [a for a in associations if a["id"] not in seen_ids]
            for a in novel:
                seen_ids.add(a["id"])
            enriched.append({
                "memory": m,
                "associations": novel
            })
        
        return enriched
    
    def decay_cycle(self) -> dict:
        """
        Run the decay engine. Reduces importance of unaccessed,
        unprotected memories over time.
        
        Returns summary: {"decayed": N, "archived": N, "protected": N}
        """
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, content, memory_type, importance, tags, 
                   access_count, active, created_at
            FROM deep_memories
            WHERE username=? AND persona=? AND active=1
        """, (self.username, self.persona))
        rows = c.fetchall()
        
        now = datetime.now(timezone.utc)
        decayed = 0
        archived = 0
        protected = 0
        
        for row in rows:
            mem_id, content, mem_type, importance, tags_json, access_count, active, created_at = row
            
            tags = json.loads(tags_json) if tags_json else []
            tag_set = {t.lower() for t in tags}
            
            # Protected?
            if tag_set & PERMANENT_TAGS or importance >= 8:
                protected += 1
                continue
            
            # Age check
            try:
                created = datetime.fromisoformat(created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = (now - created).days
            except (ValueError, TypeError):
                continue
            
            if age_days < 30:
                continue
            
            # Calculate decay
            cycles = age_days / 30
            if mem_type in SLOW_DECAY_TYPES:
                decay_amount = cycles * DECAY_RATE_SLOW
            else:
                decay_amount = cycles * DECAY_RATE_NORMAL
            
            # Access resistance
            decay_amount = max(0, decay_amount - (access_count * 0.2))
            new_importance = max(0, round(importance - decay_amount))
            
            if new_importance < importance:
                if new_importance <= 0:
                    c.execute("UPDATE deep_memories SET active=0, importance=0 WHERE id=?", (mem_id,))
                    archived += 1
                else:
                    c.execute("UPDATE deep_memories SET importance=? WHERE id=?", (new_importance, mem_id))
                    decayed += 1
        
        conn.commit()
        conn.close()
        
        return {"decayed": decayed, "archived": archived, "protected": protected}
    
    def get_temporal_anchor(self) -> str:
        """
        Returns a fuzzy time-since-last-interaction string.
        """
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT timestamp FROM conversations 
            WHERE username=? AND persona=?
            ORDER BY id DESC LIMIT 1
        """, (self.username, self.persona))
        row = c.fetchone()
        conn.close()
        
        last_time = row[0] if row else None
        delta = _fuzzy_time_delta(last_time)
        
        now = datetime.now()
        time_of_day = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening" if now.hour < 21 else "night"
        day_name = now.strftime("%A")
        
        return f"[{day_name} {time_of_day}. Last spoke with user: {delta}.]"
    
    def get_context_block(self, max_memories: int = 8) -> str:
        """
        Build the full context injection block for the LLM.
        
        Includes temporal anchor + top memories with associations.
        Formatted as a quiet contextual block, not a directive.
        """
        parts = []
        
        # Temporal anchor
        anchor = self.get_temporal_anchor()
        parts.append(anchor)
        
        # Recall top memories
        recalled = self.recall(limit=max_memories)
        
        if recalled:
            parts.append("[DEEP_MEMORY]")
            for item in recalled:
                m = item["memory"]
                emotion_str = ""
                if m["emotions"]:
                    top_emo = sorted(m["emotions"].items(), key=lambda x: x[1], reverse=True)[:2]
                    emotion_str = f" ({', '.join(f'{k}:{v}' for k, v in top_emo)})"
                
                parts.append(f"- [{m['memory_type']}] {m['content']}{emotion_str}")
                
                # Associations (involuntary recall)
                if item["associations"]:
                    parts.append("  [involuntary_echoes: your brain went there anyway]")
                    for assoc in item["associations"][:2]:
                        parts.append(f"  → {assoc['content'][:80]}")
            
            parts.append("[/DEEP_MEMORY]")
        
        return "\n".join(parts)
    
    def get_memory_count(self) -> int:
        """Return the number of active deep memories."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM deep_memories WHERE username=? AND persona=? AND active=1",
                  (self.username, self.persona))
        count = c.fetchone()[0]
        conn.close()
        return count
    
    # ─── INTERNAL MECHANICS ───────────────────────────────
    
    def _auto_associate(self, new_memory: dict):
        """
        Scan recent memories and build weighted connections.
        Capped at ASSOCIATION_SCAN_CAP most recent active memories.
        """
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, content, memory_type, domain, emotions, tags, importance, connections
            FROM deep_memories
            WHERE username=? AND persona=? AND active=1 AND id != ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (self.username, self.persona, new_memory["id"], ASSOCIATION_SCAN_CAP))
        rows = c.fetchall()
        
        new_tags = set(new_memory.get("tags", []))
        new_emotions = new_memory.get("emotions", {})
        new_domain = new_memory.get("domain", "")
        new_type = new_memory.get("memory_type", "")
        new_importance = new_memory.get("importance", 5)
        new_words = _extract_keywords(new_memory.get("content", ""))
        
        candidates = []
        
        for row in rows:
            mem_id, content, mem_type, domain, emo_json, tags_json, importance, conn_json = row
            
            mem_tags = set(json.loads(tags_json)) if tags_json else set()
            mem_emotions = json.loads(emo_json) if emo_json else {}
            
            score = 0
            reasons = []
            
            # Tag overlap (+3 per shared tag)
            shared_tags = new_tags & mem_tags
            if shared_tags:
                score += len(shared_tags) * 3
                reasons.append(f"tags: {', '.join(list(shared_tags)[:3])}")
            
            # Emotion overlap (+2 per shared, +1 intensity bonus)
            shared_emotions = set(new_emotions.keys()) & set(mem_emotions.keys())
            if shared_emotions:
                emo_score = 0
                for emo in shared_emotions:
                    emo_score += 2
                    if abs(new_emotions.get(emo, 0) - mem_emotions.get(emo, 0)) <= 2:
                        emo_score += 1
                score += emo_score
                top_shared = sorted(shared_emotions, key=lambda e: new_emotions.get(e, 0), reverse=True)[:2]
                reasons.append(f"feelings: {', '.join(top_shared)}")
            
            # Domain match (+2)
            if new_domain and new_domain == domain:
                score += 2
            
            # Type match (+1)
            if new_type and new_type == mem_type:
                score += 1
            
            # Content keyword overlap (+1 per word, capped at 5)
            mem_words = _extract_keywords(content or "")
            shared_words = new_words & mem_words
            if shared_words:
                score += min(len(shared_words), 5)
            
            # Importance proximity (+1 if within 2)
            if abs(new_importance - (importance or 5)) <= 2:
                score += 1
            
            if score >= ASSOCIATION_THRESHOLD:
                strength = min(10, max(1, score // 2))
                candidates.append({
                    "mem_id": mem_id,
                    "content": content,
                    "score": score,
                    "strength": strength,
                    "connections_json": conn_json
                })
        
        # Sort and take top N
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:MAX_ASSOCIATIONS]
        
        if not top:
            conn.close()
            return
        
        # Build connection entries for the new memory
        new_connections = new_memory.get("connections", [])
        
        for cand in top:
            # Add to new memory's connections
            new_connections.append({
                "target_id": cand["mem_id"],
                "strength": cand["strength"],
                "auto": True
            })
            
            # Add bidirectional link to existing memory
            existing_conns = json.loads(cand["connections_json"]) if cand["connections_json"] else []
            existing_ids = {c.get("target_id") for c in existing_conns}
            
            if new_memory["id"] not in existing_ids:
                existing_conns.append({
                    "target_id": new_memory["id"],
                    "strength": cand["strength"],
                    "auto": True
                })
                c.execute("UPDATE deep_memories SET connections=? WHERE id=?",
                         (json.dumps(existing_conns), cand["mem_id"]))
        
        # Update new memory's connections
        c.execute("UPDATE deep_memories SET connections=? WHERE id=?",
                 (json.dumps(new_connections), new_memory["id"]))
        
        conn.commit()
        conn.close()
    
    def _get_associations(self, memory: dict, depth: int = 1, max_per_level: int = 3) -> list:
        """
        Walk the connection graph to find associated memories.
        depth=2 gives "associations of associations" (involuntary recall).
        """
        conn = sqlite3.connect(DB_PATH)
        results = []
        seen_ids = {memory["id"]}
        
        def _gather(mem, current_depth):
            if current_depth > depth:
                return
            
            connections = mem.get("connections", [])
            connections = sorted(connections, key=lambda c: c.get("strength", 0), reverse=True)
            
            count = 0
            for link in connections:
                if count >= max_per_level:
                    break
                
                target_id = link.get("target_id")
                if target_id in seen_ids:
                    continue
                
                # Fetch from DB
                c = conn.cursor()
                c.execute("""
                    SELECT id, content, memory_type, domain, emotions, emotion_score,
                           importance, tags, connections, access_count, last_accessed,
                           active, embedding, created_at
                    FROM deep_memories WHERE id=? AND active=1
                """, (target_id,))
                row = c.fetchone()
                
                if not row:
                    continue
                
                target = self._row_to_dict(row)
                seen_ids.add(target_id)
                results.append(target)
                count += 1
                
                # Recurse for deeper associations
                if current_depth < depth:
                    _gather(target, current_depth + 1)
        
        _gather(memory, 1)
        conn.close()
        return results
    
    def _bump_access(self, memories: list):
        """Increment access_count and update last_accessed for recalled memories."""
        if not memories:
            return
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        now = _now_iso()
        for m in memories:
            c.execute("""
                UPDATE deep_memories 
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id = ?
            """, (now, m["id"]))
        conn.commit()
        conn.close()
    
    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert a database row to a memory dict."""
        return {
            "id": row[0],
            "content": row[1],
            "memory_type": row[2],
            "domain": row[3],
            "emotions": json.loads(row[4]) if row[4] else {},
            "emotion_score": row[5],
            "importance": row[6],
            "tags": json.loads(row[7]) if row[7] else [],
            "connections": json.loads(row[8]) if row[8] else [],
            "access_count": row[9],
            "last_accessed": row[10],
            "active": row[11],
            "embedding": row[12],
            "created_at": row[13],
        }
