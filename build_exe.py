"""Build a standalone Windows executable with PyInstaller."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

import main


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
ICON_PATH = BUILD_DIR / "write-or-die.ico"


def make_windows_icon() -> Path:
    BUILD_DIR.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication(["build_exe"])
    icon = main.make_icon()
    pix = icon.pixmap(256, 256)
    if pix.isNull():
        raise RuntimeError("Failed to render application icon")
    if not pix.save(str(ICON_PATH), "ICO"):
        raise RuntimeError(f"Failed to save icon: {ICON_PATH}")
    app.quit()
    return ICON_PATH


def build() -> None:
    icon_path = make_windows_icon()
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        "WriteOrDie",
        "--icon",
        str(icon_path),
        str(ROOT / "main.py"),
    ]
    subprocess.run(cmd, cwd=ROOT, check=True)
    exe = DIST_DIR / "WriteOrDie.exe"
    if not exe.exists():
        raise RuntimeError(f"Build finished but executable was not found: {exe}")
    print(exe)


if __name__ == "__main__":
    build()
