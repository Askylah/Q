# Storage Hardening — Lab Notes

## Status: APPLIED (2026-08-28), pending post-restart cleanup

Origin: audit of whether the model could destroy the zettel graph. The zettel API
turned out to be safe (append-only, no delete tool reachable from tool-space). The
actual exposure was one level down — `users.db` sat inside the filesystem-MCP
writable root with a six-week-old backup.

### What changed

| # | Change | Location |
|---|---|---|
| 1 | Protected-path deny-list (Layer 4b) | `output_validator.py` |
| 2 | Single source of truth for storage paths | `app_paths.py` (new) |
| 3 | `DB_PATH` now imported, not recomputed | `database.py:~10`, `memory_engine.py:~41` |
| 4 | `users.db` relocated to `%LOCALAPPDATA%\PersonaApp\` | via `app_paths.py --migrate` |
| 5 | Verified online-backup rotation | `database.py` `backup_database()` |
| 6 | Backup on startup, 6h floor | `main.py` `backup_database_on_start()` |

### Why each, briefly

1. **Deny-list, not re-root.** Re-rooting the MCP server was considered and
   REJECTED: `read_file_lines` (`llm_engine.py:1417`) and `grep_workspace`
   (`:1445`) bypass MCP and stay pinned to project root, so re-rooting creates
   split-brain (grep finds a path that `read_file` then refuses). The git server
   (`mcp_router.py:293`) shares the same root too. Source access is a *designed*
   capability — see `mcp_server.py:16`.
   The gate must match on **basename** and recurse into nested args, because
   `validate_tool_call` (`llm_engine.py:1315`) sits in front of TWO dispatch
   shapes: direct (`filesystem__write_file`, `args["path"]`) and the wrapper
   (`call_mcp_tool`, `args["arguments"]["path"]`, possibly a JSON *string* —
   normalised later at `:1325`). Basename matching also neutralises `../`
   traversal and the doubled-path bug at `:1330`.
   Verified 37/37 including every evasion above.

2/3. **`DB_PATH` was defined twice** (`database.py:10`, `memory_engine.py:41`),
   agreeing only because both files shared a directory. `database.py` installs a
   global `sqlite3.connect` wrapper keyed on **string equality** with its own
   `DB_PATH` (`:13-20`) — divergence would have silently stripped `timeout=30.0`
   from memory_engine's 11 connections, surfacing as random "database is locked".
   Now `database.DB_PATH is app_paths.DB_PATH` → True.

4. **Relocation** removes MCP reachability entirely (server-filesystem has an
   allow-list but no deny-list, and source files share the root, so the root
   cannot be granted without granting the DB). Bonus: gets a WAL-mode database
   out of OneDrive sync, which is an independent documented corruption vector.

5. **`conn.backup()`, never `shutil.copy2`.** WAL is on (`database.py:~31`). A byte
   copy can capture a main file and `-wal` that disagree — a backup that looks
   fine and fails only when needed. Every backup is `PRAGMA quick_check`ed before
   being named into place.

6. 6h floor because `main.py:953` runs uvicorn `reload=True`; startup hooks refire
   on every source edit.

### Incident (read this before touching `app_paths.py`)

The first revision called `migrate_legacy_database()` **at module scope**. Editing
`database.py` tripped the reloader, which imported `app_paths`, which relocated the
live database under a running server. Data survived intact (row parity verified:
2 users / 309 conversations / 209 memories / 555 zettels / 151 links / 468 obs) —
only because the migration used checkpoint + online-backup + integrity-check
rather than a byte copy. But a stale worker holding the old path recreated a 10KB
empty stub 7s later.

**Rule: no file I/O at import scope in this project.** Migration is now explicit:
`python app_paths.py --migrate`, app stopped. An un-migrated install keeps using
its real data and logs loudly rather than initialising a blank one.
`_looks_populated()` guards the fallback so it can never bind to a stub.

**Companion rule (found 2026-08-28): no durable policy state in process memory.**
Same amplifier, different surface. `stream_worker.py:33` holds a 24h cooldown
registry in a plain dict; because `stream_worker` is a grandchild of the reload
worker, every source edit resets the guard. Measured 30/45 cooldown violations.
See `lab_notes/entropic_gap_livelock.md` §3b.

### Open

1. **Cleanup unblocked — restart happened, not yet performed.** PID 32632 is gone;
   PID 6336 (`python main.py`, up 8/28 19:07:05) now owns port 8000. Verified
   post-edit: `migration_required: False`, `database.DB_PATH is
   app_paths.DB_PATH` → True (also `memory_engine`), fresh 7.58MB backup at
   19:00:43. The old-location `users.db` was last written 18:57:49 — nine minutes
   *before* the current process started — and is a **partial** schema
   (`deep_memories` only, 0 rows), confirming it is the dead worker's stub and
   nothing holds it. Still to delete: that stub, plus the four `-wal`/`-shm`
   sidecars beside `users.db.migrated-*` / `users.db.backup_2026071*`. (Correction
   to the earlier note: the `-shm` files are 32768 bytes, not 0; the `-wal` are 0.
   Parents pass `quick_check`.)
2. ~~KEEP `users.db.backup_20260713_220107`~~ — **RESOLVED, safe to retire.** It
   was a deliberate prune of daemon spam. Exact id-set diff: 4930 rows deleted, of
   which 4882 are `entropic_gap` in **three** content groups (33.6× duplication),
   48 are duplicate `user_message` re-submissions, and **0** lack a surviving
   identical twin. Zero unique content lost. Full analysis and the reason the
   table grew in the first place: `lab_notes/entropic_gap_livelock.md`.
3. **Retire `users.db.migrated-20260828_185742`** (7.23MB, pre-migration truth)
   after ~1 week of confirmed operation. Row-identical to LIVE across all 19
   tables, so it is pure redundancy once operation is confirmed.
4. `governance_manager` classifies by tool NAME and refuses to inspect arguments,
   so all argument-aware policy must live in `output_validator`. Keep it there.
5. Consider `AGENTS.md` excluding `node_modules/` and the bundled Monaco files
   (`swc.*.node` 24MB, `rolldown-binding.*.node` 21MB, `ts.worker-*.js`) — an
   unglobbed search dumps megabytes into context.
6. **Active thread → `lab_notes/entropic_gap_livelock.md`.** The audit that
   started here (can the model destroy the graph?) ended up finding an unbounded
   *write* loop rather than a delete risk: `observations` has no reaper of any
   kind, and the daemon emits ~288 gap rows/day rotating across a 293-node orphan
   pool. Diagnosed, **not fixed**. Also relevant here: 71% of `zettel_nodes` are
   orphans.

### Diagnostics hygiene

When inspecting a snapshot, open it read-only **and immutable**:

```python
sqlite3.connect("file:" + path + "?mode=ro&immutable=1", uri=True)
```

`mode=ro` alone still lets SQLite create `-wal`/`-shm` sidecars for a WAL
database — that is what littered this directory and produced open item #1.
`immutable=1` suppresses them.

Prefer **id-set diffs over row counts** when comparing snapshots. A net
`zettel_links 183 → 151` in the original note concealed 147 deletes plus 115
inserts, and hid a 196-node regeneration completely.

### Verification commands

```
python app_paths.py --status          # expect migration_required: False
python -c "import app_paths,database; print(database.DB_PATH is app_paths.DB_PATH)"
```

Expected startup log: `[SYSTEM] Storage: ...\PersonaApp\users.db`,
`[BACKUP] Skipped — newest backup is 0.Xh old`, governance validated,
and **no** `[APP_PATHS]` warning block.
