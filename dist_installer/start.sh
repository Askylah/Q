#!/bin/bash
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
    echo -e "\033[31m[CRITICAL ERROR] Docker is not running or not installed!\033[0m"
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
