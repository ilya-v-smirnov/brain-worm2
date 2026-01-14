from __future__ import annotations

import json
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
import threading


from gui.db_gateway import DbGateway, FileRow
from gui.extracted_text_dialog import ExtractedTextDialog
from gui.file_ops import open_file
from gui.rename_new_pdfs_dialog import RenameNewPdfsDialog
from gui.summary_generation_dialog import SummaryGenerationDialog
from ai_summary.generator import generate_summary
from docx_utils.docx_writer import append_ai_summary_to_docx
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

        columns = ("summary", "lecture", "audio")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")

        self.tree.heading("#0", text="Article Database")
        self.tree.heading("summary", text="Summary")
        self.tree.heading("lecture", text="Lecture")
        self.tree.heading("audio", text="Audio")

        self.tree.column("#0", width=720, stretch=True)
        self.tree.column("summary", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.column("lecture", width=90, anchor=tk.CENTER, stretch=False)
        self.tree.column("audio", width=90, anchor=tk.CENTER, stretch=False)

        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(root)
        bottom.pack(fill=tk.X, padx=10, pady=8)

        for col in range(3):
            bottom.columnconfigure(col, weight=1, uniform="bottom_buttons")

        self.btn_generate_summary = ttk.Button(
            bottom,
            text="1. Generate Summary",
            command=lambda: self._on_generate("summary"),
        )
        self.btn_generate_summary.grid(row=0, column=0, sticky="ew", padx=6)

        self.btn_generate_lecture = ttk.Button(
            bottom,
            text="2. Generate Lecture",
            command=lambda: self._on_generate("lecture"),
        )
        self.btn_generate_lecture.grid(row=0, column=1, sticky="ew", padx=6)

        self.btn_generate_audio = ttk.Button(
            bottom,
            text="3. Generate Audio",
            command=lambda: self._on_generate("audio"),
        )
        self.btn_generate_audio.grid(row=0, column=2, sticky="ew", padx=6)

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(root, textvariable=self.status_var, anchor="w").pack(fill=tk.X, pady=(8, 0))

        self.tree.bind("<Double-1>", self._on_double_click)

        # Context menu (ПКМ)
        self._ctx = tk.Menu(self.master, tearoff=False)
        # NOTE: актуальный обработчик называется _on_view_extracted_text
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

        # NOTE: intentionally DO NOT auto-generate JSON on startup anymore.
        # JSON extraction should be user-initiated from "Extracted Text" window.
        # self._set_status("Extracting JSON for new articles…")
        # self.db.extract_contents_for_new_articles()

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

            iid = self.tree.insert(parent_iid, "end", text=name, values=("", "", ""))
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
        lecture = CHECK if row.lecture_text_path else DASH
        audio = CHECK if row.lecture_audio_path else DASH

        iid = self.tree.insert(parent_iid, "end", text=filename, values=(summary, lecture, audio))
        self._iid_to_payload[iid] = {
            "type": "pdf",
            "article_id": row.article_id,
            "pdf_path": row.pdf_path,
            "summary_path": row.summary_path,
            "lecture_text_path": row.lecture_text_path,
            "lecture_audio_path": row.lecture_audio_path,
        }

    # ---------------- Handlers ----------------

    def _on_refresh_db(self) -> None:
        try:
            self._set_status("Syncing Article Database…")
            self.db.sync_article_database()

            # NOTE: intentionally DO NOT auto-generate JSON on refresh anymore.
            # self._set_status("Extracting JSON for new articles…")
            # self.db.extract_contents_for_new_articles()

            self._set_status("Building tree…")
            self._reload_tree()
            self._set_status("Database updated")
        except Exception as e:
            messagebox.showerror("Refresh error", f"{type(e).__name__}: {e}")
            self._set_status("Error")

    def _on_rename_new(self) -> None:
        RenameNewPdfsDialog(self.master)

    def _ask_summary_mode(self) -> str | None:
        """
        Returns:
        "auto"  - automated generation
        "semi"  - semi-manual generation
        None    - cancel
        """
        win = tk.Toplevel(self.master)
        win.title("Summary Generation")
        win.resizable(False, False)
        win.transient(self.master)
        win.grab_set()

        result: dict[str, str | None] = {"mode": None}

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Choose summary generation mode:").pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(10, 0))

        def choose(mode: str | None) -> None:
            result["mode"] = mode
            win.destroy()

        ttk.Button(btns, text="Automated generation", command=lambda: choose("auto")).pack(fill=tk.X)
        ttk.Button(btns, text="Semi-Manual generation", command=lambda: choose("semi")).pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Cancel", command=lambda: choose(None)).pack(fill=tk.X, pady=(6, 0))

        win.protocol("WM_DELETE_WINDOW", lambda: choose(None))

        # center-ish
        try:
            win.update_idletasks()
            x = self.master.winfo_rootx() + 80
            y = self.master.winfo_rooty() + 80
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass

        self.master.wait_window(win)
        return result["mode"]

    def _on_generate(self, kind: str) -> None:
        payload = self._get_selected_payload()
        if not payload or payload.get("type") != "pdf":
            messagebox.showwarning("Generate", "Select an article PDF first.")
            return

        if kind != "summary":
            messagebox.showinfo("Generate", "Not implemented yet")
            return
        
        mode = self._ask_summary_mode()
        if mode is None:
            return
        
        if mode == "semi":
            article_id = int(payload["article_id"])

            json_rel = self.db.fetch_json_path_for_article(article_id)
            if not json_rel:
                messagebox.showwarning("Semi-Manual Summary", "No extracted JSON for this article yet.")
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
                parse_pdf_func=None,
                db_gateway=self.db,
                article_id=article_id,
                existing_summary_path=payload.get("summary_path"),
            )
            # After dialog closes, refresh the tree (summary path might have changed)
            try:
                self.master.wait_window(win)
            except Exception:
                pass
            self._reload_tree()
            return

        if mode == "auto":
            dlg = SummaryGenerationDialog(self.master, default_model="ChatGPT-5.2", default_language="EN")
            opts = dlg.show()
            if opts is None:
                return

        article_id = int(payload["article_id"])
        pdf_path_rel = payload.get("pdf_path")  # то, что в БД (обычно относительное)
        if not pdf_path_rel:
            messagebox.showerror("Generate Summary", "Internal error: PDF path not found in DB payload.")
            return

        out_docx = self._build_summary_docx_path(pdf_path_rel)
        out_docx.parent.mkdir(parents=True, exist_ok=True)

        self._set_busy(True, "Generating summary…")

        def worker() -> None:
            try:
                # 1) read parsed JSON
                paths = self.db.get_article_paths(article_id)
                json_rel = paths.get("json_path")
                if not json_rel:
                    raise KeyError("json_path is missing for this article in DB (Article.json_path).")

                json_abs = self.db.resolve_path(json_rel)
                data = json.loads(Path(json_abs).read_text(encoding="utf-8"))

                # 2) generate summary (strategy auto)
                summary, _usage = generate_summary(
                    data,
                    model=opts.model,
                    language=opts.language,
                    strategy="auto",
                    header_defaults={
                        "source_path": pdf_path_rel,  # путь PDF из БД (зеркалится корректно)
                    },
                )

                # 3) write docx (append)
                append_ai_summary_to_docx(docx_path=out_docx, summary=summary)

                # 4) update DB path (store rel if possible)
                self.db.set_summary_path_for_article(article_id, out_docx)

                # 5) back to UI thread: reload + open
                self.master.after(0, lambda: self._on_summary_success(out_docx))

            except ValueError as e:
                self.master.after(0, lambda err=e: self._on_summary_error(err, is_user_fixable=True))
            except Exception as e:
                self.master.after(0, lambda err=e: self._on_summary_error(err, is_user_fixable=False))

        threading.Thread(target=worker, daemon=True).start()


    def _on_summary_done(self, out_docx: Path) -> None:
        self._reload_tree()
        self._set_busy(False, "Ready")
        self._open_with_system_app(out_docx)

    def _on_summary_error(self, e: Exception) -> None:
        self._set_busy(False, "Ready")
        # Friendly handling for the key expected error
        if isinstance(e, ValueError) and "No Results subsections" in str(e):
            messagebox.showerror(
                "Generate Summary",
                "Невозможно сгенерировать summary: секция Results пуста или не распознана.\n\n"
                "Пожалуйста, заполните Results вручную (подразделы Results должны существовать), "
                "затем попробуйте снова.",
            )
            return
        messagebox.showerror("Generate Summary", f"Generation failed:\n{type(e).__name__}: {e}")

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.tree.focus()
        payload = self._iid_to_payload.get(iid)
        if not payload or payload.get("type") != "pdf":
            return

        col = self.tree.identify_column(event.x)  # '#0', '#1', '#2', '#3'

        def open_rel(rel_or_abs: str | None) -> None:
            if not rel_or_abs:
                return
            p = self.db.resolve_path(rel_or_abs)
            open_file(p)

        if col == "#0":
            open_rel(payload.get("pdf_path"))
        elif col == "#1":
            open_rel(payload.get("summary_path"))
        elif col == "#2":
            open_rel(payload.get("lecture_text_path"))
        elif col == "#3":
            open_rel(payload.get("lecture_audio_path"))

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
            parse_pdf_func=lambda p: self.db.parse_pdf_for_article(str(p)),
            on_saved_close=_after_saved,
        )


    def _build_summary_docx_path(self, pdf_rel_or_abs: str) -> Path:
        """Build summary DOCX path.

        Requirement:
        - Save under PROJECT_HOME_DIR/PDF_summaries
        - Mirror the folder structure *inside* the Article Database.

        Examples:
          "Article Database/folder1/folder2/paper.pdf" ->
          "PDF_summaries/folder1/folder2/paper.docx"  (relative to project home)

          "folder1/folder2/paper.pdf" ->
          "PDF_summaries/folder1/folder2/paper.docx"  (relative to project home)
        """

        p = Path(pdf_rel_or_abs)
        # If absolute, try to make it project-relative
        if p.is_absolute():
            try:
                p = p.relative_to(self.db.project_home)
            except Exception:
                # absolute but outside the project -> just mirror its name
                p = Path(p.name)

        # Strip leading "Article Database" if present
        if p.parts and p.parts[0] == "Article Database":
            p_inside = Path(*p.parts[1:])
        else:
            p_inside = p

        out = self.db.project_home / "PDF_summaries" / p_inside
        return out.with_suffix(".docx")

    def _open_with_system_app(self, path: Path) -> None:
        """
        Open file using system default application (Ubuntu: xdg-open).
        Non-blocking.
        """
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except FileNotFoundError:
            messagebox.showerror("Open file", "xdg-open not found. Install 'xdg-utils' package.")
        except Exception as e:
            messagebox.showerror("Open file", f"Failed to open file:\n{type(e).__name__}: {e}")

    def _on_summary_success(self, out_docx: Path) -> None:
        self._set_busy(False, "Ready")
        self._reload_tree()
        self._open_with_system_app(out_docx)

    def _on_summary_error(self, e: Exception, *, is_user_fixable: bool) -> None:
        self._set_busy(False, "Ready")

        msg = f"{type(e).__name__}: {e}"
        if is_user_fixable and "No Results subsections found" in str(e):
            messagebox.showerror(
                "Generate Summary",
                "Results section is empty.\n\n"
                "Please fill Results subsections manually (in the extracted JSON), then try again.\n\n"
                f"Details:\n{msg}",
            )
            return

        messagebox.showerror(
            "Generate Summary",
            "Summary generation failed.\n\n"
            f"Details:\n{msg}",
        )

    def _set_busy(self, busy: bool, status_text: str) -> None:
        self.status_var.set(status_text)
        state = "disabled" if busy else "normal"
        for btn in (getattr(self, "btn_generate_summary", None),
                    getattr(self, "btn_generate_lecture", None),
                    getattr(self, "btn_generate_audio", None)):
            if btn is not None:
                btn.configure(state=state)

        try:
            self.master.configure(cursor="watch" if busy else "")
        except Exception:
            pass

    def _build_summary_docx_path(self, pdf_rel_or_abs: str) -> Path:
        """
        Mirrors Article Database PDF path into PROJECT_HOME_DIR/PDF_summaries.

        Examples:
          "Article Database/f1/f2/A.pdf" -> "<PROJECT_HOME>/PDF_summaries/f1/f2/A.docx"
          "f1/f2/A.pdf"                 -> "<PROJECT_HOME>/PDF_summaries/f1/f2/A.docx"
        """
        project_home = self.db.project_home
        p = Path(pdf_rel_or_abs)

        # normalize to relative where possible
        if p.is_absolute():
            try:
                p = p.relative_to(project_home)
            except Exception:
                # if absolute but not under project_home, we still mirror its tail path
                p = Path(*p.parts[1:])  # drop root "/"

        parts = list(p.parts)
        if parts and parts[0] == "Article Database":
            parts = parts[1:]

        mirrored = Path(*parts).with_suffix(".docx")
        return project_home / "PDF_summaries" / mirrored

    # ---------------- Utils ----------------

    def _get_selected_payload(self) -> dict | None:
        return self._iid_to_payload.get(self.tree.focus())

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        self.master.update_idletasks()

    def _set_busy(self, busy: bool, status_text: str) -> None:
        """Enable/disable main actions and show status."""
        self._set_status(status_text)
        state = "disabled" if busy else "normal"
        # Buttons may not exist during early startup
        for btn_name in ("btn_generate_summary", "btn_generate_lecture", "btn_generate_audio"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                try:
                    btn.configure(state=state)
                except Exception:
                    pass

        try:
            self.master.configure(cursor="watch" if busy else "")
        except Exception:
            pass
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
                "- связанный ИИ-контент (JSON, summary, lecture)\n\n"
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



