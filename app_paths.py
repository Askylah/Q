"""
app_paths.py — single source of truth for on-disk data locations.

WHY THIS MODULE EXISTS
======================

1) There were TWO independent definitions of the database path:
       database.py:10       DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")
       memory_engine.py:41  DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")
   `memory_engine` does not import `database`; the two agreed only by the accident
   of living in the same directory. That mattered because database.py installs a
   global sqlite3.connect wrapper keyed on string equality with its own DB_PATH
   (database.py:13-20). Had those two values ever diverged, memory_engine's 11
   connections would have silently lost the timeout=30.0 lock protection — no
   error, no warning, just intermittent "database is locked" crashes under load.
   Both now import from here, so they agree by construction instead of by luck.

2) The database used to live in the project root, which is also the writable root
   of the filesystem MCP server (mcp_router.py:277-281) and the git server
   (mcp_router.py:293). @modelcontextprotocol/server-filesystem supports an
   allow-list of directories but has NO deny-list, and the application's source
   files sit in that same root — so the root could not be granted without also
   granting users.db. A single mis-aimed write_file could replace the SQLite
   container wholesale: zettel graph, deep memories, conversations and user
   accounts, gone in one call with no schema, transaction or parser involved.
   Relocating the data out of that root removes the reachability entirely while
   leaving the model's designed source-tree access untouched.

3) The project root is inside a OneDrive-synced folder. SQLite running in WAL
   mode (database.py:31, `PRAGMA journal_mode=WAL`) inside a cloud-sync directory
   is a well-documented corruption vector: the sync client can upload users.db
   and users.db-wal at different instants, or take a lock mid-transaction,
   yielding a torn database whose WAL no longer matches its main file. Moving the
   live data to %LOCALAPPDATA% takes it out of sync scope. This is a real fault
   that existed independently of the security issue above.

OVERRIDES
=========
    PERSONAAPP_DATA_DIR  full data directory (db + backups live inside)
    PERSONAAPP_DB_PATH   exact database file path (wins over DATA_DIR)
Both are honoured so tests and the installer can redirect storage without
patching module internals.
"""

import os
import sqlite3
import sys
from datetime import datetime

# ── Project location (source tree — still the MCP/git root, deliberately) ──────
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Where the database used to live, and may still live on an un-migrated install.
LEGACY_DB_PATH = os.path.join(APP_ROOT, "users.db")


