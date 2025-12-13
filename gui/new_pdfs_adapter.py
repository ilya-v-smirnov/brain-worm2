from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pdfparser.pdf_extract_title_year import extract_title_and_year, YEAR_MIN, YEAR_MAX

# Переиспользуем проверенную логику из dbmanager.new_manager (включая move-правила)
from dbmanager.new_manager import (
    _compute_file_hash,
    _check_article_exists_by_hash,
    _build_new_filename,
    _get_new_dirs,
    _ensure_dirs_exist,
    _ensure_unique_path,
    _is_pdf_readable,
)

Destination = Literal["Renamed", "Already in database", "Manual review"]


@dataclass
class NewPdfItem:
    source_path: Path
    suggested_year: int | None
    suggested_title: str | None

    # editable by user
    user_year: int | None
    user_title: str | None

    exists_in_db: bool
    destination: Destination
    parsing_error: str | None = None


def scan_new_pdfs() -> list[Path]:
    """Сканирует только корень Article Database/!New на pdf-файлы.

    Не заходит в служебные подкаталоги (Renamed/Already/Manual).
    """
    dirs = _get_new_dirs()
    new_dir = dirs["new"]
    if not new_dir.exists():
        return []

    out: list[Path] = []
    for p in sorted(new_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() == ".pdf":
            out.append(p)
    return out


def _compute_destination(*, year: int | None, title: str | None, exists_in_db: bool, parsing_error: str | None) -> Destination:
    if parsing_error or year is None or not title:
        return "Manual review"
    if exists_in_db:
        return "Already in database"
    return "Renamed"



def analyze_new_pdfs_for_gui() -> list[NewPdfItem]:
    """Анализ !New для GUI: извлекает Year/Title, считает hash и проверяет дубликаты.

    ВАЖНО: не перемещает файлы.

    Устойчивость: ошибки обработки ОДНОГО файла не должны ронять весь список.
    """
    items: list[NewPdfItem] = []

    for pdf_path in scan_new_pdfs():
        try:
            pdf_path = pdf_path.resolve()

            parsing_error: str | None = None
            year_int: int | None = None
            title: str | None = None

            if not _is_pdf_readable(pdf_path):
                parsing_error = "PDF is not readable or appears to be corrupted."
            else:
                try:
                    info = extract_title_and_year(
                        pdf_path=pdf_path,
                        use_llm_fallback=True,
                        print_result=False,
                    )
                except Exception as e:
                    info = {}
                    parsing_error = f"extract_title_and_year_error: {type(e).__name__}: {e}"

                if isinstance(info, dict):
                    title = (info.get("title") or "").strip() or None
                    year_str = (info.get("year") or "").strip()
                    parsing_error = parsing_error or (info.get("parsing_error") or None)

                    if year_str:
                        try:
                            y = int(year_str)
                            if YEAR_MIN <= y <= YEAR_MAX:
                                year_int = y
                            else:
                                parsing_error = parsing_error or f"year_out_of_range: {y} not in [{YEAR_MIN}, {YEAR_MAX}]"
                        except ValueError:
                            parsing_error = parsing_error or f"invalid_year_string: {year_str!r}"

            file_hash = _compute_file_hash(pdf_path)
            exists = _check_article_exists_by_hash(file_hash)

            dest = _compute_destination(year=year_int, title=title, exists_in_db=exists, parsing_error=parsing_error)

            items.append(
                NewPdfItem(
                    source_path=pdf_path,
                    suggested_year=year_int,
                    suggested_title=title,
                    user_year=year_int,
                    user_title=title,
                    exists_in_db=exists,
                    destination=dest,
                    parsing_error=parsing_error,
                )
            )

        except Exception as e:
            items.append(
                NewPdfItem(
                    source_path=pdf_path,
                    suggested_year=None,
                    suggested_title=None,
                    user_year=None,
                    user_title=None,
                    exists_in_db=False,
                    destination="Manual review",
                    parsing_error=f"analyze_error: {type(e).__name__}: {e}",
                )
            )

    return items
@dataclass
class ApplySummary:
    moved_renamed: int = 0
    moved_already: int = 0
    skipped_manual: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def apply_rename(items: list[NewPdfItem]) -> ApplySummary:
    """Применяет переименование/перемещение для строк, которые НЕ Manual review.

    Manual review строки пропускаются (не двигаются).
    """
    dirs = _get_new_dirs()
    renamed_dir = dirs["renamed"]
    already_dir = dirs["already"]
    manual_dir = dirs["manual"]
    _ensure_dirs_exist(renamed_dir, already_dir, manual_dir)

    summary = ApplySummary()

    for item in items:
        # пересчитать destination на основе актуальных user_* значений
        dest = _compute_destination(
            year=item.user_year,
            title=item.user_title,
            exists_in_db=item.exists_in_db,
            parsing_error=item.parsing_error,
        )
        item.destination = dest

        if dest == "Manual review":
            summary.skipped_manual += 1
            continue

        if item.user_year is None or not item.user_title:
            summary.skipped_manual += 1
            continue

        try:
            new_filename = _build_new_filename(item.user_year, item.user_title)
            target_dir = already_dir if dest == "Already in database" else renamed_dir
            target_path = _ensure_unique_path(target_dir / new_filename)
            shutil.move(str(item.source_path), str(target_path))
            item.source_path = target_path  # чтобы UI мог обновиться при желании
            if dest == "Already in database":
                summary.moved_already += 1
            else:
                summary.moved_renamed += 1
        except Exception as e:
            summary.errors.append(f"{item.source_path.name}: {type(e).__name__}: {e}")

    return summary
