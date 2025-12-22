import json
import os
from pathlib import Path
from typing import Any, Dict


def _project_root() -> Path:
    # .../brain-worm2/config/settings.py -> .../brain-worm2
    return Path(__file__).resolve().parents[1]


def load_settings() -> Dict[str, Any]:
    """
    Loads settings from:
      1) <project_root>/config/settings.json
      2) <project_root>/config/settings_example.json (fallback)
    Additionally allows OPENAI_API_KEY env var to override.
    """
    root = _project_root()
    cfg_dir = root / "config"

    candidates = [
        cfg_dir / "settings.json",
        cfg_dir / "settings_example.json",
    ]

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            "Settings file not found. Create 'config/settings.json' "
            "(you can copy from 'config/settings_example.json')."
        )

    data = json.loads(path.read_text(encoding="utf-8"))

    # allow env override for key (useful for CI / local shells)
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        data["openai_api_key"] = env_key

    return data
