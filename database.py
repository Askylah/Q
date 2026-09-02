import sqlite3
import hashlib
import hmac
import os
import re
import bcrypt
import json
from datetime import datetime, date, timedelta

# Single source of truth for storage locations. Importing app_paths also performs
# the one-time migration of a project-root database into the data directory, and
# that must happen before anything opens a connection. See app_paths.py for the
# full rationale (MCP writable root, OneDrive/WAL corruption, duplicate DB_PATH).
from app_paths import DB_PATH, BACKUP_DIR, ensure_dirs

# Automatically inject timeout=30.0 for all database connections to prevent lock crashes
# NOTE: this wrapper is keyed on STRING EQUALITY with DB_PATH. That is precisely why
# every module must import the shared app_paths value instead of recomputing its own
# path — a divergence here silently disables lock protection with no error at all.
_original_connect = sqlite3.connect
def _custom_connect(*args, **kwargs):
    if len(args) > 0 and args[0] == DB_PATH:
        kwargs.setdefault('timeout', 30.0)
    elif 'database' in kwargs and kwargs['database'] == DB_PATH:
        kwargs.setdefault('timeout', 30.0)
    return _original_connect(*args, **kwargs)
sqlite3.connect = _custom_connect

_BACKUP_PREFIX = "users_"
_BACKUP_SUFFIX = ".db"


