from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from typing import Protocol


class FindReplaceProvider(Protocol):
    def fr_find_next(self, query: str, *, match_case: bool) -> bool: ...
    def fr_replace_current(self, query: str, replacement: str, *, match_case: bool) -> bool: ...
    def fr_replace_all(self, query: str, replacement: str, *, match_case: bool) -> int: ...


@dataclass
class FindReplaceState:
    last_query: str = ""
    last_replacement: str = ""
    match_case: bool = False


class FindReplaceDialog(tk.Toplevel):
    """
    Standard Find/Replace dialog.
    UI lives here; logic delegated to provider (host window).
    """

    def __init__(self, master: tk.Misc, *, provider: FindReplaceProvider, state: FindReplaceState | None = None) -> None:
        super().__init__(master)
        self.title("Find and Replace")
        self.resizable(False, False)

        self.provider = provider
        self.state = state or FindReplaceState()

        # Be friendly to window managers
        try:
            self.transient(master.winfo_toplevel())
        except Exception:
            pass
        self.lift()

        self.find_var = tk.StringVar(value=self.state.last_query)
        self.repl_var = tk.StringVar(value=self.state.last_replacement)
        self.match_case_var = tk.BooleanVar(value=bool(self.state.match_case))

        self._build_ui()

        # shortcuts inside dialog
        self.bind("<Return>", lambda _e: self._on_find_next())
        self.bind("<Escape>", lambda _e: self._on_close())

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.find_entry.focus_set()
        self.find_entry.selection_range(0, tk.END)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.grid(row=0, column=0, sticky="nsew")

        ttk.Label(root, text="Find:").grid(row=0, column=0, sticky="w")
        self.find_entry = ttk.Entry(root, textvariable=self.find_var, width=40)
        self.find_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(root, text="Replace:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.repl_entry = ttk.Entry(root, textvariable=self.repl_var, width=40)
        self.repl_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        # Track last active entry to support "insert where cursor is"
        self._last_entry: tk.Entry | None = self.find_entry
        self.find_entry.bind("<FocusIn>", lambda _e: self._set_last_entry(self.find_entry), add="+")
        self.repl_entry.bind("<FocusIn>", lambda _e: self._set_last_entry(self.repl_entry), add="+")
        self._shift_once = False

        opts = ttk.Frame(root)
        opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(opts, text="Match case", variable=self.match_case_var).pack(anchor="w")

        btns = ttk.Frame(root)
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for c in range(5):
            btns.columnconfigure(c, weight=1)

        ttk.Button(btns, text="Find next", command=self._on_find_next).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Replace", command=self._on_replace).grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Replace all", command=self._on_replace_all).grid(row=0, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Close", command=self._on_close).grid(row=0, column=3, sticky="ew", padx=(0, 6))

        # Optional: spacer button slot (keeps layout flexible)
        ttk.Label(btns, text="").grid(row=0, column=4, sticky="ew")

        # --- Greek alphabet panel (lowercase) + one-shot Shift ---
        greek = ttk.Frame(root)
        greek.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        # Layout: Shift + letters in a compact grid
        # You can tweak columns count to taste; 10 fits well for typical window widths.
        columns = 10
        for c in range(columns):
            greek.columnconfigure(c, weight=1)

        self._shift_btn = ttk.Button(greek, text="Shift", command=self._arm_shift_once)
        self._shift_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))

        letters = list("αβγδεζηθικλμνξοπρστυφχψω") + ["ς", "×", "°"]  # + final sigma, multiply, degree

        # Start placing from column 1 because column 0 is Shift
        r = 0
        c = 1
        for ch in letters:
            b = ttk.Button(greek, text=ch, width=3, command=lambda x=ch: self._insert_greek(x))
            b.grid(row=r, column=c, sticky="ew", padx=(0, 6), pady=(0, 6))
            c += 1
            if c >= columns:
                r += 1
                c = 0

    def _set_last_entry(self, entry: tk.Entry) -> None:
        self._last_entry = entry

    def _arm_shift_once(self) -> None:
        self._shift_once = True
        # small visual cue
        try:
            self._shift_btn.configure(text="Shift*")
        except Exception:
            pass

    def _insert_greek(self, ch: str) -> None:
        # Decide case for one shot
        out = ch.upper() if self._shift_once else ch

        # Prefer the widget that currently owns focus, if it's an Entry
        w = self.focus_get()
        target = None
        if isinstance(w, (tk.Entry, ttk.Entry)):
            target = w
        else:
            target = self._last_entry or self.find_entry

        try:
            target.insert(tk.INSERT, out)
            target.focus_set()
        except Exception:
            # fallback: find_entry
            try:
                self.find_entry.insert(tk.INSERT, out)
                self.find_entry.focus_set()
            except Exception:
                pass

        # Auto-reset shift after one insertion
        if self._shift_once:
            self._shift_once = False
            try:
                self._shift_btn.configure(text="Shift")
            except Exception:
                pass

    def _snapshot_state(self) -> None:
        self.state.last_query = (self.find_var.get() or "").strip()
        self.state.last_replacement = self.repl_var.get() or ""
        self.state.match_case = bool(self.match_case_var.get())

    def _on_find_next(self) -> None:
        q = (self.find_var.get() or "").strip()
        if not q:
            self.find_entry.focus_set()
            return
        self._snapshot_state()
        self.provider.fr_find_next(q, match_case=bool(self.match_case_var.get()))

    def _on_replace(self) -> None:
        q = (self.find_var.get() or "").strip()
        if not q:
            self.find_entry.focus_set()
            return
        self._snapshot_state()
        self.provider.fr_replace_current(
            q,
            self.repl_var.get() or "",
            match_case=bool(self.match_case_var.get()),
        )

    def _on_replace_all(self) -> None:
        q = (self.find_var.get() or "").strip()
        if not q:
            self.find_entry.focus_set()
            return
        self._snapshot_state()
        self.provider.fr_replace_all(
            q,
            self.repl_var.get() or "",
            match_case=bool(self.match_case_var.get()),
        )

    def _on_close(self) -> None:
        self._snapshot_state()
        try:
            self.destroy()
        except Exception:
            pass
