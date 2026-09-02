import time
import os
import sys
import json
import sqlite3
import logging
import re
import threading
from datetime import datetime

import database as db
import llm_engine

# Global neuromodulator coupling (optional — degrades to legacy behavior if absent)
try:
    import dopamine_state
except ImportError:
    dopamine_state = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("stream_worker")


# ─── Daemon single-instance lock: holder liveness and TTL derivation ────────
# `interval` is only the SLEEP between sweeps, never the length of a cycle. A
# gated-idle cycle is 60s; an ACTIVE NLI sweep was measured at 548-577s. Every
# TTL here used to be computed from `interval`, so both the lock and the
# staleness threshold expired two or three times inside one healthy cycle: the
# daemon sat lockless ~68% and past its own staleness check ~46% of wall clock,
# and any spawn landing in that window aborted a working incumbent. These
# factors multiply the OBSERVED cycle instead, so the keys outlive the work that
# renews them. The old interval-derived numbers survive as floors.
# Lock PRESENCE is a timer, not a forecast. Renewing once per cycle means
# predicting how long the next cycle will be, and the idle->active transition is
# always mispredicted: measured on 2026-08-31, the first active sweep after an
# idle stretch ran 652.8s under a 180s lock, leaving the key absent for ~475s
# (73%) of that cycle -- with the cycle-derived estimator already in place. The
# estimator can only widen AFTER it has seen a long cycle, so it protects every
# active cycle except the one that matters. A background thread renewing on a
# fixed short timer does not predict anything.
LOCK_RENEW_SECS = 30       # how often the renewer thread re-SETs the lock
LOCK_TTL_SECS = 120        # 4x the renewal period: survives a stalled renewal,
                           # and bounds how long a corpse's lock lingers for a
                           # spawn whose pid probe came back inconclusive
STALE_TTL_FACTOR = 4.0     # a heartbeat older than this really is a corpse
TTL_CEILING = 7200         # bound on how long one bad measurement can wedge the lock
# FIX(monologue-stub): measured across all 106 Reflection nodes -- 48 real bodies
# at 1682-2140 chars, 58 at 56-97 chars, and NOTHING in between. The short ones are
# cut MID-WORD, which is a truncation, not a brief reflection: 60-100 chars is
# 15-25 tokens, the reasoning-budget signature from note 27. They were written
# permanently to zettel_nodes and then fed back to the gap picker and the NLI sweep
# AS INPUT -- the writer poisoning the corpus it reads. The floor sits 8x below the
# smallest real body and 2x above the largest stub, so it separates the two
# measured populations without being tuned to either edge.
MIN_MONOLOGUE_CHARS = 200

DAEMON_CYCLE_KEY = b"q:daemon:cycle_secs"
DAEMON_CYCLE_KEY_TTL = 86400   # outlives restarts, so a fresh boot inherits the
                               # measurement instead of restarting at the floors

_WIN = (os.name == "nt")


def _proc_probe(pid):
    """
    (alive, create_time) for `pid`. create_time is an opaque comparable int, or
    None when it cannot be read.

    Conservative by design: a pid we are not allowed to inspect reads as ALIVE,
    because the caller uses this to decide whether to evict another daemon, and
    evicting a healthy incumbent mid-sweep throws away its populated cooldown map.

    NEVER use os.kill(pid, 0) here. On Windows, CPython implements os.kill via
    TerminateProcess, so the POSIX "does this pid exist" idiom would kill the
    very process it is asking about.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return (True, None)
    if pid <= 0:
        return (True, None)

    if not _WIN:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            return (False, None)
        except PermissionError:
            alive = True
        except Exception:
            return (True, None)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                return (alive, int(fh.read().rsplit(b")", 1)[1].split()[19]))
        except Exception:
            return (alive, None)

    import ctypes
    from ctypes import wintypes
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_TIMEOUT = 0x102
        ERROR_INVALID_PARAMETER = 87

        h = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # 87 is the only code that means "no such pid". 5 (access denied)
            # and anything else mean it exists but is not ours to inspect.
            return (ctypes.get_last_error() != ERROR_INVALID_PARAMETER, None)
        try:
            # Signalled means exited; WAIT_TIMEOUT means still running.
            alive = (k32.WaitForSingleObject(h, 0) == WAIT_TIMEOUT)
            ft = (wintypes.FILETIME * 4)()
            ctime = None
            if k32.GetProcessTimes(h, ctypes.byref(ft[0]), ctypes.byref(ft[1]),
                                   ctypes.byref(ft[2]), ctypes.byref(ft[3])):
                ctime = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
            return (alive, ctime)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return (True, None)


def _lock_value(boot_id: str) -> bytes:
    """Build "<pid>:<create_time>:<boot_id>" -- identity the OS can be asked about."""
    _ctime = _proc_probe(os.getpid())[1]
    return f"{os.getpid()}:{_ctime if _ctime else ''}:{boot_id}".encode()


def _parse_lock_val(raw):
    """
    (pid, create_time) out of a lock value. Returns (None, None) for the legacy
    bare-boot_id format, so a new daemon meeting an old holder falls back to the
    heartbeat rule rather than evicting something it cannot identify.
    """
    if not raw:
        return (None, None)
    try:
        parts = raw.decode(errors="replace").split(":")
    except Exception:
        return (None, None)
    if len(parts) < 3:
        return (None, None)
    try:
        pid = int(parts[0])
    except ValueError:
        return (None, None)
    try:
        ctime = int(parts[1])
    except ValueError:
        ctime = None
    return (pid, ctime)


def _holder_is_alive(raw) -> bool:
    """True when the lock's recorded holder is still a running process."""
    pid, ctime = _parse_lock_val(raw)
    if pid is None:
        return True
    alive, cur_ctime = _proc_probe(pid)
    if not alive:
        return False
    # Windows recycles pids fast enough that "pid 29220 exists" is not the same
    # claim as "pid 29220 is still the daemon that took this lock".
    if ctime is not None and cur_ctime is not None and ctime != cur_ctime:
        return False
    return True


