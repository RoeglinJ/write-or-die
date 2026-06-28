"""Build platform-specific Write or Die desktop artifacts with PyInstaller.

PyInstaller does not cross-compile. Run this script on the target OS:
Windows for .exe, macOS for .app, and Debian-based Linux for the Linux binary
and optional .deb package.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

import main


ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
APP_NAME = "WriteOrDie"
DISPLAY_NAME = "Write or Die"
SVG_ICON_PATH = ROOT / "assets" / "write-or-die-icon.svg"


def ensure_app() -> QApplication:
    return QApplication.instance() or QApplication(["build_app"])


def render_icon_png(size: int, path: Path) -> Path:
    ensure_app()
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = main.make_icon().pixmap(size, size)
    if pix.isNull():
        raise RuntimeError("Failed to render application icon")
    if not pix.save(str(path), "PNG"):
        raise RuntimeError(f"Failed to save icon: {path}")
    return path


def make_windows_icon() -> Path:
    ensure_app()
    path = BUILD_DIR / "write-or-die.ico"
    path.parent.mkdir(exist_ok=True)
    pix = main.make_icon().pixmap(256, 256)
    if pix.isNull():
        raise RuntimeError("Failed to render application icon")
    if not pix.save(str(path), "ICO"):
        raise RuntimeError(f"Failed to save icon: {path}")
    return path


def make_macos_icon() -> Path:
    iconset = BUILD_DIR / "write-or-die.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        render_icon_png(size, iconset / f"icon_{size}x{size}.png")
    icns = BUILD_DIR / "write-or-die.icns"
    iconutil = shutil.which("iconutil")
    if not iconutil:
        raise RuntimeError("macOS iconutil is required to create .icns icons")
    subprocess.run(
        [iconutil, "-c", "icns", str(iconset), "-o", str(icns)],
        cwd=ROOT,
        check=True,
    )
    return icns


def add_data_arg() -> str:
    return f"{SVG_ICON_PATH}{os.pathsep}assets"


def run_pyinstaller(args: list[str]) -> None:
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", *args]
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_windows() -> Path:
    icon_path = make_windows_icon()
    run_pyinstaller(
        [
            "--onefile",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(icon_path),
            "--add-data",
            add_data_arg(),
            str(ROOT / "main.py"),
        ]
    )
    return DIST_DIR / f"{APP_NAME}.exe"


def build_macos() -> Path:
    icon_path = make_macos_icon()
    run_pyinstaller(
        [
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(icon_path),
            "--add-data",
            add_data_arg(),
            str(ROOT / "main.py"),
        ]
    )
    return DIST_DIR / f"{APP_NAME}.app"


def build_linux() -> Path:
    icon_path = render_icon_png(256, BUILD_DIR / "write-or-die.png")
    run_pyinstaller(
        [
            "--onefile",
            "--windowed",
            "--name",
            APP_NAME,
            "--icon",
            str(icon_path),
            "--add-data",
            add_data_arg(),
            str(ROOT / "main.py"),
        ]
    )
    exe = DIST_DIR / APP_NAME
    build_deb(exe, icon_path)
    return exe


def build_deb(exe: Path, icon_path: Path) -> Path | None:
    dpkg_deb = shutil.which("dpkg-deb")
    if not dpkg_deb:
        print("dpkg-deb not found; skipping .deb package")
        return None
    with tempfile.TemporaryDirectory(prefix="write-or-die-deb-") as staging_tmp:
        package_root = Path(staging_tmp) / "write-or-die"
        (package_root / "DEBIAN").mkdir(parents=True)
        (package_root / "usr" / "bin").mkdir(parents=True)
        (package_root / "usr" / "share" / "applications").mkdir(parents=True)
        icon_dir = package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        icon_dir.mkdir(parents=True)

        bin_path = package_root / "usr" / "bin" / "write-or-die"
        icon_dest = icon_dir / "write-or-die.png"
        shutil.copy2(exe, bin_path)
        shutil.copy2(icon_path, icon_dest)
        bin_path.chmod(0o755)
        icon_dest.chmod(0o644)
        (package_root / "DEBIAN" / "control").write_text(
            "\n".join(
                [
                    "Package: write-or-die",
                    "Version: 0.1.1",
                    "Section: editors",
                    "Priority: optional",
                    "Architecture: amd64",
                    "Maintainer: Write or Die <noreply@example.invalid>",
                    "Description: Dangerous-writing Markdown editor",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (package_root / "usr" / "share" / "applications" / "write-or-die.desktop").write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    f"Name={DISPLAY_NAME}",
                    "Exec=write-or-die",
                    "Icon=write-or-die",
                    "Categories=Utility;TextEditor;",
                    "Terminal=false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        deb = DIST_DIR / "write-or-die_0.1.1_amd64.deb"
        DIST_DIR.mkdir(exist_ok=True)
        subprocess.run(
            [dpkg_deb, "--root-owner-group", "--build", str(package_root), str(deb)],
            check=True,
        )
    print(deb)
    return deb


def current_target() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    raise RuntimeError(f"Unsupported platform: {platform.system()}")


def build(target: str | None = None) -> Path:
    target = target or current_target()
    if target == "windows":
        artifact = build_windows()
    elif target == "macos":
        artifact = build_macos()
    elif target == "linux":
        artifact = build_linux()
    else:
        raise ValueError(f"Unknown target: {target}")
    if not artifact.exists():
        raise RuntimeError(f"Build finished but artifact was not found: {artifact}")
    print(artifact)
    return artifact


def main_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["windows", "macos", "linux"],
        default=None,
        help="Target platform. Defaults to the current OS.",
    )
    args = parser.parse_args()
    build(args.target)


if __name__ == "__main__":
    main_cli()

