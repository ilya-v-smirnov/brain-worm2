from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from tkinter import messagebox

@dataclass(frozen=True)
class SummaryGenerationOptions:
    model: str
    language: str  # "EN" | "RU"


class SummaryGenerationDialog:
    """Modal dialog to collect summary generation options."""

    MODELS = [
        "ChatGPT-4.1",
        "ChatGPT-4.0-mini",
        "ChatGPT-5.0",
        "ChatGPT-5.0-mini",
        "ChatGPT-5.1",
        "ChatGPT-5.1-mini",
        "ChatGPT-5.2",
        "ChatGPT-5.2-mini",
    ]
    LANGUAGES = ["EN", "RU"]

    def __init__(self, parent: tk.Misc, *, default_model: str = "ChatGPT-5.2", default_language: str = "EN"):
        self._parent = parent
        self._result: SummaryGenerationOptions | None = None

        self._win = tk.Toplevel(parent)
        self._win.title("Summary Generation")
        self._win.resizable(False, False)
        self._win.transient(parent)

        self._model_var = tk.StringVar(value=default_model if default_model in self.MODELS else self.MODELS[-2])
        self._lang_var = tk.StringVar(value=default_language if default_language in self.LANGUAGES else "EN")

        self._build_ui()
        self._bind_keys()

        # Modal behavior
        self._win.grab_set()
        self._win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._center_over_parent()

    def show(self) -> SummaryGenerationOptions | None:
        """Show modally and return options or None if cancelled."""
        self._win.wait_window()
        return self._result

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = self._win

        content = ttk.Frame(root, padding=12)
        content.grid(row=0, column=0, sticky="nsew")

        content.columnconfigure(1, weight=1)

        ttk.Label(content, text="Model:").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8))
        model_box = ttk.Combobox(
            content,
            textvariable=self._model_var,
            values=self.MODELS,
            state="readonly",
            width=28,
        )
        model_box.grid(row=0, column=1, sticky="ew", pady=(0, 8))

        ttk.Label(content, text="Language:").grid(row=1, column=0, sticky="w", padx=(0, 10))
        lang_box = ttk.Combobox(
            content,
            textvariable=self._lang_var,
            values=self.LANGUAGES,
            state="readonly",
            width=10,
        )
        lang_box.grid(row=1, column=1, sticky="w")

        # Buttons bottom-right
        btns = ttk.Frame(content)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(14, 0))

        cancel_btn = ttk.Button(btns, text="Cancel", command=self._on_cancel)
        cancel_btn.grid(row=0, column=0, padx=(0, 8))

        generate_btn = ttk.Button(btns, text="Generate", command=self._on_generate)
        generate_btn.grid(row=0, column=1)

        # Focus on model combobox
        model_box.focus_set()

    def _bind_keys(self) -> None:
        self._win.bind("<Escape>", lambda _e: self._on_cancel())
        self._win.bind("<Return>", lambda _e: self._on_generate())

    def _center_over_parent(self) -> None:
        self._win.update_idletasks()
        try:
            px = self._parent.winfo_rootx()
            py = self._parent.winfo_rooty()
            pw = self._parent.winfo_width()
            ph = self._parent.winfo_height()
        except Exception:
            # fallback: let WM place it
            return

        ww = self._win.winfo_width()
        wh = self._win.winfo_height()
        x = px + max(0, (pw - ww) // 2)
        y = py + max(0, (ph - wh) // 2)
        self._win.geometry(f"+{x}+{y}")

    # ---------------- Actions ----------------

    def _on_cancel(self) -> None:
        self._result = None
        self._win.destroy()

    def _on_generate(self) -> None:
        self._result = SummaryGenerationOptions(
            model=self._model_var.get().strip(),
            language=self._lang_var.get().strip(),
        )
        self._win.destroy()
