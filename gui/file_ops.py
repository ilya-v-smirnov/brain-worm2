from __future__ import annotations

import subprocess
from pathlib import Path
from tkinter import messagebox


def xdg_open(path: Path) -> None:
    """Открыть файл/папку системным viewer'ом (Ubuntu: xdg-open)."""
    if not path.exists():
        messagebox.showerror("Open error", f"File not found:\n{path}")
        return
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror("Open error", f"{type(e).__name__}: {e}")


def open_file(path: Path) -> None:
    xdg_open(path)


def open_folder(path: Path) -> None:
    xdg_open(path)
