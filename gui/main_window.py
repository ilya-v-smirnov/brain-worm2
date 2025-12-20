from __future__ import annotations

import json
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk


from gui.db_gateway import DbGateway, FileRow
from gui.extracted_text_dialog import ExtractedTextDialog
from gui.file_ops import open_file
from gui.rename_new_pdfs_dialog import RenameNewPdfsDialog
from gui.summary_generation_dialog import SummaryGenerationDialog
from docx_utils.docx_writer import write_article_json_to_docx


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

        ttk.Button(
            bottom,
            text="1. Generate Summary",
            command=lambda: self._on_generate("summary"),
        ).grid(row=0, column=0, sticky="ew", padx=6)

        ttk.Button(
            bottom,
            text="2. Generate Lecture",
            command=lambda: self._on_generate("lecture"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Button(
            bottom,
            text="3. Generate Audio",
            command=lambda: self._on_generate("audio"),
        ).grid(row=0, column=2, sticky="ew", padx=6)


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

    # ---------------- Startup pipeline ----------------

    def _startup_pipeline(self) -> None:
        self._set_status("Initializing database…")
        self.db.init_db_schema()

        self._set_status("Syncing Article Database…")
        self.db.sync_article_database()

        self._set_status("Extracting JSON for new articles…")
        self.db.extract_contents_for_new_articles()

        self._set_status("Building tree…")
        self._reload_tree()

        self._set_status("Ready")

    # ---------------- Tree building ----------------

    def _reload_tree(self) -> None:
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
            return iid

        for row in rows:
            self._insert_pdf_row(row, ensure_folder)

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
            self._set_status("Extracting JSON for new articles…")
            self.db.extract_contents_for_new_articles()
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

        # 1) options dialog (modal)
        dlg = SummaryGenerationDialog(self.master, default_model="ChatGPT-5.2", default_language="EN")
        opts = dlg.show()
        if opts is None:
            return

        # 2) resolve JSON path for selected article
        article_id = int(payload["article_id"])

        try:
            json_rel = self.db.fetch_json_path_for_article(article_id)
        except Exception as e:
            messagebox.showerror("Generate Summary", f"{type(e).__name__}: {e}")
            return

        if not json_rel:
            messagebox.showwarning("Generate Summary", "No extracted JSON for this article yet.")
            return

        json_path = Path(self.db.resolve_path(json_rel))

        # 3) resolve PDF abs path (for mirrored output)
        pdf_abs = Path(self.db.resolve_path(payload["pdf_path"]))

        # 4) load JSON
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Generate Summary", f"Failed to read JSON:\n{type(e).__name__}: {e}")
            return

        # 5) export DOCX
        out_docx = self._build_summary_docx_path(pdf_abs)

        try:
            write_article_json_to_docx(
                data,
                out_docx,
                title=pdf_abs.stem,
            )
        except Exception as e:
            messagebox.showerror("Generate Summary", f"Failed to write DOCX:\n{type(e).__name__}: {e}")
            return

        self._open_with_system_app(out_docx)

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
        finally:
            self._ctx.grab_release()

    def _on_view_extracted_text(self) -> None:
        payload = self._get_selected_payload()
        if not payload or payload.get("type") != "pdf":
            messagebox.showwarning("View extracted text", "Select an article PDF first.")
            return

        try:
            json_rel = self.db.fetch_json_path_for_article(int(payload["article_id"]))
        except Exception as e:
            messagebox.showerror("View extracted text", f"{type(e).__name__}: {e}")
            return

        if not json_rel:
            messagebox.showwarning("View extracted text", "No extracted JSON for this article yet.")
            return

        json_path = self.db.resolve_path(json_rel)
        pdf_path = self.db.resolve_path(payload["pdf_path"])
        ExtractedTextDialog(
            self.master,
            json_path=json_path,
            pdf_path=pdf_path,
            parse_pdf_func=lambda p: self.db.parse_pdf_for_article(str(p)),
        )

    def _build_summary_docx_path(self, pdf_abs_path: Path) -> Path:
        """
        Mirror:
          Article Database/.../file.pdf -> PDF_summaries/.../file.docx

        If "Article Database" is not found in the path, fallback to ./PDF_summaries/<name>.docx
        """
        pdf_abs_path = Path(pdf_abs_path)

        parts = list(pdf_abs_path.parts)
        if "Article Database" in parts:
            idx = parts.index("Article Database")
            articles_root = Path(*parts[: idx + 1])          # .../Article Database
            rel = pdf_abs_path.relative_to(articles_root)    # folder1/folder2/file.pdf
            return articles_root.parent / "PDF_summaries" / rel.with_suffix(".docx")

        # fallback
        return Path("PDF_summaries") / pdf_abs_path.with_suffix(".docx").name

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

