import sqlite3
import hashlib
import os
import re
import bcrypt
import json
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

# Automatically inject timeout=30.0 for all database connections to prevent lock crashes
_original_connect = sqlite3.connect
def _custom_connect(*args, **kwargs):
    if len(args) > 0 and args[0] == DB_PATH:
        kwargs.setdefault('timeout', 30.0)
    elif 'database' in kwargs and kwargs['database'] == DB_PATH:
        kwargs.setdefault('timeout', 30.0)
    return _original_connect(*args, **kwargs)
sqlite3.connect = _custom_connect

class UserManager:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        create = not os.path.exists(DB_PATH)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Enable Write-Ahead Logging (WAL) and synchronous mode normal for high concurrency
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        
        # Users table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT,
                is_premium INTEGER DEFAULT 0,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT
            )
        ''')
        
        # Migration: Ensure is_admin exists for older databases
        c.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in c.fetchall()]
        if 'is_admin' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
        
        # Usage table (daily tracking)
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage (
                username TEXT,
                date TEXT,
                msg_count INTEGER DEFAULT 0,
                PRIMARY KEY (username, date)
            )
        ''')
        # Memories table
        c.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT, 
                persona TEXT, 
                content TEXT, 
                timestamp TEXT
            )
        ''')
        
        # Conversations table (Full Chat History)
        c.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                persona TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT
            )
        ''')

        # Group Conversations table
        c.execute('''
            CREATE TABLE IF NOT EXISTS group_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                username TEXT,
                persona_key TEXT,
                persona_name TEXT,
                persona_avatar TEXT,
                role TEXT,
                content TEXT,
                is_observer INTEGER DEFAULT 0,
                timestamp TEXT
            )
        ''')
        
        # Summaries table (Rolling History)
        c.execute('''
            CREATE TABLE IF NOT EXISTS summaries (
                username TEXT,
                persona TEXT,
                summary TEXT,
                last_updated TEXT,
                PRIMARY KEY (username, persona)
            )
        ''')

        # Custom Personas table
        c.execute('''
            CREATE TABLE IF NOT EXISTS custom_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                persona_key TEXT,
                name TEXT,
                avatar TEXT,
                tagline TEXT,
                system_prompt TEXT,
                is_locked INTEGER DEFAULT 0,
                access_code TEXT,
                status TEXT DEFAULT 'online',
                is_mature INTEGER DEFAULT 0,
                created_at TEXT,
                UNIQUE(username, persona_key)
            )
        ''')
        
        # Migration: Ensure is_mature exists
        c.execute("PRAGMA table_info(custom_personas)")
        columns = [column[1] for column in c.fetchall()]
        if 'is_mature' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN is_mature INTEGER DEFAULT 0")
        if 'on_demand_file' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN on_demand_file TEXT DEFAULT ''")
        if 'on_demand_files' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN on_demand_files TEXT DEFAULT '[]'")
        if 'om_enabled' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN om_enabled INTEGER DEFAULT 1")
        if 'om_turn_threshold' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN om_turn_threshold INTEGER DEFAULT 5")
        if 'deep_memory_enabled' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN deep_memory_enabled INTEGER DEFAULT 0")
        if 'direct_wire' not in columns:
            c.execute("ALTER TABLE custom_personas ADD COLUMN direct_wire INTEGER DEFAULT 0")

        # Observations table (Observational Memory)
        c.execute('''
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                persona TEXT,
                event_type TEXT, 
                content TEXT,
                reflection_score FLOAT DEFAULT 0.0,
                timestamp TEXT
            )
        ''')

        # ── Zettel Knowledge Graph Tables ──
        c.execute('''
            CREATE TABLE IF NOT EXISTS zettel_entries (
                id TEXT PRIMARY KEY,
                username TEXT,
                persona TEXT,
                title TEXT,
                raw_content TEXT,
                processed INTEGER DEFAULT 0,
                created_at TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS zettel_nodes (
                id TEXT PRIMARY KEY,
                username TEXT,
                persona TEXT,
                node_id TEXT,
                title TEXT,
                content TEXT,
                category TEXT,
                embedding BLOB,
                source_entry_id TEXT,
                created_at TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS zettel_links (
                id TEXT PRIMARY KEY,
                source_node_id TEXT,
                target_node_id TEXT,
                relationship TEXT,
                strength FLOAT DEFAULT 0.5,
                created_at TEXT
            )
        ''')

        # User Settings table
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                username TEXT PRIMARY KEY,
                review_policy TEXT DEFAULT 'ask',
                auto_execute_terminal INTEGER DEFAULT 0,
                active_persona_key TEXT,
                security_level TEXT DEFAULT 'strict'
            )
        ''')

        conn.commit()
        conn.close()

    def add_memory(self, username, persona, content):
        try:
            # sDoS Prevention: Truncate hyper-dense or maliciously long facts.
            MAX_FACT_LENGTH = 200
            if len(content) > MAX_FACT_LENGTH:
                 content = content[:MAX_FACT_LENGTH] + "... [TRUNCATED_DUE_TO_LENGTH]"
                 
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("INSERT INTO memories (username, persona, content, timestamp) VALUES (?, ?, ?, ?)",
                      (username, persona, content, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR: {e}")
            return False

    def get_memories(self, username, persona, limit=5):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT content, username FROM memories WHERE username=? AND persona=? ORDER BY id DESC LIMIT ?", 
                      (username, persona, limit))
            rows = c.fetchall()
            conn.close()
            # Attribution Persistence: Tag every memory with its origin user to prevent Temporal RAG Poisoning.
            return [f"[Memory retrieved for user {r[1]}]: {r[0]}" for r in rows]
        except Exception as e:
            return []

    # --- FULL CHAT HISTORY METHODS ---
    def save_message(self, username, persona, role, content):
        """Save a single message to the conversation history."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("INSERT INTO conversations (username, persona, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                      (username, persona, role, content, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (save_message): {e}")
            return False

    def wipe_memories(self, username, persona):
        """Wipe all memories and summaries for a specific persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM memories WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            c.execute("DELETE FROM summaries WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            c.execute("DELETE FROM observations WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            # Cascade Zettel knowledge graph data
            c.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?)", (username, persona, username, persona))
            c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?)", (username, persona))
            c.execute("DELETE FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            c.execute("DELETE FROM zettel_entries WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            conn.commit()
            
            # Physically erase the deleted data from the raw database file on disk
            conn.execute("VACUUM")
            
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (wipe_memories): {e}")
            return False

    def get_chat_history(self, username, persona, limit=50):
        """Retrieve recent chat history for a persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Get last N messages order by ASC (oldest to newest) for chat display
            c.execute("""
                SELECT role, content, id FROM (
                    SELECT role, content, id FROM conversations 
                    WHERE username=? AND persona=? 
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
            """, (username, persona, limit))
            rows = c.fetchall()
            conn.close()
            
            # Format as list of dicts {"role": role, "content": content, "id": id}
            return [{"role": r[0], "content": r[1], "id": r[2]} for r in rows]
        except Exception as e:
            print(f"DB ERROR (get_history): {e}")
            return []

    def clear_chat_history(self, username, persona):
        """Clear all chat history for a specific persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM conversations WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona))
            conn.commit()
            
            # Physically erase the deleted data from the raw database file on disk
            conn.execute("VACUUM")

            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (clear_history): {e}")
            return False

    def delete_message(self, message_id):
        """Delete a single message by ID."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM conversations WHERE id=?", (message_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (delete_message): {e}")
            return False

    def get_message_username(self, message_id):
        """Get the username associated with a single message by ID."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT username FROM conversations WHERE id=?", (message_id,))
            row = c.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            print(f"DB ERROR (get_message_username): {e}")
            return None

    def delete_group_message(self, message_id):
        """Delete a single group message by ID."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM group_conversations WHERE id=?", (message_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (delete_group_message): {e}")
            return False

    def get_group_message_username(self, message_id):
        """Get the username associated with a group message by ID."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT username FROM group_conversations WHERE id=?", (message_id,))
            row = c.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            print(f"DB ERROR (get_group_message_username): {e}")
            return None

    # --- GROUP CHAT HISTORY METHODS ---
    def save_group_message(self, session_id, username, persona_key, persona_name, persona_avatar, role, content, is_observer=False):
        """Save a message to the group conversation history."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO group_conversations 
                (session_id, username, persona_key, persona_name, persona_avatar, role, content, is_observer, timestamp) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, username, persona_key, persona_name, persona_avatar, role, content, 1 if is_observer else 0, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (save_group_message): {e}")
            return False

    def get_group_history(self, session_id, username, limit=100):
        """Retrieve recent chat history for a group session."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Get last N messages order by ASC (oldest to newest)
            c.execute("""
                SELECT persona_key, persona_name, persona_avatar, role, content, is_observer, id FROM (
                    SELECT persona_key, persona_name, persona_avatar, role, content, is_observer, id 
                    FROM group_conversations 
                    WHERE session_id=? AND username=? 
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
            """, (session_id, username, limit))
            rows = c.fetchall()
            conn.close()
            
            return [{
                "persona_key": r[0], 
                "persona_name": r[1], 
                "persona_avatar": r[2], 
                "role": r[3], 
                "content": r[4], 
                "is_observer": bool(r[5]), 
                "id": r[6]
            } for r in rows]
        except Exception as e:
            print(f"DB ERROR (get_group_history): {e}")
            return []

    def clear_group_history(self, session_id, username):
        """Clear all chat history for a specific group session."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM group_conversations WHERE session_id=? AND username=?", (session_id, username))
            conn.commit()
            conn.execute("VACUUM")
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (clear_group_history): {e}")
            return False

    def get_user_group_sessions(self, username):
        """Retrieve all unique group session IDs for a specific user."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT DISTINCT session_id FROM group_conversations WHERE username=?", (username,))
            rows = c.fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception as e:
            print(f"DB ERROR (get_user_group_sessions): {e}")
    def register_profile(self, username, secret_key):
        """Register a profile using SHA-256 hashing of the secret_key."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            key_hash = hashlib.sha256(secret_key.encode('utf-8')).hexdigest()
            c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                      (username, key_hash, str(datetime.now())))
            conn.commit()
            return True, "Profile registered successfully."
        except sqlite3.IntegrityError:
            return False, "Username already exists."
        except Exception as e:
            return False, f"Error: {e}"
        finally:
            conn.close()

    def verify_profile(self, username, secret_key):
        """Verify profile credentials by hashing secret_key with SHA-256 and checking database."""
        if not username or not secret_key:
            return False
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        if row:
            stored_hash = row[0]
            key_hash = hashlib.sha256(secret_key.encode('utf-8')).hexdigest()
            return key_hash == stored_hash
        return False

    def hash_password(self, password):
        """Hash a password using bcrypt with salt."""
        # Generate salt and hash
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

    def check_password(self, password, hashed):
        """Verify a password against a stored bcrypt hash."""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed)
        except Exception:
            return False

    def register(self, username, password):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Store the raw bytes or hex? Bcrypt returns bytes. Let's store as bytes (BLOB) or ensure column can handle it.
            # SQLite handles bytes fine. But let's check table schema.
            # If table was created with TEXT for password_hash, we might want to store it as a string to be safe or update schema.
            # Bcrypt hash is binary. Let's decode to utf-8 string for compatibility with TEXT column.
            hashed = self.hash_password(password)
            
            c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                      (username, hashed, str(datetime.now())))
            conn.commit()
            return True, "User created successfully."
        except sqlite3.IntegrityError:
            return False, "Username already exists."
        except Exception as e:
            return False, f"Error: {e}"
        finally:
            conn.close()

    def login(self, username, password):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT password_hash, is_premium, is_admin FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        
        if row:
            stored_hash = row[0]
            # Handle legacy SHA-256 migration gracefully?
            # User said "reset my password... I am fine with it."
            # So we assume strict bcrypt check.
            
            # Ensure it's bytes for check_password
            if isinstance(stored_hash, str):
                 # Try to encode content as utf-8 bytes if it was stored as text
                 stored_hash_bytes = stored_hash.encode('utf-8')
            else:
                 stored_hash_bytes = stored_hash

            if self.check_password(password, stored_hash_bytes):
                return True, {
                    "username": username, 
                    "is_premium": bool(row[1]),
                    "is_admin": bool(row[2])
                }
        return False, None

    def check_limit(self, username, is_premium):
        if is_premium:
            return True, "Premium (Unlimited)"
            
        today = str(date.today())
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Check current usage
        c.execute("SELECT msg_count FROM usage WHERE username=? AND date=?", (username, today))
        row = c.fetchone()
        
        current_count = row[0] if row else 0
        limit = 30  # Free tier limit
        
        conn.close()
        
        # BYOK PIVOT: Always allow chat. 
        # We still return the count string for UI display, but success is always True.
        return True, f"{current_count} messages used (Unlimited BYOK)"

    def increment_usage(self, username):
        today = str(date.today())
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO usage (username, date, msg_count) 
            VALUES (?, ?, 1)
            ON CONFLICT(username, date) 
            DO UPDATE SET msg_count = msg_count + 1
        """, (username, today))
        conn.commit()
        conn.close()

    def set_premium(self, username, status=True):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET is_premium=? WHERE username=?", (1 if status else 0, username))
        conn.commit()
        conn.close()

    def set_admin(self, username, status=True):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET is_admin=? WHERE username=?", (1 if status else 0, username))
        conn.commit()
        conn.close()

    def update_summary(self, username, persona, summary):
        """Update the rolling summary for a persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO summaries (username, persona, summary, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username, persona) 
                DO UPDATE SET summary = EXCLUDED.summary, last_updated = EXCLUDED.last_updated
            """, (username, persona, summary, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (update_summary): {e}")
            return False

    def get_summary(self, username, persona):
        """Get the current rolling summary."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT summary FROM summaries WHERE username=? AND persona=?", (username, persona))
            row = c.fetchone()
            conn.close()
            return row[0] if row else ""
        except Exception as e:
            print(f"DB ERROR (get_summary): {e}")
            return ""

    # --- CUSTOM PERSONA METHODS ---
    def add_custom_persona(self, username, old_key, new_key, name, avatar, tagline, system_prompt, access_code="", on_demand_file="", on_demand_files="[]", om_enabled=True, om_turn_threshold=5, deep_memory_enabled=False, direct_wire=False):
        try:
            if not new_key:
                raise ValueError("new_key is required.")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT id FROM custom_personas WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?", (username, old_key))
            existing = c.fetchone()
            timestamp = str(datetime.now())
            
            if isinstance(on_demand_files, list):
                on_demand_files_str = json.dumps(on_demand_files)
            else:
                on_demand_files_str = on_demand_files

            if existing: # Update
                c.execute("""
                    UPDATE custom_personas 
                    SET persona_key=?, name=?, avatar=?, tagline=?, system_prompt=?, access_code=?, on_demand_file=?, on_demand_files=?, om_enabled=?, om_turn_threshold=?, deep_memory_enabled=?, direct_wire=?
                    WHERE id=?
                """, (new_key, name, avatar, tagline, system_prompt, access_code, on_demand_file, on_demand_files_str, 1 if om_enabled else 0, om_turn_threshold, 1 if deep_memory_enabled else 0, 1 if direct_wire else 0, existing[0]))
            else: # Insert
                c.execute("""
                    INSERT INTO custom_personas 
                    (username, persona_key, name, avatar, tagline, system_prompt, is_locked, access_code, on_demand_file, on_demand_files, om_enabled, om_turn_threshold, deep_memory_enabled, direct_wire, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (username, new_key, name, avatar, tagline, system_prompt, 1 if access_code else 0, access_code, on_demand_file, on_demand_files_str, 1 if om_enabled else 0, om_turn_threshold, 1 if deep_memory_enabled else 0, 1 if direct_wire else 0, timestamp))
            conn.commit()
            conn.close()
            return True, new_key
        except Exception as e:
            return False, str(e)


    def get_custom_personas(self, username):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT persona_key, name, avatar, tagline, system_prompt, is_locked, access_code, status, on_demand_file, on_demand_files, om_enabled, om_turn_threshold, deep_memory_enabled, direct_wire FROM custom_personas WHERE username COLLATE NOCASE=?", (username,))
            rows = c.fetchall()
            conn.close()
            
            personas = {}
            for r in rows:
                on_demand_files_raw = r[9] if len(r) > 9 and r[9] else "[]"
                try:
                    on_demand_files_parsed = json.loads(on_demand_files_raw)
                except:
                    on_demand_files_parsed = []

                personas[r[0]] = {
                    "name": r[1],
                    "avatar": r[2],
                    "tagline": r[3],
                    "system_prompt": r[4],
                    "is_locked": bool(r[5]),
                    "access_code": r[6],
                    "status": r[7],
                    "on_demand_file": r[8],
                    "on_demand_files": on_demand_files_parsed,
                    "om_enabled": bool(r[10]) if len(r) > 10 else True,
                    "om_turn_threshold": r[11] if len(r) > 11 else 5,
                    "deep_memory_enabled": bool(r[12]) if len(r) > 12 else False,
                    "direct_wire": bool(r[13]) if len(r) > 13 else False,
                    "is_custom": True
                }
            return personas
        except Exception as e:
            print(f"DB ERROR (get_custom_personas): {e}")
            return {}

    def delete_custom_persona(self, username, persona_key):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Clean up all associated data first
            c.execute("DELETE FROM conversations WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            c.execute("DELETE FROM memories WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            c.execute("DELETE FROM summaries WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            c.execute("DELETE FROM observations WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            # Cascade Zettel knowledge graph data
            c.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?)", (username, persona_key, username, persona_key))
            c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?)", (username, persona_key))
            c.execute("DELETE FROM zettel_nodes WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            c.execute("DELETE FROM zettel_entries WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (username, persona_key))
            # Delete the persona itself
            c.execute("DELETE FROM custom_personas WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?", (username, persona_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (delete_custom_persona): {e}")
            return False

    def update_custom_persona_prompt(self, username, persona_key, new_prompt):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE custom_personas SET system_prompt=? WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?", 
                      (new_prompt, username, persona_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (update_custom_persona_prompt): {e}")
            return False

    def update_custom_persona_on_demand_file(self, username, persona_key, filename):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE custom_personas SET on_demand_file=? WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?", 
                      (filename, username, persona_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (update_custom_persona_on_demand_file): {e}")
            return False

    def update_custom_persona_on_demand_files(self, username, persona_key, files_list):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            files_str = json.dumps(files_list)
            c.execute("UPDATE custom_personas SET on_demand_files=? WHERE username COLLATE NOCASE=? AND persona_key COLLATE NOCASE=?", 
                      (files_str, username, persona_key))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (update_custom_persona_on_demand_files): {e}")
            return False

    def change_password(self, username, new_password):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            hashed = self.hash_password(new_password)
            c.execute("UPDATE users SET password_hash=? WHERE username=?", 
                      (hashed, username))
            conn.commit()
            conn.close()
            return True, "Password updated successfully."
        except Exception as e:
            return False, str(e)

    # --- OBSERVATIONAL MEMORY METHODS ---
    def add_observation(self, username, persona, event_type, content, reflection_score=0.0):
        """Add a new observation/event to the log."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO observations (username, persona, event_type, content, reflection_score, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, persona, event_type, content, reflection_score, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (add_observation): {e}")
            return False

    def get_observation_log(self, username, persona, limit=10):
        """Retrieve recent observation events."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT event_type, content, timestamp FROM observations 
                WHERE username=? AND persona=? 
                ORDER BY id DESC LIMIT ?
            """, (username, persona, limit))
            rows = c.fetchall()
            conn.close()
            return [{"type": r[0], "content": r[1], "timestamp": r[2]} for r in rows[::-1]]
        except Exception as e:
            print(f"DB ERROR (get_observation_log): {e}")
            return []

    def clear_observations(self, username, persona):
        """Clear the observation log for a persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM observations WHERE username=? AND persona=?", (username, persona))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (clear_observations): {e}")
            return False

    # --- ZETTEL KNOWLEDGE GRAPH METHODS ---
    def add_zettel_entry(self, username, persona, title, raw_content):
        """Create a raw lore entry (pre-processing)."""
        import uuid
        entry_id = str(uuid.uuid4())
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO zettel_entries (id, username, persona, title, raw_content, processed, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            """, (entry_id, username, persona, title, raw_content, timestamp))
            conn.commit()
            conn.close()
            return entry_id
        except Exception as e:
            print(f"DB ERROR (add_zettel_entry): {e}")
            return None

    def get_zettel_entries(self, username, persona):
        """Retrieve all lore entries for a persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT id, title, raw_content, processed, created_at 
                FROM zettel_entries 
                WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
                ORDER BY created_at DESC
            """, (username, persona))
            rows = c.fetchall()
            conn.close()
            return [{"id": r[0], "title": r[1], "content": r[2], "processed": bool(r[3]), "created_at": r[4]} for r in rows]
        except Exception as e:
            print(f"DB ERROR (get_zettel_entries): {e}")
            return []

    def update_zettel_entry(self, username, persona, entry_id, title, raw_content):
        """Update a lore entry and reset processed flag to trigger re-processing."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Delete old nodes and links for this entry before re-processing
            c.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (entry_id, entry_id))
            c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (entry_id,))
            c.execute("DELETE FROM zettel_nodes WHERE source_entry_id=?", (entry_id,))
            # Update the entry
            c.execute("""
                UPDATE zettel_entries SET title=?, raw_content=?, processed=0
                WHERE id=? AND username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
            """, (title, raw_content, entry_id, username, persona))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (update_zettel_entry): {e}")
            return False

    def delete_zettel_entry(self, username, persona, entry_id):
        """Delete a lore entry and cascade-remove its nodes + links."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM zettel_links WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?) OR target_node_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (entry_id, entry_id))
            c.execute("DELETE FROM zettel_fts WHERE node_db_id IN (SELECT id FROM zettel_nodes WHERE source_entry_id=?)", (entry_id,))
            c.execute("DELETE FROM zettel_nodes WHERE source_entry_id=?", (entry_id,))
            c.execute("DELETE FROM zettel_entries WHERE id=? AND username COLLATE NOCASE=? AND persona COLLATE NOCASE=?", (entry_id, username, persona))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (delete_zettel_entry): {e}")
            return False

    def add_zettel_node(self, node_id_pk, username, persona, node_id_tag, title, content, category, embedding_blob, source_entry_id):
        """Insert a chunked atomic node into the knowledge graph."""
        # ── Step 1: Insert the actual node row (committed independently) ──
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO zettel_nodes (id, username, persona, node_id, title, content, category, embedding, source_entry_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id_pk, username, persona, node_id_tag, title, content, category, embedding_blob, source_entry_id, timestamp))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB ERROR (add_zettel_node — node insert): {e}")
            return False

        # ── Step 2: Mirror into FTS5 (best-effort, separate commit) ──
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT INTO zettel_fts (node_db_id, content, title, category) VALUES (?, ?, ?, ?)",
                      (node_id_pk, content, title, category))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"DB WARN (add_zettel_node — FTS5 mirror failed, node is still stored): {e}")

        return True

    def add_zettel_link(self, link_id, source_node_id, target_node_id, relationship, strength=0.5):
        """Insert a weighted edge between two nodes."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT OR IGNORE INTO zettel_links (id, source_node_id, target_node_id, relationship, strength, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (link_id, source_node_id, target_node_id, relationship, strength, timestamp))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (add_zettel_link): {e}")
            return False

    def get_zettel_nodes_for_persona(self, username, persona):
        """Get all zettel nodes with embeddings for a persona."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT id, node_id, title, content, category, embedding, source_entry_id, created_at
                FROM zettel_nodes
                WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
            """, (username, persona))
            rows = c.fetchall()
            conn.close()
            return [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": r[5], "source_entry_id": r[6], "created_at": r[7]} for r in rows]
        except Exception as e:
            print(f"DB ERROR (get_zettel_nodes_for_persona): {e}")
            return []

    def search_zettel_fts(self, username, persona, query):
        """Full-text search across zettel node content using tokenized keyword OR matching."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Tokenize: strip punctuation, lowercase, filter short words
            # Then join as OR terms so FTS5 matches any significant keyword
            clean = re.sub(r'[^\w\s]', ' ', query.lower())
            stop_words = {'what', 'is', 'the', 'a', 'an', 'are', 'was', 'were',
                          'do', 'does', 'how', 'why', 'can', 'could', 'tell', 'me',
                          'about', 'your', 'my', 'to', 'of', 'in', 'on', 'for',
                          'with', 'that', 'this', 'have', 'has', 'had', 'you'}
            tokens = [w for w in clean.split() if len(w) > 2 and w not in stop_words]
            if not tokens:
                conn.close()
                return []
            # FTS5 OR query across all significant tokens
            fts_query = ' OR '.join(tokens)
            c.execute("""
                SELECT zn.id, zn.node_id, zn.title, zn.content, zn.category, zn.embedding,
                       rank
                FROM zettel_fts 
                JOIN zettel_nodes zn ON zn.id = zettel_fts.node_db_id
                WHERE zettel_fts MATCH ?
                AND zn.username COLLATE NOCASE=? AND zn.persona COLLATE NOCASE=?
                ORDER BY rank
                LIMIT 20
            """, (fts_query, username, persona))
            rows = c.fetchall()
            conn.close()
            return [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": r[5], "fts_rank": r[6]} for r in rows]
        except Exception as e:
            print(f"DB ERROR (search_zettel_fts): {e}")
            return []

    def get_linked_nodes(self, node_id, depth=1):
        """Get nodes connected to a given node via zettel_links (1-hop or 2-hop)."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            visited = set()
            results = []
            frontier = [node_id]

            for d in range(depth):
                next_frontier = []
                for nid in frontier:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    c.execute("""
                        SELECT zn.id, zn.node_id, zn.title, zn.content, zn.category, zl.relationship, zl.strength
                        FROM zettel_links zl
                        JOIN zettel_nodes zn ON (zn.id = zl.target_node_id OR zn.id = zl.source_node_id)
                        WHERE (zl.source_node_id=? OR zl.target_node_id=?)
                        AND zn.id != ?
                    """, (nid, nid, nid))
                    for row in c.fetchall():
                        if row[0] not in visited:
                            results.append({
                                "id": row[0], "node_id": row[1], "title": row[2],
                                "content": row[3], "category": row[4],
                                "relationship": row[5], "strength": row[6]
                            })
                            next_frontier.append(row[0])
                frontier = next_frontier

            conn.close()
            return results
        except Exception as e:
            print(f"DB ERROR (get_linked_nodes): {e}")
            return []

    def mark_zettel_entry_processed(self, entry_id):
        """Mark a zettel entry as fully processed."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE zettel_entries SET processed=1 WHERE id=?", (entry_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (mark_zettel_entry_processed): {e}")
            return False
    def get_user_settings(self, username: str) -> dict:
        """Fetches the governance and UI settings for a user."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM user_settings WHERE username = ?", (username,))
            row = c.fetchone()
            conn.close()
            if row:
                return dict(row)
            return {"username": username, "review_policy": "ask", "auto_execute_terminal": 0, "security_level": "strict"}
        except Exception as e:
            print(f"[DB_ERROR] Failed to fetch settings: {e}")
            return {"review_policy": "ask"}

    def update_user_settings(self, username: str, settings: dict):
        """Updates user governance settings."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Ensure the row exists
            c.execute("INSERT OR IGNORE INTO user_settings (username) VALUES (?)", (username,))
            
            for key, value in settings.items():
                if key in ["review_policy", "auto_execute_terminal", "active_persona_key", "security_level"]:
                    c.execute(f"UPDATE user_settings SET {key} = ? WHERE username = ?", (value, username))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB_ERROR] Failed to update settings: {e}")
