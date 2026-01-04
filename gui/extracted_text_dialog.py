from __future__ import annotations

import json
from copy import deepcopy
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, filedialog
from tkinter import ttk
from typing import Any, Callable

from gui.file_ops import open_file
from docx_utils.docx_writer import export_extracted_text_to_docx
from gui.find_replace_dialog import FindReplaceDialog, FindReplaceState


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
    """Модальный редактор JSON.

    Вкладки:
    - Introduction
    - Methods
    - Results (общая прокрутка)
    - Figures (общая прокрутка)
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        json_path: Path,
        pdf_path: Path | None = None,
        parse_pdf_func: Callable[[Path], dict[str, Any]] | None = None,
        on_saved_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Extracted Text")
        self.geometry("980x720")

        # self.transient(master)
        self.grab_set()

        # Optional: hint WM that this is a normal window (helps on some WMs)
        try:
            self.wm_attributes("-type", "normal")
        except tk.TclError:
            pass

        self.lift()

        self.json_path = json_path
        self.pdf_path = pdf_path
        self.parse_pdf_func = parse_pdf_func

        self._on_saved_close = on_saved_close
        self._notify_parent = master

        self.original_data: dict = {}
        self._result_widgets: list[_ResultSectionWidgets] = []
        self._figure_widgets: list[_FigureWidgets] = []

        # Export/copy filters (default OFF)
        self.include_methods_var = tk.BooleanVar(value=False)
        self.include_figures_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_json()
        self._install_find_shortcut()

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
        ttk.Button(actions, text="Export to docx", command=self._export_to_docx).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Open PDF", command=self._on_open_pdf).pack(side=tk.LEFT)

        options = ttk.Frame(header)
        options.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        options.columnconfigure(0, weight=1)

        # "Пружина" слева, чтобы чекбоксы уехали вправо
        ttk.Label(options, text="").pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Checkbutton(
            options,
            text="Include Figure Captions",
            variable=self.include_figures_var,
        ).pack(side=tk.RIGHT)

        ttk.Checkbutton(
            options,
            text="Include Methods",
            variable=self.include_methods_var,
        ).pack(side=tk.RIGHT, padx=(0, 16))

        common = ttk.Frame(root)
        common.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        common.columnconfigure(1, weight=1)

        self.title_var = tk.StringVar()
        self.year_var = tk.StringVar()

        ttk.Label(common, text="Title:").grid(row=0, column=0, sticky="w")
        self.title_entry = ttk.Entry(common, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(common, text="Year:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.year_entry = ttk.Entry(common, textvariable=self.year_var, width=12)
        self.year_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

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

        # Context menu + Ctrl+M
        self._install_text_context_menu(self.intro_text)
        self._install_text_context_menu(self.methods_text)
        self._install_text_context_menu(self.discussion_text)

        # Results: scrollable
        self.results_canvas, self.results_inner = self._make_scrollable_tab(self.tab_results)
        self._build_results_inner(self.results_inner)

        # Figures: scrollable
        self.figures_canvas, self.figures_inner = self._make_scrollable_tab(self.tab_figures)
        self._build_figures_inner(self.figures_inner)

        self._setup_mousewheel_routing()

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

        self._setup_paragraph_spacing(txt)
        return txt

        # ---------------- Context menu: text helpers ----------------

    def _install_text_context_menu(self, txt: tk.Text) -> None:
        """
        Adds right-click context menu to a tk.Text:
        - Remove new lines (in selection)
        Also binds Ctrl+M to the same action when this Text is focused.
        """
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="Remove new lines",
            command=lambda w=txt: self._remove_newlines_in_selection(w),
        )

        def _popup(event: tk.Event) -> str:
            # Show menu only for Text widgets
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
            return "break"

        # Right-click (Windows/Linux)
        txt.bind("<Button-3>", _popup, add=True)

        # macOS sometimes uses Button-2; harmless elsewhere
        txt.bind("<Button-2>", _popup, add=True)

        # Ctrl+M on this widget
        txt.bind("<Control-m>", lambda _e, w=txt: self._remove_newlines_in_selection(w), add=True)
        txt.bind("<Control-M>", lambda _e, w=txt: self._remove_newlines_in_selection(w), add=True)

    def _remove_newlines_in_selection(self, txt: tk.Text) -> str:
        """
        Remove all newline characters in the selected text fragment of the given Text widget.
        Replaces '\\n' with a single space to avoid accidental word concatenation.
        """
        try:
            start = txt.index("sel.first")
            end = txt.index("sel.last")
        except tk.TclError:
            # No selection
            return "break"

        selected = txt.get(start, end)
        if not selected:
            return "break"

        replaced = selected.replace("\n", " ")
        if replaced == selected:
            return "break"

        # Replace selection
        txt.delete(start, end)
        txt.insert(start, replaced)

        # Restore selection over the replaced text
        try:
            new_end = f"{start}+{len(replaced)}c"
            txt.tag_add("sel", start, new_end)
            txt.mark_set(tk.INSERT, new_end)
            txt.see(tk.INSERT)
        except Exception:
            pass

        return "break"

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

        return canvas, inner

    def _is_descendant(self, widget: tk.Widget | None, ancestor: tk.Widget) -> bool:
        """True if widget is ancestor itself or inside it."""
        w = widget
        while w is not None:
            if w == ancestor:
                return True
            w = w.master  # type: ignore[assignment]
        return False

    def _setup_mousewheel_routing(self) -> None:
        """
        Mouse wheel routing rules (Linux/Windows/macOS-friendly):
        - If pointer is over a tk.Text: scroll that Text.
        - Else if pointer is inside Results scrollable area: scroll Results canvas.
        - Else if pointer is inside Figures scrollable area: scroll Figures canvas.
        """
        if getattr(self, "_mousewheel_routing_installed", False):
            return
        self._mousewheel_routing_installed = True

        def _on_wheel(event: tk.Event) -> str | None:
            try:
                w = self.winfo_containing(event.x_root, event.y_root)
            except (KeyError, tk.TclError):
                return None

            if w is None:
                return None

            try:
                if w.winfo_toplevel() is not self:
                    return None
            except tk.TclError:
                return None

            if isinstance(w, tk.Text):
                if getattr(event, "num", None) == 4:
                    w.yview_scroll(-1, "units")
                elif getattr(event, "num", None) == 5:
                    w.yview_scroll(1, "units")
                else:
                    delta = getattr(event, "delta", 0)
                    if delta:
                        w.yview_scroll(int(-delta / 120), "units")
                return "break"

            target_canvas: tk.Canvas | None = None
            try:
                if hasattr(self, "results_canvas") and self._is_descendant(w, self.results_canvas):
                    target_canvas = self.results_canvas
                elif hasattr(self, "figures_canvas") and self._is_descendant(w, self.figures_canvas):
                    target_canvas = self.figures_canvas
            except Exception:
                target_canvas = None

            if target_canvas is None:
                return None

            if getattr(event, "num", None) == 4:
                target_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                target_canvas.yview_scroll(1, "units")
            else:
                delta = getattr(event, "delta", 0)
                if delta:
                    target_canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        self.bind_all("<Button-4>", _on_wheel)
        self.bind_all("<Button-5>", _on_wheel)
        self.bind_all("<MouseWheel>", _on_wheel)

    def _build_results_inner(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Result sections:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.results_frame = ttk.Frame(parent)
        self.results_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _build_figures_inner(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Figures:", font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT, anchor="w")
        ttk.Button(
            header,
            text="Order",
            command=self._order_figures_by_number,
        ).pack(side=tk.RIGHT, padx=(0, 12))

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

        if not self._figure_widgets:
            self._add_figure()

        self.results_canvas.update_idletasks()
        self.figures_canvas.update_idletasks()

    def _populate_from_data(self, data: dict[str, Any]) -> None:
        """Заполняет UI данными (без записи на диск)."""
        self.original_data = data

        self.title_var.set(str(data.get("title", "") or ""))
        self.year_var.set(str(data.get("year", "") or ""))

        self._set_text(self.intro_text, str(data.get("introduction", "") or ""))
        self._set_text(self.methods_text, str(data.get("methods", "") or ""))
        self._set_text(self.discussion_text, str(data.get("discussion", "") or ""))

        self._clear_results()
        results = data.get("results") or []
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict):
                    self._add_result_section(
                        section_title=str(item.get("section_title", "") or ""),
                        section_text=str(item.get("section_text", "") or ""),
                    )
        if not self._result_widgets:
            self._add_result_section()

        self._clear_figures()
        figures = data.get("figures") or []
        if isinstance(figures, list):
            for item in figures:
                if isinstance(item, dict):
                    num = item.get("figure_number", "")
                    self._add_figure(
                        figure_number=str(num if num is not None else ""),
                        caption=str(item.get("caption", "") or ""),
                    )
        if not self._figure_widgets:
            self._add_figure()

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

    # ---------------- Export/copy filters ----------------

    def _apply_export_filters(self, obj: dict[str, Any]) -> dict[str, Any]:
        """
        Returns a filtered COPY of obj according to UI tick-boxes:
        - If Include Methods is OFF -> methods becomes ""
        - If Include Figure Captions is OFF -> figures becomes []
        """
        out = deepcopy(obj)

        if not bool(self.include_methods_var.get()):
            if "methods" in out:
                out["methods"] = ""

        if not bool(self.include_figures_var.get()):
            if "figures" in out:
                out["figures"] = []

        return out

    # ---------------- Text formatting helpers ----------------

    def _setup_paragraph_spacing(self, widget: tk.Text) -> None:
        widget.tag_configure("blankline", spacing1=10, spacing3=10)

        def _on_modified(event: tk.Event) -> None:
            w: tk.Text = event.widget  # type: ignore[assignment]
            if not w.edit_modified():
                return
            self._retag_blank_lines(w)
            w.edit_modified(False)

        widget.bind("<<Modified>>", _on_modified, add=True)
        self._retag_blank_lines(widget)

    def _retag_blank_lines(self, widget: tk.Text) -> None:
        widget.tag_remove("blankline", "1.0", tk.END)
        end_idx = widget.index("end-1c")
        try:
            last_line = int(end_idx.split(".")[0])
        except Exception:
            return

        for line in range(1, last_line + 1):
            line_start = f"{line}.0"
            line_end = f"{line}.end"
            txt = widget.get(line_start, line_end)
            if txt.strip() == "":
                widget.tag_add("blankline", line_start, line_end)

    def _setup_figure_caption_behavior(self, widget: tk.Text) -> None:
        widget.tag_configure("blankline", spacing1=10, spacing3=10)

        def _on_modified(event: tk.Event) -> None:
            w: tk.Text = event.widget  # type: ignore[assignment]
            if not w.edit_modified():
                return

            raw = w.get("1.0", tk.END).rstrip("\n")
            normalized = " ".join(raw.replace("\n", " ").split())
            if normalized != raw.strip():
                try:
                    insert = w.index(tk.INSERT)
                except Exception:
                    insert = "1.0"
                w.delete("1.0", tk.END)
                w.insert("1.0", normalized)
                try:
                    w.mark_set(tk.INSERT, insert)
                except Exception:
                    pass

            self._retag_blank_lines(w)
            w.edit_modified(False)

        widget.bind("<<Modified>>", _on_modified, add=True)
        self._retag_blank_lines(widget)

    def _order_figures_by_number(self) -> None:
        def _key(w: _FigureWidgets) -> tuple[int, int]:
            raw = w.number_var.get().strip()
            try:
                return (0, int(raw))
            except Exception:
                return (1, 10**9)

        self._figure_widgets.sort(key=_key)

        for w in self._figure_widgets:
            w.frame.pack_forget()
        for w in self._figure_widgets:
            w.frame.pack(fill=tk.X, expand=True)

        self.figures_canvas.update_idletasks()
        self.figures_canvas.configure(scrollregion=self.figures_canvas.bbox("all"))

    # ---------------- Dynamic blocks: Results ----------------

    def _add_result_section(
        self,
        section_title: str = "",
        section_text: str = "",
        insert_index: int | None = None,
        focus_subtitle: bool = False,
    ) -> None:
        frame = ttk.Frame(self.results_frame, padding=(0, 8, 0, 8))

        if insert_index is None or insert_index >= len(self._result_widgets):
            frame.pack(fill=tk.X, expand=True)
            list_index = len(self._result_widgets)
        else:
            before_frame = self._result_widgets[insert_index].frame
            frame.pack(fill=tk.X, expand=True, before=before_frame)
            list_index = insert_index

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(0, weight=0)
        left.columnconfigure(1, weight=1)

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky="n", padx=(10, 12))

        title_var = tk.StringVar(value=section_title)
        ttk.Label(left, text="Subtitle:").grid(row=0, column=0, sticky="w")
        title_entry = ttk.Entry(left, textvariable=title_var)
        title_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        txt = tk.Text(left, wrap="word", height=9)
        scr = ttk.Scrollbar(left, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscrollcommand=scr.set)

        txt.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        scr.grid(row=1, column=2, sticky="ns", pady=(6, 0), padx=(6, 0))
        left.columnconfigure(2, weight=0)
        left.rowconfigure(1, weight=1)

        self._set_text(txt, section_text)
        self._setup_paragraph_spacing(txt)
        self._install_text_context_menu(txt)

        w = _ResultSectionWidgets(frame=frame, title_var=title_var, title_entry=title_entry, text=txt)
        self._result_widgets.insert(list_index, w)

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
        right.grid(row=0, column=1, sticky="n", padx=(10, 12))

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
        self._setup_figure_caption_behavior(cap)
        self._install_text_context_menu(cap)

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
        if not self.pdf_path:
            messagebox.showinfo("Extract text again", "PDF path is not available.")
            return

        if not self.pdf_path.exists():
            messagebox.showerror("Extract text again", f"PDF not found:\n{self.pdf_path}")
            return

        if not self.parse_pdf_func:
            messagebox.showerror(
                "Extract text again",
                "Parsing function is not available (parse_pdf_func is None).",
            )
            return

        ok = messagebox.askyesno(
            "Extract text again",
            "Re-extract content from the PDF?",
            icon="warning"
        )
        if not ok:
            return
        try:
            data = self.parse_pdf_func(self.pdf_path)
            if not isinstance(data, dict):
                raise TypeError("parse_pdf_func must return dict")
            self._populate_from_data(data)
        except Exception as e:
            messagebox.showerror("Extract text again", f"{type(e).__name__}: {e}")

    def _copy_json_from_disk(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Copy JSON", f"JSON not found:\n{self.json_path}")
            return
        try:
            obj = json.loads(self.json_path.read_text(encoding="utf-8"))
            obj = self._apply_export_filters(obj)
            txt = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Copy JSON", f"{type(e).__name__}: {e}")
            return
        self.clipboard_clear()
        self.clipboard_append(txt)

    def _export_to_docx(self) -> None:
        try:
            raw = self.json_path.read_text(encoding="utf-8")
            obj = json.loads(raw)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read JSON:\n{e}")
            return

        obj = self._apply_export_filters(obj)

        default_name = (str(obj.get("title") or "article").strip() or "article")
        default_name = "".join(ch if ch.isalnum() or ch in " _-." else "_" for ch in default_name)[:80]

        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export to docx",
            defaultextension=".docx",
            initialfile=f"{default_name}.docx",
            filetypes=[("Word document", "*.docx")],
        )
        if not path:
            return

        try:
            export_extracted_text_to_docx(
                docx_path=Path(path),
                article=obj,
                source_path=str(self.pdf_path or self.json_path),
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export docx:\n{e}")
            return


    def _copy_text_from_disk(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Copy text", f"JSON not found:\n{self.json_path}")
            return
        try:
            obj = json.loads(self.json_path.read_text(encoding="utf-8"))
            obj = self._apply_export_filters(obj)
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

        # Methods included only if checkbox ON
        if bool(self.include_methods_var.get()):
            add_block("Methods", str(obj.get("methods", "") or ""))

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

        add_block("Discussion", str(obj.get("discussion", "") or ""))

        # Figures included only if checkbox ON
        if bool(self.include_figures_var.get()):
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
        try:
            new_data = deepcopy(self.original_data)

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
            new_data["methods"] = methods

            discussion = self._get_text(self.discussion_text).strip()
            if not discussion:
                raise ValueError("Discussion must not be empty.")
            new_data["discussion"] = discussion

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
                cb = getattr(self, "_on_saved_close", None)
                if cb is not None:
                    try:
                        parent = getattr(self, "_notify_parent", None) or getattr(self, "master", None)
                        if parent is not None and hasattr(parent, "after"):
                            parent.after(0, cb)   # важно: планируем на parent, не на закрываемом Toplevel
                        else:
                            cb()
                    except Exception:
                        try:
                            cb()
                        except Exception:
                            pass
                self._on_cancel()
            return True

        except Exception as e:
            messagebox.showerror("Save error", f"{type(e).__name__}: {e}")
            return False

    # ---------------- Find/Replace (Ctrl+F) ----------------

    def _install_find_shortcut(self) -> None:
        if getattr(self, "_find_shortcut_installed", False):
            return
        self._find_shortcut_installed = True

        # Ctrl+F (latin), Ctrl+А (russian layout key for F)
        def on_ctrl_keypress(event: tk.Event) -> str | None:
            keysym = (getattr(event, "keysym", "") or "")
            char = (getattr(event, "char", "") or "")
            if char in ("f", "F", "а", "А") or keysym in ("f", "F", "Cyrillic_a", "Cyrillic_A"):
                self._open_find_replace()
                return "break"
            return None

        # bind on toplevel: works even when focus is inside Text
        self.bind("<Control-KeyPress>", on_ctrl_keypress, add=True)

    def _open_find_replace(self) -> None:
        # lazy import to avoid circulars if needed
        from gui.find_replace_dialog import FindReplaceDialog, FindReplaceState

        dlg = getattr(self, "_find_dialog", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.lift()
                    return
            except Exception:
                pass

        if not hasattr(self, "_find_state"):
            self._find_state = FindReplaceState()

        self._find_dialog = FindReplaceDialog(self, provider=self, state=self._find_state)

    def _fr_targets(self) -> list[tk.Widget]:
        """
        IMPORTANT: returns ALL editable fields, not just current tab.
        """
        out: list[tk.Widget] = []

        # Title/Year
        if hasattr(self, "title_entry"):
            out.append(self.title_entry)
        if hasattr(self, "year_entry"):
            out.append(self.year_entry)

        # Main tabs
        out.append(self.intro_text)
        out.append(self.methods_text)
        out.append(self.discussion_text)

        # Results dynamic blocks
        for w in getattr(self, "_result_widgets", []):
            out.append(w.title_entry)
            out.append(w.text)

        # Figures dynamic blocks
        for w in getattr(self, "_figure_widgets", []):
            out.append(w.number_entry)
            out.append(w.caption)

        return out

    def _fr_current_target_index(self, targets: list[tk.Widget]) -> int:
        """
        If focus is in Find dialog, focus_get() is not one of targets.
        In that case, continue from last found target.
        """
        try:
            cur = self.focus_get()
        except Exception:
            cur = None

        if cur in targets:
            return targets.index(cur)

        # focus is elsewhere (e.g., Find dialog) -> continue from last found target
        idx = getattr(self, "_fr_last_target_idx", None)
        if isinstance(idx, int) and 0 <= idx < len(targets):
            return idx

        return 0

    def _fr_select_entry(self, ent: ttk.Entry, s: int, e: int) -> None:
        ent.focus_set()
        ent.selection_range(s, e)
        ent.icursor(e)

    def _fr_select_text(self, txt: tk.Text, s: str, e: str) -> None:
        txt.focus_set()
        txt.tag_remove("sel", "1.0", tk.END)
        txt.tag_add("sel", s, e)
        txt.mark_set(tk.INSERT, e)
        txt.see(s)

    def fr_find_next(self, query: str, *, match_case: bool) -> bool:
        targets = self._fr_targets()
        if not targets or not query:
            return False

        # detect whether focus is actually inside a target widget
        try:
            cur_focus = self.focus_get()
        except Exception:
            cur_focus = None
        focus_in_targets = cur_focus in targets

        start_i = self._fr_current_target_index(targets)

        def _find_in_widget(w: tk.Widget, *, from_cursor: bool) -> tuple[bool, int | str, int | str]:
            if isinstance(w, ttk.Entry):
                text = w.get()
                hay = text if match_case else text.lower()
                needle = query if match_case else query.lower()

                start = 0
                if from_cursor:
                    try:
                        if w.selection_present():
                            start = int(w.index("sel.last"))
                        else:
                            start = int(w.index(tk.INSERT))
                    except Exception:
                        start = 0

                pos = hay.find(needle, start)
                if pos < 0:
                    return (False, -1, -1)
                return (True, pos, pos + len(query))

            if isinstance(w, tk.Text):
                nocase = 0 if match_case else 1
                start_idx = "1.0"
                if from_cursor:
                    try:
                        start_idx = w.index("sel.last")
                    except Exception:
                        try:
                            start_idx = w.index(tk.INSERT)
                        except Exception:
                            start_idx = "1.0"

                idx = w.search(query, start_idx, stopindex="end-1c", nocase=nocase)
                if not idx:
                    return (False, "", "")
                end = f"{idx}+{len(query)}c"
                return (True, idx, end)

            return (False, -1, -1)

        # PHASE 1 (no wrap inside widget):
        # On the first checked widget we MUST search "from cursor/selection" even if focus is in Find dialog.
        for step in range(len(targets)):
            i = start_i + step
            if i >= len(targets):
                break
            w = targets[i]

            from_cursor = (step == 0) and (focus_in_targets or hasattr(self, "_fr_last_target_idx"))
            found, s, e = _find_in_widget(w, from_cursor=from_cursor)
            if found:
                if isinstance(w, ttk.Entry):
                    self._fr_select_entry(w, int(s), int(e))  # type: ignore[arg-type]
                else:
                    self._fr_select_text(w, str(s), str(e))  # type: ignore[arg-type]

                # remember where we are, so next click continues even if focus is in Find dialog
                self._fr_last_target_idx = i
                return True

        # PHASE 2 (wrap across targets):
        for i in range(0, start_i + 1):
            w = targets[i]
            found, s, e = _find_in_widget(w, from_cursor=False)
            if found:
                if isinstance(w, ttk.Entry):
                    self._fr_select_entry(w, int(s), int(e))  # type: ignore[arg-type]
                else:
                    self._fr_select_text(w, str(s), str(e))  # type: ignore[arg-type]
                self._fr_last_target_idx = i
                return True

        return False
    
    def fr_replace_current(self, query: str, replacement: str, *, match_case: bool) -> bool:
        """
        Replace current match (selection). If no suitable selection -> Find next, then replace.
        After replacing, moves to next match.
        """
        def _sel_matches(s: str) -> bool:
            return s == query if match_case else s.lower() == query.lower()

        def _replace_in_entry(ent: ttk.Entry) -> bool:
            try:
                if not ent.selection_present():
                    return False
                s = int(ent.index("sel.first"))
                e = int(ent.index("sel.last"))
                selected = ent.get()[s:e]
                if not _sel_matches(selected):
                    return False
                new_text = ent.get()[:s] + replacement + ent.get()[e:]
                ent.delete(0, tk.END)
                ent.insert(0, new_text)
                ent.selection_range(s, s + len(replacement))
                ent.icursor(s + len(replacement))
                return True
            except Exception:
                return False

        def _replace_in_text(txt: tk.Text) -> bool:
            try:
                s = txt.index("sel.first")
                e = txt.index("sel.last")
                selected = txt.get(s, e)
                if not _sel_matches(selected):
                    return False
                txt.delete(s, e)
                txt.insert(s, replacement)
                new_end = f"{s}+{len(replacement)}c"
                txt.tag_remove("sel", "1.0", tk.END)
                txt.tag_add("sel", s, new_end)
                txt.mark_set(tk.INSERT, new_end)
                txt.see(s)
                return True
            except tk.TclError:
                return False
            except Exception:
                return False

        # 1) Try replace current selection
        try:
            w = self.focus_get()
        except Exception:
            w = None

        replaced = False
        if isinstance(w, ttk.Entry):
            replaced = _replace_in_entry(w)
        elif isinstance(w, tk.Text):
            replaced = _replace_in_text(w)

        # 2) If nothing replaced -> find next and replace that
        if not replaced:
            if not self.fr_find_next(query, match_case=match_case):
                return False
            try:
                w = self.focus_get()
            except Exception:
                w = None
            if isinstance(w, ttk.Entry):
                replaced = _replace_in_entry(w)
            elif isinstance(w, tk.Text):
                replaced = _replace_in_text(w)

        # 3) move forward
        self.fr_find_next(query, match_case=match_case)
        return replaced


    def fr_replace_all(self, query: str, replacement: str, *, match_case: bool) -> int:
        targets = self._fr_targets()
        if not targets:
            return 0

        total = 0

        for w in targets:
            if isinstance(w, ttk.Entry):
                text = w.get()
                if not query:
                    continue
                if match_case:
                    cnt = text.count(query)
                    if cnt:
                        w.delete(0, tk.END)
                        w.insert(0, text.replace(query, replacement))
                        total += cnt
                else:
                    # case-insensitive replace (simple, non-regex)
                    low = text.lower()
                    needle = query.lower()
                    if needle not in low:
                        continue
                    # rebuild
                    out = []
                    i = 0
                    while True:
                        j = low.find(needle, i)
                        if j < 0:
                            out.append(text[i:])
                            break
                        out.append(text[i:j])
                        out.append(replacement)
                        i = j + len(query)
                        total += 1
                    new_text = "".join(out)
                    w.delete(0, tk.END)
                    w.insert(0, new_text)

            elif isinstance(w, tk.Text):
                # iterative search to keep tags/indices stable
                if not query:
                    continue
                start = "1.0"
                nocase = 0 if match_case else 1
                while True:
                    idx = w.search(query, start, stopindex="end-1c", nocase=nocase)
                    if not idx:
                        break
                    end = f"{idx}+{len(query)}c"
                    w.delete(idx, end)
                    w.insert(idx, replacement)
                    total += 1
                    start = f"{idx}+{len(replacement)}c"

        return total
