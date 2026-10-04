.venv\Scripts\python -m pip install pyinstaller
# РћР±С‰Р°СЏ РїСЂРѕРіСЂР°РјРјР°: РѕРєРЅРѕ Р±РѕС‚Р° + HUD + С‚СЂРµР№
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name MiscritsBot run_app.py
# РўРѕР»СЊРєРѕ HUD, РєР°Рє СЂР°РЅСЊС€Рµ
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name MiscritsHUD run_hud.py