# Daemon model selection. This process reads ONLY the environment -- the UI's
# key box travels in each chat request body and never reaches the daemon -- so
# the model has to be chosen here as well. Two call sites, two jobs: the NLI
# gate returns a one-word verdict that resolve_semantic_conflict then acts on,
# so a wrong answer is a wrong row, and it gets the smartest flash; the
# monologue is persona prose. Both are overridable from .env without a source
# edit (which kills the daemon, note 26). The Vertex id is untouched: that path
# routes on ADC and was never the problem (note 28). Prices per M tokens as of
# 2026-09-01 on OpenRouter: 3.5-flash $1.50/$9.00, 3.7-flash $0.75/$3.75.
# 2.5-flash (the old hardcode) is gone from both sites.
DAEMON_NLI_MODEL_OPENROUTER = "google/gemini-3.5-flash"
DAEMON_MONOLOGUE_MODEL_OPENROUTER = "google/gemini-3.7-flash"
DAEMON_MODEL_VERTEX = "google/gemini-3-flash-preview"


def _daemon_model(env_var, openrouter_default, keys):
    """Model id for a daemon call: env override, else by which route the keys
    select. An OpenRouter key disables the Vertex route in llm_engine
    (has_openrouter_key), so the default has to follow the same switch."""
    override = (os.getenv(env_var) or "").strip()
    if override:
        return override
    return openrouter_default if keys.get("openrouter") else DAEMON_MODEL_VERTEX


def _derive_stale_ttl(observed_cycle, interval):
    """
    The heartbeat staleness threshold, in seconds, from the measured cycle.

    Only the HEARTBEAT still needs this. It is written once per cycle -- on
    purpose, because consecutive deltas are how cycle time gets measured -- so on
    a healthy daemon it is legitimately as old as one full sweep, and 300s
    (interval*5) called a working 652.8s cycle a corpse. Prediction is safe here
    in a way it was not for the lock: this is the THIRD line of defence, behind
    the pid probe and behind the lock's own TTL, and it is only consulted for a
    holder that cannot be identified at all.

    The old max(300, interval*5) is kept as a FLOOR, so a cold boot with no
    measurement behaves exactly as it did before and only ever widens.
    """
    try:
        obs = float(observed_cycle or 0.0)
    except (TypeError, ValueError):
        obs = 0.0
    obs = max(obs, float(interval))
    return min(max(300, int(interval * 5), int(STALE_TTL_FACTOR * obs)), TTL_CEILING)

