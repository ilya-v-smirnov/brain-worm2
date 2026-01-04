from __future__ import annotations

import json
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk
from typing import Any, Callable

from gui.file_ops import open_file  # :contentReference[oaicite:2]{index=2}
from gui.extracted_text_dialog import ExtractedTextDialog  # :contentReference[oaicite:3]{index=3}
from docx_utils.docx_writer import append_semi_manual_summary_to_docx

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from gui.db_gateway import DbGateway


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_prompts_from_config() -> dict[str, str]:
    """
    Load prompt templates from config/prompts.json.
    Returns dict: {prompt_key: template_string}
    If file is missing/broken, returns safe defaults and shows a clear warning.
    """
    config_path = Path(__file__).resolve().parents[1] / "config" / "prompts.json"

    defaults = {
        "introduction": "Write a concise summary in {LANG} of the Introduction. Focus on background, gap, and objective.",
        "methods": "Summarize in {LANG} the Methods focusing on experimental design, key materials, and core procedures.",
        "results": "Summarize in {LANG} this Results subsection: key findings, comparisons, and reported metrics. No speculation.",
        "discussion": "Summarize in {LANG} the Discussion: interpretation, implications, limitations, and main conclusion.",
        "key_points": "In {LANG}, produce 5–8 key bullet points capturing the paper’s main contributions and outcomes.",
        "figure_narrative": "In {LANG}, create a coherent narrative of the figures: what each figure shows and the overall story.",
    }

    if not config_path.exists():
        messagebox.showwarning("Prompts", f"prompts.json not found:\n{config_path}\n\nUsing built-in defaults.")
        return defaults

    try:
        txt = config_path.read_text(encoding="utf-8").strip()
        if not txt:
            messagebox.showwarning("Prompts", f"prompts.json is empty:\n{config_path}\n\nUsing built-in defaults.")
            return defaults

        raw = json.loads(txt)
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(v, dict) and isinstance(v.get("template"), str):
                out[k] = v["template"]
        missing = [k for k in defaults.keys() if k not in out]
        if missing:
            for k in missing:
                out[k] = defaults[k]
            messagebox.showwarning(
                "Prompts",
                "prompts.json is missing some keys; defaults were used for:\n" + ", ".join(missing),
            )
        return out

    except Exception as e:
        messagebox.showwarning(
            "Prompts",
            f"Failed to parse prompts.json:\n{config_path}\n\n{type(e).__name__}: {e}\n\nUsing built-in defaults.",
        )
        return defaults


def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)


def _mirrored_docx_path_from_pdf(pdf_path: Path) -> Path:
    """
    Пример:
      Article database/f1/f2/2025 Title.pdf
    -> PDF_summaries/f1/f2/2025 Title.docx

    Корень считается как папка-родитель каталога "Article database".
    """
    pdf_path = Path(pdf_path).resolve()
    parts = list(pdf_path.parts)

    try:
        idx = parts.index("Article database")
    except ValueError:
        # fallback: сохраняем в PDF_summaries рядом с pdf (лучше чем падать)
        return (pdf_path.parent / "PDF_summaries" / pdf_path.with_suffix(".docx").name).resolve()

    project_root = Path(*parts[:idx])  # .../<project_root>
    rel_under_articles = Path(*parts[idx + 1 :])  # f1/f2/2025 Title.pdf
    out = project_root / "PDF_summaries" / rel_under_articles
    return out.with_suffix(".docx")


def _set_text(widget: tk.Text, value: str, *, readonly: bool) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert("1.0", value or "")
    widget.configure(state="disabled" if readonly else "normal")


def _get_text(widget: tk.Text) -> str:
    return widget.get("1.0", tk.END).rstrip("\n")


def _make_text(
    parent: ttk.Frame,
    *,
    height: int,
    readonly: bool,
) -> tk.Text:
    """
    Text + vertical scrollbar. GRID only (no pack anywhere).
    Expects parent to be able to expand; we place the text box on row=1.
    """
    # Ensure parent grid can expand its text area
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    box = ttk.Frame(parent)
    box.grid(row=1, column=0, sticky="nsew")

    box.rowconfigure(0, weight=1)
    box.columnconfigure(0, weight=1)

    txt = tk.Text(box, wrap="word", height=height)
    scr = ttk.Scrollbar(box, orient=tk.VERTICAL, command=txt.yview)
    txt.configure(yscrollcommand=scr.set)

    txt.grid(row=0, column=0, sticky="nsew")
    scr.grid(row=0, column=1, sticky="ns")

    txt.configure(state="disabled" if readonly else "normal")
    return txt


def _make_labeled_block(parent: ttk.Frame, title: str) -> ttk.Frame:
    """
    Container with label at row=0, uses GRID only.
    """
    f = ttk.Frame(parent)
    f.columnconfigure(0, weight=1)
    ttk.Label(f, text=title).grid(row=0, column=0, sticky="w")
    return f


