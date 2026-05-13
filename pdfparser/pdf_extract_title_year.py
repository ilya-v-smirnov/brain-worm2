#!/usr/bin/env python3
"""
Извлечение названия статьи и года публикации из PDF.

Реализация полностью на pypdf — без scipdf, GROBID, Docker, LLM.
Подходит для запуска на Windows без внешних сервисов.

Основной интерфейс:
    from pdfparser.pdf_extract_title_year import extract_title_and_year

CLI:
    python -m pdfparser.pdf_extract_title_year path/to/file_or_dir

Результат:
    {
        "file_name": "sample.pdf",
        "title": "Engineered IgG1-Fc Molecules...",
        "year": "2017",          # всегда строка (или "" если не найден)
        "method": "pypdf" | "pypdf_meta" | "pypdf_text" | "unknown",
        "parsing_error": None | "<описание ошибки>",
    }
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from pypdf import PdfReader


# ---------- Конфиг ----------

YEAR_MIN = 1980
YEAR_MAX = 2050

# Сколько первых страниц анализировать для поиска года.
# Обычно года достаточно искать в первой странице, но иногда год
# указан только в нижнем колонтитуле/первом упоминании цитирования.
FIRST_PAGES_FOR_YEAR = 2


# ---------- Структура результата ----------


@dataclass
class ExtractResult:
    file_name: str
    title: str
    year: str
    method: str  # "pypdf" | "pypdf_meta" | "pypdf_text" | "unknown"
    parsing_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "title": self.title,
            "year": self.year,
            "method": self.method,
            "parsing_error": self.parsing_error,
        }


# ---------- Утилиты ----------


_GARBAGE_TITLE_MARKERS = (
    ".doc",
    ".docx",
    ".pdf",
    ".tex",
    ".rtf",
    "microsoft word",
    "untitled",
    "document1",
)


def _clean_metadata_title(title: str) -> str:
    """
    Отфильтровывает заведомо мусорные значения PDF Title:
    пути к файлам, "Microsoft Word - ...", "Untitled" и т.п.

    Возвращает очищенный title или "" если значение признано мусором.
    """
    t = (title or "").strip()
    if not t:
        return ""

    # Слишком короткий — скорее всего не настоящий заголовок
    if len(t) < 5:
        return ""

    t_lower = t.lower()
    for marker in _GARBAGE_TITLE_MARKERS:
        if marker in t_lower:
            return ""

    # Заменяем неуместные служебные пробелы и переносы
    t = re.sub(r"\s+", " ", t).strip()

    return t


def _extract_first_pages_text(reader: PdfReader, n_pages: int = FIRST_PAGES_FOR_YEAR) -> str:
    """
    Извлекает текст первых n_pages страниц PDF.
    Ошибки на отдельных страницах игнорируются.
    """
    chunks: list[str] = []
    n = min(n_pages, len(reader.pages))
    for i in range(n):
        try:
            t = reader.pages[i].extract_text() or ""
            chunks.append(t)
        except Exception:
            continue
    return "\n".join(chunks)


def _extract_year_from_text(text: str) -> str:
    """
    Ищет первое 4-значное число в диапазоне [YEAR_MIN, YEAR_MAX].

    Простейшая, но эффективная для научных статей эвристика:
    год публикации почти всегда стоит в шапке первой страницы
    (журнал, copyright, дата приёма/публикации) и попадается раньше других чисел.
    """
    if not text:
        return ""

    for m in re.finditer(r"\b(\d{4})\b", text):
        try:
            y = int(m.group(1))
        except ValueError:
            continue
        if YEAR_MIN <= y <= YEAR_MAX:
            return str(y)
    return ""


# ---------- Основная функция ----------


def extract_title_and_year(
    pdf_path: Union[str, Path],
    use_llm_fallback: bool = True,   # игнорируется, оставлено для совместимости
    grobid_url: str = "",            # игнорируется, оставлено для совместимости
    print_result: bool = False,
    force_llm: bool = False,         # игнорируется, оставлено для совместимости
) -> dict:
    """
    Извлекает название статьи и год публикации из PDF-файла.

    :param pdf_path: путь к PDF
    :param print_result: печатать ли результат в консоль
    :return: словарь с ключами: file_name, title, year, method, parsing_error

    Параметры use_llm_fallback / grobid_url / force_llm сохранены лишь для
    обратной совместимости и игнорируются.
    """
    # Параметры специально не используются — сохранены ради совместимости API
    del use_llm_fallback, grobid_url, force_llm

    path = Path(pdf_path)
    result = ExtractResult(
        file_name=path.name,
        title="",
        year="",
        method="unknown",
        parsing_error=None,
    )

    # Открываем PDF
    try:
        reader = PdfReader(str(path))
    except Exception as e:
        result.parsing_error = f"pypdf_open_error: {type(e).__name__}: {e}"
        if print_result:
            _print_result(result)
        return result.to_dict()

    # 1) Title — из метаданных PDF
    title = ""
    try:
        meta = reader.metadata
        if meta is not None:
            raw_title = getattr(meta, "title", None)
            if raw_title:
                title = _clean_metadata_title(str(raw_title))
    except Exception:
        # Метаданные могут быть некорректно закодированы — это не критично
        title = ""

    # 2) Year — из текста первых страниц
    year = ""
    try:
        page_text = _extract_first_pages_text(reader, n_pages=FIRST_PAGES_FOR_YEAR)
        year = _extract_year_from_text(page_text)
    except Exception:
        year = ""

    # 3) Метод
    if title and year:
        method = "pypdf"
    elif title:
        method = "pypdf_meta"
    elif year:
        method = "pypdf_text"
    else:
        method = "unknown"

    result.title = title
    result.year = year
    result.method = method

    # parsing_error выставляем только если совсем ничего не получилось:
    # частичный успех (есть только title или только year) лучше тихо отдать
    # в GUI — пользователь увидит предзаполненное поле и дополнит вручную.
    if not title and not year:
        result.parsing_error = "Could not infer title or year from PDF metadata or first-page text."

    if print_result:
        _print_result(result)

    return result.to_dict()


def _print_result(result: ExtractResult) -> None:
    """
    Короткий принт в консоль (для человека).
    Title усечён до 50 символов.
    """
    title_short = result.title if result.title else "<none>"
    if len(title_short) > 50:
        title_short = title_short[:47] + "..."

    year_display = result.year if result.year else "<unknown>"

    line = (
        f"{result.file_name} | "
        f"Title: {title_short} | "
        f"Year: {year_display} | "
        f"method={result.method}"
    )

    print(line)
    if result.parsing_error:
        print(f"  [parsing_error] {result.parsing_error}", file=sys.stderr)


# ---------- CLI ----------


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract article title and publication year from a PDF using pypdf."
    )
    parser.add_argument(
        "path",
        help="Path to a PDF file or a directory containing PDF files.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    path = Path(args.path)

    if not path.exists():
        print(f"[ERROR] Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    pdf_files: list[Path] = []
    if path.is_file():
        pdf_files = [path]
    elif path.is_dir():
        pdf_files = sorted(p for p in path.glob("*.pdf") if p.is_file())
        if not pdf_files:
            print(f"[WARN] No PDF files found in directory: {path}", file=sys.stderr)
    else:
        print(f"[ERROR] Path is neither file nor directory: {path}", file=sys.stderr)
        sys.exit(1)

    for pdf in pdf_files:
        print(f"[INFO] Processing: {pdf}")
        extract_title_and_year(pdf_path=pdf, print_result=True)


if __name__ == "__main__":
    main()
