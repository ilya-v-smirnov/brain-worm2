#!/usr/bin/env python3
"""
Парсер содержимого научной статьи из PDF в структурированный JSON.

Извлекает:
    - title
    - year
    - introduction
    - methods (Materials and Methods / Methods / Experimental...)
    - results (подразделы между Introduction и Discussion; а также явные Results)
    - discussion
    - figures (figure_number + caption)
    - parsing_error (описание проблем при парсинге или None)

CLI:
    Обработка одного файла:
        python -m pdfparser.pdf_extract_content path/to/file.pdf

        # Явно указать путь для JSON:
        python -m pdfparser.pdf_extract_content path/to/file.pdf --out path/to/result.json

    Обработка директории:
        python -m pdfparser.pdf_extract_content path/to/dir

        # Сохранять JSON в отдельную директорию:
        python -m pdfparser.pdf_extract_content path/to/dir --out-dir parsed_json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from scipdf import parse_pdf_to_dict


YEAR_MIN = 1980
YEAR_MAX = 2050


@dataclass
class SectionInfo:
    index: int
    raw_heading: str
    clean_title: str
    norm_title: str
    text: str
    section_type: str  # "intro" | "methods" | "results" | "discussion" | "other"


def _extract_year_from_pub_date(pub_date: Any) -> Optional[str]:
    """
    Пытается извлечь год публикации из поля pub_date (строка/словарь/число),
    ограничиваясь диапазоном YEAR_MIN–YEAR_MAX.
    Возвращает строку с годом или None.
    """

    # Год уже числом
    if isinstance(pub_date, int):
        if YEAR_MIN <= pub_date <= YEAR_MAX:
            return str(pub_date)
        return None

    # Словарь
    if isinstance(pub_date, dict):
        for key in ("year", "pub_year", "publication_year"):
            val = pub_date.get(key)
            if isinstance(val, int) and YEAR_MIN <= val <= YEAR_MAX:
                return str(val)
            if isinstance(val, str):
                y = _extract_year_from_pub_date(val)
                if y is not None:
                    return y
        # Пробуем по всем строковым значениям
        for val in pub_date.values():
            if isinstance(val, str):
                y = _extract_year_from_pub_date(val)
                if y is not None:
                    return y
        return None

    # Строка
    if isinstance(pub_date, str):
        candidates = re.findall(r"\b(\d{4})\b", pub_date)
        for c in candidates:
            year_int = int(c)
            if YEAR_MIN <= year_int <= YEAR_MAX:
                return str(year_int)
        return None

    return None


def _normalize_heading(heading: str) -> Tuple[str, str]:
    """
    Убирает начальную нумерацию и лишние пробелы из заголовка.
    Возвращает (clean_title, norm_title),
        clean_title - "человеческий" заголовок,
        norm_title  - верхний регистр, упрощённый, для классификации.
    """
    if not isinstance(heading, str):
        heading = ""

    # Убираем начальные номера, типа "1.", "2.3", "I.", "II", etc.
    h = heading.strip()
    h = re.sub(r"^[\dIVXivx\.\s\-]+", "", h).strip()

    clean_title = h
    norm_title = re.sub(r"\s+", " ", h).strip().upper()

    return clean_title, norm_title


def _classify_section_title(norm_title: str) -> str:
    """
    Классификация секции по нормализованному заголовку.
    Возвращает один из:
        "intro", "methods", "results", "discussion", "other"
    """

    if not norm_title:
        return "other"

    # Introduction
    if "INTRODUCTION" in norm_title:
        return "intro"

    # Results & Discussion (считаем как results)
    if "RESULT" in norm_title and "DISCUSSION" in norm_title:
        return "results"

    # Clean Discussion (без results)
    if "DISCUSSION" in norm_title:
        return "discussion"

    # Methods / Materials and Methods / Experimental / Methodology
    if (
        "MATERIALS AND METHODS" in norm_title
        or "MATERIAL AND METHODS" in norm_title
        or "MATERIALS & METHODS" in norm_title
        or "METHODS" in norm_title
        or "METHOD" in norm_title
        or "METHODOLOGY" in norm_title
        or "EXPERIMENTAL" in norm_title
        or "EXPERIMENTS" in norm_title
        or "PROCEDURE" in norm_title
    ):
        return "methods"

    # Явные Results
    if "RESULT" in norm_title:
        return "results"

    return "other"


def _collect_sections(article: Dict[str, Any]) -> List[SectionInfo]:
    """
    Собирает список секций с их классификацией.
    """
    sections_raw = article.get("sections") or []
    result: List[SectionInfo] = []

    for idx, sec in enumerate(sections_raw):
        if not isinstance(sec, dict):
            continue

        heading = (
            sec.get("heading")
            or sec.get("section_title")
            or sec.get("title")
            or ""
        )
        text = sec.get("text") or sec.get("paragraph") or ""
        if not isinstance(text, str):
            text = ""

        clean_title, norm_title = _normalize_heading(heading)
        section_type = _classify_section_title(norm_title)

        result.append(
            SectionInfo(
                index=idx,
                raw_heading=heading or "",
                clean_title=clean_title,
                norm_title=norm_title,
                text=text,
                section_type=section_type,
            )
        )

    return result


def _extract_figures(article: Dict[str, Any]) -> List[Dict[str, Union[int, str]]]:
    """
    Извлекает подписи к рисункам и номера фигур.

    Стратегия (двухступенчатая):

    1) Сначала пытаемся найти подписи в тексте статьи по шаблонам:
        - строки, содержащие "Figure N" или "Fig. N"
        - номер берём из этой строки
    2) Если ничего не нашли, fallback к article["figures"]:
        - figure_number: порядковый номер (1, 2, 3, ...)
        - caption:
            * если scipdf дал осмысленный caption — используем его;
            * если нет — используем просто "Figure <i>", чтобы не было пустых подписей.
    """
    figures: List[Dict[str, Union[int, str]]] = []

    # ---------- 1. Попробуем вытащить подписи из текста ----------
    sections = article.get("sections") or []
    caption_candidates: List[str] = []
    seen_numbers: set[int] = set()

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        text = sec.get("text") or sec.get("paragraph") or ""
        if not isinstance(text, str):
            continue

        for line in text.splitlines():
            s = line.strip()
            # Ищем "Figure 1", "Fig. 2", "Figure 3A" и т.п. где угодно в строке
            m = re.search(r"(Figure|Fig\.)\s+(\d+)[A-Za-z]?", s)
            if m:
                num = int(m.group(2))
                if num in seen_numbers:
                    continue
                seen_numbers.add(num)
                caption_candidates.append(s)

    if caption_candidates:
        for cap in caption_candidates:
            m = re.search(r"(?:Figure|Fig\.)\s+(\d+)", cap)
            if m:
                fig_no: Union[int, str] = int(m.group(1))
            else:
                fig_no = len(figures) + 1

            figures.append(
                {
                    "figure_number": fig_no,
                    "caption": cap,
                }
            )
        return figures

    # ---------- 2. Fallback: используем article["figures"] ----------
    figures_raw = article.get("figures") or []
    for i, fig in enumerate(figures_raw, start=1):
        if not isinstance(fig, dict):
            continue

        caption_raw = (
            fig.get("caption")
            or fig.get("fig_caption")
            or fig.get("text")
            or ""
        )
        if not isinstance(caption_raw, str):
            caption_raw = ""

        caption_raw = caption_raw.strip()

        # Если у scipdf есть нормальный caption — используем его.
        # Если нет — хотя бы ставим "Figure <i>", чтобы не было пустоты.
        if caption_raw:
            caption = caption_raw
        else:
            caption = f"Figure {i}"

        fig_number: Union[int, str] = i

        figures.append(
            {
                "figure_number": fig_number,
                "caption": caption,
            }
        )

    return figures



def parse_pdf_content(pdf_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Парсит PDF в структурированный объект (который потом конвертируется в JSON).
    НЕ использует pdf_extract_title_year, а строит свою логику.
    """

    path = Path(pdf_path)
    result: Dict[str, Any] = {
        "title": "",
        "year": "",
        "introduction": "",
        "methods": "",
        "results": [],
        "discussion": "",
        "figures": [],
        "parsing_error": None,
    }

    try:
        article = parse_pdf_to_dict(str(path))
    except Exception as e:
        result["parsing_error"] = f"scipdf_error: {type(e).__name__}: {e}"
        return result

    if not isinstance(article, dict):
        result["parsing_error"] = "Unexpected article structure (not a dict)."
        return result

    # ---- Title & Year ----
    title = article.get("title")
    if isinstance(title, str):
        result["title"] = title.strip()

    pub_date = article.get("pub_date")
    year = _extract_year_from_pub_date(pub_date)
    if year is not None:
        result["year"] = year

        # ---- Sections ----
    sections = _collect_sections(article)
    if not sections:
        # Нечего делить, возвращаем только title/year/figures
        return result

    # Индексы по типам
    intro_indices = [sec.index for sec in sections if sec.section_type == "intro"]
    methods_indices = [sec.index for sec in sections if sec.section_type == "methods"]
    results_indices = [sec.index for sec in sections if sec.section_type == "results"]
    discussion_indices = [sec.index for sec in sections if sec.section_type == "discussion"]

    first_methods_idx = min(methods_indices) if methods_indices else None
    first_results_idx = min(results_indices) if results_indices else None
    first_discussion_idx = min(discussion_indices) if discussion_indices else None

    # ---- Introduction ----
    intro_parts: List[str] = []
    intro_range_end_idx = -1

    if intro_indices:
        # Явные Introduction по заголовкам
        for sec in sections:
            if sec.section_type == "intro" and sec.text.strip():
                intro_parts.append(sec.text.strip())
        intro_range_end_idx = max(intro_indices)
    else:
        # Fallback: всё до первого "якоря" (methods/results/discussion)
        anchors = [idx for idx in (first_methods_idx, first_results_idx, first_discussion_idx) if idx is not None]
        if anchors:
            boundary = min(anchors)
            intro_candidates = [sec for sec in sections if sec.index < boundary and sec.text.strip()]
        else:
            # Нет вообще явных структурных заголовков — берём первую секцию
            intro_candidates = [sections[0]] if sections and sections[0].text.strip() else []

        intro_parts = [sec.text.strip() for sec in intro_candidates]
        intro_range_end_idx = intro_candidates[-1].index if intro_candidates else -1

    result["introduction"] = "\n\n".join(intro_parts).strip()

    # ---- Methods ----
    methods_parts: List[str] = []
    if methods_indices:
        # Берём блок от первого methods до ближайшего якоря (results/discussion),
        # включая секции без явного заголовка (section_type == "other").
        start_idx = first_methods_idx
        # ближайший "якорь" после методов
        anchors_after_methods = [
            idx
            for idx in (first_results_idx, first_discussion_idx)
            if idx is not None and idx > start_idx
        ]
        if anchors_after_methods:
            end_idx = min(anchors_after_methods)
        else:
            end_idx = sections[-1].index + 1  # до конца статьи

        for sec in sections:
            if start_idx <= sec.index < end_idx and sec.text.strip():
                methods_parts.append(sec.text.strip())
    else:
        # Нет явных methods-секций по заголовкам — оставляем пустым
        methods_parts = []

    result["methods"] = "\n\n".join(methods_parts).strip()


    # ---- Discussion ----
    discussion_parts: List[str] = []
    for sec in sections:
        if sec.section_type == "discussion" and sec.text.strip():
            discussion_parts.append(sec.text.strip())
    result["discussion"] = "\n\n".join(discussion_parts).strip()

    # ---- Results ----
    results_sections: List[Dict[str, str]] = []

    has_explicit_results = bool(results_indices)
    res_first_idx = first_results_idx
    disc_first_idx = first_discussion_idx

    if has_explicit_results:
        # 1) Явный блок Results + все "подразделы" до Discussion
        for sec in sections:
            if sec.index < res_first_idx:
                continue
            if disc_first_idx is not None and sec.index >= disc_first_idx:
                continue
            if sec.section_type in ("results", "other") and sec.text.strip():
                title = sec.clean_title or "Results"
                results_sections.append(
                    {
                        "section_title": title,
                        "section_text": sec.text.strip(),
                    }
                )
    else:
        # 2) Нет явных Results: если есть intro и discussion,
        #    всё между ними (кроме intro/methods/discussion) считаем Results-подразделами.
        if result["introduction"] and result["discussion"] and discussion_indices:
            disc_first_idx = min(discussion_indices)
            for sec in sections:
                if sec.index <= intro_range_end_idx:
                    continue
                if sec.index >= disc_first_idx:
                    continue
                if sec.section_type not in ("intro", "methods", "discussion") and sec.text.strip():
                    title = sec.clean_title or "Results"
                    results_sections.append(
                        {
                            "section_title": title,
                            "section_text": sec.text.strip(),
                        }
                    )

    result["results"] = results_sections

    # ---- Figures ----
    result["figures"] = _extract_figures(article)

    return result


