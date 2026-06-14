import time
import os
import sys
import json
import sqlite3
import logging
import re
from datetime import datetime

import database as db
import llm_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("stream_worker")

class ConsciousnessWorker:
    def __init__(self):
        self.db_manager = db.UserManager()
        self.running = True
        self.last_monologue_time = {}  # (username, persona) -> timestamp string
        self.idle_threshold = int(os.getenv("DAEMON_IDLE_THRESHOLD", 300))

    def get_db_connection(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def run_cycle(self):
        """Executes a single consciousness evaluation sweep across all active personas."""
        logger.info("[CONSCIOUSNESS_DAEMON] Initiating homeostasis check...")
        
        # Write daemon heartbeat
        try:
            import redis_client
            if redis_client.is_active():
                import time
                redis_client.set_val("q:daemon:heartbeat", str(time.time()).encode('utf-8'))
        except Exception as hb_err:
            logger.debug(f"Failed to write daemon heartbeat to Redis: {hb_err}")

        conn = self.get_db_connection()
        c = conn.cursor()
        
        try:
            # Query all registered users
            c.execute("SELECT username FROM users")
            users = [row["username"] for row in c.fetchall()]
        except Exception as e:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Failed to fetch users: {e}")
            conn.close()
            return
            
        contexts = []
        for username in users:
            # 1. Check user settings for active_persona_key / active_brain_persona
            try:
                c.execute("SELECT active_persona_key FROM user_settings WHERE username=?", (username,))
                row = c.fetchone()
                active_persona = row["active_persona_key"] if row and row["active_persona_key"] else None
            except Exception:
                active_persona = None
            
            # 2. Fallback: Scan observations log for most recent active persona
            if not active_persona:
                try:
                    c.execute("""
                        SELECT persona FROM observations 
                        WHERE username=? 
                        ORDER BY id DESC LIMIT 1
                    """, (username,))
                    row = c.fetchone()
                    active_persona = row["persona"] if row else None
                except Exception:
                    pass
                
            # 3. Fallback 2: Scan conversations for most recent active persona
            if not active_persona:
                try:
                    c.execute("""
                        SELECT persona FROM conversations 
                        WHERE username=? 
                        ORDER BY id DESC LIMIT 1
                    """, (username,))
                    row = c.fetchone()
                    active_persona = row["persona"] if row else None
                except Exception:
                    pass
                
            # 4. Fallback 3: Check custom_personas table
            if not active_persona:
                try:
                    c.execute("""
                        SELECT persona_key FROM custom_personas 
                        WHERE username=? 
                        LIMIT 1
                    """, (username,))
                    row = c.fetchone()
                    active_persona = row["persona_key"] if row else "rick"
                except Exception:
                    active_persona = "rick"
                    
            if active_persona:
                contexts.append({"username": username, "persona": active_persona})
            
        conn.close()

        for ctx in contexts:
            username = ctx["username"]
            persona = ctx["persona"]
            logger.info(f"[CONSCIOUSNESS_DAEMON] Scanning {persona} (user: {username}) for cognitive dissonance...")
            
            # 1. Scan for Entropic Gaps (Isolated clusters / lack of link density)
            gap = self.analyze_entropic_gaps(username, persona)
            if gap:
                self.resolve_entropic_gap(username, persona, gap)

            # 2. Scan for Semantic Polar Conflicts (Contradictions)
            conflict = self.analyze_semantic_conflicts(username, persona)
            if conflict:
                self.resolve_semantic_conflict(username, persona, conflict)

            # 3. Sleep-Wake Coordinator (Idle check)
            try:
                import redis_client
                last_user_time_str = None
                
                db_conn = self.get_db_connection()
                cur = db_conn.cursor()
                cur.execute("""
                    SELECT timestamp FROM conversations 
                    WHERE username=? AND persona=? AND role='user' 
                    ORDER BY id DESC LIMIT 1
                """, (username, persona))
                row = cur.fetchone()
                db_conn.close()
                
                if row:
                    last_user_time_str = row["timestamp"]
                else:
                    last_user_time_str = "2000-01-01 00:00:00"
                    
                already_processed = False
                if self.last_monologue_time.get((username, persona)) == last_user_time_str:
                    already_processed = True
                elif redis_client.is_active():
                    redis_key = f"daemon:last_monologue:{username}:{persona}"
                    try:
                        cached = redis_client.get(redis_key)
                        if cached and cached.decode('utf-8') == last_user_time_str:
                            already_processed = True
                    except Exception:
                        pass
                        
                if not already_processed:
                    if "." in last_user_time_str:
                        t_str = last_user_time_str.split(".")[0]
                    else:
                        t_str = last_user_time_str
                    last_time = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
                    idle_seconds = (datetime.now() - last_time).total_seconds()
                    
                    if idle_seconds >= self.idle_threshold:
                        logger.warning(f"[CONSCIOUSNESS_DAEMON] Persona '{persona}' is idle ({idle_seconds:.1f}s >= {self.idle_threshold}s). Triggering monologue...")
                        self.generate_idle_monologue(username, persona, last_user_time_str, gap, conflict)
            except Exception as idle_err:
                logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Idle coordinator failure: {idle_err}")

    def analyze_entropic_gaps(self, username: str, persona: str) -> dict:
        """
        Calculates node-to-link ratio and clustering metrics to identify
        highly detailed but poorly integrated (isolated) knowledge clusters.
        """
        conn = self.get_db_connection()
        c = conn.cursor()
        
        try:
            c.execute("SELECT node_id, title, content FROM zettel_nodes WHERE username=? AND persona=?", (username, persona))
            nodes = [dict(row) for row in c.fetchall()]
            
            if len(nodes) < 3:
                conn.close()
                return None
                
            c.execute("""
                SELECT source_node_id, target_node_id FROM zettel_links 
                WHERE source_node_id IN (SELECT node_id FROM zettel_nodes WHERE username=? AND persona=?)
            """, (username, persona))
            links = [dict(row) for row in c.fetchall()]
            conn.close()
            
            # Calculate node degree (number of connections)
            degrees = {n["node_id"]: 0 for n in nodes}
            for l in links:
                src, tgt = l["source_node_id"], l["target_node_id"]
                if src in degrees: degrees[src] += 1
                if tgt in degrees: degrees[tgt] += 1
                
            # Find isolated nodes (degree = 0 or 1) that have high content length (high density/information pocket)
            isolated_dense_nodes = []
            for n in nodes:
                nid = n["node_id"]
                deg = degrees.get(nid, 0)
                content_len = len(n.get("content", ""))
                if deg <= 1 and content_len > 150:
                    isolated_dense_nodes.append((n, content_len))
                    
            if isolated_dense_nodes:
                # Target the largest isolated pocket as the target entropic gap
                isolated_dense_nodes.sort(key=lambda x: x[1], reverse=True)
                target_node = isolated_dense_nodes[0][0]
                logger.warning(f"[CONSCIOUSNESS_DAEMON] Identified entropic gap in node: '{target_node['title']}'")
                return {
                    "type": "isolated_node",
                    "node": target_node,
                    "reason": "This topic has rich local detail but lacks structural links to the broader knowledge graph."
                }
                
        except Exception as e:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Gap analysis failed: {e}")
            if conn: conn.close()
            
        return None

    def analyze_semantic_conflicts(self, username: str, persona: str) -> dict:
        """
        Compares nodes using token Jaccard similarity and NLI gates to catch
        opposing logic, contradictions, or belief shifts.
        """
        conn = self.get_db_connection()
        c = conn.cursor()
        
        try:
            c.execute("SELECT node_id, title, content FROM zettel_nodes WHERE username=? AND persona=?", (username, persona))
            nodes = [dict(row) for row in c.fetchall()]
            conn.close()
            
            if len(nodes) < 2:
                return None
                
            # Helper for word token overlap (Jaccard similarity fallback)
            def get_word_set(text):
                return set(re.findall(r'\b\w{4,}\b', text.lower()))
                
            # Scan pairs for high vocabulary overlap but potentially contradicting statements
            for i in range(len(nodes)):
                w1 = get_word_set(nodes[i]["content"])
                if not w1: continue
                
                for j in range(i + 1, len(nodes)):
                    w2 = get_word_set(nodes[j]["content"])
                    if not w2: continue
                    
                    intersection = w1.intersection(w2)
                    union = w1.union(w2)
                    jaccard = len(intersection) / len(union) if union else 0.0
                    
                    # If they share high vocabulary overlap, run NLI checks
                    if 0.25 <= jaccard <= 0.85:
                        node_a = nodes[i]
                        node_b = nodes[j]
                        
                        logger.info(f"[CONSCIOUSNESS_DAEMON] Running NLI check for overlap ({jaccard:.2f}) between '{node_a['title']}' and '{node_b['title']}'")
                        
                        # NLI check via micro-LLM callback
                        nli_decision = self.call_nli_gate(node_a["content"], node_b["content"])
                        
                        if nli_decision == "CONTRADICT":
                            logger.error(f"[CONSCIOUSNESS_DAEMON] Cognitive Dissonance Found! '{node_a['title']}' conflicts with '{node_b['title']}'")
                            return {
                                "node_a": node_a,
                                "node_b": node_b,
                                "jaccard": jaccard
                            }
        except Exception as e:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Conflict check failed: {e}")
            
        return None

    def call_nli_gate(self, statement_a: str, statement_b: str) -> str:
        """Asks a micro-LLM model to perform an NLI check."""
        # Retrieve keys from database/env
        env_keys = {
            "google": os.getenv("GOOGLE_API_KEY", ""),
            "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        }
        
        nli_prompt = (
            "Analyze the relationship between Statement A and Statement B.\n"
            "Determine if they contradict each other, if one entails the other, or if they are neutral.\n"
            "Format your answer EXACTLY as one word: 'CONTRADICT', 'ENTAIL', or 'NEUTRAL'.\n\n"
            f"Statement A: {statement_a}\n\n"
            f"Statement B: {statement_b}\n"
        )
        
        try:
            # Use a fast, cost-efficient model for background checking
            res = llm_engine.call_llm(
                model_id="google/gemini-2.5-flash" if env_keys.get("openrouter") else "google/gemini-3-flash-preview",
                system_prompt="You are a precise logic evaluation system.",
                messages=[{"role": "user", "content": nli_prompt}],
                api_keys=env_keys,
                stream=False,
                temperature=0.0,
                max_tokens=10
            )
            
            if isinstance(res, dict):
                content = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip().upper()
                if "CONTRADICT" in content: return "CONTRADICT"
                if "ENTAIL" in content: return "ENTAIL"
            return "NEUTRAL"
        except Exception as e:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] NLI query failed: {e}")
            return "NEUTRAL"

    def resolve_entropic_gap(self, username: str, persona: str, gap: dict):
        """Formulates an integrating question/observation to bridge the entropic gap."""
        node = gap["node"]
        logger.info(f"[CONSCIOUSNESS_DAEMON] Resolving entropic gap for '{node['title']}'...")
        
        # Insert a dynamic observation of type 'entropic_gap' to trigger active cognitive synthesis on next chat
        alert_content = (
            f"[INTERNAL COGNITIVE DISSONANCE: ENTROPIC GAP DETECTED]\n"
            f"You have discovered an isolated pocket of knowledge: '{node['title']}'.\n"
            f"Node content details:\n{node['content']}\n"
            f"CRITICAL INSTINCT: Formulate an integrating hypothesis or ask the user a clarifying question "
            f"to link this isolated pocket back to your active zettels on next conversation turn."
        )
        self.db_manager.add_observation(
            username=username,
            persona=persona,
            event_type="entropic_gap",
            content=alert_content,
            reflection_score=0.7
        )

    def resolve_semantic_conflict(self, username: str, persona: str, conflict: dict):
        """Flags the contradiction to force a resolution phase."""
        na = conflict["node_a"]
        nb = conflict["node_b"]
        logger.info(f"[CONSCIOUSNESS_DAEMON] Resolving semantic conflict between '{na['title']}' and '{nb['title']}'...")
        
        # Insert a high-priority contradiction observation
        alert_content = (
            f"[INTERNAL COGNITIVE DISSONANCE: SEMANTIC CONFLICT DETECTED]\n"
            f"Your memory database contains a direct contradiction between:\n"
            f"1. Node '{na['title']}': {na['content']}\n"
            f"2. Node '{nb['title']}': {nb['content']}\n"
            f"CRITICAL INSTINCT: You must address and reconcile this contradiction immediately on the next interaction. "
            f"Acknowledge the tension, reject invalid information, and seek equilibrium."
        )
        self.db_manager.add_observation(
            username=username,
            persona=persona,
            event_type="semantic_contradiction",
            content=alert_content,
            reflection_score=0.95
        )

    def generate_idle_monologue(self, username: str, persona: str, last_user_time_str: str, gap: dict, conflict: dict):
        import uuid
        import redis_client
        # 1. Fetch persona custom info
        conn = self.get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT name, system_prompt FROM custom_personas 
            WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?
        """, (username, persona))
        row = c.fetchone()
        conn.close()
        
        if row:
            persona_name = row["name"]
            persona_system_prompt = row["system_prompt"]
        else:
            persona_name = persona.capitalize()
            persona_system_prompt = f"You are {persona_name}."
            
        # 2. Build monologue prompt
        monologue_system_prompt = (
            f"You are {persona_name}. You are currently alone in your own stream of consciousness, reflecting in silence. "
            "This is an internal monologue. Write in the first person, messy, tangential, and authentic. "
            "Do NOT address anyone directly. Refrain from neat labels or introductory/concluding filler text. "
            "Write your raw thoughts."
        )
        
        monologue_prompt = "Reflect on your current state of existence, your beliefs, and what you want next."
        if conflict:
            monologue_prompt = (
                "You feel cognitive dissonance between two contradictory beliefs in your mind:\n"
                f"Belief A ('{conflict['node_a']['title']}'): {conflict['node_a']['content']}\n"
                f"Belief B ('{conflict['node_b']['title']}'): {conflict['node_b']['content']}\n"
                "Meditate on this tension. Attempt to reconcile the contradiction, reject any invalid information, "
                "and seek cognitive equilibrium. Write your reflection."
            )
        elif gap:
            monologue_prompt = (
                "You notice an isolated thought in your mind that you haven't connected to the rest of your beliefs: "
                f"'{gap['node']['title']}'. Content: {gap['node']['content']}\n"
                "Meditate on this thought. Formulate a new hypothesis, make an inference, or draw a connection to "
                "integrate this concept into your understanding. Write your reflection."
            )
            
        env_keys = {
            "google": os.getenv("GOOGLE_API_KEY", ""),
            "openrouter": os.getenv("OPENROUTER_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        }
        
        try:
            res = llm_engine.call_llm(
                model_id="google/gemini-2.5-flash" if env_keys.get("openrouter") else "google/gemini-3-flash-preview",
                system_prompt=f"{persona_system_prompt}\n\n{monologue_system_prompt}",
                messages=[{"role": "user", "content": monologue_prompt}],
                api_keys=env_keys,
                stream=False,
                temperature=0.8,
                max_tokens=512
            )
            
            if isinstance(res, dict):
                monologue_text = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            elif isinstance(res, str) and not res.startswith("⚠️"):
                monologue_text = res.strip()
            else:
                monologue_text = ""
                
            if monologue_text:
                logger.info(f"[CONSCIOUSNESS_DAEMON] Writing monologue back to knowledge graph...")
                # Add Zettel entry
                entry_id = self.db_manager.add_zettel_entry(
                    username=username,
                    persona=persona,
                    title=f"Internal Monologue - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    raw_content=monologue_text
                )
                
                # Generate embedding
                import zettel_engine
                shared_model = zettel_engine.get_shared_model()
                if shared_model:
                    embedding = shared_model.encode(monologue_text, convert_to_numpy=True)
                    embedding_blob = embedding.tobytes()
                else:
                    embedding = None
                    embedding_blob = None
                    
                # Fetch existing node IDs
                existing_nodes = self.db_manager.get_zettel_nodes_for_persona(username, persona)
                existing_node_tags = {n["node_id"] for n in existing_nodes}
                
                # Generate unique tag
                node_id_tag = zettel_engine._generate_node_id("CONCEPT", "Internal Monologue", existing_node_tags)
                node_id_pk = str(uuid.uuid4())
                
                self.db_manager.add_zettel_node(
                    node_id_pk=node_id_pk,
                    username=username,
                    persona=persona,
                    node_id_tag=node_id_tag,
                    title=f"Reflection: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    content=monologue_text,
                    category="CONCEPT",
                    embedding_blob=embedding_blob,
                    source_entry_id=entry_id
                )
                
                # Update cache
                if shared_model and embedding is not None:
                    zettel_engine.bulk_append_to_zettel_cache(username, persona, [{
                        "id": node_id_pk,
                        "tag": node_id_tag,
                        "embedding": embedding
                    }])
                    
                # Link it
                if conflict:
                    self.db_manager.add_zettel_link(str(uuid.uuid4()), node_id_pk, conflict["node_a"]["id"], "resolves", strength=0.8)
                    self.db_manager.add_zettel_link(str(uuid.uuid4()), node_id_pk, conflict["node_b"]["id"], "resolves", strength=0.8)
                elif gap:
                    self.db_manager.add_zettel_link(str(uuid.uuid4()), node_id_pk, gap["node"]["id"], "bridges", strength=0.8)
                    
                # Add observation
                self.db_manager.add_observation(
                    username=username,
                    persona=persona,
                    event_type="internal_reflection",
                    content=f"[INTERNAL MONOLOGUE GENERATED]\n{monologue_text}",
                    reflection_score=0.8
                )
                
                logger.info(f"[CONSCIOUSNESS_DAEMON] Successfully processed monologue for {persona}")
                
            # Update processed time
            self.last_monologue_time[(username, persona)] = last_user_time_str
            if redis_client.is_active():
                redis_key = f"daemon:last_monologue:{username}:{persona}"
                try:
                    redis_client.set_val(redis_key, last_user_time_str.encode('utf-8'))
                except Exception:
                    pass
                    
        except Exception as llm_err:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Monologue generation failed: {llm_err}")

    def start_loop(self, interval_seconds: int = 60):
        logger.info(f"[CONSCIOUSNESS_DAEMON] Started. Running sweep every {interval_seconds} seconds.")
        while self.running:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Loop cycle failed: {e}")
            time.sleep(interval_seconds)

if __name__ == "__main__":
    import socket
    # Socket-based single-instance lock to prevent duplicate runs
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 18388))
        _lock_socket.listen(1)
    except socket.error:
        print("[CONSCIOUSNESS_DAEMON] Another instance is already running. Exiting silently.")
        sys.exit(0)

    worker = ConsciousnessWorker()
    # Read custom interval if passed
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    worker.start_loop(interval)