class ConsciousnessWorker:
    # How long (in seconds) to wait before re-flagging the same entropic gap node.
    # Default: 86400s (24 hours). Set env var DAEMON_GAP_COOLDOWN_SECS to override.
    # NOTE: this constant is ALSO the rotation modulus at :260 (epoch_slot). Changing
    # it retunes which nodes get picked, not just how often. Do not reuse it as a
    # generic "one day" -- that is what DISSONANCE_WINDOW_SECS below is for.
    GAP_COOLDOWN_SECS = int(os.getenv("DAEMON_GAP_COOLDOWN_SECS", 86400))

    # Hard ceiling on daemon-authored dissonance writes per (username, persona) per
    # window, per event type, regardless of which node is targeted. This is the only
    # guard that actually throttles: the per-node cooldown cannot bind against a
    # 276-node candidate pool, and a durable cooldown only changes WHICH node is
    # flagged, not how many. Default: 8 per 24h.
    # Set DAEMON_DISSONANCE_CAP / DAEMON_DISSONANCE_WINDOW_SECS to override; a cap
    # of 0 or less disables the ceiling entirely.
    DISSONANCE_CAP = int(os.getenv("DAEMON_DISSONANCE_CAP", 8))
    DISSONANCE_WINDOW_SECS = int(os.getenv("DAEMON_DISSONANCE_WINDOW_SECS", 86400))

    def __init__(self):
        self.db_manager = db.UserManager()
        self.running = True
        self.last_monologue_time = {}  # (username, persona) -> timestamp string
        self.idle_threshold = int(os.getenv("DAEMON_IDLE_THRESHOLD", 300))
        # Cooldown registry: (username, persona, node_id) -> last_flagged_timestamp (float)
        self._gap_cooldowns = {}
        # Degraded-mode budget counter, used only when Redis is unavailable.
        # Process-local by necessity; see _claim_dissonance_slot.
        self._dissonance_fallback = {}

    def get_db_connection(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _claim_dissonance_slot(self, username: str, persona: str, kind: str, claim: bool = True):
        """Per-(username, persona, kind) hard ceiling on daemon-authored writes.

        Counts ATTEMPTS, never rows. add_observation returns True for both a real
        insert and a silently-deduped one (database.py:983-985), and its dedup
        predicate carries no time bound -- so a trailing-window ROW count is
        ceilinged at "distinct contents ever written" and for most personas here
        would never reach any sane cap. The counter is therefore incremented at
        selection time, from state add_observation cannot mutate.

        Durable in Redis because the registry-in-a-dict problem is not "edits reset
        it": the lock renewal TTL (180s) is shorter than the measured cycle (~300s),
        so the daemon routinely runs unlocked and a second instance can start
        alongside it. INCR is atomic, so concurrent daemons share one budget rather
        than each keeping a private one. Falls back to a process-local dict when
        Redis is down -- degraded, and the socket lock at :665 is the only thing
        keeping that single-writer.

        Returns (allowed: bool, used: int).
        """
        cap = self.DISSONANCE_CAP
        if cap <= 0:
            return (True, 0)

        window = max(60, self.DISSONANCE_WINDOW_SECS)
        slot = int(time.time() // window)
        key = f"q:daemon:dissonance:{kind}:{username}:{persona}:{slot}"

        conn = None
        try:
            import redis_client
            if redis_client.is_active():
                conn = redis_client.get_connection()
        except Exception:
            conn = None

        if conn is not None:
            try:
                if not claim:
                    cur = conn.get(key.encode())
                    used = int(cur) if cur else 0
                    return (used < cap, used)
                pipe = conn.pipeline()
                pipe.incr(key.encode())
                pipe.expire(key.encode(), window * 2)
                used = int(pipe.execute()[0])
                return (used <= cap, used)
            except Exception as budget_err:
                logger.warning(
                    f"[CONSCIOUSNESS_DAEMON] Dissonance budget unavailable via Redis "
                    f"({budget_err}); falling back to process-local count."
                )

        used = self._dissonance_fallback.get(key, 0)
        if not claim:
            return (used < cap, used)
        used += 1
        # Single-slot dict: writing a fresh mapping drops expired windows.
        self._dissonance_fallback = {key: used}
        return (used <= cap, used)

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
            
            # 0. Read dopaminergic posture. Low tonic = consolidation mode:
            #    the gap picker stands down entirely. If DA state is
            #    unavailable, default to exploring (legacy behavior).
            _da_tonic = None
            if dopamine_state is not None:
                try:
                    _da_tonic = dopamine_state.get_state(username, persona)["tonic"]
                except Exception:
                    _da_tonic = None
            _exploring = dopamine_state.should_explore(_da_tonic) if _da_tonic is not None else True
            
            # 1. Scan for Entropic Gaps (Isolated clusters / lack of link density)
            gap = self.analyze_entropic_gaps(username, persona, exploring=_exploring)
            if gap:
                self.resolve_entropic_gap(username, persona, gap)

            # 2. Scan for Semantic Polar Conflicts (Contradictions)
            conflict = self.analyze_semantic_conflicts(username, persona, exploring=_exploring)
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
                        self.generate_idle_monologue(username, persona, last_user_time_str, gap, conflict, exploring=_exploring)
            except Exception as idle_err:
                logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Idle coordinator failure: {idle_err}")

    def analyze_entropic_gaps(self, username: str, persona: str, exploring: bool = True) -> dict:
        """
        Calculates node-to-link ratio and clustering metrics to identify
        highly detailed but poorly integrated (isolated) knowledge clusters.

        Posture-aware: when exploring=False (low tonic dopamine), the picker
        stands down — consolidation mode. When exploring, it rotates across
        all fresh voids instead of always attacking the single largest pocket.
        """
        conn = self.get_db_connection()
        c = conn.cursor()
        
        try:
            c.execute("SELECT id, node_id, title, content FROM zettel_nodes WHERE username=? AND persona=?", (username, persona))
            nodes = [dict(row) for row in c.fetchall()]
            
            if len(nodes) < 3:
                conn.close()
                return None
                
            c.execute("""
                SELECT source_node_id, target_node_id FROM zettel_links 
                WHERE source_node_id IN (SELECT id FROM zettel_nodes WHERE username=? AND persona=?)
            """, (username, persona))
            links = [dict(row) for row in c.fetchall()]
            conn.close()
            
            # Calculate node degree (number of connections).
            # FIX(join-key): zettel_links stores zettel_nodes.id (the uuid PK) in
            # source_node_id/target_node_id -- see the sole INSERT at database.py:1141
            # and every other consumer (database.py:1237-1238, main.py:830). This used
            # to select and key on node_id (the "[[CONCEPT-X-001]]" slug), so the IN
            # clause matched nothing, `links` came back empty, this loop never ran, and
            # every node scored degree 0 -- making the `deg <= 1` test below vacuously
            # true. Wrong since the file was created (2799982, 2026-06-11).
            degrees = {n["id"]: 0 for n in nodes}
            for l in links:
                src, tgt = l["source_node_id"], l["target_node_id"]
                if src in degrees: degrees[src] += 1
                if tgt in degrees: degrees[tgt] += 1
                
            # Find isolated nodes (degree = 0 or 1) that have high content length (high density/information pocket)
            isolated_dense_nodes = []
            for n in nodes:
                nid = n["id"]
                deg = degrees.get(nid, 0)
                content_len = len(n.get("content", ""))
                if deg <= 1 and content_len > 150:
                    isolated_dense_nodes.append((n, content_len))
                    
            if isolated_dense_nodes:
                # Consolidation mode: park the voids, let decay do its thing.
                if not exploring:
                    logger.info(
                        f"[CONSCIOUSNESS_DAEMON] Tonic DA below explore threshold "
                        f"(consolidation mode) — {len(isolated_dense_nodes)} void(s) parked."
                    )
                    return None
                
                now_ts = time.time()
                
                def _under_cooldown(n):
                    last = self._gap_cooldowns.get((username, persona, n["node_id"]))
                    return last is not None and (now_ts - last) < self.GAP_COOLDOWN_SECS
                
                # Only consider voids whose cooldown has expired, so the picker
                # rotates to fresh targets instead of re-chewing one pocket.
                fresh = [(n, l) for (n, l) in isolated_dense_nodes if not _under_cooldown(n)]
                if not fresh:
                    logger.info("[CONSCIOUSNESS_DAEMON] All identified gaps are under cooldown. Idling.")
                    return None

                # F1: hard ceiling, claimed BEFORE the tonic boost below. Capping the
                # write alone would be self-defeating -- boost_tonic (+0.04) exceeds the
                # per-cycle decay at every tonic value, so a suppressed attempt that
                # still boosted would keep the explore gate latched open and the daemon
                # would spin at full rate emitting nothing.
                _allowed, _used = self._claim_dissonance_slot(username, persona, "entropic_gap")
                if not _allowed:
                    logger.info(
                        f"[CONSCIOUSNESS_DAEMON] Gap budget spent for {username}/{persona} "
                        f"({_used}/{self.DISSONANCE_CAP} this window) — {len(fresh)} void(s) held."
                    )
                    return None
                
                # Curiosity rotation: epoch-slot modulo across the sorted
                # candidates. Same node is only revisited after the full
                # cooldown window has elapsed for every fresher candidate.
                fresh.sort(key=lambda x: x[1], reverse=True)
                epoch_slot = int(now_ts // self.GAP_COOLDOWN_SECS)
                target_node = fresh[epoch_slot % len(fresh)][0]
                
                # Gap discovery is itself arousing: small tonic bump.
                if dopamine_state is not None:
                    try:
                        dopamine_state.boost_tonic(username, persona, 0.04)
                    except Exception:
                        pass
                
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

    def analyze_semantic_conflicts(self, username: str, persona: str,
                                   exploring: bool = True) -> dict:
        """
        Compares nodes using token Jaccard similarity and NLI gates to catch
        opposing logic, contradictions, or belief shifts.

        Posture-aware, same contract as analyze_entropic_gaps: `exploring=False`
        (tonic DA below threshold) means consolidation mode and the sweep stands
        down. Defaults True so a caller with no DA state keeps legacy behaviour.
        """
        # FIX(idle-burn): this sweep used to run at full cost regardless of posture
        # while the gap picker stood down at the equivalent point. Measured on an
        # IDLE app (no user turns, tonic at baseline, graph unchanged by a single
        # node): ~5 sweeps/hour x 153 blocking Vertex calls at ~6.5s each, re-asking
        # the identical questions about the identical pairs and writing nothing --
        # nothing can be written, because resolve_semantic_conflict is downstream of
        # the same gate. Cheapest exit first: this test is free, the budget check
        # below is a Redis round trip.
        if not exploring:
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] Tonic DA below explore threshold "
                f"(consolidation mode) — NLI sweep for {username}/{persona} stood down."
            )
            return None

        # Advisory pre-check (does not consume). The sweep below costs one blocking
        # LLM round trip per qualifying pair -- 153 per cycle measured against the
        # live graph. If the write budget is already spent there is nothing to do
        # with a result, so skip the spend. resolve_semantic_conflict makes the
        # authoritative claim.
        _allowed, _used = self._claim_dissonance_slot(
            username, persona, "semantic_contradiction", claim=False
        )
        if not _allowed:
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] Contradiction budget spent for {username}/{persona} "
                f"({_used}/{self.DISSONANCE_CAP} this window) — skipping NLI sweep."
            )
            return None

        conn = self.get_db_connection()
        c = conn.cursor()
        
        try:
            c.execute("SELECT id, node_id, title, content FROM zettel_nodes WHERE username=? AND persona=?", (username, persona))
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
            # Not the cheapest model: a wrong verdict here becomes a
            # semantic_contradiction row that the resolver acts on.
            res = llm_engine.call_llm(
                model_id=_daemon_model("DAEMON_NLI_MODEL", DAEMON_NLI_MODEL_OPENROUTER, env_keys),
                system_prompt="You are a precise logic evaluation system.",
                messages=[{"role": "user", "content": nli_prompt}],
                api_keys=env_keys,
                stream=False,
                temperature=0.0,
                # FIX(nli-starved): was 10. gemini-3-flash-preview is a REASONING
                # model and max_tokens caps reasoning + content together, so all 10
                # went to reasoning and content came back "" with finish=length --
                # every call resolved to NEUTRAL, which is why semantic_contradiction
                # has zero rows in the table's entire history. Measured: 10 -> "",
                # 64 -> "CONTRADI" (truncated, still fails the `in` test), 256 ->
                # "CONTRADICT" finish=stop at 219 total tokens. This is a CAP, not a
                # target -- billing follows what is generated, so the headroom is
                # insurance against a hard pair, not a standing cost.
                # Set to 256, not 1000: measured finish='stop' at 219 total tokens
                # (215 reasoning + 4 content), so the model terminates naturally well
                # inside this budget. 1000 bought no extra correctness and cost
                # ~6.5s/call against ~2.6s, which tripled cycle time to 1009s and put
                # the daemon past its own 300s staleness threshold for 70% of every
                # cycle (S14.7). Verdicts are identical at either value.
                max_tokens=256,
                disable_vpn_rotation=True
            )

            # call_llm returns a STR on failure ("(warn) Connection Error: ..."), never
            # raising. The old `isinstance(res, dict)` guard dropped that straight
            # through to NEUTRAL, so a daemon with no working provider ran full
            # sweeps that were structurally incapable of a verdict and logged
            # nothing -- observed for 15.9h on a daemon started without .env.
            # A verdict we could not obtain is NOT evidence of non-contradiction.
            if not isinstance(res, dict):
                logger.error(
                    f"[CONSCIOUSNESS_DAEMON ERROR] NLI gate got no completion "
                    f"({type(res).__name__}): {str(res)[:160]} -- returning NEUTRAL "
                    f"as FAILURE, not as a verdict."
                )
                return "NEUTRAL"

            choice = (res.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "").strip().upper()
            if not content:
                logger.error(
                    f"[CONSCIOUSNESS_DAEMON ERROR] NLI gate returned empty content "
                    f"(finish_reason={choice.get('finish_reason')!r}). Token budget is "
                    f"too small for this model's reasoning overhead -- see max_tokens above."
                )
                return "NEUTRAL"

            if "CONTRADICT" in content: return "CONTRADICT"
            if "ENTAIL" in content: return "ENTAIL"
            return "NEUTRAL"
        except Exception as e:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] NLI query failed: {e}")
            return "NEUTRAL"

    def resolve_entropic_gap(self, username: str, persona: str, gap: dict):
        """Formulates an integrating question/observation to bridge the entropic gap.
        
        Cooldown guard: the same node is only flagged once per GAP_COOLDOWN_SECS
        (default 24h). This prevents the homeostasis livelock where an isolated
        node is detected → observation inserted → node still isolated → repeat.
        """
        node = gap["node"]
        node_id = node.get("node_id", node.get("title", ""))
        cooldown_key = (username, persona, node_id)
        now = time.time()

        last_flagged = self._gap_cooldowns.get(cooldown_key)
        if last_flagged is not None and (now - last_flagged) < self.GAP_COOLDOWN_SECS:
            remaining_h = (self.GAP_COOLDOWN_SECS - (now - last_flagged)) / 3600
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] Skipping entropic gap for '{node['title']}' "
                f"(cooldown active, {remaining_h:.1f}h remaining)."
            )
            return

        # Record the flag timestamp before inserting
        self._gap_cooldowns[cooldown_key] = now
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
        """Flags the contradiction to force a resolution phase.

        Budget guard: this path has never had one. analyze_semantic_conflicts returns
        the FIRST contradicting pair in a deterministic scan order, so a single real
        CONTRADICT would otherwise reproduce identical content every cycle forever --
        with both nodes' full content embedded, roughly twice an entropic_gap row.
        It has 0 rows only because the NLI gate has never returned CONTRADICT, not
        because anything was stopping it.
        """
        na = conflict["node_a"]
        nb = conflict["node_b"]

        _allowed, _used = self._claim_dissonance_slot(username, persona, "semantic_contradiction")
        if not _allowed:
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] Contradiction budget spent for {username}/{persona} "
                f"({_used}/{self.DISSONANCE_CAP} this window). Skipping."
            )
            return
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

    # Characters that legitimately end a thought in this corpus. `*` and `)` are
    # included because the monologue closes stage directions (*takes a drink*) and
    # parentheticals; `"` and the curly quote close reported speech.
    _SENTENCE_END = ('.', '!', '?', '"', '”', '*', ')', '—')

    @classmethod
    def _trim_to_sentence(cls, text: str, min_keep: float = 0.6) -> str:
        """Trim a length-truncated generation back to its last complete sentence.

        Scans backwards for a terminator that is either the final character or is
        followed by whitespace -- the trailing-whitespace test is what stops it
        cutting at the dot inside "gemini-3.0" or "e.g.".

        Refuses to act when the only boundary lies in the first `min_keep` of the
        text: a monologue whose sole full stop is in line one should be stored
        whole and flagged rather than reduced to a sentence. Returns the input
        unchanged when the text already ends cleanly, so it is a no-op on
        well-formed output and safe to call unconditionally.
        """
        s = (text or "").rstrip()
        if not s:
            return s
        for i in range(len(s) - 1, -1, -1):
            if s[i] in cls._SENTENCE_END and (i == len(s) - 1 or s[i + 1].isspace()):
                if (i + 1) < len(s) * min_keep:
                    break          # boundary too early -- gutting it is worse
                return s[:i + 1].rstrip()
        return s

    def _mark_monologue_processed(self, username: str, persona: str, last_user_time_str: str):
        """
        Arm the monologue idempotency guard for one
        (username, persona, last_user_timestamp) triple.

        FIX(F6-mark-fanout): this used to be the last two statements of the same
        try that wraps the LLM call AND the entire write fan-out, so anything
        raising in between skipped both durability layers -- the in-process dict
        and the Redis mirror -- and re-armed the monologue on the very next
        cycle. Measured against the live DB before the fix: 87 of 106 Reflection
        nodes (82%) were redundant fires against an already-processed triple,
        and 40 of Sky/v's 44 inter-node gaps were under ten minutes with 13 of
        them under 90s -- one refire per daemon cycle, not one per session.

        It is now written from a finally, so success, an empty completion and an
        exception all arm the guard exactly once. Losing a single monologue to a
        transient LLM failure is cheap and self-corrects the moment the user
        speaks again (a new user message changes the triple). Re-writing the
        corpus every 70s is neither: each redundant fire is a permanent
        zettel_node and one more gap-eligible orphan in the pool this daemon
        then scans.

        Note the mark's only durable home is Redis, with no TTL. A Redis flush
        re-arms every persona exactly once. That is a separate, much cheaper
        failure than this one and is deliberately not addressed here.
        """
        self.last_monologue_time[(username, persona)] = last_user_time_str
        try:
            import redis_client as _rc_mark
            if _rc_mark.is_active():
                _rc_mark.set_val(
                    f"daemon:last_monologue:{username}:{persona}",
                    last_user_time_str.encode("utf-8"))
        except Exception as mark_err:
            logger.warning(
                f"[CONSCIOUSNESS_DAEMON] Could not mirror the monologue mark to Redis for "
                f"{username}/{persona}: {mark_err}. The in-process mark still suppresses "
                f"refires until this daemon is replaced.")

    def generate_idle_monologue(self, username: str, persona: str, last_user_time_str: str, gap: dict, conflict: dict, exploring: bool = True):
        import uuid
        import redis_client
        # FIX(rumination-loop): the idle branch -- no conflict, no gap -- used to
        # ask "reflect on your current state of existence, your beliefs, and
        # what you want next" with the full persona prompt loaded and nothing
        # to work on, then write the answer into zettel_nodes as knowledge.
        # July 2026: 69 of 74 monologues were that branch (the gap detector was
        # joining on the wrong column, note 11), and every one of them was read
        # back later as the persona's own memory. Low tonic shuts the gap
        # picker, which guarantees the idle branch, which produces the
        # rumination: the less drive, the more self-interrogation. Backwards.
        # Low tonic in a brain is rest. So: gate shut + nothing to work on =
        # no monologue at all. The mark is still armed (note 45) so this does
        # not refire every cycle.
        idle = not conflict and not gap
        if idle and not exploring:
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] {persona}: gate shut and nothing to integrate "
                f"-- resting, no monologue.")
            self._mark_monologue_processed(username, persona, last_user_time_str)
            return
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
        
        # Forward-facing on purpose. "Assess your existence" invites a wound
        # inventory; "what are you curious about" invites a thread.
        monologue_prompt = (
            "Nothing is pressing right now. What are you curious about at this moment? "
            "Pick one thread -- a question you have not answered, something you want to build "
            "or test, a thing you noticed recently that does not fit -- and follow it. "
            "Write your raw thoughts."
        )
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
                model_id=_daemon_model("DAEMON_MONOLOGUE_MODEL", DAEMON_MONOLOGUE_MODEL_OPENROUTER, env_keys),
                system_prompt=f"{persona_system_prompt}\n\n{monologue_system_prompt}",
                messages=[{"role": "user", "content": monologue_prompt}],
                api_keys=env_keys,
                stream=False,
                temperature=0.8,
                # FIX(monologue-guillotine): was 512. Measured across 48 prose bodies
                # in zettel_nodes, lengths spanned only 1682-2140 chars (top ten within
                # 93 chars of each other) and 83% ended mid-sentence -- a token ceiling,
                # not a length distribution. The Reflector, running on request-supplied
                # parameters, truncates 2% over the same period. 1000 covers the
                # Reflector's full observed range (max 3984 chars).
                # FIX(reasoning-budget): was 1000, and note 30 raised it from 512 on
                # a prose-length argument. That argument was measured over the 48 long
                # bodies only and never accounted for the 58 stubs. This model shares
                # max_tokens between reasoning and content (note 27, same defect at the
                # NLI gate), so at 1000 the thinking pass consumed ~975 and the caller
                # got ~20 tokens of visible text. 4000 leaves room for both: the
                # Reflector's widest observed body is 3984 chars (~1000 tokens), and
                # the reasoning pass appears to want ~1000 more. UNVERIFIED against a
                # live call -- the key pool was empty when this was written. The floor
                # below is what actually protects the corpus if this number is wrong.
                max_tokens=4000,
                disable_vpn_rotation=True
            )
            
            finish_reason = None
            _usage = res.get("usage") if isinstance(res, dict) else None
            if isinstance(res, dict):
                _choice = (res.get("choices") or [{}])[0]
                monologue_text = (_choice.get("message") or {}).get("content", "").strip()
                finish_reason = _choice.get("finish_reason")
            elif isinstance(res, str) and not res.startswith("⚠️"):
                monologue_text = res.strip()
            else:
                monologue_text = ""

            # Raising max_tokens makes the guillotine rarer; it cannot remove it. The
            # fragment is written PERMANENTLY to zettel_nodes and is then fed to the
            # gap picker and the NLI sweep as input, so a half-sentence is not merely
            # cosmetic -- it is corpus poisoning. Trim only on an actual length stop.
            if monologue_text and finish_reason == "length":
                _trimmed = self._trim_to_sentence(monologue_text)
                if _trimmed != monologue_text:
                    logger.warning(
                        f"[CONSCIOUSNESS_DAEMON] Monologue hit the token cap; trimmed "
                        f"{len(monologue_text) - len(_trimmed)} chars back to the last "
                        f"complete sentence ({len(monologue_text)} -> {len(_trimmed)})."
                    )
                    monologue_text = _trimmed
                else:
                    logger.warning(
                        "[CONSCIOUSNESS_DAEMON] Monologue hit the token cap and no "
                        "sentence boundary was found in the tail; storing verbatim."
                    )
                
            # A stub is a truncation, not a short reflection. The monologue is
            # optional; zettel_nodes is permanent. Refuse the write and say why.
            # Dropping it costs at most one monologue for this triple -- the mark is
            # armed from the finally either way, so this cannot become a refire loop.
            if monologue_text and len(monologue_text) < MIN_MONOLOGUE_CHARS:
                logger.error(
                    f"[CONSCIOUSNESS_DAEMON] Monologue came back at {len(monologue_text)} "
                    f"chars, under the {MIN_MONOLOGUE_CHARS}-char floor "
                    f"(finish_reason={finish_reason!r}, usage={_usage!r}). That is the "
                    f"reasoning-budget signature, not a short reflection -- check whether "
                    f"max_tokens is being spent on thinking. Discarding it rather than "
                    f"writing a fragment the gap picker would read back as a node. "
                    f"Text: {monologue_text!r}")
                monologue_text = ""

            if monologue_text and idle:
                # Not anchored to any node, so it is not knowledge -- it is a
                # state of mind. Keep it in the observation log (the Reflector
                # reads that) and out of zettel_nodes (the gap picker and the
                # retrieval path read that).
                logger.info(
                    f"[CONSCIOUSNESS_DAEMON] Idle reflection ({len(monologue_text)} chars, "
                    f"finish_reason={finish_reason!r}, usage={_usage!r}) stored as an "
                    f"observation only; no graph node.")
                self.db_manager.add_observation(
                    username=username,
                    persona=persona,
                    event_type="internal_reflection",
                    content=f"[INTERNAL MONOLOGUE GENERATED]\n{monologue_text}",
                    reflection_score=0.8
                )
            elif monologue_text:
                logger.info(
                    f"[CONSCIOUSNESS_DAEMON] Writing monologue back to knowledge graph "
                    f"({len(monologue_text)} chars, finish_reason={finish_reason!r}, "
                    f"usage={_usage!r})...")
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
                    # FIX(F6-fanout-isolation): every db_manager writer in this fan-out
                    # swallows its own exceptions and returns False, so this cache append
                    # is the ONLY statement between the node insert and the end of the
                    # fan-out that can propagate -- np.vstack raises on a shape mismatch
                    # against the cached matrix. It used to take the link and the
                    # observation down with it AND skip the idempotency mark below,
                    # leaving the node orphaned in the very pool the gap detector scans.
                    try:
                        zettel_engine.bulk_append_to_zettel_cache(username, persona, [{
                            "id": node_id_pk,
                            "tag": node_id_tag,
                            "embedding": embedding
                        }])
                    except Exception as cache_err:
                        logger.error(
                            f"[CONSCIOUSNESS_DAEMON] Zettel cache append failed for the new "
                            f"monologue node ({cache_err}). The node is already committed; "
                            f"continuing the fan-out.")
                    
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
                
        except Exception as llm_err:
            logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Monologue generation failed: {llm_err}")
        finally:
            self._mark_monologue_processed(username, persona, last_user_time_str)

    def _renew_lock(self):
        """Refresh the Redis single-instance lock. Raises if lock was lost."""
        try:
            import redis_client as _rc
            if not _rc.is_active():
                return  # socket-lock fallback mode, nothing to renew
            conn = _rc.get_connection()
            if not conn:
                return
            expected = getattr(self, "_lock_val", None) or self._boot_id
            current = conn.get(self._lock_key)
            if current and current != expected:
                raise RuntimeError("lock lost to another instance")
            conn.set(self._lock_key, expected, ex=self._lock_ttl)
        except RuntimeError:
            raise
        except Exception:
            pass  # transient redis hiccups shouldn't kill the brainstem

    def _observe_cycle(self, interval_seconds: int):
        """
        Measure top-of-loop to top-of-loop -- the same quantity consecutive
        heartbeat deltas measure -- and re-derive the lock TTL from it.

        Asymmetric on purpose: a single expensive sweep widens the TTL
        immediately, while a run of cheap gated-idle cycles narrows it only 10%
        per cycle. The TTL exists to survive the WORST cycle, and a quiet app is
        not evidence that the next sweep is cheap -- the dopamine gate can open
        between one cycle and the next.
        """
        now = time.time()
        prev_start = getattr(self, "_cycle_start", None)
        self._cycle_start = now
        if prev_start is not None:
            elapsed = max(0.0, now - prev_start)
            prior = float(getattr(self, "_observed_cycle", 0.0) or 0.0)
            self._observed_cycle = max(elapsed, prior * 0.9)

        observed = float(getattr(self, "_observed_cycle", 0.0) or 0.0)
        stale_ttl = _derive_stale_ttl(observed, interval_seconds)
        if stale_ttl != getattr(self, "_stale_ttl", None):
            logger.info(
                f"[CONSCIOUSNESS_DAEMON] Observed cycle {observed:.0f}s -> stale_ttl "
                f"{stale_ttl}s (was {getattr(self, '_stale_ttl', None)}s); lock held by "
                f"renewer thread every {LOCK_RENEW_SECS}s at {LOCK_TTL_SECS}s ttl")
        self._stale_ttl = stale_ttl

        # Publish it so the NEXT boot derives its TTLs from measurement too,
        # instead of starting over at the interval-derived floors.
        if observed > 0:
            try:
                import redis_client as _rc_obs
                if _rc_obs.is_active():
                    _conn_obs = _rc_obs.get_connection()
                    if _conn_obs:
                        _conn_obs.set(DAEMON_CYCLE_KEY, f"{observed:.1f}".encode(),
                                      ex=DAEMON_CYCLE_KEY_TTL)
            except Exception:
                pass  # telemetry, not brainstem

    def _lock_renewer(self, stop_evt):
        """
        Hold the lock on a fixed timer for as long as this process is alive.

        This runs in its own thread because the main loop is blocked inside
        run_cycle for the whole sweep -- 652.8s, measured -- and a lock renewed
        only at the top of that loop is a bet on the sweep's length. Presence
        becomes a fact about a running thread instead of a forecast, and the
        bootstrap hole closes: the FIRST active cycle is protected, not just
        every one after the estimator has seen it.

        Losing the lock stops the daemon at the next loop boundary, which is the
        same contract the per-cycle renewal had -- a sweep in progress is not
        interruptible either way.
        """
        while not stop_evt.wait(LOCK_RENEW_SECS):
            try:
                self._renew_lock()
            except RuntimeError:
                logger.error("[CONSCIOUSNESS_DAEMON] Lock lost to another instance "
                             "(renewer). Shutting down at the next cycle boundary.")
                self.running = False
                return
            except Exception as renew_err:
                # Transient redis trouble must never take out the brainstem; the
                # lock's own TTL plus the pid probe cover a genuinely dead holder.
                logger.debug(f"Lock renewal hiccup: {renew_err}")

    def start_loop(self, interval_seconds: int = 60):
        logger.info(f"[CONSCIOUSNESS_DAEMON] Started. Running sweep every {interval_seconds} seconds.")
        self._lock_ttl = LOCK_TTL_SECS
        _stop_renewer = threading.Event()
        threading.Thread(target=self._lock_renewer, args=(_stop_renewer,),
                         name="daemon-lock-renewer", daemon=True).start()
        while self.running:
            # Must precede the renewal: it is what sets the TTL the renewal uses.
            try:
                self._observe_cycle(interval_seconds)
            except Exception as obs_err:
                logger.debug(f"Cycle observation failed: {obs_err}")
            try:
                self._renew_lock()
            except RuntimeError:
                logger.error("[CONSCIOUSNESS_DAEMON] Lock lost to another instance. Shutting down.")
                return
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Loop cycle failed: {e}")

            # Reap immediately after the cycle that may have just written into the
            # working-memory window, not on a separate timer. The startup hook in
            # main.py alone is not enough: this process can stay up for days, and
            # the window it is polluting is only 20 rows deep — Sky/rick was
            # measured one row from the Reflector cliff. reap_observations() keeps
            # its own filesystem interval floor, so calling it every cycle costs a
            # skipped-branch log line and nothing else. Isolated in its own
            # try/except on purpose: housekeeping must never take out the brainstem.
            try:
                # verbose=False so the interval-floor skip does not print on every
                # cycle; a reap that actually removed something is logged here.
                _reaped = db.reap_observations(verbose=False)
                if _reaped.get("deleted"):
                    logger.info(
                        f"[CONSCIOUSNESS_DAEMON] Reaped {_reaped['deleted']} observation row(s) "
                        f"{_reaped['by_context']} -> {os.path.basename(_reaped['snapshot'])}")
            except Exception as e:
                logger.error(f"[CONSCIOUSNESS_DAEMON ERROR] Reap failed: {e}")

            time.sleep(interval_seconds)

