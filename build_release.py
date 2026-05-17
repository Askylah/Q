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
        "ingest.py", "output_validator.py"
    ]
    for f in core_files:
        src = os.path.join(root_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(release_dir, f))
            
    # Configs & Docs
    for f in ["requirements.txt", "Dockerfile.padded_room", "personas.json", "skills.json"]:
        src = os.path.join(root_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(release_dir, f))
            
    # 4. Copy Frontend Build
    print("\n[4/5] Injecting Compiled Frontend into Unified Server...")
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
    python -m venv .venv
)

echo Activating environment and verifying dependencies...
call .venv\\Scripts\\activate
python -m pip install -r requirements.txt -q

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
python -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
"""
    with open(os.path.join(release_dir, "start.bat"), "w") as f:
        f.write(bat_content)
        
    print("\n[6/5] Compressing release into Q_Installer.zip...")
    zip_path = os.path.join(root_dir, "Q_Installer")
    shutil.make_archive(zip_path, 'zip', release_dir)
        
    print("\n=== SUCCESS ===")
    print(f"The distributable 'One-Click' build is ready in: {release_dir}")
    print(f"A compressed version is ready for GitHub Releases: {zip_path}.zip")
    print("Upload the .zip file to your GitHub Releases tab for users to download!")

if __name__ == "__main__":
    build_release()
