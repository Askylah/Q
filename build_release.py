import os
import shutil
import subprocess
import sys

def build_release():
    print("========================================================")
    print("                 Q - ZERO-FRICTION BUILD                ")
    print("========================================================")
    
    root_dir = os.path.abspath(os.path.dirname(__file__))
    vite_dir = os.path.join(root_dir, "vite-project")
    release_dir = os.path.join(root_dir, "dist_installer")
    
    # 1. Compile React Frontend
    print("\n[1/5] Compiling React Frontend (npm run build)...")
    try:
        subprocess.run(["npm", "install"], cwd=vite_dir, check=True, shell=True)
        subprocess.run(["npm", "run", "build"], cwd=vite_dir, check=True, shell=True)
    except Exception as e:
        print("\n[ERROR] Failed to compile frontend. Make sure Node.js is installed on your dev machine.")
        print(e)
        return

    # 2. Setup Release Directory
    print(f"\n[2/5] Preparing Release Directory: {release_dir}")
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir)
    os.makedirs(release_dir)
    
    # 3. Copy Backend & Core Files
    print("\n[3/5] Copying Core Engine Files...")
    core_files = [
        "main.py", "llm_engine.py", "workspace_engine.py", "secure_runner.py", 
        "firewall.py", "database.py", "rag_engine.py", "mode_engine.py", 
        "memory_engine.py", "observational_memory.py", "zettel_engine.py", 
        "on_demand_loader.py", "plugin_manager.py", "mcp_client.py", 
        "mcp_server.py", "skill_orchestrator.py", "governance_manager.py",
        "ingest.py", "output_validator.py", "inversion_engine.py", "redis_client.py",
        "alignment_engine.py"
    ]
    for f in core_files:
        src = os.path.join(root_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(release_dir, f))
            
    # Configs & Docs
    for f in ["requirements.txt", "Dockerfile.padded_room"]:
        src = os.path.join(root_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(release_dir, f))
            
    # Ship a clean persona slate (protects developer IP like Rick)
    example_personas = os.path.join(root_dir, "personas.json.example")
    if os.path.exists(example_personas):
        shutil.copy2(example_personas, os.path.join(release_dir, "personas.json"))
    else:
        with open(os.path.join(release_dir, "personas.json"), "w") as f:
            f.write("{}")
            
    # 4. Copy Core Directories
    print("\n[4/6] Copying Core Directories...")
    core_dirs = ["plugins", "skills", "knowledge_bases", "personas", "uploads", "labs"]
    for d in core_dirs:
        src = os.path.join(root_dir, d)
        dest = os.path.join(release_dir, d)
        if os.path.exists(src):
            # Automatically filter out Rick's proprietary files from being distributed
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*rick*", "*Rick*"))
        else:
            os.makedirs(dest)
            
    # 5. Copy Frontend Build
    print("\n[5/6] Injecting Compiled Frontend into Unified Server...")
    os.makedirs(os.path.join(release_dir, "vite-project"), exist_ok=True)
    shutil.copytree(os.path.join(vite_dir, "dist"), os.path.join(release_dir, "vite-project", "dist"))
    
    # 5. Generate start.bat Bootstrapper
    print("\n[5/5] Forging Bootstrapper (start.bat)...")
    bat_content = """@echo off
title Q Server
color 0b

echo ========================================================
echo                 Q - IGNITION SEQUENCE
echo ========================================================
echo.

echo [1/3] Checking Python Environment...
if not exist ".venv" (
    echo Creating isolated virtual environment...
    py -m venv .venv
)

echo Activating environment and verifying dependencies...
call .venv\\Scripts\\activate
py -m pip install -r requirements.txt -q

echo.
echo [2/3] Verifying Docker Security Sandbox...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    color 0c
    echo.
    echo [CRITICAL ERROR] Docker Desktop is not running or not installed!
    echo The security sandbox requires Docker. Please install Docker Desktop:
    echo https://www.docker.com/products/docker-desktop/
    echo Install it, start it, and run this file again.
    pause
    exit /b
)

echo Rebuilding Padded Room image (if necessary)...
docker build -t padded_room -f Dockerfile.padded_room . -q

echo.
echo [3/3] Igniting Unified Server...
echo The app will open in your browser automatically.
start http://127.0.0.1:8000
py -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
"""
    with open(os.path.join(release_dir, "start.bat"), "w") as f:
        f.write(bat_content)

    print("\n[6/6] Forging Linux/Mac Bootstrapper (start.sh)...")
    sh_content = """#!/bin/bash
echo "========================================================"
echo "                 Q - IGNITION SEQUENCE                  "
echo "========================================================"
echo ""

echo "[1/3] Checking Python Environment..."
if [ ! -d ".venv" ]; then
    echo "Creating isolated virtual environment..."
    python3 -m venv .venv
fi

echo "Activating environment and verifying dependencies..."
source .venv/bin/activate
python3 -m pip install -r requirements.txt -q

echo ""
echo "[2/3] Verifying Docker Security Sandbox..."
if ! command -v docker &> /dev/null; then
    echo -e "\\033[31m[CRITICAL ERROR] Docker is not running or not installed!\\033[0m"
    echo "The security sandbox requires Docker. Please install Docker:"
    echo "https://docs.docker.com/get-docker/"
    echo "Install it, start it, and run this script again."
    exit 1
fi

echo "Rebuilding Padded Room image (if necessary)..."
docker build -t padded_room -f Dockerfile.padded_room . -q

echo ""
echo "[3/3] Igniting Unified Server..."
echo "The app will open in your browser automatically."

# Cross-platform open browser
if which xdg-open > /dev/null; then
  xdg-open http://127.0.0.1:8000 &
elif which open > /dev/null; then
  open http://127.0.0.1:8000 &
fi

python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
"""
    sh_path = os.path.join(release_dir, "start.sh")
    with open(sh_path, "w", newline='\n') as f:
        f.write(sh_content)
    
    # Try to make the shell script executable if we are on a unix-like system
    try:
        os.chmod(sh_path, 0o755)
    except Exception:
        pass
        
    print("\n[7/7] Compressing release into Q_Installer.zip...")
    zip_path = os.path.join(root_dir, "Q_Installer")
    shutil.make_archive(zip_path, 'zip', release_dir)
        
    print("\n=== SUCCESS ===")
    print(f"The distributable 'One-Click' build is ready in: {release_dir}")
    print(f"A compressed version is ready for GitHub Releases: {zip_path}.zip")
    print("Upload the .zip file to your GitHub Releases tab for users to download!")

if __name__ == "__main__":
    build_release()
