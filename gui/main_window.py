from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk


from gui.db_gateway import DbGateway, FileRow
from gui.extracted_text_dialog import ExtractedTextDialog
from gui.file_ops import open_file
from gui.rename_new_pdfs_dialog import RenameNewPdfsDialog
from gui.semi_manual_summary_dialog import SemiManualSummaryDialog


CHECK = "✓"
DASH = "-"


class MainWindow:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        self.db = DbGateway()
        self._iid_to_payload: dict[str, dict] = {}

        self._build_layout()
        self._startup_pipeline()

    def _build_layout(self) -> None:
        self.master.geometry("1100x700")

        root = ttk.Frame(self.master, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        self.root = root

        top = ttk.Frame(root)
        top.pack(fill=tk.X)

        ttk.Button(top, text="Rename New PDFs", command=self._on_rename_new).pack(side=tk.LEFT)
        ttk.Button(top, text="Refresh database", command=self._on_refresh_db).pack(side=tk.LEFT, padx=(10, 0))

        tree_frame = ttk.Frame(root)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

        columns = ("summary",)
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")

        self.tree.heading("#0", text="Article Database")
        self.tree.heading("summary", text="Summary")

        self.tree.column("#0", width=900, stretch=True)
        self.tree.column("summary", width=90, anchor=tk.CENTER, stretch=False)

        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X, padx=10, pady=8)

        self.btn_generate_summary = ttk.Button(
            bottom,
            text="Generate Summary",
            command=lambda: self._on_generate("summary"),
        )
        self.btn_generate_summary.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(root, textvariable=self.status_var, anchor="w").pack(fill=tk.X, pady=(8, 0))

        self.tree.bind("<Double-1>", self._on_double_click)

        # Context menu (ПКМ)
        self._ctx = tk.Menu(self.master, tearoff=False)
        self._ctx.add_command(label="View extracted text", command=self._on_view_extracted_text)
        self._ctx.add_separator()
        self._ctx.add_command(label="Delete…", command=self._ctx_delete_article)
        self.tree.bind("<Button-3>", self._on_right_click)

        # Remove old problematic binding (X11: FocusOut fires during tk_popup)
        try:
            self.master.unbind("<FocusOut>")
        except Exception:
            pass

        # Autoclose context menu:
        # - any left click outside the menu
        # - window deactivated (Alt+Tab)
        # - window minimized/unmapped
        self.master.bind("<Button-1>", self._hide_ctx_menu, add="+")
        self.master.bind("<Deactivate>", self._hide_ctx_menu, add="+")
        self.master.bind("<Unmap>", self._hide_ctx_menu, add="+")

    # ---------------- Startup pipeline ----------------

    def _startup_pipeline(self) -> None:
        self._set_status("Initializing database…")
        self.db.init_db_schema()

        self._set_status("Syncing Article Database…")
        self.db.sync_article_database()

        self._set_status("Reconciling JSON/DOCX links…")
        self.db.reconcile_article_paths()

        self._set_status("Building tree…")
        self._reload_tree()

        self._set_status("Ready")

    # ---------------- Tree building ----------------

    def _collect_open_folder_keys(self) -> set[str]:
        """
        Collects 'folder_key' for currently expanded folders.
        Uses payload['key'] which is stable across reloads.
        """
        open_keys: set[str] = set()

        def walk(parent_iid: str) -> None:
            for iid in self.tree.get_children(parent_iid):
                payload = self._iid_to_payload.get(iid)
                if payload and payload.get("type") == "folder":
                    try:
                        if bool(self.tree.item(iid, "open")):
                            key = payload.get("key")
                            if isinstance(key, str) and key:
                                open_keys.add(key)
                    except Exception:
                        pass
                    walk(iid)

        walk("")
        return open_keys

    def _expand_first_branch(self) -> None:
        """
        Expands ONLY the very first root node (no deep expansion).
        """
        roots = self.tree.get_children("")
        if not roots:
            return

        iid = roots[0]
        try:
            self.tree.item(iid, open=True)
        except Exception:
            pass

    def _reload_tree(self) -> None:
        # Preserve expanded folders (by stable folder_key) before rebuilding
        open_folder_keys = self._collect_open_folder_keys()

        self.tree.delete(*self.tree.get_children())
        self._iid_to_payload.clear()

        rows = self.db.fetch_file_rows()

        folder_iids: dict[str, str] = {}

        def ensure_folder(parent_iid: str, folder_key: str, name: str) -> str:
            if folder_key in folder_iids:
                return folder_iids[folder_key]

            iid = self.tree.insert(parent_iid, "end", text=name, values=("",))
            self._iid_to_payload[iid] = {"type": "folder", "key": folder_key}
            folder_iids[folder_key] = iid

            # Restore open state for previously expanded folders
            if folder_key in open_folder_keys:
                try:
                    self.tree.item(iid, open=True)
                except Exception:
                    pass

            return iid

        for row in rows:
            self._insert_pdf_row(row, ensure_folder)

        # If nothing was open (e.g., first app start), expand the first branch
        if not open_folder_keys:
            self._expand_first_branch()

    def _insert_pdf_row(self, row: FileRow, ensure_folder) -> None:
        parts = row.pdf_path.split("/")
        if not parts:
            return

        parent_key = ""
        parent_iid = ""
        for seg in parts[:-1]:
            parent_key = f"{parent_key}/{seg}" if parent_key else seg
            parent_iid = ensure_folder(parent_iid, parent_key, seg)

        filename = parts[-1]
        summary = CHECK if row.summary_path else DASH

        iid = self.tree.insert(parent_iid, "end", text=filename, values=(summary,))
        self._iid_to_payload[iid] = {
            "type": "pdf",
            "article_id": row.article_id,
            "pdf_path": row.pdf_path,
            "summary_path": row.summary_path,
        }

    # ---------------- Handlers ----------------

    def _on_refresh_db(self) -> None:
        try:
            self._set_status("Syncing Article Database…")
            self.db.sync_article_database()

            self._set_status("Building tree…")
            self._reload_tree()
            self._set_status("Database updated")
        except Exception as e:
            messagebox.showerror("Refresh error", f"{type(e).__name__}: {e}")
            self._set_status("Error")

    def _on_rename_new(self) -> None:
        RenameNewPdfsDialog(self.master)

    def _on_generate(self, kind: str) -> None:
        payload = self._get_selected_payload()
        if not payload or payload.get("type") != "pdf":
            messagebox.showwarning("Generate", "Select an article PDF first.")
            return

        if kind != "summary":
            messagebox.showinfo("Generate", "Not implemented yet")
            return

        article_id = int(payload["article_id"])

        json_rel = self.db.fetch_json_path_for_article(article_id)
        if not json_rel:
            messagebox.showwarning(
                "Semi-Manual Summary",
                "No extracted JSON for this article yet.\n\n"
                "Use right-click → 'View extracted text' first to create one.",
            )
            return
        json_path = Path(self.db.resolve_path(json_rel))

        pdf_rel = payload.get("pdf_path")
        if not pdf_rel:
            messagebox.showerror("Semi-Manual Summary", "Internal error: PDF path not found in DB payload.")
            return
        pdf_path = Path(self.db.resolve_path(pdf_rel))

        win = SemiManualSummaryDialog(
            self.master,
            json_path=json_path,
            pdf_path=pdf_path,
            db_gateway=self.db,
            article_id=article_id,
            existing_summary_path=payload.get("summary_path"),
        )
        try:
            self.master.wait_window(win)
        except Exception:
            pass
        self._reload_tree()

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.tree.focus()
        payload = self._iid_to_payload.get(iid)
        if not payload or payload.get("type") != "pdf":
            return

        col = self.tree.identify_column(event.x)  # '#0', '#1'

        def open_rel(rel_or_abs: str | None) -> None:
            if not rel_or_abs:
                return
            p = self.db.resolve_path(rel_or_abs)
            open_file(p)

        if col == "#0":
            open_rel(payload.get("pdf_path"))
        elif col == "#1":
            open_rel(payload.get("summary_path"))

    def _on_right_click(self, event: tk.Event) -> None:
        self._hide_ctx_menu()

        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)

        payload = self._iid_to_payload.get(iid)
        if not payload or payload.get("type") != "pdf":
            return

        try:
            self._ctx.tk_popup(event.x_root, event.y_root)
            self._ctx_posted = True
            self._start_ctx_watch()
        finally:
            try:
                self._ctx.grab_release()
            except Exception:
                pass

    def _on_view_extracted_text(self) -> None:
        payload = self._get_selected_payload()
        if not payload or payload.get("type") != "pdf":
            messagebox.showwarning("View extracted text", "Select an article PDF first.")
            return

        article_id = int(payload["article_id"])
        pdf_rel = payload.get("pdf_path")
        if not pdf_rel:
            messagebox.showerror("View extracted text", "Internal error: PDF path not found in payload.")
            return

        # Resolve PDF absolute path
        pdf_path = Path(self.db.resolve_path(pdf_rel))

        try:
            json_rel = self.db.fetch_json_path_for_article(article_id)
        except Exception as e:
            messagebox.showerror("View extracted text", f"{type(e).__name__}: {e}")
            return

        # If DB has no json_path yet, use default Contents/<pdf_name>.json
        if not json_rel:
            json_rel = str(Path("Contents") / (Path(pdf_rel).name)).replace(".pdf", ".json")

        json_path = Path(self.db.resolve_path(json_rel))

        def _after_saved() -> None:
            # Persist json_path into DB (store rel if possible) and refresh UI
            try:
                self.db.set_json_path_for_article(article_id, json_path)
            except Exception:
                # Even if DB update fails, keep UI alive; user still has the JSON on disk
                pass
            try:
                self._reload_tree()
            except Exception:
                pass

        ExtractedTextDialog(
            self.master,
            json_path=json_path,
            pdf_path=pdf_path,
            on_saved_close=_after_saved,
        )

    # ---------------- Utils ----------------

    def _get_selected_payload(self) -> dict | None:
        return self._iid_to_payload.get(self.tree.focus())

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.master.update_idletasks()

    def _ctx_delete_article(self):
        iid = self.tree.focus()
        payload = self._iid_to_payload.get(iid)
        if not payload or payload.get("type") != "pdf":
            return

        article_id = int(payload["article_id"])
        pdf_path = payload["pdf_path"]

        db = self.db

        pdf_paths = db.list_article_pdf_paths(article_id)
        _paths = db.get_article_paths(article_id)  # можно использовать позже для расширенного UI

        has_multiple = len(pdf_paths) > 1

        # --- выбор режима удаления ---
        if has_multiple:
            choice = messagebox.askquestion(
                "Delete article",
                "У этой статьи есть несколько копий PDF.\n\n"
                "YES — удалить ТОЛЬКО этот PDF-путь\n"
                "NO — удалить ВСЕ копии и связанный ИИ-контент",
                icon="warning",
            )
            delete_everywhere = (choice == "no")
        else:
            delete_everywhere = True

        # --- подтверждение ---
        if delete_everywhere:
            confirm = messagebox.askyesno(
                "Confirm deletion",
                "Это действие удалит:\n"
                "- все PDF-файлы статьи\n"
                "- запись статьи из БД\n"
                "- связанный ИИ-контент (JSON, summary)\n\n"
                "Действие необратимо. Продолжить?",
                icon="warning",
            )
            if not confirm:
                return

            report = db.delete_article_everywhere(
                article_id=article_id,
                delete_physical_pdfs=True,
                delete_ai_files=True,
            )
        else:
            confirm = messagebox.askyesno(
                "Confirm deletion",
                f"Удалить только этот PDF?\n\n{pdf_path}\n\n"
                "Запись статьи и ИИ-контент будут сохранены.",
                icon="warning",
            )
            if not confirm:
                return

            report = db.delete_single_pdf_path(
                article_id=article_id,
                pdf_path=pdf_path,
                delete_physical_pdf=True,
            )

        # --- обновление GUI ---
        self._reload_tree()

        # --- краткий отчёт ---
        msg = (
            f"Удаление завершено.\n\n"
            f"Удалено файлов: {len(report.deleted_files)}\n"
            f"Отсутствовало файлов: {len(report.missing_files)}"
        )
        if report.updated_master_path_to:
            msg += f"\nНовый master PDF:\n{report.updated_master_path_to}"
        if report.errors:
            msg += "\n\nОшибки:\n" + "\n".join(report.errors)

        messagebox.showinfo("Delete result", msg)

    def _hide_ctx_menu(self, _event: tk.Event | None = None) -> None:
        try:
            self._ctx.unpost()
        except Exception:
            pass
        self._ctx_posted = False

    def _start_ctx_watch(self) -> None:
        """
        While context menu is posted, periodically check if the toplevel lost focus.
        This is the most reliable way on Ubuntu/X11 where <Deactivate> may not fire.
        """
        if getattr(self, "_ctx_watch_running", False):
            return
        self._ctx_watch_running = True

        def _tick() -> None:
            if not getattr(self, "_ctx_posted", False):
                self._ctx_watch_running = False
                return

            try:
                # If the window is not the focus owner (Alt+Tab), focus_displayof() becomes None.
                # On minimize/unmap, winfo_viewable() becomes False.
                inactive = (self.master.focus_displayof() is None) or (not bool(self.master.winfo_viewable()))
            except Exception:
                inactive = True

            if inactive:
                self._hide_ctx_menu()
                self._ctx_watch_running = False
                return

            self.master.after(100, _tick)

        self.master.after(100, _tick)
