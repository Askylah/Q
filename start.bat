@echo off
echo [*] PersonaApp Launcher
echo [*] Starting Streamlit...
cd /d "%~dp0"
python -m streamlit run app.py --server.port 8501
pause