def _default_data_dir() -> str:
    """Per-user application data, outside the source tree and outside cloud sync."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "PersonaApp")


DATA_DIR = os.path.abspath(os.environ.get("PERSONAAPP_DATA_DIR") or _default_data_dir())

DB_PATH = os.path.abspath(
    os.environ.get("PERSONAAPP_DB_PATH") or os.path.join(DATA_DIR, "users.db")
)

# Backups live beside the live database — also outside the MCP root, so a
# compromised write cannot destroy the database and then tidy up the evidence.
BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")

# SQLite sidecars that belong to a WAL-mode database and must travel with it.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def ensure_dirs() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _quick_check(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        return bool(row) and str(row[0]).lower() == "ok"
    except sqlite3.Error:
        return False


def migrate_legacy_database(verbose: bool = True) -> bool:
    """
    Move a project-root database to the new data directory, non-destructively.

    Strategy, in order of paranoia:
      * no-op if the new database already exists (idempotent, reload-safe)
      * no-op if there is no legacy database (fresh install)
      * WAL is checkpointed and the copy is made with sqlite3's ONLINE BACKUP API
        rather than shutil.copy2 — a byte copy of a WAL database can capture a
        main file and a -wal that disagree, producing a corrupt target
      * the copy is integrity-checked with PRAGMA quick_check BEFORE it is put
        into place; a backup that is quietly corrupt is worse than none, because
        it buys false confidence
      * the copy lands on a temp name and is moved in with os.replace (atomic)
      * the ORIGINAL IS NEVER DELETED. It is renamed to users.db.migrated-<stamp>
        so that nothing reopens it by accident, and left for manual removal once
        the operator has confirmed the new location works.

    Returns True if a migration was performed.
    """
    if os.path.exists(DB_PATH):
        return False
    if not os.path.exists(LEGACY_DB_PATH):
        return False

    ensure_dirs()
    staging = DB_PATH + ".migrating"
    if os.path.exists(staging):
        os.remove(staging)

    if verbose:
        print(f"[APP_PATHS] Migrating database\n"
              f"            from: {LEGACY_DB_PATH}\n"
              f"              to: {DB_PATH}")

    source = sqlite3.connect(LEGACY_DB_PATH, timeout=30.0)
    try:
        # Fold the WAL back into the main file so the snapshot is self-contained.
        try:
            source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            if verbose:
                print(f"[APP_PATHS] WAL checkpoint skipped ({exc}); continuing.")

        target = sqlite3.connect(staging)
        try:
            with target:
                source.backup(target)
            if not _quick_check(target):
                raise RuntimeError("integrity check failed on migrated copy")
        finally:
            target.close()
    except Exception:
        if os.path.exists(staging):
            os.remove(staging)
        raise
    finally:
        source.close()

    os.replace(staging, DB_PATH)

    # Retire the original rather than removing it.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    retired = f"{LEGACY_DB_PATH}.migrated-{stamp}"
    try:
        os.replace(LEGACY_DB_PATH, retired)
        for suffix in _SIDECAR_SUFFIXES:
            sidecar = LEGACY_DB_PATH + suffix
            if os.path.exists(sidecar):
                os.replace(sidecar, retired + suffix)
        if verbose:
            print(f"[APP_PATHS] Original retained as: {os.path.basename(retired)}\n"
                  f"            Delete it manually once the new location is confirmed.")
    except OSError as exc:
        if verbose:
            print(f"[APP_PATHS] Could not rename original ({exc}). "
                  f"New database is live; old file is now unused.")

    return True


def describe() -> str:
    """Human-readable summary for startup logs and diagnostics."""
    exists = "present" if os.path.exists(DB_PATH) else "absent"
    return (f"db={DB_PATH} ({exists})\n"
            f"backups={BACKUP_DIR}\n"
            f"app_root={APP_ROOT}")


def _looks_populated(path: str) -> bool:
    """True if `path` is a database that actually holds account data.

    Guards against binding to a stub. A crashed or half-initialised process can
    leave behind a schema-only database of a few kilobytes; treating that as
    'the data' is how you turn a recoverable situation into a real loss.
    """
    if not os.path.exists(path) or os.path.getsize(path) < 4096:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not row or not row[0]:
            return False
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def migration_required() -> bool:
    """A populated legacy database exists and has not yet been migrated."""
    return not os.path.exists(DB_PATH) and _looks_populated(LEGACY_DB_PATH)


# ── Path selection ────────────────────────────────────────────────────────────
#
# IMPORTANT: this module performs NO file I/O beyond directory creation at import
# time, and it NEVER migrates on import.
#
# An earlier revision called migrate_legacy_database() at module scope. main.py:953
# runs uvicorn with reload=True, so a single source edit re-imported this module
# and executed a live database relocation underneath a running server. The data
# survived (the online-backup path is sound) but a stale worker still holding the
# old path immediately recreated an empty stub at the vacated location. Relocating
# storage is an offline maintenance operation and is now gated behind an explicit
# call:
#
#     python app_paths.py --migrate      (with the app stopped)
#
# Until that runs, an un-migrated install deliberately keeps using its existing
# database. Continuing on real data and complaining loudly beats silently
# initialising a blank one.
if migration_required():
    print("=" * 72, file=sys.stderr)
    print("[APP_PATHS] Database has NOT been migrated out of the project root.", file=sys.stderr)
    print(f"[APP_PATHS]   in use: {LEGACY_DB_PATH}", file=sys.stderr)
    print(f"[APP_PATHS]   target: {DB_PATH}", file=sys.stderr)
    print("[APP_PATHS] It remains inside the filesystem-MCP writable root and", file=sys.stderr)
    print("[APP_PATHS] inside OneDrive sync. Stop the app and run:", file=sys.stderr)
    print("[APP_PATHS]     python app_paths.py --migrate", file=sys.stderr)
    print("=" * 72, file=sys.stderr)
    DB_PATH = LEGACY_DB_PATH
    BACKUP_DIR = os.path.join(APP_ROOT, "backups")

ensure_dirs()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="PersonaApp storage location maintenance. Run with the app STOPPED.")
    parser.add_argument("--migrate", action="store_true",
                        help="relocate a project-root database into the data directory")
    parser.add_argument("--status", action="store_true", help="show resolved paths")
    opts = parser.parse_args()

    if opts.migrate:
        if migrate_legacy_database():
            print("[APP_PATHS] Migration complete.")
        else:
            print("[APP_PATHS] Nothing to migrate.")
        print(describe())
    else:
        print(describe())
        print("migration_required:", migration_required())
