"""Nuitka build script for ATRI-IndexTTS-GUI.

Usage:
    python build.py               # dev build (standalone folder)
    python build.py --onefile     # single exe (slower, easier to distribute)
"""

import subprocess
import sys

NUITKA_ARGS = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--enable-plugin=tk-inter",
    "--include-package=gui,httpx,playsound3,dotenv",
    "--include-package-data=flet",
    "--include-data-dir=gui=gui",
    "--output-dir=dist",
    "--output-filename=ATRI-IndexTTS",
    "--assume-yes-for-downloads",
]

if sys.platform == "win32":
    NUITKA_ARGS.insert(NUITKA_ARGS.index("--assume-yes-for-downloads"), "--windows-console-mode=disable")
if sys.platform == "darwin":
    NUITKA_ARGS.insert(NUITKA_ARGS.index("--assume-yes-for-downloads"), "--macos-create-app-bundle")


def main():
    args = list(NUITKA_ARGS)
    if "--onefile" in sys.argv:
        args.insert(args.index("--standalone") + 1, "--onefile")
    args.append("main.py")
    subprocess.run(args, check=True)


if __name__ == "__main__":
    main()
