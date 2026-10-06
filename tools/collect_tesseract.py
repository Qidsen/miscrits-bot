"""Copies a minimal Tesseract (tesseract.exe, the DLLs it actually loads, English data) into vendor/tesseract,
so the bot works without a separate Tesseract install. Tesseract is Apache-2.0 — its licence goes along.

Usage: .venv\\Scripts\\python tools\\collect_tesseract.py ["C:\\Program Files\\Tesseract-OCR"]
Needs dumpbin (Visual Studio Build Tools) to follow DLL dependencies."""

import glob
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Program Files\Tesseract-OCR")
TARGET = Path(__file__).resolve().parent.parent / "vendor" / "tesseract"
DATA = ("eng.traineddata",)  # osd (поворот страницы) боту не нужен


def dumpbin() -> str:
    found = glob.glob(r"C:\Program Files*\Microsoft Visual Studio\*\*\VC\Tools\MSVC\*\bin\Hostx64\x64\dumpbin.exe")
    if not found:
        sys.exit("dumpbin не найден — поставьте Visual Studio Build Tools (C++)")
    return sorted(found)[-1]


def dependents(tool: str, binary: Path) -> list:
    out = subprocess.run([tool, "/dependents", str(binary)], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip().lower().endswith(".dll")]


def main():
    tool = dumpbin()
    needed, queue = set(), [SOURCE / "tesseract.exe"]
    while queue:
        for name in dependents(tool, queue.pop()):
            local = SOURCE / name
            if local.exists() and name.lower() not in needed:  # системные DLL (KERNEL32 и т.п.) не копируем
                needed.add(name.lower())
                queue.append(local)
    shutil.rmtree(TARGET, ignore_errors=True)
    (TARGET / "tessdata").mkdir(parents=True)
    shutil.copy2(SOURCE / "tesseract.exe", TARGET)
    for name in sorted(needed):
        shutil.copy2(SOURCE / name, TARGET)
    for name in DATA:
        shutil.copy2(SOURCE / "tessdata" / name, TARGET / "tessdata")
    for licence in ("LICENSE", "doc/LICENSE", "doc/AUTHORS"):
        if (SOURCE / licence).exists():
            shutil.copy2(SOURCE / licence, TARGET / Path(licence).name)
    size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file()) / 2**20
    print(f"{len(needed)} DLL, {size:.0f} MB -> {TARGET}")


if __name__ == "__main__":
    main()
