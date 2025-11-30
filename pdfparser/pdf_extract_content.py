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

import warnings

try:
    # Глушим предупреждение BeautifulSoup про HTML-парсер на XML.
    # В современных версиях bs4 класс лежит в bs4.builder.
    try:
        from bs4.builder import XMLParsedAsHTMLWarning  # основной путь
    except Exception:
        # Fallback для старых/нестандартных версий bs4
        from bs4 import XMLParsedAsHTMLWarning  # type: ignore[assignment]

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    # Если bs4 нет или структура другая — просто не глушим, но не падаем
    pass


from scipdf import parse_pdf_to_dict

try:
    from pypdf import PdfReader  # type: ignore[import]
except Exception:  # ModuleNotFoundError, ImportError, etc.
    PdfReader = None  # type: ignore[assignment]


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

def _is_ignored_tail_section(sec: SectionInfo) -> bool:
    """
    Секции, которые не должны попадать в Results/основные результаты:
    ACKNOWLEDGMENTS и т.п.
    """
    title = (sec.norm_title or sec.clean_title or "").upper()
    # Можно расширять по мере появления кейсов
    if "ACKNOWLEDGMENT" in title or "ACKNOWLEDGEMENTS" in title or "ACKNOWLEDGMENTS" in title:
        return True
    return False


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


def _is_trivial_figure_caption(caption: str, fig_no: int) -> bool:
    """
    Эвристика: считаем подпись "тривиальной", если она содержит только метку
    вида "Figure N" / "Figure N." / "Figure N:" или "Fig. N" без описательного текста.
    """
    cap = caption.strip()
    if not cap:
        return True
    # Явное совпадение с Figure <N> или Fig. <N> и, возможно, завершающей пунктуацией
    pattern = rf"^(?:Figure|Fig\.)\s*{fig_no}\s*[:\.]?$"
    if re.match(pattern, cap, flags=re.IGNORECASE):
        return True
    # Очень короткая подпись без описания
    if len(cap) <= 12 and " " not in cap[cap.lower().find(str(fig_no)) + len(str(fig_no)) :]:
        return True
    return False


def _extract_figures(article: Dict[str, Any]) -> List[Dict[str, Union[int, str]]]:
    """
    Извлекает подписи к рисункам и номера фигур ТОЛЬКО из основного текста статьи.

    Мы намеренно НЕ используем article["figures"] из scipdf, а ищем подписи как целые абзацы,
    начинающиеся с одного из следующих паттернов (в начале строки / абзаца):

    Паттерн 1: "Figure N", "Figure N.", "Figure N:", "Figure N)" и т.п.
        - регулярное выражение: ^(Figure|FIGURE)\s+([0-9]+)[\.:)]?\s*(.*)$

    Паттерн 2: "Fig. N", "Fig. N.", "Fig. N:", "Fig. N)" и т.п.
        - регулярное выражение: ^(Fig\.|FIG\.)\s+([0-9]+)[\.:)]?\s*(.*)$

    Где:
        - N — целое положительное число (номер рисунка);
        - (.*) — оставшийся текст строки после префикса "Figure N" / "Fig. N" (может быть пустым).

    Подписью считаем ВЕСЬ абзац целиком, а не только первую строку.
    Абзацы выделяем по двум и более переводам строки (пустая строка разделяет абзацы).
    """
    figures: List[Dict[str, Union[int, str]]] = []

    sections = article.get("sections") or []
    if not isinstance(sections, list):
        return figures

    # Собираем все тексты секций в один список абзацев
    paragraphs: List[str] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        text = sec.get("text") or sec.get("paragraph") or ""
        if not isinstance(text, str):
            continue
        # Делим на абзацы по 1+ пустым строкам
        raw_paragraphs = re.split(r"\n\s*\n", text)
        for para in raw_paragraphs:
            para_norm = para.strip()
            if para_norm:
                paragraphs.append(para_norm)

    # Паттерн 1: Figure N ...
    pattern_figure = re.compile(r"^(Figure|FIGURE)\s+([0-9]+)[\.:)]?\s*(.*)$")
    # Паттерн 2: Fig. N ...
    pattern_fig = re.compile(r"^(Fig\.|FIG\.)\s+([0-9]+)[\.:)]?\s*(.*)$")

    seen_numbers: set[int] = set()

    for para in paragraphs:
        # Пытаемся сопоставить с "Figure N ..."
        m = pattern_figure.match(para)
        if not m:
            # Если не подошло, пробуем "Fig. N ..."
            m = pattern_fig.match(para)

        if not m:
            continue

        # Во всех паттернах вторая группа — номер N
        num_str = m.group(2)
        try:
            num = int(num_str)
        except ValueError:
            continue

        if num in seen_numbers:
            # Не дублируем один и тот же номер
            continue
        seen_numbers.add(num)

        figures.append(
            {
                "figure_number": num,
                "caption": para.strip(),
            }
        )

    # figures всегда остаётся списком (возможно, пустым), как требует спецификация
    return figures