def list_backups():
    """Existing backups, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    entries = []
    for name in os.listdir(BACKUP_DIR):
        if name.startswith(_BACKUP_PREFIX) and name.endswith(_BACKUP_SUFFIX):
            full = os.path.join(BACKUP_DIR, name)
            if os.path.isfile(full):
                entries.append(full)
    return sorted(entries, key=lambda p: os.path.getmtime(p), reverse=True)


def backup_database(retain=10, min_interval_hours=6.0, force=False, verbose=True):
    """
    Take a point-in-time backup of the database using SQLite's ONLINE BACKUP API.

    Why conn.backup() and not shutil.copy2:
        journal_mode=WAL is enabled in _init_db below. A plain byte copy of a
        WAL-mode database can capture a main file and a -wal file that disagree,
        yielding a target that appears fine and only fails when you finally need
        it. The online backup API takes a transactionally consistent snapshot
        even while writers are active. This is not a stylistic preference; the
        copy2 approach produces silently broken backups under exactly the load
        conditions that make you want a backup.

    Every backup is verified with PRAGMA quick_check before it is kept. A corrupt
    backup is worse than no backup because it buys false confidence.

    min_interval_hours exists because main.py:953 starts uvicorn with reload=True,
    so startup hooks re-fire on every source edit. Without a floor, this would
    mint a fresh multi-megabyte file every time a file is saved.

    Returns the backup path, or None if skipped.
    """
    if not os.path.exists(DB_PATH):
        if verbose:
            print(f"[BACKUP] No database at {DB_PATH}; nothing to back up.")
        return None

    ensure_dirs()
    existing = list_backups()

    if not force and existing and min_interval_hours > 0:
        age_hours = (datetime.now().timestamp() - os.path.getmtime(existing[0])) / 3600.0
        if age_hours < min_interval_hours:
            if verbose:
                print(f"[BACKUP] Skipped — newest backup is {age_hours:.1f}h old "
                      f"(floor {min_interval_hours}h).")
            return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"{_BACKUP_PREFIX}{stamp}{_BACKUP_SUFFIX}")
    staging = dest + ".partial"

    source = _original_connect(DB_PATH, timeout=30.0)
    try:
        target = _original_connect(staging)
        try:
            with target:
                source.backup(target)
            row = target.execute("PRAGMA quick_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                raise RuntimeError(f"quick_check failed: {row}")
        finally:
            target.close()
    except Exception as exc:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass
        print(f"[BACKUP] FAILED: {exc}")
        return None
    finally:
        source.close()

    # Only named into place after passing verification.
    os.replace(staging, dest)

    pruned = 0
    for stale in list_backups()[max(retain, 1):]:
        try:
            os.remove(stale)
            pruned += 1
        except OSError:
            pass

    if verbose:
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        print(f"[BACKUP] {os.path.basename(dest)} ({size_mb:.1f} MB) verified ok"
              + (f", pruned {pruned} old" if pruned else ""))
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# THE OBSERVATION REAPER
# ─────────────────────────────────────────────────────────────────────────────
# There has never been a reaper for `observations`. memory_engine.decay_cycle()
# is the only decay engine in the tree: it selects FROM deep_memories, and even
# there it only does SET active=0 — it archives, it does not delete. The comment
# in stream_worker ("park the voids, let decay do its thing") points at an engine
# that does not cover the table it is writing to. Until now the only deletions
# available were three all-or-nothing per-persona wipes.
#
# THIS IS NOT A DISK FEATURE. The flood that motivated it was 85 rows. The harm
# is that get_observation_log() is a fixed-size window — the newest 15 to 20 rows
# — and that window IS the persona's working memory and the Reflector's input.
# Daemon alarm rows and real conversation compete for the same slots, so an
# unbounded writer does not bloat the table, it evicts the conversation. Measured
# immediately before this landed: Sky/rick's 20-row window held exactly 5 raw
# events against a reflect() threshold of 5. One more daemon row would have made
# it 4, at which point reflect() returns before the LLM call and
# dense_observation stops being written for that persona *permanently*, because
# nothing else removes daemon rows from the window. That cliff is why this runs
# on a short interval, and it is why the retention clause below is not optional.
#
# RECOVERABILITY. Every reaped row is written to a JSONL snapshot in BACKUP_DIR
# and verified by read-back before a single row is deleted, using the same
# .partial -> verify -> os.replace discipline as backup_database().

_REAP_PREFIX = "reaped_"
_REAP_SUFFIX = ".jsonl"

# Filesystem mtime as durable last-run — the idiom backup_database() already uses
# via list_backups() + getmtime. It needs no schema change and no process memory,
# so it survives every way the daemon dies (parent killed on source edit, zombie
# takeover when the lock TTL lapses, Redis-down socket fallback).
#
# It is a SEPARATE stamp file rather than the newest snapshot's mtime, which is
# where the borrowed idiom needed adapting: backup_database always produces a
# file, but a reaper run that finds nothing to reap produces none. Keying off the
# snapshot would mean such a run never records that it happened, the floor would
# never bind, and the reaper would re-enter on every single daemon cycle forever.
_REAP_STAMP = "reaped_last_run.stamp"

# Daemon-authored event types: alarms, not memories. Nothing reads these back as
# content — llm_engine pulls only dense_observation for <agent_reflections> — but
# they occupy the same working-memory window as real user turns.
#
# internal_reflection is in the default set deliberately, and it is the one that
# looks like content worth keeping. It is here because it is the only writer on
# this path with no rate limit at all: the daemon's dissonance cap covers
# entropic_gap and semantic_contradiction, while generate_idle_monologue's
# idempotency mark is the last statement of the try block that also wraps its LLM
# call and its entire write fan-out — so anything raising in between skips the
# mark and re-arms it for the next cycle. Measured: 22 of 37 rows (59%) fired
# against an already-processed timestamp. Until that mark is written independently
# of the fan-out, this reaper is the only bound on that writer.
# Override with PERSONAAPP_REAP_TYPES.
_DEFAULT_REAP_TYPES = "entropic_gap,semantic_contradiction,internal_reflection"


def _reap_types():
    raw = os.getenv("PERSONAAPP_REAP_TYPES", _DEFAULT_REAP_TYPES)
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def _touch_reap_stamp(path):
    """Record a completed run. Written even when nothing was reaped, so the
    interval floor binds on no-op runs too."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(datetime.now()) + "\n")
    except OSError as exc:
        print(f"[REAP] WARNING: could not update run stamp: {exc}")


