"""Nuitka build script for ViewIndexTTS.

Usage:
    python build.py               # dev build (standalone folder)
    python build.py --onefile     # single exe (slower, easier to distribute)
"""

import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import flet_desktop
import flet_desktop.version

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--enable-plugin=tk-inter",
    "--include-package=gui,httpx,playsound3,dotenv,flet,flet_desktop",
    "--include-package-data=flet",
    "--include-data-dir=gui=gui",
    "--include-data-dir=flet_client=flet_client",
    "--output-dir=dist",
    "--output-filename=ViewIndexTTS",
    "--assume-yes-for-downloads",
]

FLET_ARTIFACTS: dict[str, str] = {
    "win32": "flet-windows.zip",
    "darwin": "flet-macos.tar.gz",
    "linux": "flet-linux-ubuntu24.04-amd64.tar.gz",
}

FLET_CLIENT_DIR = Path("flet_client")


def _ensure_flet_client() -> None:
    """Download and extract Flet Flutter engine to flet_client/ for bundling."""
    artifact = FLET_ARTIFACTS.get(sys.platform)
    if artifact is None:
        print(f"[warn] Unknown platform {sys.platform}, skipping")
        return

    if FLET_CLIENT_DIR.exists() and any(FLET_CLIENT_DIR.iterdir()):
        print(f"[ok] Flet client already extracted: {FLET_CLIENT_DIR}")
        return

    ver = flet_desktop.version.version
    url = f"https://github.com/flet-dev/flet/releases/download/v{ver}/{artifact}"
    print(f"Downloading Flet client v{ver}: {artifact} ...")

    archive_path = FLET_CLIENT_DIR.with_name(f"flet_tmp_{artifact}")
    urllib.request.urlretrieve(url, str(archive_path))

    FLET_CLIENT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Extracting to {FLET_CLIENT_DIR}/ ...")

    if artifact.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(FLET_CLIENT_DIR)
    else:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(FLET_CLIENT_DIR)

    archive_path.unlink()
    print(f"[ok] Extracted {len(list(FLET_CLIENT_DIR.rglob('*')))} files")


def main():
    _ensure_flet_client()

    args = list(NUITKA_ARGS)
    if sys.platform == "win32":
        args.insert(args.index("--assume-yes-for-downloads"), "--windows-console-mode=disable")
    if sys.platform == "darwin":
        args.insert(args.index("--assume-yes-for-downloads"), "--macos-create-app-bundle")
    if "--onefile" in sys.argv:
        args.insert(args.index("--standalone") + 1, "--onefile")
    args.append("main.py")
    subprocess.run(args, check=True)


if __name__ == "__main__":
    main()