def _is_footer_or_noise_line(s: str) -> bool:
    """
    Эвристика: строка, похожая на колонтитул/служебный мусор,
    которую не стоит включать в подпись к рисунку.
    """
    s = s.strip()
    if not s:
        return False

    low = s.lower()

    # Явные маркеры скачивания/URL/служебных сообщений журнала
    if "downloaded from" in low:
        return True
    if "all rights reserved" in low:
        return True
    if "copyright" in low:
        return True
    if "doi:" in low:
        return True
    if "http://" in low or "https://" in low or "www." in low:
        return True

    # Частый паттерн: "VOLUME 119, NUMBER 13" и т.п.
    if "volume" in low and "number" in low:
        return True

    # Общий случай: строка вида "3088 ALOULOU et al BLOOD, 29 MARCH 2012 VOLUME 119, NUMBER 13"
    # — много ВЕРХНЕГО регистра + есть цифры.
    letters = re.sub(r"[^A-Za-z]", "", s)
    if letters:
        upper = sum(1 for c in letters if c.isupper())
        ratio = upper / len(letters)
        if ratio >= 0.8 and any(ch.isdigit() for ch in s):
            return True

    return False



def _extract_figures_from_pdf_text(pdf_path: Path) -> List[Dict[str, Union[int, str]]]:
    """
    Извлекает подписи к рисункам, читая текст PDF напрямую через pdfplumber.

    Логика:

    1) Читаем текст построчно со всех страниц.
    2) Ищем строки, которые НАЧИНАЮТСЯ с одного из паттернов (без учёта регистра):

       Паттерн 1 (Figure):
           ^\\s*(Figure|FIGURE)\\s*([0-9]+)\\W?\\s*(.*)$

           Примеры:
               "Figure 1 Caption..."
               "Figure1 Caption..."
               "FIGURE3| Sometextwithoutspaces..."
               "FIGURE2) Moretext..."

       Паттерн 2 (Fig):
           ^\\s*(Fig\\.?|FIG\\.?)\\s*([0-9]+)\\W?\\s*(.*)$

           Примеры:
               "Fig. 2 Caption..."
               "Fig2 Caption..."
               "FIG3) Anothercaption..."

       Где:
           ([0-9]+) — номер рисунка;
           (.*) — хвост первой строки подписи (может быть пустым).

    3) Как только нашли такую строку — считаем, что это НАЧАЛО подписи.
       Далее захватываем последующие строки в ту же подпись, пока:

           - не встретили пустую строку (разделитель блоков), ИЛИ
           - не встретили новую строку, начинающуюся с Figure/Fig для следующего рисунка.

       В caption кладём ВСЕ строки подписи, склеенные пробелами.
    """

    figures: List[Dict[str, Union[int, str]]] = []

    # Если PdfReader недоступен — тихо выходим, дальше сработает fallback.
    if PdfReader is None:
        return figures

    # 1) Читаем текст построчно со всех страниц
    try:
        all_lines: List[str] = []
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            text_page = page.extract_text() or ""
            if not isinstance(text_page, str):
                continue
            all_lines.extend(text_page.splitlines())
    except Exception:
        # Не ломаем общий парсинг, просто отдаём пустой список фигур.
        return figures


    # 2) Регулярки для первой строки подписи
    pattern_figure = re.compile(r"^\s*(Figure|FIGURE)\s*([0-9]+)\W?\s*(.*)$")
    pattern_fig = re.compile(r"^\s*(Fig\.?|FIG\.?)\s*([0-9]+)\W?\s*(.*)$")

    seen_numbers: set[int] = set()

    n = len(all_lines)
    i = 0
    while i < n:
        line = all_lines[i]
        if not isinstance(line, str):
            i += 1
            continue

        s = line.strip()
        if not s:
            i += 1
            continue

        m = pattern_figure.match(s)
        if not m:
            m = pattern_fig.match(s)
        if not m:
            i += 1
            continue

        tail = m.group(3) or ""
        tail = tail.lstrip() 
        if tail.startswith(","):
            i += 1
            continue

        # Есть старт подписи
        num_str = m.group(2)
        try:
            num = int(num_str)
        except ValueError:
            i += 1
            continue


        if num in seen_numbers:
            # Уже брали подпись для этого номера — пропускаем
            i += 1
            continue
        seen_numbers.add(num)

        # 3) Собираем все последующие строки, пока не встретили пустую
        #    или следующую Figure/Fig
                # Определяем "компактный режим": первая строка подписи почти без пробелов
        # (типичный случай FIGURE3|... без пробелов между словами).
        first_spaces = s.count(" ")
        compact_mode = first_spaces <= 1

                # Определяем "компактный режим": первая строка подписи почти без пробелов
        first_spaces = s.count(" ")
        compact_mode = first_spaces <= 1

        caption_lines = [s]
        j = i + 1

        MAX_CAPTION_LINES = 30  # мягкий лимит на длину подписи

        while j < n and len(caption_lines) < MAX_CAPTION_LINES:
            line2 = all_lines[j]
            if not isinstance(line2, str):
                j += 1
                continue

            s2 = line2.strip()
            if not s2:
                # Пустая строка — конец подписи
                break

            # Новая подпись к следующей фигуре — стоп
            if pattern_figure.match(s2) or pattern_fig.match(s2):
                break

            # Служебные строки журнала / колонтитулы — не включаем в подпись
            if _is_footer_or_noise_line(s2):
                break

            # "Компактный режим": первая строка подписи без пробелов,
            # заканчиваем, когда пошёл "обычный" текст статьи.
            if compact_mode:
                spaces2 = s2.count(" ")
                if spaces2 > 1:
                    break

            caption_lines.append(s2)
            j += 1


        caption_full = " ".join(caption_lines).strip()

        figures.append(
            {
                "figure_number": num,
                "caption": caption_full,
            }
        )

        # Перепрыгиваем на конец текущей подписи
        i = j
    return figures



def parse_pdf_content(pdf_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Парсит PDF в структурированный объект (который потом конвертируется в JSON).
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
        for sec in sections:
            if sec.index < res_first_idx:
                continue
            if disc_first_idx is not None and sec.index >= disc_first_idx:
                continue
            if _is_ignored_tail_section(sec):
                continue  # <-- новая строка
            if sec.section_type in ("results", "other") and sec.text.strip():
                title = sec.clean_title or "Results"
                results_sections.append(
                    {
                        "section_title": title,
                        "section_text": sec.text.strip(),
                    }
                )
    else:
        if result["introduction"] and result["discussion"] and discussion_indices:
            disc_first_idx = min(discussion_indices)
            for sec in sections:
                if sec.index <= intro_range_end_idx:
                    continue
                if sec.index >= disc_first_idx:
                    continue
                if _is_ignored_tail_section(sec):
                    continue  # <-- новая строка
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
    figures_pdf = _extract_figures_from_pdf_text(path)
    if figures_pdf:
        result["figures"] = figures_pdf
    else:
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
