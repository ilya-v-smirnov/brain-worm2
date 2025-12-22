from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from typing import Optional


# UI labels -> OpenAI model ids used by ai_summary.generator/openai_client
MODEL_LABEL_TO_ID = {
    "GPT-4.0": "gpt-4",
    "GPT-4.1": "gpt-4.1",
    "GPT-5.0": "gpt-5",
    "GPT-5.0-mini": "gpt-5-mini",
    "GPT-5.1": "gpt-5.1",
    "GPT-5.1-mini": "gpt-5-mini",
    "GPT-5.2": "gpt-5.2",
    "GPT-5.2-mini": "gpt-5-mini",
}
DEFAULT_MODEL_LABELS = list(MODEL_LABEL_TO_ID.keys())


@dataclass(frozen=True)
class SummaryGenerationOptions:
    model: str
    language: str  # "EN" | "RU"


class SummaryGenerationDialog:
    """Modal dialog to collect summary generation options."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        default_model: str = "ChatGPT-5.2",
        default_language: str = "EN",
        available_models: Optional[list[str]] = None,
    ) -> None:
        self._parent = parent
        self._result: Optional[SummaryGenerationOptions] = None

        self._win = tk.Toplevel(parent)
        self._win.title("Generate Summary")
        self._win.transient(parent)
        self._win.resizable(False, False)
        self._win.grab_set()

        self._model_var = tk.StringVar()
        self._lang_var = tk.StringVar(value=default_language)

        # --- UI ---
        root = ttk.Frame(self._win, padding=12)
        root.grid(row=0, column=0, sticky="nsew")

        MODELS = available_models or DEFAULT_MODEL_LABELS

        # Support legacy defaults like "ChatGPT-5.2"
        legacy_map = {
            "ChatGPT-5.2": "GPT-5.2",
            "ChatGPT-5.2-mini": "GPT-5.2-mini",
        }
        default_label = legacy_map.get(default_model, default_model)
        if default_label not in MODELS:
            default_label = "GPT-5.2" if "GPT-5.2" in MODELS else MODELS[0]

        ttk.Label(root, text="Model:").grid(row=0, column=0, sticky="w")
        self.model_cb = ttk.Combobox(
            root,
            textvariable=self._model_var,
            values=MODELS,
            state="readonly",
        )
        self._model_var.set(default_label)
        self.model_cb.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(root, text="Language:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._lang_cb = ttk.Combobox(
            root,
            textvariable=self._lang_var,
            values=["EN", "RU"],
            state="readonly",
            width=8,
        )
        self._lang_cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(root)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))

        ttk.Button(btns, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(btns, text="Generate", command=self._on_generate).pack(side=tk.RIGHT)

        root.columnconfigure(1, weight=1)

        self._win.update_idletasks()
        self._center_over_parent()

        self._win.bind("<Escape>", lambda e: self._on_cancel())
        self._win.bind("<Return>", lambda e: self._on_generate())

    def show(self) -> Optional[SummaryGenerationOptions]:
        self._win.wait_window()
        return self._result

    def _center_over_parent(self) -> None:
        try:
            px = self._parent.winfo_rootx()
            py = self._parent.winfo_rooty()
            pw = self._parent.winfo_width()
            ph = self._parent.winfo_height()
        except Exception:
            return

        ww = self._win.winfo_width()
        wh = self._win.winfo_height()
        x = px + max(0, (pw - ww) // 2)
        y = py + max(0, (ph - wh) // 2)
        self._win.geometry(f"+{x}+{y}")

    def _on_cancel(self) -> None:
        self._result = None
        self._win.destroy()

    def _on_generate(self) -> None:
        label = self._model_var.get().strip()
        model_id = MODEL_LABEL_TO_ID.get(label, label)  # allow passing raw ids too

        self._result = SummaryGenerationOptions(
            model=model_id,
            language=self._lang_var.get().strip(),
        )
        self._win.destroy()
