.venv\Scripts\python -m pip install pyinstaller
# Combined app: bot window + HUD overlay + tray
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name MiscritsBot run_app.py
# HUD only, as before
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name MiscritsHUD run_hud.py
