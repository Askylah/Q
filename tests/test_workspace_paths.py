"""
Workspace path handling. Covers lab_notes/entropic_gap_livelock.md 24, 34 and 51.

Standalone, like the rest of tests/ -- no pytest:

    python tests/test_workspace_paths.py

Exits non-zero on any failure. Safe to run against a live app: it resolves paths
and asserts on exceptions. It never writes, deletes or mutates anything outside
one temporary directory it creates and removes itself.

═══════════════════════════════════════════════════════════════════════════════
READ THIS BEFORE "FIXING" THE WORKSPACE ENDPOINTS.

/workspace/save, /create and /delete each build their SafeWorkspace root out of
the requested path, which means those endpoints reach the whole filesystem. That
is DELIBERATE and it is the product decision, recorded in 51.1: this workspace is
a general-purpose file manager, held to the same reach as any agent CLI operating
on a directory the user points it at. Narrowing it to the application directory
was tried in Session 8 and reverted the same session, because it made the tool
less capable than the thing it exists to be.

So the "jail" is not a security boundary and must not be described as one. What
IS worth protecting is SafeWorkspace._resolve itself: once a root IS chosen, it
has to hold. Section [1] is that contract. Section [3] pins the endpoint decision
so a future session cannot re-narrow it by accident.
═══════════════════════════════════════════════════════════════════════════════
"""
import os, sys, ast, shutil, tempfile, pathlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAILS.append(name)


def check_true(name, got):
    check(name, bool(got), True)


def raises_violation(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except SecurityViolation:
        return True
    except Exception:
        return False


from workspace_engine import SafeWorkspace, SecurityViolation


# -- 1. given a root, _resolve holds ---------------------------------------
# This is the part that has to be right. It is also the only part that is a
# security property at all.
print("\n[1] SafeWorkspace._resolve, given an explicitly chosen root")
box = tempfile.mkdtemp(prefix="wsroot_")
try:
    ws = SafeWorkspace(box)
    box_real = pathlib.Path(box).resolve()

    check("a relative path inside the root resolves",
          ws._resolve("notes.txt"), box_real / "notes.txt")
    check("a nested relative path resolves",
          ws._resolve("a/b/c.txt"), box_real / "a" / "b" / "c.txt")
    check("an ABSOLUTE path inside the root resolves -- get_file_tree hands the "
          "UI absolute paths, so this must keep working",
          ws._resolve(str(box_real / "notes.txt")), box_real / "notes.txt")

    for label, probe in [
        ("../ traversal", "../escaped.txt"),
        ("deep traversal", "../../../../etc/passwd"),
        ("absolute outside the root", os.path.join(os.path.expanduser("~"), "x.txt")),
        ("sibling-prefix escape -- the FIX(prefix-escape) case",
         str(box_real) + "-evil/secrets.txt"),
    ]:
        check(f"refused: {label}", raises_violation(ws._resolve, probe), True)

    print("\n[2] delete_item resolves BEFORE it can reach shutil.rmtree")
    check("_resolve runs outside the try, so a violation propagates rather than "
          "being swallowed into {'success': False}",
          raises_violation(ws.delete_item, "../../nope"), True)
finally:
    shutil.rmtree(box, ignore_errors=True)
    check("temp root cleaned up", os.path.exists(box), False)


# -- 3. the endpoint reach is a DECISION, not an oversight -----------------
print("\n[3] the workspace endpoints are deliberately unrestricted (51.1)")
_main = open(os.path.join(ROOT, "main.py"), "rb").read().decode("utf-8")
_tree = ast.parse(_main)
WANT = {"save_workspace_file", "create_workspace_item", "delete_workspace_item"}
for fn in ast.walk(_tree):
    if isinstance(fn, ast.FunctionDef) and fn.name in WANT:
        dumped = ast.dump(fn)
        check_true(f"{fn.name} still takes its root from the request -- if this "
                   f"fails, someone narrowed the file manager; read 51.1 before "
                   f"deciding that was a fix",
                   "dirname" in dumped and "SafeWorkspace" in dumped)
        check_true(f"{fn.name} does not silently jail to the app directory",
                   "_APP_ROOT" not in dumped)
        WANT.discard(fn.name)
check("all three endpoints were found and checked", WANT, set())

check_true("_APP_ROOT still exists for the staging endpoints that do want it",
           "_APP_ROOT" in _main)


# -- 4. the one consequence of a per-request root that is worth knowing -----
print("\n[4] documented consequence: constructing a SafeWorkspace creates its root")
tmp = tempfile.mkdtemp(prefix="wsmkdir_")
try:
    ghost = os.path.join(tmp, "made", "by", "construction")
    check("precondition: it does not exist", os.path.isdir(ghost), False)
    SafeWorkspace(ghost)
    check("SafeWorkspace.__init__ mkdirs its root", os.path.isdir(ghost), True)
    print("       ^ with a per-request root this means a save/create/delete for a")
    print("         path that does not exist creates its parent chain first, even")
    print("         if the request then fails. Known, recorded in 51.1, not a")
    print("         reason to narrow the reach.")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    check("temp dir cleaned up", os.path.exists(tmp), False)

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILURE(S): {FAILS}"))
sys.exit(1 if FAILS else 0)
