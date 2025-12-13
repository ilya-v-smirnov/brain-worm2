from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from typing import Optional

from gui import new_pdfs_adapter as adapter
from gui.file_ops import open_file

ORANGE = "#d97706"  # оранжевый шрифт для Already in database


class RenameNewPdfsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("Rename New PDFs")
        self.geometry("1050x620")

        self.transient(master)
        self.grab_set()

        self.items: list[adapter.NewPdfItem] = []

        self._build_ui()
        self._load_items()

        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Refresh list", command=self._load_items).pack(side=tk.LEFT)
        ttk.Button(top, text="Open !New folder", command=self._open_new_folder).pack(side=tk.LEFT, padx=(10, 0))

        # Table
        table_frame = ttk.Frame(root)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

        cols = ("orig", "year", "title", "dest")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        self.tree.heading("orig", text="Original file")
        self.tree.heading("year", text="Year")
        self.tree.heading("title", text="Title")
        self.tree.heading("dest", text="Destination")

        self.tree.column("orig", width=320, anchor=tk.W, stretch=False)
        self.tree.column("year", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.column("title", width=520, anchor=tk.W, stretch=True)
        self.tree.column("dest", width=160, anchor=tk.W, stretch=False)

        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # tag for duplicates
        try:
            self.tree.tag_configure("duplicate", foreground=ORANGE)
        except Exception:
            pass

        # in-place editor / open file on double click
        self.tree.bind("<Double-1>", self._on_double_click)

        # Bottom
        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X)

        self.summary_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.summary_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(bottom, text="Close", command=self._close).pack(side=tk.RIGHT)
        self.btn_apply = ttk.Button(bottom, text="Apply rename", command=self._on_apply)
        self.btn_apply.pack(side=tk.RIGHT, padx=(0, 10))

    def _open_new_folder(self) -> None:
        try:
            import subprocess

            new_dir = adapter._get_new_dirs()["new"]  # type: ignore[attr-defined]
            subprocess.Popen(["xdg-open", str(new_dir)])
        except Exception as e:
            messagebox.showerror("Open folder error", f"{type(e).__name__}: {e}")

    def _load_items(self) -> None:
        self.tree.delete(*self.tree.get_children())
        try:
            self.items = adapter.analyze_new_pdfs_for_gui()
        except Exception as e:
            messagebox.showerror("Analyze error", f"{type(e).__name__}: {e}")
            self.items = []
            self._update_summary()
            return

        for idx, item in enumerate(self.items):
            iid = str(idx)
            values = (
                item.source_path.name,
                "" if item.user_year is None else str(item.user_year),
                item.user_title or "",
                item.destination,
            )
            tags = ("duplicate",) if item.exists_in_db else ()
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)

        self._update_summary()

    def _update_item_from_row(self, iid: str) -> None:
        idx = int(iid)
        item = self.items[idx]

        orig_s, year_s, title_s, _dest = self.tree.item(iid, "values")
        year_s = str(year_s).strip()
        title_s = str(title_s).strip()

        # parse year
        year: Optional[int] = None
        if year_s != "":
            try:
                year = int(year_s)
            except ValueError:
                year = None

        item.user_year = year
        item.user_title = title_s if title_s else None

        # destination recalculation (destination is a display value too)
        item.destination = adapter._compute_destination(  # type: ignore[attr-defined]
            year=item.user_year,
            title=item.user_title,
            exists_in_db=item.exists_in_db,
            parsing_error=item.parsing_error,
        )
        self.tree.set(iid, "dest", item.destination)

    def _update_summary(self) -> None:
        renamed = already = manual = 0
        for iid in self.tree.get_children(""):
            self._update_item_from_row(iid)
            dest = self.tree.set(iid, "dest")
            if dest == "Renamed":
                renamed += 1
            elif dest == "Already in database":
                already += 1
            else:
                manual += 1

        self.summary_var.set(
            f"Preview: Renamed={renamed} | Already in database={already} | Manual review={manual}"
        )

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)  # '#1'=orig, '#2'=year, '#3'=title, '#4'=dest
        if not iid:
            return

        # Original file: open PDF
        if col == "#1":
            try:
                idx = int(iid)
                open_file(self.items[idx].source_path)
            except Exception as e:
                messagebox.showerror("Open file error", f"{type(e).__name__}: {e}")
            return

        # Year/Title editable
        if col not in ("#2", "#3"):
            return

        x, y, w, h = self.tree.bbox(iid, col)
        key = "year" if col == "#2" else "title"
        value = self.tree.set(iid, key)

        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, value)
        entry.focus_set()
        entry.selection_range(0, tk.END)

        def commit(_evt=None) -> None:
            new_val = entry.get()
            self.tree.set(iid, key, new_val)
            entry.destroy()
            self._update_summary()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda _e: entry.destroy())

    def _on_apply(self) -> None:
        # sync model from table
        for iid in self.tree.get_children(""):
            self._update_item_from_row(iid)

        # apply only non-manual
        to_apply = [it for it in self.items if it.destination != "Manual review"]

        if not to_apply:
            messagebox.showinfo("Apply rename", "Nothing to rename/move (all rows are Manual review).")
            return

        summary = adapter.apply_rename(to_apply)

        if summary.errors:
            messagebox.showerror("Apply rename", "Some files failed to move:\n" + "\n".join(summary.errors))

        messagebox.showinfo(
            "Apply rename",
            f"Done. Renamed={summary.moved_renamed}, Already in database={summary.moved_already}, Skipped manual={summary.skipped_manual}",
        )

        self._load_items()

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
