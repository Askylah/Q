import subprocess
import sys
import os

# Files modified in this session
MODIFIED_FILES = [
    "firewall.py",
    "workspace_engine.py",
    "main.py",
    "build_release.py",
    "tests/test_extraction_firewall.py",
    "docs/SECURITY_AND_SANDBOX.md"
]

def print_banner():
    banner = """
============================================================
                      Q - GIT PUSHER            
============================================================
    """
    print(banner)

def run_git(args):
    print(f"Executing: git {' '.join(args)}")
    res = subprocess.run(["git"] + args, capture_output=True, text=True)
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print("[ERROR/WARN]", res.stderr)
    return res.returncode

def main():
    print_banner()
    
    # 1. Show status first
    print("\n--- Current Git Status ---")
    run_git(["status", "-s"])
    
    # 2. Add modified files
    print("\n--- Staging Syntax Firewall Updates ---")
    for f in MODIFIED_FILES:
        if os.path.exists(f):
            run_git(["add", f])
        else:
            print(f"[SKIP] File not found: {f}")
            
    # 3. Commit
    commit_msg = "feat: deploy Zero-Friction deployment pipeline, unified architecture, and harden Layer A/B security envelopes"
    print(f"\n--- Committing Updates ---")
    run_git(["commit", "-m", commit_msg])
    
    # 4. Push
    print("\n--- Pushing to GitHub ---")
    code = run_git(["push"])
    
    if code == 0:
        print("\n[SUCCESS] Firewall deployed and pushed to GitHub! 🚀")
    else:
        print("\n[FAILURE] Git push failed. Please verify your internet connection or upstream credentials.")

if __name__ == "__main__":
    main()
