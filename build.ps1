.venv\Scripts\python -m pip install pyinstaller
# Tesseract goes inside the app, so nobody has to install it (needs it installed here + VS Build Tools for dumpbin)
.venv\Scripts\python tools\collect_tesseract.py
# Combined app: bot window + HUD overlay + tray
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name MiscritsBot --add-data "vendor\tesseract;tesseract" run_app.py
# Same app as a folder (zip it for the release) — antiviruses flag it less often than a single self-extracting exe
.venv\Scripts\pyinstaller --noconfirm --clean --onedir --windowed --name MiscritsBot --add-data "vendor\tesseract;tesseract" --distpath dist_dir --workpath build_dir run_app.py
# HUD only, as before
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name MiscritsHUD run_hud.py
