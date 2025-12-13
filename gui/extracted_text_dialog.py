from __future__ import annotations

import json
from copy import deepcopy
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk

from gui.file_ops import open_file


@dataclass
class _ResultSectionWidgets:
    frame: ttk.Frame
    title_var: tk.StringVar
    title_entry: ttk.Entry
    text: tk.Text


@dataclass
class _FigureWidgets:
    frame: ttk.Frame
    number_var: tk.StringVar
    number_entry: ttk.Entry
    caption: tk.Text


class ExtractedTextDialog(tk.Toplevel):
    """Модальный редактор JSON по ТЗ (с вкладками и прокруткой).

    Вкладки:
    - Introduction
    - Methods
    - Results (общая прокрутка)
    - Figures (общая прокрутка)
    """

    def __init__(self, master: tk.Misc, *, json_path: Path, pdf_path: Path | None = None) -> None:
        super().__init__(master)
        self.title("Extracted Text")
        self.geometry("980x720")

        self.transient(master)
        self.grab_set()

        self.json_path = json_path
        self.pdf_path = pdf_path

        self.original_data: dict = {}
        self._result_widgets: list[_ResultSectionWidgets] = []
        self._figure_widgets: list[_FigureWidgets] = []

        self._build_ui()
        self._load_json()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        # Use grid to guarantee bottom buttons visibility
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)  # notebook row

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Extracted text", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, sticky="w")

        actions = ttk.Frame(header)
        actions.grid(row=0, column=1, sticky="e")

        ttk.Button(actions, text="Extract text again", command=self._on_extract_text_again).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Copy text", command=self._copy_text_from_disk).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Copy JSON", command=self._copy_json_from_disk).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Open PDF", command=self._on_open_pdf).pack(side=tk.LEFT)

        common = ttk.Frame(root)
        common.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        common.columnconfigure(1, weight=1)

        self.title_var = tk.StringVar()
        self.year_var = tk.StringVar()

        ttk.Label(common, text="Title:").grid(row=0, column=0, sticky="w")
        ttk.Entry(common, textvariable=self.title_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(common, text="Year:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(common, textvariable=self.year_var, width=12).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        self.nb = ttk.Notebook(root)
        self.nb.grid(row=2, column=0, sticky="nsew")

        self.tab_intro = ttk.Frame(self.nb, padding=10)
        self.tab_methods = ttk.Frame(self.nb, padding=10)

        self.tab_discussion = ttk.Frame(self.nb, padding=10)

        # Scrollable tabs for Results/Figures
        self.tab_results = ttk.Frame(self.nb, padding=0)
        self.tab_figures = ttk.Frame(self.nb, padding=0)

        self.nb.add(self.tab_intro, text="Introduction")
        self.nb.add(self.tab_methods, text="Methods")
        self.nb.add(self.tab_results, text="Results")
        self.nb.add(self.tab_discussion, text="Discussion")
        self.nb.add(self.tab_figures, text="Figures")

        self.intro_text = self._text_area(self.tab_intro)
        self.methods_text = self._text_area(self.tab_methods)
        self.discussion_text = self._text_area(self.tab_discussion)

        # Results: scrollable
        self.results_canvas, self.results_inner = self._make_scrollable_tab(self.tab_results)
        self._build_results_inner(self.results_inner)

        # Figures: scrollable
        self.figures_canvas, self.figures_inner = self._make_scrollable_tab(self.tab_figures)
        self._build_figures_inner(self.figures_inner)

        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        ttk.Button(bottom, text="Cancel", command=self._on_cancel).grid(row=0, column=3, sticky="e")
        ttk.Button(bottom, text="Save & Close", command=self._on_save_and_close).grid(row=0, column=2, sticky="e", padx=(0, 10))
        ttk.Button(bottom, text="Save", command=self._on_save).grid(row=0, column=1, sticky="e", padx=(0, 10))

    def _text_area(self, parent: ttk.Frame) -> tk.Text:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        box = ttk.Frame(parent)
        box.grid(row=0, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        txt = tk.Text(box, wrap="word")
        scr = ttk.Scrollbar(box, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scr.set)

        txt.grid(row=0, column=0, sticky="nsew")
        scr.grid(row=0, column=1, sticky="ns")
        return txt

    def _make_scrollable_tab(self, parent: ttk.Frame) -> tuple[tk.Canvas, ttk.Frame]:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        wrapper = ttk.Frame(parent, padding=10)
        wrapper.grid(row=0, column=0, sticky="nsew")
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(0, weight=1)

        canvas = tk.Canvas(wrapper, highlightthickness=0)
        vscroll = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_inner_configure(_evt=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(evt: tk.Event) -> None:
            canvas.itemconfigure(inner_id, width=evt.width)

        inner.bind("<Configure>", on_inner_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        # mouse wheel support when cursor over tab
        def _on_mousewheel(event: tk.Event) -> None:
            # On Linux, delta is usually 120/-120 via event.num 4/5 or event.delta in some envs
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            else:
                delta = getattr(event, "delta", 0)
                if delta:
                    canvas.yview_scroll(int(-delta / 120), "units")

        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        return canvas, inner

    def _build_results_inner(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Result sections:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        self.results_frame = ttk.Frame(parent)
        self.results_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _build_figures_inner(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Figures:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        self.figures_frame = ttk.Frame(parent)
        self.figures_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    # ---------------- Load/Populate ----------------

    def _load_json(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Error", f"JSON not found:\n{self.json_path}")
            self._on_cancel()
            return

        try:
            self.original_data = json.loads(self.json_path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Error", f"{type(e).__name__}: {e}")
            self._on_cancel()
            return

        self.title_var.set(str(self.original_data.get("title", "") or ""))
        self.year_var.set(str(self.original_data.get("year", "") or ""))

        self._set_text(self.intro_text, str(self.original_data.get("introduction", "") or ""))
        self._set_text(self.methods_text, str(self.original_data.get("methods", "") or ""))
        self._set_text(self.discussion_text, str(self.original_data.get("discussion", "") or ""))

        self._clear_results()
        results = self.original_data.get("results") or []
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    self._add_result_section(
                        section_title=str(item.get("section_title", "") or ""),
                        section_text=str(item.get("section_text", "") or ""),
                    )


        # If JSON has no result sections, keep one empty block for editing
        if not self._result_widgets:
            self._add_result_section()

        self._clear_figures()
        figures = self.original_data.get("figures") or []
        if isinstance(figures, list):
            for item in figures:
                if isinstance(item, dict):
                    num = item.get("figure_number", "")
                    self._add_figure(
                        figure_number=str(num if num is not None else ""),
                        caption=str(item.get("caption", "") or ""),
                    )


        # If JSON has no figures, keep one empty block for editing
        if not self._figure_widgets:
            self._add_figure()

        # ensure scrollregion updated
        self.results_canvas.update_idletasks()
        self.figures_canvas.update_idletasks()

    def _clear_results(self) -> None:
        for w in list(self._result_widgets):
            self._delete_result_section(w, keep_one=False)
        self._result_widgets.clear()

    def _clear_figures(self) -> None:
        for w in list(self._figure_widgets):
            self._delete_figure(w, keep_one=False)
        self._figure_widgets.clear()

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)

    @staticmethod
    def _get_text(widget: tk.Text) -> str:
        return widget.get("1.0", tk.END).rstrip("\n")

    # ---------------- Dynamic blocks: Results ----------------

    
    def _add_result_section(
        self,
        section_title: str = "",
        section_text: str = "",
        insert_index: int | None = None,
        focus_subtitle: bool = False,
    ) -> None:
        """
        Add a Results section UI block.

        If insert_index is provided, the new block is inserted BEFORE the block at that index.
        """
        frame = ttk.Frame(self.results_frame, padding=(0, 8, 0, 8))

        # Insert into UI + list
        if insert_index is None or insert_index >= len(self._result_widgets):
            frame.pack(fill=tk.X, expand=True)
            list_index = len(self._result_widgets)
        else:
            before_frame = self._result_widgets[insert_index].frame
            frame.pack(fill=tk.X, expand=True, before=before_frame)
            list_index = insert_index

        # Two-column layout: left content, right buttons
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(0, weight=0)
        left.columnconfigure(1, weight=1)

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="n", padx=(10, 0))

        # Subtitle row
        title_var = tk.StringVar(value=section_title)
        ttk.Label(left, text="Subtitle:").grid(row=0, column=0, sticky="w")
        title_entry = ttk.Entry(left, textvariable=title_var)
        title_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # Text box (taller)
        txt = tk.Text(left, wrap="word", height=9)
        scr = ttk.Scrollbar(left, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scr.set)

        txt.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        scr.grid(row=1, column=2, sticky="ns", pady=(6, 0), padx=(6, 0))
        left.columnconfigure(2, weight=0)
        left.rowconfigure(1, weight=1)

        self._set_text(txt, section_text)

        w = _ResultSectionWidgets(frame=frame, title_var=title_var, title_entry=title_entry, text=txt)

        # Insert into list at computed position
        self._result_widgets.insert(list_index, w)

        # Button commands compute index dynamically (stable after insert/delete)
        ttk.Button(
            right,
            text="Add above",
            command=lambda w=w: self._add_result_section(
                insert_index=self._result_widgets.index(w),
                focus_subtitle=True,
            ),
        ).pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            right,
            text="Delete",
            command=lambda w=w: self._delete_result_section(w),
        ).pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            right,
            text="Add below",
            command=lambda w=w: self._add_result_section(
                insert_index=self._result_widgets.index(w) + 1,
                focus_subtitle=True,
            ),
        ).pack(fill=tk.X)

        # update scroll region
        self.results_canvas.update_idletasks()
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

        if focus_subtitle:
            try:
                title_entry.focus_set()
            except Exception:
                pass

    def _delete_result_section(self, w: _ResultSectionWidgets, keep_one: bool = True) -> None:
        if w in self._result_widgets:
            self._result_widgets.remove(w)
        w.frame.destroy()

        # Keep at least one empty section so the tab isn't "blank"
        if keep_one and not self._result_widgets:
            self._add_result_section()

        self.results_canvas.update_idletasks()
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

    # ---------------- Dynamic blocks: Figures ----------------

    
    def _add_figure(
        self,
        figure_number: str = "",
        caption: str = "",
        insert_index: int | None = None,
        focus_number: bool = False,
    ) -> None:
        """
        Add a Figure UI block.

        If insert_index is provided, the new block is inserted BEFORE the block at that index.
        """
        frame = ttk.Frame(self.figures_frame, padding=(0, 8, 0, 8))

        if insert_index is None or insert_index >= len(self._figure_widgets):
            frame.pack(fill=tk.X, expand=True)
            list_index = len(self._figure_widgets)
        else:
            before_frame = self._figure_widgets[insert_index].frame
            frame.pack(fill=tk.X, expand=True, before=before_frame)
            list_index = insert_index

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(0, weight=0)
        left.columnconfigure(1, weight=1)

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="n", padx=(10, 0))

        num_var = tk.StringVar(value=figure_number)
        ttk.Label(left, text="Figure number:").grid(row=0, column=0, sticky="w")
        num_entry = ttk.Entry(left, textvariable=num_var, width=10)
        num_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(left, text="Caption:").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        cap = tk.Text(left, wrap="word", height=8)
        scr = ttk.Scrollbar(left, orient=tk.VERTICAL, command=cap.yview)
        cap.configure(yscrollcommand=scr.set)

        cap.grid(row=1, column=1, sticky="nsew", pady=(6, 0))
        scr.grid(row=1, column=2, sticky="ns", pady=(6, 0), padx=(6, 0))
        left.columnconfigure(2, weight=0)
        left.rowconfigure(1, weight=1)

        self._set_text(cap, caption)

        w = _FigureWidgets(frame=frame, number_var=num_var, number_entry=num_entry, caption=cap)
        self._figure_widgets.insert(list_index, w)

        ttk.Button(
            right,
            text="Add above",
            command=lambda w=w: self._add_figure(
                insert_index=self._figure_widgets.index(w),
                focus_number=True,
            ),
        ).pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            right,
            text="Delete",
            command=lambda w=w: self._delete_figure(w),
        ).pack(fill=tk.X, pady=(0, 6))

        ttk.Button(
            right,
            text="Add below",
            command=lambda w=w: self._add_figure(
                insert_index=self._figure_widgets.index(w) + 1,
                focus_number=True,
            ),
        ).pack(fill=tk.X)

        self.figures_canvas.update_idletasks()
        self.figures_canvas.configure(scrollregion=self.figures_canvas.bbox("all"))

        if focus_number:
            try:
                num_entry.focus_set()
            except Exception:
                pass

    def _delete_figure(self, w: _FigureWidgets, keep_one: bool = True) -> None:
        if w in self._figure_widgets:
            self._figure_widgets.remove(w)
        w.frame.destroy()
        self.figures_canvas.update_idletasks()
        self.figures_canvas.configure(scrollregion=self.figures_canvas.bbox("all"))

    # ---------------- Actions ----------------

    def _on_open_pdf(self) -> None:
        if not self.pdf_path:
            messagebox.showinfo("Open PDF", "PDF path is not available.")
            return
        open_file(self.pdf_path)

    def _on_extract_text_again(self) -> None:
        messagebox.showinfo("Not implemented", "Re-extraction for the current PDF is not implemented yet.")

    def _copy_json_from_disk(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Copy JSON", f"JSON not found:\n{self.json_path}")
            return
        try:
            obj = json.loads(self.json_path.read_text(encoding="utf-8"))
            txt = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Copy JSON", f"{type(e).__name__}: {e}")
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        messagebox.showinfo("Copy JSON", "Saved JSON has been copied to clipboard.")

    def _copy_text_from_disk(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Copy text", f"JSON not found:\n{self.json_path}")
            return
        try:
            obj = json.loads(self.json_path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Copy text", f"{type(e).__name__}: {e}")
            return

        parts: list[str] = []
        title = str(obj.get("title", "") or "")
        year = obj.get("year", "")
        year_s = "" if year is None else str(year)
        if title:
            parts.append(f"Title: {title}")
        if year_s:
            parts.append(f"Year: {year_s}")

        def add_block(label: str, text: str) -> None:
            t = (text or "").strip()
            if t:
                parts.append(f"\n{label}\n{t}")

        add_block("Introduction", str(obj.get("introduction", "") or ""))
        add_block("Methods", str(obj.get("methods", "") or ""))
        add_block("Discussion", str(obj.get("discussion", "") or ""))

        results = obj.get("results") or []
        if isinstance(results, list) and results:
            parts.append("\nResults")
            for item in results:
                if not isinstance(item, dict):
                    continue
                sub = str(item.get("section_title", "") or "").strip()
                txt = str(item.get("section_text", "") or "").strip()
                if sub or txt:
                    if sub:
                        parts.append(f"\n{sub}\n{txt}".rstrip())
                    else:
                        parts.append(f"\n{txt}".rstrip())

        figures = obj.get("figures") or []
        if isinstance(figures, list) and figures:
            parts.append("\nFigures")
            for item in figures:
                if not isinstance(item, dict):
                    continue
                num = item.get("figure_number", "")
                cap = str(item.get("caption", "") or "").strip()
                if num is None:
                    num = ""
                num_s = str(num).strip()
                if num_s or cap:
                    header = f"Figure {num_s}".strip()
                    if header:
                        parts.append(f"\n{header}\n{cap}".rstrip())
                    else:
                        parts.append(f"\n{cap}".rstrip())

        text_out = "\n".join(parts).strip() + "\n"
        self.clipboard_clear()
        self.clipboard_append(text_out)
        messagebox.showinfo("Copy text", "Saved text has been copied to clipboard.")

    def _on_cancel(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _on_save(self) -> None:
        self._save_json(close_after=False)

    def _on_save_and_close(self) -> None:
        self._save_json(close_after=True)

    def _save_json(self, close_after: bool) -> bool:
        """Validate, write JSON to disk. Returns True if saved."""
        try:
            new_data = deepcopy(self.original_data)

            # ---- Required top-level fields ----
            title = self.title_var.get().strip()
            if not title:
                raise ValueError("Title must not be empty.")
            new_data["title"] = title

            year_raw = self.year_var.get().strip()
            if "year" in self.original_data and isinstance(self.original_data.get("year"), int):
                if year_raw == "":
                    raise ValueError("Year must not be empty.")
                new_data["year"] = int(year_raw)
            else:
                new_data["year"] = year_raw

            introduction = self._get_text(self.intro_text).strip()
            if not introduction:
                raise ValueError("Introduction must not be empty.")
            new_data["introduction"] = introduction

            methods = self._get_text(self.methods_text).strip()
            if not methods:
                raise ValueError("Methods must not be empty.")
            new_data["methods"] = methods

            discussion = self._get_text(self.discussion_text).strip()
            if not discussion:
                raise ValueError("Discussion must not be empty.")
            new_data["discussion"] = discussion

            # ---- Results: validate sections ----
            empty_text_sections: list[str] = []
            missing_title_sections: list[str] = []
            results: list[dict] = []

            for i, w in enumerate(self._result_widgets, start=1):
                subtitle = w.title_var.get().strip()
                body = self._get_text(w.text).strip()

                if body and not subtitle:
                    missing_title_sections.append(f"Results section #{i}")
                    continue

                if subtitle and not body:
                    empty_text_sections.append(subtitle)
                    continue

                if not subtitle and not body:
                    continue

                results.append({"section_title": subtitle, "section_text": body})

            if missing_title_sections:
                raise ValueError(
                    "Section titles cannot be empty. Please fill the titles for:\n"
                    + "\n".join(f"- {x}" for x in missing_title_sections)
                )


            # ---- Figures: validate items ----
            empty_caption_figs: list[str] = []
            missing_number_figs: list[str] = []
            figures: list[dict] = []

            for i, w in enumerate(self._figure_widgets, start=1):
                num_raw = w.number_var.get().strip()
                caption = self._get_text(w.caption).strip()

                if caption and not num_raw:
                    missing_number_figs.append(f"Figure #{i}")
                    continue

                if num_raw and not caption:
                    empty_caption_figs.append(f"Figure {num_raw}")
                    continue

                if not num_raw and not caption:
                    continue

                try:
                    num_int = int(num_raw)
                except Exception:
                    raise ValueError(f"Figure number must be int (got: {num_raw!r}).")

                figures.append({"figure_number": num_int, "caption": caption})

            if missing_number_figs:
                raise ValueError(
                    "Figure numbers cannot be empty. Please fill the numbers for:\n"
                    + "\n".join(f"- {x}" for x in missing_number_figs)
                )


            # ---- Warning: drop sections without text/caption ----
            warn_lines: list[str] = []
            if empty_text_sections:
                warn_lines.append("Results sections without text will not be saved:")
                warn_lines.extend([f"- {s}" for s in empty_text_sections])
            if empty_caption_figs:
                warn_lines.append("Figures without caption will not be saved:")
                warn_lines.extend([f"- {s}" for s in empty_caption_figs])

            if warn_lines:
                warn_lines.append("")
                warn_lines.append("Do you want to continue and save the changes?")
                ok = messagebox.askyesno("Warning", "\n".join(warn_lines))
                if not ok:
                    return False

            new_data["results"] = results
            new_data["figures"] = figures

            self.json_path.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")

            if close_after:
                self._on_cancel()
            return True
        except Exception as e:
            messagebox.showerror("Save error", f"{type(e).__name__}: {e}")
            return False
