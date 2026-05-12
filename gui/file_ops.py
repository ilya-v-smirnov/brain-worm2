from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tkinter import messagebox


def _open_path(path: Path) -> None:
    """
    Открывает файл или папку через дефолтное системное приложение.
    Кросс-платформенно: Windows / macOS / Linux.
    """
    if not path.exists():
        messagebox.showerror("Open error", f"Path not found:\n{path}")
        return

    try:
        if sys.platform.startswith("win"):
            # Windows
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            # macOS
            subprocess.Popen(["open", str(path)])
        else:
            # Linux / other Unix
            subprocess.Popen(["xdg-open", str(path)])
    except FileNotFoundError as e:
        # На Linux может отсутствовать xdg-utils
        messagebox.showerror(
            "Open error",
            f"System opener not found:\n{type(e).__name__}: {e}\n\n"
            "On Linux, install the 'xdg-utils' package.",
        )
    except Exception as e:
        messagebox.showerror("Open error", f"{type(e).__name__}: {e}")


def open_file(path: Path) -> None:
    """Открывает файл системным viewer-ом."""
    _open_path(Path(path))


def open_folder(path: Path) -> None:
    """Открывает папку в системном файловом менеджере."""
    _open_path(Path(path))