def list_reap_snapshots():
    """Reaper snapshots, newest first.

    Deliberately a different prefix/suffix pair from list_backups(). That helper
    matches users_*.db and backup_database() deletes everything past its retain-th
    entry, so a snapshot named into that namespace would be silently pruned by an
    unrelated subsystem the first time a backup ran.
    """
    if not os.path.isdir(BACKUP_DIR):
        return []
    entries = []
    for name in os.listdir(BACKUP_DIR):
        if name.startswith(_REAP_PREFIX) and name.endswith(_REAP_SUFFIX):
            full = os.path.join(BACKUP_DIR, name)
            if os.path.isfile(full):
                entries.append(full)
    return sorted(entries, key=lambda p: os.path.getmtime(p), reverse=True)


def reap_observations(ttl_days=None, keep_per_context=None, min_interval_hours=None,
                      event_types=None, retain_snapshots=30, force=False,
                      dry_run=False, verbose=True):
    """Age off daemon-authored observation rows, snapshotting them first.

    A reapable row dies if EITHER is true:
      * it is older than ttl_days                    — bounds the table
      * it is not among the newest keep_per_context   — bounds the WINDOW
        reapable rows for its (username, persona)

    The second clause is the load-bearing one, and it is not an age policy. The
    flood this was written for put 38 rows down in three hours: a TTL of any sane
    length leaves all 38 in place, and the working-memory window stays entirely
    alarm rows. Age bounds the table; only a per-context retention cap bounds what
    the persona actually sees. Measured on the live data, every pure-TTL setting
    from 30 days down to 2 left the Reflector at exactly its threshold.

    keep_per_context is a COMBINED budget across every reapable type for one
    (username, persona), not a budget per type — that is what makes it able to
    bound the injected window, which does not care which daemon wrote a row.

    It is floored at 1 on purpose, never 0, so an ACTIVE context keeps its newest
    daemon row: stream_worker picks which persona to service next by reading the
    single newest observation row for a username, and reaping a context to zero
    can silently change which persona the daemon scans.

    That floor is NOT absolute, because the two clauses are OR'd: once the last
    surviving row is itself older than ttl_days the TTL takes it too, and a
    dormant context ends up with no daemon rows at all. Verified, not assumed —
    a context holding one 60-day-old row is reaped to zero. This is intended. A
    context whose newest alarm is two weeks old is not the one driving the
    selector (its newest observation is a user turn by then), and exempting it
    would mint an alarm row no reaper could ever remove. It is spelled out here
    because "floored at 1" reads like a guarantee it does not make, and a comment
    promising more than its code delivers is the exact defect that started this
    whole investigation.

    It defaults to 1 because that is the only value at which every live context
    lands at 0-1 daemon rows in the 5-slot window llm_engine actually injects;
    2 leaves SkyTest/rick at 2/5 and 3 leaves Sky/rick at 3/5. Understand what
    that does and does not buy: it is true at the instant of a reap and survives
    exactly ONE new daemon row. Retention cannot hold that property — only
    filtering daemon types out of llm_engine's recent_events can. Raise this if
    you would rather the persona kept more standing alarm context than have the
    window clean immediately after each reap.

    Returns a summary dict. Never raises — a reaper that can take down the app is
    worse than a table that grows.
    """
    def _env_num(name, fallback, cast):
        try:
            return cast(os.getenv(name, fallback))
        except (TypeError, ValueError):
            return cast(fallback)

    if ttl_days is None:
        ttl_days = _env_num("PERSONAAPP_REAP_TTL_DAYS", 14, int)
    if keep_per_context is None:
        keep_per_context = _env_num("PERSONAAPP_REAP_KEEP", 1, int)
    keep_per_context = max(1, keep_per_context)
    if min_interval_hours is None:
        min_interval_hours = _env_num("PERSONAAPP_REAP_INTERVAL_HOURS", 1.0, float)
    types = tuple(event_types) if event_types else _reap_types()

    result = {"ran": False, "deleted": 0, "snapshot": None, "scanned": 0,
              "by_context": {}, "skipped": None, "dry_run": bool(dry_run)}

    if not types:
        result["skipped"] = "no reapable event types configured"
        return result
    if not os.path.exists(DB_PATH):
        result["skipped"] = "no database"
        return result

    ensure_dirs()
    stamp_path = os.path.join(BACKUP_DIR, _REAP_STAMP)

    # Sweep staging files abandoned by a hard kill. The except path below unlinks
    # its own staging, but SIGKILL or a machine losing power does not run it, and
    # nothing else in the tree janitors this directory — backup_database's prune
    # only ever considers users_*.db, so an orphan .partial would sit here
    # forever. A .partial is by definition a snapshot that never landed, so no row
    # was ever deleted on its behalf and removing one loses nothing.
    #
    # Age-gated to an hour because two daemon instances can run at once: an
    # unconditional sweep would let one process delete the other's in-flight
    # staging file mid-write. That fails safe (the writer's os.replace raises, it
    # rolls back, nothing is deleted) but it is a spurious abort, and a real
    # snapshot write takes single-digit milliseconds.
    try:
        _now = datetime.now().timestamp()
        for name in os.listdir(BACKUP_DIR):
            if name.startswith(_REAP_PREFIX) and name.endswith(".partial"):
                orphan = os.path.join(BACKUP_DIR, name)
                try:
                    if _now - os.path.getmtime(orphan) > 3600:
                        os.remove(orphan)
                except OSError:
                    pass
    except OSError:
        pass

    if not force and min_interval_hours > 0 and os.path.exists(stamp_path):
        age_hours = (datetime.now().timestamp() - os.path.getmtime(stamp_path)) / 3600.0
        if age_hours < min_interval_hours:
            result["skipped"] = f"last run {age_hours:.2f}h ago (floor {min_interval_hours}h)"
            if verbose:
                print(f"[REAP] Skipped — {result['skipped']}")
            return result

    cutoff = str(datetime.now() - timedelta(days=ttl_days))
    placeholders = ",".join("?" for _ in types)
    staging = None
    committed = False

    # backup_database() calls _original_connect with an EXPLICIT timeout rather
    # than trusting the module-level monkeypatch, and so does this. That patch is
    # keyed on string equality with DB_PATH and only exists once this module has
    # been imported at all; relying on it is how a caller silently gets sqlite's
    # 5-second default and intermittent "database is locked" under exactly the
    # write load that makes a reaper necessary in the first place.
    conn = _original_connect(DB_PATH, timeout=30.0)
    try:
        # BEGIN IMMEDIATE takes the write lock up front. Python's sqlite3 is in
        # legacy transaction control here — nothing in the tree sets
        # isolation_level or autocommit — so a bare SELECT opens no transaction
        # and only the first DML starts one. Without this, a
        # "SELECT ... WHERE timestamp < cutoff" followed by a
        # "DELETE ... WHERE timestamp < cutoff" re-evaluates the predicate against
        # newer state, and any row the daemon commits in between is deleted
        # without ever having been snapshotted. Two independent defences, because
        # that failure is silent and unrecoverable: this transaction, and deleting
        # by an explicit captured id list instead of by re-running the predicate.
        conn.execute("BEGIN IMMEDIATE")

        # Read the sequence INSIDE the transaction, never before it. A concurrent
        # add_observation legitimately advances sqlite_sequence, so a baseline
        # captured before the write lock was held turns every racing insert into a
        # spurious "sequence moved" abort — measured at 23 of 40 runs under a
        # synthetic two-writer race, each one rolling back an otherwise good reap.
        # Inside BEGIN IMMEDIATE no other writer can commit, so the only thing that
        # could move this value is a statement of ours, which is exactly what the
        # check downstream is for.
        seq_before = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='observations'").fetchone()

        rows = conn.execute(
            f"""SELECT id, username, persona, event_type, content, reflection_score, timestamp
                FROM observations
                WHERE event_type IN ({placeholders})
                ORDER BY username, persona, id""",
            types,
        ).fetchall()
        result["scanned"] = len(rows)

        # Group by context, then mark. Done in Python rather than with a window
        # function: the reapable set is small, and it keeps the whole policy
        # readable in one place instead of split between SQL and code.
        by_ctx = {}
        for r in rows:
            by_ctx.setdefault((r[1], r[2]), []).append(r)

        doomed = []
        for (uname, pers), ctx_rows in by_ctx.items():
            survivors = {r[0] for r in ctx_rows[-keep_per_context:]}
            for r in ctx_rows:
                if r[0] not in survivors or (r[6] or "") < cutoff:
                    doomed.append(r)
                    label = f"{uname}/{pers}"
                    result["by_context"][label] = result["by_context"].get(label, 0) + 1

        if not doomed:
            conn.rollback()
            result["ran"] = True
            _touch_reap_stamp(stamp_path)
            if verbose:
                print(f"[REAP] Nothing to reap ({len(rows)} reapable rows, all retained).")
            return result

        doomed.sort(key=lambda r: r[0])
        doomed_ids = [r[0] for r in doomed]

        if dry_run:
            conn.rollback()
            result["ran"] = True
            result["deleted"] = len(doomed_ids)
            if verbose:
                print(f"[REAP] DRY RUN — would delete {len(doomed_ids)} of {len(rows)} "
                      f"reapable rows: {result['by_context']}")
            return result

        # --- the snapshot lands atomically BEFORE anything is deleted ---
        # Second granularity is NOT enough here, unlike backup_database which is
        # floored at 6 hours. force=True exists for operators clearing a backlog,
        # and the daemon can genuinely run as two instances at once because its
        # lock TTL is shorter than a measured cycle — so two reaps can land in the
        # same second. When they did, both computed the same filename and the
        # second os.replace silently overwrote the first, making the first run's
        # deleted rows unrecoverable. Measured: 40 runs inside one second produced
        # one surviving snapshot holding 6 of 234 deleted rows.
        # Microseconds plus a collision loop make the destination unique on disk;
        # the pid makes the staging path unique per process, so two daemons can
        # never scribble over each other's half-written snapshot.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = os.path.join(BACKUP_DIR, f"{_REAP_PREFIX}{stamp}{_REAP_SUFFIX}")
        _collision = 1
        while os.path.exists(dest):
            dest = os.path.join(
                BACKUP_DIR, f"{_REAP_PREFIX}{stamp}_{_collision}{_REAP_SUFFIX}")
            _collision += 1
        staging = f"{dest}.{os.getpid()}.partial"
        cols = ("id", "username", "persona", "event_type", "content",
                "reflection_score", "timestamp")
        with open(staging, "w", encoding="utf-8", newline="\n") as fh:
            for r in doomed:
                fh.write(json.dumps(dict(zip(cols, r)), ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        # Verify by reading back what was written rather than trusting the write.
        # This file is the only thing that makes the DELETE below reversible, so
        # an unparseable snapshot is worse than none at all — it buys false
        # confidence, exactly the reason backup_database quick_checks its output.
        recovered = []
        with open(staging, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    recovered.append(json.loads(line)["id"])
        if recovered != doomed_ids:
            raise RuntimeError(
                f"snapshot verification failed: wrote {len(doomed_ids)} rows, "
                f"read back {len(recovered)}")

        os.replace(staging, dest)
        staging = None
        result["snapshot"] = dest

        # --- only now delete, and only these exact ids ---
        for i in range(0, len(doomed_ids), 400):
            chunk = doomed_ids[i:i + 400]
            conn.execute(
                f"DELETE FROM observations WHERE id IN ({','.join('?' for _ in chunk)})",
                chunk)

        # observations.id is INTEGER PRIMARY KEY AUTOINCREMENT, and sqlite_sequence
        # is what makes "deleted" distinguishable from "never created" when you
        # diff two snapshots — the property every forensic pass over this table has
        # relied on. A DELETE with a WHERE clause does not touch it. Assert that
        # rather than assume it: losing it is silent and cannot be undone.
        seq_after = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='observations'").fetchone()
        if seq_before != seq_after:
            raise RuntimeError(
                f"sqlite_sequence.observations moved {seq_before} -> {seq_after}")

        conn.commit()
        committed = True
        result["ran"] = True
        result["deleted"] = len(doomed_ids)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        if staging and os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass
        # The snapshot is named into place before the DELETE, so a failure between
        # os.replace and COMMIT — the sequence assertion below it, or the DELETE
        # itself — leaves a landed snapshot describing rows that are still in the
        # table. That file is not a loss, it is a lie: restoring from it would
        # re-insert live rows as duplicates. The snapshot and the deletion have to
        # be all-or-nothing in both directions, so retract it.
        if not committed and result["snapshot"]:
            try:
                os.remove(result["snapshot"])
            except OSError:
                pass
            result["snapshot"] = None
        print(f"[REAP] FAILED: {exc}")
        result["skipped"] = f"error: {exc}"
        return result
    finally:
        conn.close()

    _touch_reap_stamp(stamp_path)

    pruned = 0
    for stale in list_reap_snapshots()[max(retain_snapshots, 1):]:
        try:
            os.remove(stale)
            pruned += 1
        except OSError:
            pass

    if verbose:
        print(f"[REAP] Deleted {result['deleted']} of {result['scanned']} reapable rows "
              f"-> {os.path.basename(result['snapshot'])} {result['by_context']}"
              + (f", pruned {pruned} old snapshot(s)" if pruned else ""))
    return result


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
                created_at TEXT,
                node_class TEXT DEFAULT 'lore',
                trigger_type TEXT DEFAULT 'PROBABILISTIC',
                content_hash TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS zettel_links (
                id TEXT PRIMARY KEY,
                source_node_id TEXT,
                target_node_id TEXT,
                relationship TEXT,
                strength FLOAT DEFAULT 0.5,
                created_at TEXT,
                label TEXT DEFAULT 'related'
            )
        ''')

        # Migration: Ensure new columns exist on zettel_nodes
        c.execute("PRAGMA table_info(zettel_nodes)")
        node_columns = [column[1] for column in c.fetchall()]
        if 'node_class' not in node_columns:
            c.execute("ALTER TABLE zettel_nodes ADD COLUMN node_class TEXT DEFAULT 'lore'")
        if 'trigger_type' not in node_columns:
            c.execute("ALTER TABLE zettel_nodes ADD COLUMN trigger_type TEXT DEFAULT 'PROBABILISTIC'")
        if 'content_hash' not in node_columns:
            c.execute("ALTER TABLE zettel_nodes ADD COLUMN content_hash TEXT")

        # Migration: Ensure label column exists on zettel_links
        c.execute("PRAGMA table_info(zettel_links)")
        link_columns = [column[1] for column in c.fetchall()]
        if 'label' not in link_columns:
            c.execute("ALTER TABLE zettel_links ADD COLUMN label TEXT DEFAULT 'related'")

        # Migration: Ensure zettel_fts virtual table exists
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS zettel_fts USING fts5(node_db_id UNINDEXED, content, title, category)")

        # User Settings table
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                username TEXT PRIMARY KEY,
                review_policy TEXT DEFAULT 'ask',
                auto_execute_terminal INTEGER DEFAULT 0,
                active_persona_key TEXT,
                security_level TEXT DEFAULT 'strict',
                global_direct_wire INTEGER DEFAULT 1
            )
        ''')
        
        # Migration: Ensure global_direct_wire exists
        c.execute("PRAGMA table_info(user_settings)")
        columns = [column[1] for column in c.fetchall()]
        if 'global_direct_wire' not in columns:
            c.execute("ALTER TABLE user_settings ADD COLUMN global_direct_wire INTEGER DEFAULT 1")

        # ── Performance indexes (idempotent) ──
        # observations: get_observation_log filters (username,persona) and
        # orders by id, twice per turn. zettel_nodes: filtered by
        # (username,persona) on every retrieval. Without these, both are full
        # scans that grow with history.
        c.execute("CREATE INDEX IF NOT EXISTS idx_obs_user_persona_id ON observations(username, persona, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_znodes_user_persona ON zettel_nodes(username, persona)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_zlinks_source ON zettel_links(source_node_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_zlinks_target ON zettel_links(target_node_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_conv_user_persona_id ON conversations(username, persona, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_group_session ON group_conversations(session_id, username, id)")

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
            # FIX(case-variant collision): the UNIQUE constraint on users.username
            # is case-sensitive, but all downstream data queries are COLLATE
            # NOCASE — so registering "Askylah" when "askylah" exists creates a
            # second account that silently shares the first one's memories,
            # personas and zettel graph. Reject case-variants explicitly.
            c.execute("SELECT 1 FROM users WHERE username COLLATE NOCASE=? LIMIT 1", (username,))
            if c.fetchone() is not None:
                return False, "Username already exists."
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
        """Verify profile credentials by hashing secret_key with SHA-256 and checking database.

        NOTE ON UNSALTED SHA-256: correct here. This credential is a
        high-entropy random token (API-key style), not a human password.
        Salting/bcrypt defend low-entropy secrets against rainbow tables and
        brute force; neither applies to 100+ bits of randomness. Fast hashing
        of random tokens is standard practice. The security of this path rests
        entirely on the TOKEN GENERATOR using a CSPRNG (secrets /
        uuid.uuid4 / crypto.getRandomValues) — never `random`, never
        time-or-name-derived.
        """
        if not username or not secret_key:
            return False
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # FIX(case-split-identity): every DATA query in this file matches
        # persona/username with COLLATE NOCASE, but auth matched case-SENSITIVE.
        # So "Askylah" and "askylah" were two distinct accounts for login while
        # sharing one set of memories, personas and zettel nodes — and typing
        # your name with different capitalization on a second device failed to
        # authenticate against data you own. Auth now matches the same way.
        # EXACT MATCH FIRST — never let the convenience fallback shadow a real
        # account. If a row matches the username byte-for-byte, that row wins,
        # always. Only when no exact row exists do we try a case-insensitive
        # lookup (the PC->phone convenience), and only if it is UNAMBIGUOUS:
        # if two case-variant rows exist, we refuse rather than guess, because
        # guessing could authenticate against the wrong account's data.
        c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = c.fetchone()
        if row is None:
            c.execute("SELECT password_hash FROM users WHERE username COLLATE NOCASE=? LIMIT 2", (username,))
            candidates = c.fetchall()
            row = candidates[0] if len(candidates) == 1 else None
        conn.close()
        if row:
            stored_hash = row[0]
            key_hash = hashlib.sha256(secret_key.encode('utf-8')).hexdigest()
            if isinstance(stored_hash, bytes):
                try:
                    stored_hash = stored_hash.decode('utf-8')
                except Exception:
                    return False
            # Constant-time compare: `==` on strings short-circuits at the first
            # differing byte, leaking a timing oracle. Free to eliminate.
            return hmac.compare_digest(key_hash, stored_hash)
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
        """Add a new observation/event to the log.
        
        Dedup guard: if an identical (username, persona, event_type, content) row
        already exists, the insert is skipped. This prevents daemon livelocks from
        flooding the observations table with repeat entries.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # --- DEDUP GUARD (dense_observation only) ---
            # FIX(turn-loss): the guard used to apply to ALL event types, so a
            # user legitimately repeating a message ("yes", "ok") or an
            # identical assistant line silently vanished from the log — which
            # shortened the Reflector's raw-event count and dropped real
            # WORKING_MEMORY turns. Only reflections (the daemon-flood case this
            # guard was written for) are deduped now; raw events are verbatim.
            if event_type == "dense_observation":
                c.execute("""
                    SELECT 1 FROM observations
                    WHERE username=? AND persona=? AND event_type=? AND content=?
                    LIMIT 1
                """, (username, persona, event_type, content))
                if c.fetchone() is not None:
                    conn.close()
                    return True  # duplicate reflection, skip silently
            # --- END DEDUP GUARD ---
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

    def add_zettel_node(self, node_id_pk, username, persona, node_id_tag, title, content, category, embedding_blob, source_entry_id, node_class='lore', trigger_type='PROBABILISTIC', content_hash=None):
        """Insert a chunked atomic node into the knowledge graph."""
        # ── Step 1: Insert the actual node row (committed independently) ──
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT INTO zettel_nodes (id, username, persona, node_id, title, content, category, embedding, source_entry_id, created_at, node_class, trigger_type, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id_pk, username, persona, node_id_tag, title, content, category, embedding_blob, source_entry_id, timestamp, node_class, trigger_type, content_hash))
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

    def add_zettel_link(self, link_id, source_node_id, target_node_id, relationship, strength=0.5, label='related'):
        """Insert a weighted edge between two nodes."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = str(datetime.now())
            c.execute("""
                INSERT OR IGNORE INTO zettel_links (id, source_node_id, target_node_id, relationship, strength, created_at, label)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (link_id, source_node_id, target_node_id, relationship, strength, timestamp, label))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"DB ERROR (add_zettel_link): {e}")
            return False

    def get_zettel_nodes_for_persona(self, username, persona, include_embeddings=True):
        """Get all zettel nodes for a persona.

        include_embeddings=False skips the embedding BLOB column, which is the
        hot per-turn path in query_knowledge_graph (node_lookup + trigger scan
        only — the embedding matrix lives in the cache). Loading blobs there was
        the exact SQLite I/O the cache exists to avoid.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            if include_embeddings:
                c.execute("""
                    SELECT id, node_id, title, content, category, embedding, source_entry_id, created_at, node_class, trigger_type
                    FROM zettel_nodes
                    WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
                """, (username, persona))
                rows = c.fetchall()
                conn.close()
                return [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": r[5], "source_entry_id": r[6], "created_at": r[7], "node_class": r[8], "trigger_type": r[9]} for r in rows]
            else:
                c.execute("""
                    SELECT id, node_id, title, content, category, source_entry_id, created_at, node_class, trigger_type
                    FROM zettel_nodes
                    WHERE username COLLATE NOCASE=? AND persona COLLATE NOCASE=?
                """, (username, persona))
                rows = c.fetchall()
                conn.close()
                return [{"id": r[0], "node_id": r[1], "title": r[2], "content": r[3], "category": r[4], "embedding": None, "source_entry_id": r[5], "created_at": r[6], "node_class": r[7], "trigger_type": r[8]} for r in rows]
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
                        SELECT zn.id, zn.node_id, zn.title, zn.content, zn.category, zl.relationship, zl.strength, zl.label, zn.node_class
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
                                "relationship": row[5], "strength": row[6],
                                "label": row[7], "node_class": row[8]
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
                res = dict(row)
                if "global_direct_wire" not in res:
                    res["global_direct_wire"] = 1
                return res
            return {"username": username, "review_policy": "ask", "auto_execute_terminal": 0, "security_level": "strict", "global_direct_wire": 1}
        except Exception as e:
            print(f"[DB_ERROR] Failed to fetch settings: {e}")
            return {"review_policy": "ask", "global_direct_wire": 1}

    def update_user_settings(self, username: str, settings: dict):
        """Updates user governance settings."""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Ensure the row exists
            c.execute("INSERT OR IGNORE INTO user_settings (username) VALUES (?)", (username,))
            
            for key, value in settings.items():
                if key in ["review_policy", "auto_execute_terminal", "active_persona_key", "security_level", "global_direct_wire"]:
                    c.execute(f"UPDATE user_settings SET {key} = ? WHERE username = ?", (value, username))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DB_ERROR] Failed to update settings: {e}")
