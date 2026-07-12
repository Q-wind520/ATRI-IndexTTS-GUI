"""Shared utilities and constants for ViewIndexTTS."""

import os
import sys
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────

EMOTION_DIMS: list[tuple[str, str]] = [
    ("Happy", "快乐"), ("Angry", "愤怒"), ("Sad", "悲伤"),
    ("Scared", "恐惧"), ("Disgusted", "厌恶"), ("Depressed", "抑郁"),
    ("Surprised", "惊讶"), ("Calm", "平静"),
]


# ── Path helpers ───────────────────────────────────────────────────

def get_config_dir() -> Path:
    """Return a user-writable config directory for ViewIndexTTS."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = base / "ViewIndexTTS"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_env_path() -> Path:
    """Return the path to the .env config file."""
    return get_config_dir() / ".env"


def get_default_output_dir() -> Path:
    """Return a user-writable default output directory for synthesized audio."""
    out_dir = get_config_dir() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir
