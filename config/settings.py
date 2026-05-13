"""
Кросс-платформенный загрузчик настроек приложения.

Логика поиска settings.json:

  Dev-режим (запущен как python-скрипт):
    <project_root>/config/settings.json

  Frozen-режим (собранный PyInstaller exe):
    1) <exe_dir>/settings.json                       (рядом с .exe)
    2) <appdata>/BrainWorm/settings.json             (Windows: %APPDATA%\\BrainWorm,
                                                      macOS:  ~/Library/Application Support/BrainWorm,
                                                      Linux:  ~/.config/BrainWorm)

Первый существующий файл побеждает.

Если ни одного файла нет — функции возвращают None (для find_settings_path)
или бросают FileNotFoundError (для load_settings). Точка входа приложения
ловит это и запускает first-run wizard, который сохраняет settings.json
в дефолтный для платформы путь через save_settings().
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


APP_NAME = "BrainWorm"


# ---------- Определение режима запуска ----------


def is_frozen() -> bool:
    """True если приложение запущено как PyInstaller-собранный exe."""
    return bool(getattr(sys, "frozen", False))


# ---------- Базовые каталоги ----------


def _project_root() -> Path:
    """
    Каталог проекта — для dev-режима.

    .../brain-worm2/config/settings.py -> .../brain-worm2
    """
    return Path(__file__).resolve().parents[1]


def _exe_dir() -> Path:
    """Каталог, где лежит исполняемый файл (для frozen-режима)."""
    return Path(sys.executable).resolve().parent


def _appdata_dir() -> Path:
    """
    Кросс-платформенный каталог для пользовательских настроек.

    Windows: %APPDATA%\\BrainWorm
    macOS:   ~/Library/Application Support/BrainWorm
    Linux:   $XDG_CONFIG_HOME/BrainWorm или ~/.config/BrainWorm
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    # Linux / other Unix
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / APP_NAME


# ---------- Публичный API ----------


def get_settings_search_paths() -> list[Path]:
    """
    Возвращает упорядоченный список кандидатных путей, по которым будет
    производиться поиск settings.json. Первый существующий побеждает.
    """
    paths: list[Path] = []

    if is_frozen():
        paths.append(_exe_dir() / "settings.json")
        paths.append(_appdata_dir() / "settings.json")
    else:
        paths.append(_project_root() / "config" / "settings.json")

    return paths


def find_settings_path() -> Optional[Path]:
    """
    Возвращает путь к существующему settings.json или None, если не найден.
    """
    for p in get_settings_search_paths():
        if p.is_file():
            return p
    return None


def get_default_settings_save_path() -> Path:
    """
    Путь, куда first-run wizard сохранит свежий settings.json.

    Frozen-режим — %APPDATA%/BrainWorm/settings.json (или эквивалент).
    Dev-режим   — <project_root>/config/settings.json.
    """
    if is_frozen():
        return _appdata_dir() / "settings.json"
    return _project_root() / "config" / "settings.json"


def load_settings() -> Dict[str, Any]:
    """
    Загружает settings.json. Бросает FileNotFoundError, если файл не найден
    ни по одному из кандидатных путей.
    """
    path = find_settings_path()
    if path is None:
        searched = "\n".join(f"  - {p}" for p in get_settings_search_paths())
        raise FileNotFoundError(
            "Settings file not found. Expected at one of:\n" + searched
        )

    return json.loads(path.read_text(encoding="utf-8"))


def save_settings(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """
    Сохраняет settings.json в указанный путь (или в дефолтный, если не указан).
    Родительские каталоги создаются автоматически.

    Возвращает фактический путь, в который записан файл.
    """
    target = path or get_default_settings_save_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