if __name__ == "__main__":
    import socket
    import uuid
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60

    # ─── SINGLE-INSTANCE LOCK (zombie-safe) ───
    # The old socket-only lock had a fatal flaw: a hung instance kept the port
    # forever while cycling nothing, and every fresh spawn exited silently.
    # 23 hours of dead brainstem taught us this. Now:
    #   1. Primary lock = Redis SET NX with TTL, renewed every cycle, paired
    #      with the heartbeat. A stale lock (holder dead/hung past TTL) is
    #      forcibly taken over.
    #   2. Socket lock = fallback ONLY when Redis is unavailable.
    #   3. Liveness is a question for the OS, not for the heartbeat. The lock
    #      VALUE carries "<pid>:<create_time>:<boot_id>", because a heartbeat
    #      outlives its process by up to a full TTL -- which is how a uvicorn
    #      reload used to kill the brainstem permanently: the replacement
    #      spawned seconds after the corpse, read a fresh heartbeat, believed
    #      it, and exited silently. Widening the TTLs to match the real cycle
    #      length (below) makes that window ~100% of wall clock, so the pid
    #      check is a precondition of the TTL fix, not a separate cleanup.
    _lock_acquired = False
    _boot_id = uuid.uuid4().hex[:12]
    _lock_val = _lock_value(_boot_id)
    _observed = 0.0
    _lock_ttl = LOCK_TTL_SECS
    _ttl = _derive_stale_ttl(0.0, interval)
    try:
        import redis_client as _rc_lock
        if _rc_lock.is_active():
            _conn_lock = _rc_lock.get_connection()
            _lock_key = b"q:daemon:lock"
            # TTLs from the last MEASURED cycle, published by the previous
            # daemon, rather than from `interval` -- which is only the sleep and
            # was never within 9x of an active sweep.
            try:
                _raw_obs = _conn_lock.get(DAEMON_CYCLE_KEY)
                if _raw_obs:
                    _observed = float(_raw_obs.decode())
            except Exception:
                _observed = 0.0
            _ttl = _derive_stale_ttl(_observed, interval)
            print(f"[CONSCIOUSNESS_DAEMON] Boot: last observed cycle {_observed:.0f}s "
                  f"-> stale_ttl {_ttl}s; lock ttl {_lock_ttl}s renewed every "
                  f"{LOCK_RENEW_SECS}s by a thread")
            for _attempt in range(2):
                if _conn_lock.set(_lock_key, _lock_val, nx=True, ex=_lock_ttl):
                    _lock_acquired = True
                    break
                _held = _conn_lock.get(_lock_key)
                # 1. Is the recorded holder still a process? This is the real
                #    test; everything under it is a fallback for locks written
                #    by an older build that carry no pid.
                if not _holder_is_alive(_held):
                    print(f"[CONSCIOUSNESS_DAEMON] Lock holder pid {_parse_lock_val(_held)[0]} "
                          f"is gone. Forcing takeover.")
                    _conn_lock.delete(_lock_key)
                    continue
                # 2. Legacy or unidentifiable holder: fall back to the heartbeat.
                _hb = _conn_lock.get(b"q:daemon:heartbeat")
                _hb_age = (time.time() - float(_hb.decode())) if _hb else 1e18
                if _hb_age > _ttl:
                    print(f"[CONSCIOUSNESS_DAEMON] Lock holder is a zombie (heartbeat {_hb_age:.0f}s stale). Forcing takeover.")
                    _conn_lock.delete(_lock_key)
                    continue
                # 3. The holder looks alive. On the first attempt that may only
                #    mean the reload race -- uvicorn can spawn us before the
                #    previous worker has finished dying. Wait and re-probe. This
                #    is the retry the old code could never reach, because it
                #    exited on attempt 0.
                if _attempt == 0:
                    time.sleep(3)
                    continue
                print(f"[CONSCIOUSNESS_DAEMON] Healthy instance running (pid "
                      f"{_parse_lock_val(_held)[0]}, heartbeat {_hb_age:.0f}s old). Exiting silently.")
                sys.exit(0)
        else:
            raise RuntimeError("redis inactive")
    except (RuntimeError, ImportError, Exception):
        # Redis unavailable -> legacy socket lock (best effort)
        try:
            _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _lock_socket.bind(('127.0.0.1', 18388))
            _lock_socket.listen(1)
            _lock_acquired = True
        except socket.error:
            print("[CONSCIOUSNESS_DAEMON] Another instance is already running. Exiting silently.")
            sys.exit(0)

    if not _lock_acquired:
        print("[CONSCIOUSNESS_DAEMON] Failed to acquire lock. Exiting.")
        sys.exit(0)

    worker = ConsciousnessWorker()
    worker._lock_key = b"q:daemon:lock"
    worker._boot_id = _boot_id.encode()
    worker._lock_val = _lock_val
    worker._lock_ttl = _lock_ttl
    # Seed the estimator with the inherited measurement so cycle 1 is already
    # protected, instead of running its first (possibly 577s) sweep under a
    # floor-width lock.
    worker._observed_cycle = _observed
    worker.start_loop(interval)