# ---------- Сохранение и CLI ----------

def _save_json(data: Dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract structured content from scientific PDF into JSON."
    )
    parser.add_argument(
        "path",
        help="Path to a PDF file or a directory containing PDF files.",
    )
    parser.add_argument(
        "--out",
        help=(
            "Output JSON file path (only for single PDF). "
            "If omitted, JSON is saved next to the PDF with .json extension."
        ),
    )
    parser.add_argument(
        "--out-dir",
        help=(
            "Directory to save JSON files when processing a directory of PDFs. "
            "If omitted, JSON files are saved next to each PDF."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    path = Path(args.path)

    if not path.exists():
        print(f"[ERROR] Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    if path.is_file():
        if path.suffix.lower() != ".pdf":
            print(f"[ERROR] Not a PDF file: {path}", file=sys.stderr)
            sys.exit(1)

        data = parse_pdf_content(path)

        if args.out:
            out_path = Path(args.out)
        else:
            out_path = path.with_suffix(".json")

        _save_json(data, out_path)
        print(f"[INFO] Saved JSON to: {out_path}")

    elif path.is_dir():
        pdf_files = sorted(p for p in path.glob("*.pdf") if p.is_file())
        if not pdf_files:
            print(f"[WARN] No PDF files found in directory: {path}", file=sys.stderr)
            return

        if args.out and not args.out_dir:
            print(
                "[ERROR] --out is only allowed for single PDF file. "
                "Use --out-dir for directory processing.",
                file=sys.stderr,
            )
            sys.exit(1)

        out_dir = Path(args.out_dir) if args.out_dir else None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)

        for pdf in pdf_files:
            print(f"[INFO] Processing: {pdf}")
            data = parse_pdf_content(pdf)

            if out_dir is not None:
                out_path = out_dir / (pdf.stem + ".json")
            else:
                out_path = pdf.with_suffix(".json")

            _save_json(data, out_path)
            print(f"[INFO] Saved JSON to: {out_path}")

    else:
        print(f"[ERROR] Path is neither file nor directory: {path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