def _clipboard_set(window: tk.Toplevel, text: str) -> None:
    window.clipboard_clear()
    window.clipboard_append(text)


def _resolve_existing_docx_path(db_gateway, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        if p.is_absolute():
            return p
        # если есть gateway — он знает project_home
        if db_gateway is not None:
            return Path(db_gateway.resolve_path(rel_or_abs))
        return p.resolve()


_WORD_RE = re.compile(r"\b\w+\b", flags=re.UNICODE)

def _word_count(text: str) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def _set_green_border(txt: tk.Text, enabled: bool, *, thickness: int = 3) -> None:
    # Use the Text highlight border (works cross-platform)
    if enabled:
        txt.configure(highlightthickness=thickness, highlightbackground="green", highlightcolor="green")
    else:
        # Restore to a neutral default; avoid guessing system color too hard
        txt.configure(highlightthickness=1, highlightbackground="#d9d9d9", highlightcolor="#d9d9d9")


@dataclass
class _ResultRow:
    frame: ttk.Frame
    section_title: str
    extracted_text: tk.Text
    summary_text: tk.Text
    copy_btn: ttk.Button
    extracted_words_lbl: ttk.Label
    summary_words_lbl: ttk.Label


class SemiManualSummaryDialog(tk.Toplevel):
    """
    Semi-manual summary UI:
    - Extracted (read-only) aligned horizontally with Summary
    - Key points: no Extracted section
    - Language: EN/RU, updates prompts by replacing language token
    """

    LANG_MAP = {"EN": "English", "RU": "Russian"}

    def __init__(
        self,
        master: tk.Misc,
        *,
        json_path: Path,
        pdf_path: Path | None = None,
        parse_pdf_func: Callable[[Path], dict[str, Any]] | None = None,
        prompts: dict[str, str] | None = None,
        db_gateway: "DbGateway | None" = None,
        article_id: int | None = None,
        existing_summary_path: str | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Semi-Manual Summary Generation")
        self.geometry("1150x800")

        # Modal behavior: block Main Window interaction while this dialog is open
        # NOTE: Do NOT use transient(master) here — many Linux window managers treat
        # transient windows as dialogs and disable Maximize.
        self.resizable(True, True)
        self.lift()

        # Make modal only after the window becomes visible (WM then handles maximize normally)
        self.after(0, self._make_modal)

        try:
            self.focus_force()
        except Exception:
            pass

        self.json_path = json_path
        self.pdf_path = pdf_path
        self.parse_pdf_func = parse_pdf_func

        self.db_gateway = db_gateway
        self.article_id = article_id

        self.existing_summary_path = existing_summary_path

        self.data: dict[str, Any] = {}

        # base prompts with a language marker
        self.prompts_base = prompts or _load_prompts_from_config()

        # widgets
        self.title_lbl: ttk.Label
        self.year_lbl: ttk.Label

        self.lang_var = tk.StringVar(value="EN")

        self.nb: ttk.Notebook
        self.tab_intro: ttk.Frame
        self.tab_methods: ttk.Frame
        self.tab_results: ttk.Frame
        self.tab_discussion: ttk.Frame
        self.tab_keypoints: ttk.Frame
        self.tab_fig_narr: ttk.Frame

        # per-tab widgets
        self.intro_extracted: tk.Text
        self.intro_summary: tk.Text
        self.intro_prompt: tk.Text
        self.intro_ex_words: ttk.Label
        self.intro_sum_words: ttk.Label

        self.methods_extracted: tk.Text
        self.methods_summary: tk.Text
        self.methods_prompt: tk.Text
        self.methods_ex_words: ttk.Label
        self.methods_sum_words: ttk.Label

        self.disc_extracted: tk.Text
        self.disc_summary: tk.Text
        self.disc_prompt: tk.Text
        self.disc_ex_words: ttk.Label
        self.disc_sum_words: ttk.Label

        self.kp_summary: tk.Text
        self.kp_prompt: tk.Text

        self.figcap_extracted: tk.Text
        self.fignarr_summary: tk.Text
        self.fignarr_prompt: tk.Text
        self.fig_ex_words: ttk.Label
        self.fignarr_sum_words: ttk.Label

        self._last_copied_text: tk.Text | None = None

        # results
        self.results_canvas: tk.Canvas
        self.results_inner: ttk.Frame
        self.results_prompt: tk.Text
        self.results_sections_frame: ttk.Frame
        self._result_rows: list[_ResultRow] = []

        self._build_ui()
        self._setup_mousewheel_routing()
        self._load()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # Top header row
        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Semi-manual summary", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        actions = ttk.Frame(header)
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="Edit extracted text", command=self._on_edit_extracted_text).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(actions, text="Open PDF", command=self._on_open_pdf).pack(side=tk.LEFT)

        # Meta row: Title + Language, Year
        meta = ttk.Frame(root)
        meta.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        meta.columnconfigure(1, weight=1)

        ttk.Label(meta, text="Title:").grid(row=0, column=0, sticky="w")

        title_line = ttk.Frame(meta)
        title_line.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        title_line.columnconfigure(0, weight=1)

        self.title_lbl = ttk.Label(title_line, text="", anchor="w")
        self.title_lbl.grid(row=0, column=0, sticky="ew")

        ttk.Label(title_line, text="Language:").grid(row=0, column=1, sticky="e", padx=(12, 4))
        lang_cb = ttk.Combobox(
            title_line,
            textvariable=self.lang_var,
            values=["EN", "RU"],
            state="readonly",
            width=5,
        )
        lang_cb.grid(row=0, column=2, sticky="e")
        lang_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_language_to_prompts())

        ttk.Label(meta, text="Year:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.year_lbl = ttk.Label(meta, text="", anchor="w")
        self.year_lbl.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        # Notebook
        self.nb = ttk.Notebook(root)
        self.nb.grid(row=2, column=0, sticky="nsew")

        self.tab_intro = ttk.Frame(self.nb, padding=10)
        self.tab_methods = ttk.Frame(self.nb, padding=10)
        self.tab_results = ttk.Frame(self.nb, padding=0)
        self.tab_discussion = ttk.Frame(self.nb, padding=10)
        self.tab_keypoints = ttk.Frame(self.nb, padding=10)
        self.tab_fig_narr = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_intro, text="Introduction")
        self.nb.add(self.tab_methods, text="Methods")
        self.nb.add(self.tab_results, text="Results")
        self.nb.add(self.tab_discussion, text="Discussion")
        self.nb.add(self.tab_keypoints, text="Key Points")
        self.nb.add(self.tab_fig_narr, text="Figure narrative")

        # tabs with 2-column alignment (Extracted | Summary)
        self._build_two_col_tab(
            self.tab_intro,
            prompt_key="introduction",
            out_widgets=("intro_extracted", "intro_summary", "intro_prompt"),
        )
        self._build_two_col_tab(
            self.tab_methods,
            prompt_key="methods",
            out_widgets=("methods_extracted", "methods_summary", "methods_prompt"),
        )
        self._build_results_tab()
        self._build_two_col_tab(
            self.tab_discussion,
            prompt_key="discussion",
            out_widgets=("disc_extracted", "disc_summary", "disc_prompt"),
        )
        self._build_keypoints_tab()  # no Extracted
        self._build_figure_narrative_tab()

        # Bottom buttons
        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        ttk.Button(bottom, text="Cancel", command=self._on_cancel).grid(row=0, column=2, sticky="e")
        ttk.Button(bottom, text="Save Summary", command=self._on_save_summary).grid(
            row=0, column=1, sticky="e", padx=(0, 10)
        )

    def _build_two_col_tab(
        self,
        tab: ttk.Frame,
        *,
        prompt_key: str,
        out_widgets: tuple[str, str, str],
    ) -> None:
        """
        Layout (grid):
        Row 0: Extracted (left) | Summary (right)  -> stretches (weight=1)
        Row 1: Copy button under Extracted         -> fixed
        Row 2: Prompt (colspan=2)                  -> fixed, max 7 lines
        """
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=0)
        tab.rowconfigure(2, weight=0)

        # Row 0 containers
        ex_block = _make_labeled_block(tab, "Extracted")
        ex_block.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ex_block.rowconfigure(1, weight=1)
        ex_block.columnconfigure(0, weight=1)
        extracted = _make_text(ex_block, height=10, readonly=True)
        # Words counters (bottom-right under each text box)
        ex_words = ttk.Label(ex_block, text="Words: 0")
        ex_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        sum_block = _make_labeled_block(tab, "Summary")
        sum_block.grid(row=0, column=1, sticky="nsew")
        sum_block.rowconfigure(1, weight=1)
        sum_block.columnconfigure(0, weight=1)
        summary   = _make_text(sum_block, height=10, readonly=False)
        sum_words = ttk.Label(sum_block, text="Words: 0, 0%")
        sum_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        # Row 1: Copy
        btn_row = ttk.Frame(tab)
        btn_row.grid(row=1, column=0, sticky="w", pady=(8, 10))
        ttk.Button(
            btn_row,
            text="Copy",
            command=lambda k=prompt_key, ex=extracted: self._copy_prompt_plus_extracted(k, ex),
        ).pack(anchor="w")

        # Row 2: Prompt fixed height (<=7 lines)
        pr_block = _make_labeled_block(tab, "Prompt")
        pr_block.grid(row=2, column=0, columnspan=2, sticky="ew")
        # prompt should not steal vertical space; height set to 7
        prompt    = _make_text(pr_block, height=7, readonly=False)

        # Store word labels on the instance
        if out_widgets[0] == "intro_extracted":
            self.intro_ex_words = ex_words
            self.intro_sum_words = sum_words
        elif out_widgets[0] == "methods_extracted":
            self.methods_ex_words = ex_words
            self.methods_sum_words = sum_words
        elif out_widgets[0] == "disc_extracted":
            self.disc_ex_words = ex_words
            self.disc_sum_words = sum_words

        setattr(self, out_widgets[0], extracted)
        setattr(self, out_widgets[1], summary)
        setattr(self, out_widgets[2], prompt)

    def _build_keypoints_tab(self) -> None:
        """
        No Extracted here.
        Row 0: Summary (stretches)
        Row 1: Copy (fixed)
        Row 2: Prompt (fixed, max 7 lines)
        """
        tab = self.tab_keypoints
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=0)
        tab.rowconfigure(2, weight=0)

        sum_block = _make_labeled_block(tab, "Summary")
        sum_block.grid(row=0, column=0, sticky="nsew")
        sum_block.rowconfigure(1, weight=1)
        sum_block.columnconfigure(0, weight=1)
        self.kp_summary = _make_text(sum_block, height=10, readonly=False)

        btn_row = ttk.Frame(tab)
        btn_row.grid(row=1, column=0, sticky="w", pady=(8, 10))
        ttk.Button(
            btn_row,
            text="Copy",
            command=self._copy_keypoints_prompt_plus_sources,
        ).pack(anchor="w")

        pr_block = _make_labeled_block(tab, "Prompt")
        pr_block.grid(row=2, column=0, sticky="ew")
        self.kp_prompt  = _make_text(pr_block, height=7, readonly=False)

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

    def _build_results_tab(self) -> None:
        # scrollable results
        self.results_canvas, self.results_inner = self._make_scrollable_tab(self.tab_results)

        # IMPORTANT: results_inner must use GRID only
        self.results_inner.columnconfigure(0, weight=1)
        self.results_inner.rowconfigure(2, weight=1)  # sections take remaining vertical space

        # Row 0: Prompt (fixed height 7)
        pr_block = _make_labeled_block(self.results_inner, "Prompt")
        pr_block.grid(row=0, column=0, sticky="ew")
        self.results_prompt = _make_text(pr_block, height=7, readonly=False)

        # Row 1: label
        ttk.Label(self.results_inner, text="Results sections:").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )

        # Row 2: sections container (stretches)
        self.results_sections_frame = ttk.Frame(self.results_inner)
        self.results_sections_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

    def _build_figure_narrative_tab(self) -> None:
        tab = self.tab_fig_narr
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=0)
        tab.rowconfigure(2, weight=0)

        ex_block = _make_labeled_block(tab, "Extracted")
        ex_block.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ex_block.rowconfigure(1, weight=1)
        ex_block.columnconfigure(0, weight=1)
        self.figcap_extracted = _make_text(ex_block, height=10, readonly=True)
        self.fig_ex_words = ttk.Label(ex_block, text="Words: 0")
        self.fig_ex_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        sum_block = _make_labeled_block(tab, "Summary")
        sum_block.grid(row=0, column=1, sticky="nsew")
        sum_block.rowconfigure(1, weight=1)
        sum_block.columnconfigure(0, weight=1)
        self.fignarr_summary  = _make_text(sum_block, height=10, readonly=False)
        self.fignarr_sum_words = ttk.Label(sum_block, text="Words: 0, 0%")
        self.fignarr_sum_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        btn_row = ttk.Frame(tab)
        btn_row.grid(row=1, column=0, sticky="w", pady=(8, 10))
        ttk.Button(
            btn_row,
            text="Copy",
            command=self._copy_figure_narrative_prompt_plus_sources,
        ).pack(anchor="w")

        pr_block = _make_labeled_block(tab, "Prompt")
        pr_block.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.fignarr_prompt   = _make_text(pr_block, height=7, readonly=False)

    def _make_modal(self) -> None:
        # Ensure window is actually mapped before grabbing input
        try:
            self.wait_visibility()
        except Exception:
            pass

        try:
            self.grab_set()
        except Exception:
            pass

        try:
            self.focus_force()
        except Exception:
            pass

    # ---------------- Load data ----------------

    def _load(self) -> None:
        if not self.json_path.exists():
            messagebox.showerror("Error", f"JSON not found:\n{self.json_path}")
            self._on_cancel()
            return

        try:
            self.data = _read_json(self.json_path)
        except Exception as e:
            messagebox.showerror("Error", f"{type(e).__name__}: {e}")
            self._on_cancel()
            return

        self.title_lbl.configure(text=_safe_str(self.data.get("title", "")))
        self.year_lbl.configure(text=_safe_str(self.data.get("year", "")))

        _set_text(self.intro_extracted, _safe_str(self.data.get("introduction", "")), readonly=True)
        _set_text(self.methods_extracted, _safe_str(self.data.get("methods", "")), readonly=True)
        _set_text(self.disc_extracted, _safe_str(self.data.get("discussion", "")), readonly=True)

        # figures captions -> one extracted block
        captions: list[str] = []
        figures = self.data.get("figures") or []
        if isinstance(figures, list):
            for it in figures:
                if not isinstance(it, dict):
                    continue
                num = _safe_str(it.get("figure_number", "")).strip()
                cap = _safe_str(it.get("caption", "")).strip()
                if not (num or cap):
                    continue
                head = f"Figure {num}".strip()
                captions.append(f"{head}\n{cap}".strip() if (head and cap) else (cap or head))
        _set_text(self.figcap_extracted, "\n\n".join(captions).strip(), readonly=True)

        # clear summaries
        _set_text(self.intro_summary, "", readonly=False)
        _set_text(self.methods_summary, "", readonly=False)
        _set_text(self.disc_summary, "", readonly=False)
        _set_text(self.kp_summary, "", readonly=False)
        _set_text(self.fignarr_summary, "", readonly=False)

        # apply prompts with current language
        self._apply_language_to_prompts()

        # results
        self._clear_results_ui()
        results = self.data.get("results") or []
        if isinstance(results, list) and results:
            for it in results:
                if not isinstance(it, dict):
                    continue
                title = _safe_str(it.get("section_title", "")).strip()
                text = _safe_str(it.get("section_text", "")).strip()
                self._add_result_row(section_title=title, extracted=text)
        else:
            self._add_result_row(section_title="(no sections in JSON)", extracted="")

        self.results_canvas.update_idletasks()
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))

        # init counters + bind live updates
        self._refresh_all_word_counters()
        self._bind_word_counter_updates()
        # Ctrl+A / Ctrl+Ф for all prompt fields
        prompt_fields = [
            self.intro_prompt,
            self.methods_prompt,
            self.results_prompt,
            self.disc_prompt,
            self.kp_prompt,
            self.fignarr_prompt,
        ]

        for p in prompt_fields:
            try:
                self._bind_select_all_shortcuts(p)
            except Exception:
                pass


    def _apply_language_to_prompts(self) -> None:
        lang_code = self.lang_var.get().strip() or "EN"
        lang_word = self.LANG_MAP.get(lang_code, "English")

        def render(base: str) -> str:
            # Replace marker or any explicit "English/Russian" token.
            # 1) marker
            out = base.replace("{LANG}", lang_word)
            # 2) fallback: replace standalone words English/Russian in a "language:" context
            out = re.sub(r"\b(English|Russian)\b", lang_word, out)
            return out

        _set_text(self.intro_prompt, render(self.prompts_base["introduction"]), readonly=False)
        _set_text(self.methods_prompt, render(self.prompts_base["methods"]), readonly=False)
        _set_text(self.disc_prompt, render(self.prompts_base["discussion"]), readonly=False)
        _set_text(self.kp_prompt, render(self.prompts_base["key_points"]), readonly=False)
        _set_text(self.results_prompt, render(self.prompts_base["results"]), readonly=False)
        _set_text(self.fignarr_prompt, render(self.prompts_base["figure_narrative"]), readonly=False)

    def _clear_results_ui(self) -> None:
        for r in self._result_rows:
            r.frame.destroy()
        self._result_rows.clear()

    def _add_result_row(self, *, section_title: str, extracted: str, summary_init: str = "") -> None:
        f = ttk.Frame(self.results_sections_frame, padding=(0, 8, 0, 8))
        f.pack(fill=tk.X, expand=True)

        ttk.Label(f, text=section_title or "(untitled)").pack(anchor="w")

        grid = ttk.Frame(f)
        grid.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        ex_block = _make_labeled_block(grid, "Extracted")
        ex_block.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        extracted_txt = _make_text(ex_block, height=8, readonly=True)
        _set_text(extracted_txt, extracted, readonly=True)
        ex_words = ttk.Label(ex_block, text="Words: 0")
        ex_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        sum_block = _make_labeled_block(grid, "Summary")
        sum_block.grid(row=0, column=1, sticky="nsew")
        summary_txt = _make_text(sum_block, height=8, readonly=False)
        _set_text(summary_txt, summary_init or "", readonly=False)
        sum_words = ttk.Label(sum_block, text="Words: 0, 0%")
        sum_words.grid(row=2, column=0, sticky="e", pady=(4, 0))

        btn_row = ttk.Frame(f)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        copy_btn = ttk.Button(
            btn_row,
            text="Copy",
            command=lambda ex=extracted_txt: self._copy_results_prompt_plus_extracted(ex),
        )
        copy_btn.pack(anchor="w")

        self._result_rows.append(
            _ResultRow(
                frame=f,
                section_title=section_title,
                extracted_text=extracted_txt,
                summary_text=summary_txt,
                copy_btn=copy_btn,
                extracted_words_lbl=ex_words,
                summary_words_lbl=sum_words,
            )
        )


    # ---------------- Mouse wheel routing ----------------

    def _is_descendant(self, widget: tk.Widget | None, ancestor: tk.Widget) -> bool:
        w = widget
        while w is not None:
            if w == ancestor:
                return True
            w = w.master  # type: ignore[assignment]
        return False

    def _setup_mousewheel_routing(self) -> None:
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

    def _collect_results_summaries_text(self) -> str:
        parts: list[str] = []
        for r in self._result_rows:
            s = _get_text(r.summary_text).strip()
            if s:
                parts.append(s)
        return "\n\n".join(parts).strip()

    def _copy_keypoints_prompt_plus_sources(self) -> None:
        # Copy sources: all Results/Summary + Discussion/Summary
        prompt = _get_text(self.kp_prompt).strip()
        src_parts: list[str] = []

        res = self._collect_results_summaries_text()
        if res:
            src_parts.append(res)

        disc = _get_text(self.disc_summary).strip()
        if disc:
            src_parts.append(disc)

        src = "\n\n".join(src_parts).strip()
        payload = (prompt + "\n\n" + src).strip() + "\n"
        _clipboard_set(self, payload)

    def _copy_figure_narrative_prompt_plus_sources(self) -> None:
        # Copy sources: all Results/Summary + Figure narrative/Extracted
        prompt = _get_text(self.fignarr_prompt).strip()
        src_parts: list[str] = []

        res = self._collect_results_summaries_text()
        if res:
            src_parts.append(res)

        fig_ex = _get_text(self.figcap_extracted).strip()
        if fig_ex:
            src_parts.append(fig_ex)

        src = "\n\n".join(src_parts).strip()
        payload = (prompt + "\n\n" + src).strip() + "\n"
        _clipboard_set(self, payload)
        self._mark_last_copied(self.figcap_extracted)
        self._refresh_all_word_counters()

    def _update_pair_counters(
        self,
        extracted: tk.Text,
        summary: tk.Text,
        extracted_lbl: ttk.Label,
        summary_lbl: ttk.Label,
    ) -> None:
        ex_w = _word_count(_get_text(extracted).strip())
        sum_w = _word_count(_get_text(summary).strip())
        pct = int(round((sum_w / ex_w) * 100)) if ex_w > 0 else 0

        extracted_lbl.configure(text=f"Words: {ex_w}")
        summary_lbl.configure(text=f"Words: {sum_w}, {pct}%")

    def _refresh_all_word_counters(self) -> None:
        # Intro/Methods/Discussion
        self._update_pair_counters(self.intro_extracted, self.intro_summary, self.intro_ex_words, self.intro_sum_words)
        self._update_pair_counters(self.methods_extracted, self.methods_summary, self.methods_ex_words, self.methods_sum_words)
        self._update_pair_counters(self.disc_extracted, self.disc_summary, self.disc_ex_words, self.disc_sum_words)

        # Figure narrative (Extracted captions vs narrative summary)
        self._update_pair_counters(self.figcap_extracted, self.fignarr_summary, self.fig_ex_words, self.fignarr_sum_words)

        # Results rows
        for r in self._result_rows:
            self._update_pair_counters(r.extracted_text, r.summary_text, r.extracted_words_lbl, r.summary_words_lbl)

    def _bind_word_counter_updates(self) -> None:
        # Track changes in editable Summary fields
        def bind_text(t: tk.Text) -> None:
            t.bind("<<Modified>>", self._on_any_text_modified)
            self._bind_select_all_shortcuts(t)

        bind_text(self.intro_summary)
        bind_text(self.methods_summary)
        bind_text(self.disc_summary)
        bind_text(self.fignarr_summary)
        bind_text(self.kp_summary)

        for r in self._result_rows:
            bind_text(r.summary_text)

    def _on_any_text_modified(self, event: tk.Event) -> None:
        w = event.widget
        if isinstance(w, tk.Text):
            # reset modified flag to keep event firing
            try:
                w.edit_modified(False)
            except Exception:
                pass
        self._refresh_all_word_counters()

    def _mark_last_copied(self, txt: tk.Text) -> None:
        # remove previous highlight
        if self._last_copied_text is not None:
            try:
                _set_green_border(self._last_copied_text, False)
            except Exception:
                pass
        self._last_copied_text = txt
        try:
            _set_green_border(txt, True)
        except Exception:
            pass

    def _bind_select_all_shortcuts(self, t: tk.Text) -> None:
        """
        Ctrl+A or Ctrl+Ф (Russian layout), any case -> select all text in the widget.
        Works by binding to Control-KeyPress and checking keysym/char.
        """
        def on_ctrl_keypress(event: tk.Event) -> str | None:
            # On different platforms/layouts Tk may report Latin keysyms or Cyrillic keysyms,
            # and event.char may contain 'a'/'A' or 'ф'/'Ф'.
            keysym = (getattr(event, "keysym", "") or "")
            char = (getattr(event, "char", "") or "")

            keysym_l = keysym.lower()
            char_l = char.lower()

            is_select_all = (
                keysym_l == "a"
                or char_l == "a"
                or char_l == "ф"
                or keysym in ("Cyrillic_ef", "Cyrillic_EF")
                or keysym_l.endswith("_ef")  # extra tolerance for some tk builds
            )

            if is_select_all:
                try:
                    t.tag_add("sel", "1.0", "end-1c")
                    t.mark_set("insert", "1.0")
                    t.see("insert")
                except Exception:
                    pass
                return "break"
            return None

        # Bind only once per widget (idempotent enough)
        t.bind("<Control-KeyPress>", on_ctrl_keypress, add=True)


    # ---------------- Actions ----------------

    def _on_edit_extracted_text(self) -> None:
        ExtractedTextDialog(
            self,
            json_path=self.json_path,
            pdf_path=self.pdf_path,
            parse_pdf_func=self.parse_pdf_func,
            on_saved_close=self._on_extracted_saved_close,
        )


    def _on_extracted_saved_close(self) -> None:
        """Called when the dependent Extracted Text window saved and closed."""
        self._reload_extracted_fields(preserve_summaries=True)

    def _reload_extracted_fields(self, *, preserve_summaries: bool = True) -> None:
        """
        Reload only *extracted/source* fields (Title/Year + Extracted blocks) from JSON on disk.
        Keeps user-written Summary fields intact; for Results, attempts to preserve per-section summaries
        by matching on section_title.
        """
        try:
            new_data = _read_json(self.json_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to reload JSON:\n{type(e).__name__}: {e}")
            return

        # Keep internal copy in sync
        self.data = new_data

        # --- Title/Year + main extracted blocks ---
        self.title_lbl.configure(text=_safe_str(new_data.get("title", "")))
        self.year_lbl.configure(text=_safe_str(new_data.get("year", "")))

        _set_text(self.intro_extracted, _safe_str(new_data.get("introduction", "")), readonly=True)
        _set_text(self.methods_extracted, _safe_str(new_data.get("methods", "")), readonly=True)
        _set_text(self.disc_extracted, _safe_str(new_data.get("discussion", "")), readonly=True)

        # --- Figures captions -> one extracted block ---
        captions: list[str] = []
        figures = new_data.get("figures") or []
        if isinstance(figures, list):
            for it in figures:
                if not isinstance(it, dict):
                    continue
                num = _safe_str(it.get("figure_number", "")).strip()
                cap = _safe_str(it.get("caption", "")).strip()
                if not (num or cap):
                    continue
                head = f"Figure {num}".strip()
                captions.append(f"{head}\n{cap}".strip() if (head and cap) else (cap or head))
        _set_text(self.figcap_extracted, "\n\n".join(captions).strip(), readonly=True)

        # --- Results (preserve summaries if possible) ---
        old_summaries_by_title: dict[str, list[str]] = {}
        if preserve_summaries:
            for r in self._result_rows:
                key = (r.section_title or "").strip()
                old_summaries_by_title.setdefault(key, []).append(_get_text(r.summary_text).strip())

        # rebuild rows based on new extracted Results
        self._clear_results_ui()
        results = new_data.get("results") or []
        if isinstance(results, list) and results:
            for it in results:
                if not isinstance(it, dict):
                    continue
                title = _safe_str(it.get("section_title", "")).strip()
                text = _safe_str(it.get("section_text", "")).strip()

                init_summary = ""
                if preserve_summaries:
                    bucket = old_summaries_by_title.get(title, [])
                    if bucket:
                        init_summary = bucket.pop(0)

                self._add_result_row(section_title=title, extracted=text, summary_init=init_summary)
        else:
            self._add_result_row(section_title="(no sections in JSON)", extracted="", summary_init="")

        try:
            self.results_canvas.update_idletasks()
            self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all"))
        except Exception:
            pass

        # Refresh counters after extracted changed
        self._refresh_all_word_counters()

    def _on_open_pdf(self) -> None:
        if not self.pdf_path:
            messagebox.showinfo("Open PDF", "PDF path is not available.")
            return
        open_file(self.pdf_path)

    def _copy_prompt_plus_extracted(self, prompt_key: str, extracted: tk.Text) -> None:
        if prompt_key == "introduction":
            prompt = _get_text(self.intro_prompt).strip()
        elif prompt_key == "methods":
            prompt = _get_text(self.methods_prompt).strip()
        elif prompt_key == "discussion":
            prompt = _get_text(self.disc_prompt).strip()
        elif prompt_key == "figure_narrative":
            prompt = _get_text(self.fignarr_prompt).strip()
        else:
            prompt = ""

        src = _get_text(extracted).strip()
        payload = (prompt + "\n\n" + src).strip() + "\n"
        _clipboard_set(self, payload)
        self._mark_last_copied(extracted)
        self._refresh_all_word_counters()

    def _copy_results_prompt_plus_extracted(self, extracted: tk.Text) -> None:
        prompt = _get_text(self.results_prompt).strip()
        src = _get_text(extracted).strip()
        payload = (prompt + "\n\n" + src).strip() + "\n"
        _clipboard_set(self, payload)
        self._mark_last_copied(extracted)
        self._refresh_all_word_counters()

    def _copy_keypoints_prompt_plus_summary(self) -> None:
        prompt = _get_text(self.kp_prompt).strip()
        src = _get_text(self.kp_summary).strip()
        payload = (prompt + "\n\n" + src).strip() + "\n"
        _clipboard_set(self, payload)
        self._refresh_all_word_counters()

    def _on_cancel(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _on_save_summary(self) -> None:
        """
        Save semi-manual summary to DOCX (+ JSON debug next to it).
        If DOCX exists: ask Append / Overwrite / Cancel.
        After saving: open the docx via system viewer.
        """
        try:
            payload = {
                "title": _safe_str(self.data.get("title", "")),
                "year": _safe_str(self.data.get("year", "")),
                "language": self.lang_var.get(),
                "summary": {
                    "introduction": _get_text(self.intro_summary).strip(),
                    "methods": _get_text(self.methods_summary).strip(),
                    "results": [
                        {"section_title": r.section_title, "summary_text": _get_text(r.summary_text).strip()}
                        for r in self._result_rows
                        if _get_text(r.summary_text).strip()
                    ],
                    "discussion": _get_text(self.disc_summary).strip(),
                    "key_points": _get_text(self.kp_summary).strip(),
                    "figure_narrative": _get_text(self.fignarr_summary).strip(),
                },
                "prompts_used": {
                    "introduction": _get_text(self.intro_prompt).strip(),
                    "methods": _get_text(self.methods_prompt).strip(),
                    "results": _get_text(self.results_prompt).strip(),
                    "discussion": _get_text(self.disc_prompt).strip(),
                    "key_points": _get_text(self.kp_prompt).strip(),
                    "figure_narrative": _get_text(self.fignarr_prompt).strip(),
                },
                "source_json": str(self.json_path),
                "source_pdf": str(self.pdf_path) if self.pdf_path else "",
            }

            if self.pdf_path:
                docx_path = _mirrored_docx_path_from_pdf(self.pdf_path)
            else:
                # если pdf_path не передали, fallback как раньше (от json)
                docx_path = self.json_path.with_suffix(".semi_manual.summary.docx")

            docx_path.parent.mkdir(parents=True, exist_ok=True)

            overwrite = False
            if docx_path.exists():
                # Yes=Append, No=Overwrite, Cancel=Abort
                ans = messagebox.askyesnocancel(
                    "Summary exists.",
                    "Summary DOCX already exists. Append?\n"
                    "YES  = Append\n"
                    "NO   = Overwrite\n",
                    default=messagebox.CANCEL,
                )
                if ans is None:
                    return
                if ans is False:
                    overwrite = True

            # --- choose output docx path ---
            docx_path: Path
            overwrite = False

            db_has_summary = bool(self.existing_summary_path)

            if db_has_summary:
                db_docx_path = _resolve_existing_docx_path(self.db_gateway, self.existing_summary_path)  # type: ignore[arg-type]
                if db_docx_path.exists():
                    # Yes=Append, No=Overwrite, Cancel=Abort
                    ans = messagebox.askyesnocancel(
                        "Summary exists",
                        "Summary file is already registered in the database and exists on disk.\n\n"
                        f"{db_docx_path}\n\n"
                        "YES  = Append (add new version at the end)\n"
                        "NO   = Overwrite (replace file)\n"
                        "CANCEL = Abort",
                        default=messagebox.CANCEL,
                    )
                    if ans is None:
                        return
                    if ans is False:
                        overwrite = True
                    docx_path = db_docx_path
                else:
                    # In DB, but missing on disk -> warn + create new
                    messagebox.showwarning(
                        "Summary missing",
                        "Summary path is present in the database, but the file is missing on disk.\n\n"
                        f"DB path:\n{db_docx_path}\n\n"
                        "A new summary file will be created.",
                    )
                    # create new at the SAME db path (consistent with DB), ensure dirs
                    docx_path = db_docx_path
                    overwrite = True
            else:
                # No summary in DB -> mirror from PDF (preferred)
                if self.pdf_path:
                    docx_path = _mirrored_docx_path_from_pdf(self.pdf_path)
                else:
                    docx_path = self.json_path.with_suffix(".semi_manual.summary.docx")

                # Optional: if already exists physically, still ask
                if docx_path.exists():
                    ans = messagebox.askyesnocancel(
                        "Summary exists",
                        "Summary DOCX already exists on disk:\n"
                        f"{docx_path}\n\n"
                        "YES  = Append (add new version at the end)\n"
                        "NO   = Overwrite (replace file)\n"
                        "CANCEL = Abort",
                        default=messagebox.CANCEL,
                    )
                    if ans is None:
                        return
                    if ans is False:
                        overwrite = True

            docx_path.parent.mkdir(parents=True, exist_ok=True)


            append_semi_manual_summary_to_docx(
                docx_path=docx_path,
                payload=payload,
                overwrite=overwrite,
            )

            # Save path to DB (if context provided)
            if self.db_gateway is not None and self.article_id is not None:
                self.db_gateway.set_summary_path_for_article(self.article_id, docx_path)

            open_file(docx_path)

        except Exception as e:
            messagebox.showerror("Save Summary", f"{type(e).__name__}: {e}")


