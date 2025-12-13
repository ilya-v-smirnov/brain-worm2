from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from gui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    root.title("SciPDF Manager")
    try:
        MainWindow(root)
    except Exception as e:
        messagebox.showerror("Startup error", f"{type(e).__name__}: {e}")
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
