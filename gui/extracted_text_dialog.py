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
        ttk.Button(header, text="Open PDF", command=self._on_open_pdf).grid(row=0, column=1, sticky="e")

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

        # Scrollable tabs for Results/Figures
        self.tab_results = ttk.Frame(self.nb, padding=0)
        self.tab_figures = ttk.Frame(self.nb, padding=0)

        self.nb.add(self.tab_intro, text="Introduction")
        self.nb.add(self.tab_methods, text="Methods")
        self.nb.add(self.tab_results, text="Results")
        self.nb.add(self.tab_figures, text="Figures")

        self.intro_text = self._text_area(self.tab_intro)
        self.methods_text = self._text_area(self.tab_methods)

        # Results: scrollable
        self.results_canvas, self.results_inner = self._make_scrollable_tab(self.tab_results)
        self._build_results_inner(self.results_inner)

        # Figures: scrollable
        self.figures_canvas, self.figures_inner = self._make_scrollable_tab(self.tab_figures)
        self._build_figures_inner(self.figures_inner)

        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        ttk.Button(bottom, text="Cancel", command=self._on_cancel).grid(row=0, column=2, sticky="e")
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

        ttk.Button(parent, text="Add section", command=self._add_result_section).pack(anchor="w", pady=(8, 0))

    def _build_figures_inner(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Figures:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        self.figures_frame = ttk.Frame(parent)
        self.figures_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        ttk.Button(parent, text="Add figure", command=self._add_figure).pack(anchor="w", pady=(8, 0))

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

        self._clear_results()
        results = self.original_data.get("results") or []
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    self._add_result_section(
                        section_title=str(item.get("section_title", "") or ""),
                        section_text=str(item.get("section_text", "") or ""),
                    )

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

        # ensure scrollregion updated
        self.results_canvas.update_idletasks()
        self.figures_canvas.update_idletasks()

    def _clear_results(self) -> None:
        for w in list(self._result_widgets):
            self._delete_result_section(w)

    def _clear_figures(self) -> None:
        for w in list(self._figure_widgets):
            self._delete_figure(w)

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)

    @staticmethod
    def _get_text(widget: tk.Text) -> str:
        return widget.get("1.0", tk.END).rstrip("\n")

    # ---------------- Dynamic blocks: Results ----------------

    def _add_result_section(self, section_title: str = "", section_text: str = "") -> None:
        frame = ttk.Frame(self.results_frame, padding=(0, 8, 0, 8))
        frame.pack(fill=tk.X, expand=True)

        header = ttk.Frame(frame)
        header.pack(fill=tk.X)

        ttk.Label(header, text="Section", font=("TkDefaultFont", 9, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Delete", command=lambda: self._delete_result_section(w)).pack(side=tk.RIGHT)

        title_var = tk.StringVar(value=section_title)
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text="Section title:").pack(side=tk.LEFT)
        title_entry = ttk.Entry(row, textvariable=title_var)
        title_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        box = ttk.Frame(frame)
        box.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        text_widget = tk.Text(box, height=6, wrap="word")
        scr = ttk.Scrollbar(box, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scr.set)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.RIGHT, fill=tk.Y)

        self._set_text(text_widget, section_text)

        w = _ResultSectionWidgets(frame=frame, title_var=title_var, title_entry=title_entry, text=text_widget)
        for child in header.winfo_children():
            if isinstance(child, ttk.Button) and child.cget("text") == "Delete":
                child.configure(command=lambda w=w: self._delete_result_section(w))
                break

        self._result_widgets.append(w)

        # update scroll region
        self.results_canvas.update_idletasks()
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

    def _delete_result_section(self, w: _ResultSectionWidgets) -> None:
        if w in self._result_widgets:
            self._result_widgets.remove(w)
        w.frame.destroy()
        self.results_canvas.update_idletasks()
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

    # ---------------- Dynamic blocks: Figures ----------------

    def _add_figure(self, figure_number: str = "", caption: str = "") -> None:
        frame = ttk.Frame(self.figures_frame, padding=(0, 8, 0, 8))
        frame.pack(fill=tk.X, expand=True)

        header = ttk.Frame(frame)
        header.pack(fill=tk.X)

        ttk.Button(header, text="Delete", command=lambda: self._delete_figure(w)).pack(side=tk.RIGHT)

        num_var = tk.StringVar(value=figure_number)
        row = ttk.Frame(frame)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Figure number:").pack(side=tk.LEFT)
        num_entry = ttk.Entry(row, textvariable=num_var, width=10)
        num_entry.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, text="Caption:").pack(anchor="w", pady=(6, 0))

        box = ttk.Frame(frame)
        box.pack(fill=tk.BOTH, expand=True)

        caption_widget = tk.Text(box, height=5, wrap="word")
        scr = ttk.Scrollbar(box, orient=tk.VERTICAL, command=caption_widget.yview)
        caption_widget.configure(yscrollcommand=scr.set)
        caption_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.RIGHT, fill=tk.Y)

        self._set_text(caption_widget, caption)

        w = _FigureWidgets(frame=frame, number_var=num_var, number_entry=num_entry, caption=caption_widget)
        for child in header.winfo_children():
            if isinstance(child, ttk.Button) and child.cget("text") == "Delete":
                child.configure(command=lambda w=w: self._delete_figure(w))
                break

        self._figure_widgets.append(w)

        self.figures_canvas.update_idletasks()
        self.figures_canvas.configure(scrollregion=self.figures_canvas.bbox("all"))

    def _delete_figure(self, w: _FigureWidgets) -> None:
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

    def _on_cancel(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _on_save(self) -> None:
        try:
            new_data = deepcopy(self.original_data)

            new_data["title"] = self.title_var.get()

            year_raw = self.year_var.get().strip()
            if "year" in self.original_data and isinstance(self.original_data.get("year"), int):
                if year_raw == "":
                    raise ValueError("Year must not be empty.")
                new_data["year"] = int(year_raw)
            else:
                new_data["year"] = year_raw

            new_data["introduction"] = self._get_text(self.intro_text)
            new_data["methods"] = self._get_text(self.methods_text)

            results: list[dict] = []
            for w in self._result_widgets:
                results.append(
                    {
                        "section_title": w.title_var.get(),
                        "section_text": self._get_text(w.text),
                    }
                )
            new_data["results"] = results

            figures: list[dict] = []
            for w in self._figure_widgets:
                num_raw = w.number_var.get().strip()
                if num_raw == "":
                    raise ValueError("Figure number must not be empty.")
                try:
                    num_int = int(num_raw)
                except Exception:
                    raise ValueError(f"Figure number must be int (got: {num_raw!r}).")
                figures.append(
                    {
                        "figure_number": num_int,
                        "caption": self._get_text(w.caption),
                    }
                )
            new_data["figures"] = figures

            self.json_path.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._on_cancel()
        except Exception as e:
            messagebox.showerror("Save error", f"{type(e).__name__}: {e}")
