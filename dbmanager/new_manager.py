
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

from dbmanager.db_core import get_project_home_dir, get_connection
from pdfparser.pdf_extract_title_year import (
    extract_title_and_year,
    YEAR_MIN,
    YEAR_MAX,
)

try:
    from pypdf import PdfReader  # type: ignore[import]
except Exception:  # pragma: no cover - в тестах можно мокать
    PdfReader = None  # type: ignore[assignment]


Classification = Literal["manual_review", "duplicate", "unique"]


@dataclass
class NewPdfResult:
    source_path: Path
    final_path: Path
    classification: Classification
    file_hash: Optional[str] = None
    year: Optional[int] = None
    title: Optional[str] = None
    parsing_error: Optional[str] = None


def _get_article_database_root() -> Path:
    """
    Корневой каталог "Article Database" внутри PROJECT_HOME_DIR.
    """
    project_home = get_project_home_dir()
    return project_home / "Article Database"


def _get_new_dirs() -> dict[str, Path]:
    """
    Возвращает пути к служебным каталогам внутри !New.
    """
    article_db = _get_article_database_root()
    new_dir = article_db / "!New"
    return {
        "article_db": article_db,
        "new": new_dir,
        "renamed": new_dir / "Renamed",
        "already": new_dir / "Already in database",
        "manual": new_dir / "Manual review",
    }


def _ensure_dirs_exist(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def _is_pdf_readable(pdf_path: Path) -> bool:
    """
    Минимальная проверка читаемости/целостности PDF.
    Если pypdf недоступен, считаем файл читаемым и
    полагаемся на extract_title_and_year.
    """
    if PdfReader is None:
        return True

    try:
        reader = PdfReader(str(pdf_path))
        # Попытаться получить количество страниц
        _ = len(reader.pages)
        return True
    except Exception:
        return False


def _compute_file_hash(pdf_path: Path, chunk_size: int = 1 << 20) -> str:
    """
    Вычисляет SHA256 для содержимого PDF-файла.
    """
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sanitize_title_for_filename(title: str, max_len: int = 150) -> str:
    """
    Делает title безопасным для использования в имени файла.
    Убирает/заменяет проблемные символы, обрезает длину.
    """
    # Заменяем запрещённые для файловой системы символы
    prohibited = '<>:"/\\|?*'
    sanitized = "".join("_" if c in prohibited else c for c in title)

    # Убираем управляющие и неотображаемые символы
    sanitized = "".join(c for c in sanitized if c.isprintable())

    sanitized = sanitized.strip()
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip()

    return sanitized


def _build_new_filename(year: int, title: str) -> str:
    """
    Формирует имя файла в формате "<Year> <Title>.pdf".
    """
    safe_title = _sanitize_title_for_filename(title)
    if not safe_title:
        raise ValueError("Cannot build filename: sanitized title is empty.")
    return f"{year} {safe_title}.pdf"


def _ensure_unique_path(path: Path) -> Path:
    """
    Если файл с таким именем уже существует, добавляет суффикс " (1)", " (2)", ...
    чтобы избежать перезаписи.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _check_article_exists_by_hash(file_hash: str) -> bool:
    """
    Проверяет, есть ли статья с данным file_hash в таблице Article.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Article WHERE file_hash = ? LIMIT 1;", (file_hash,))
        row = cur.fetchone()
    return row is not None


def process_new_pdf_file(pdf_path: Path) -> NewPdfResult:
    """
    Обрабатывает один PDF-файл, находящийся в папке !New.

    Логика:
        1. Проверка читаемости PDF.
        2. Вызов extract_title_and_year.
        3. При проблемах (повреждён, не определены год/название) —
           перенос в !New/Manual review.
        4. Для корректных файлов:
            - формирование имени "<Year> <Title>.pdf";
            - вычисление SHA256(file_hash);
            - проверка наличия статьи с таким file_hash в БД:
                * если есть → дубликат → перенос в !New/Already in database;
                * если нет  → новая статья → перенос в !New/Renamed.
    """
    pdf_path = pdf_path.resolve()
    dirs = _get_new_dirs()
    manual_dir = dirs["manual"]
    renamed_dir = dirs["renamed"]
    already_dir = dirs["already"]

    _ensure_dirs_exist(manual_dir, renamed_dir, already_dir)

    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # 1. Минимальная проверка читаемости/целостности
    if not _is_pdf_readable(pdf_path):
        final_path = _ensure_unique_path(manual_dir / pdf_path.name)
        shutil.move(str(pdf_path), final_path)
        return NewPdfResult(
            source_path=pdf_path,
            final_path=final_path,
            classification="manual_review",
            parsing_error="PDF is not readable or appears to be corrupted.",
        )

    # 2. Попытка извлечь title и year
    parsing_error: Optional[str] = None
    try:
        info = extract_title_and_year(
            pdf_path=pdf_path,
            use_llm_fallback=True,
            print_result=False,
        )
    except Exception as e:  # на всякий случай, чтобы не падать на одном файле
        parsing_error = f"extract_title_and_year_error: {type(e).__name__}: {e}"
        info = {}

    title = (info.get("title") or "").strip() if isinstance(info, dict) else ""
    year_str = (info.get("year") or "").strip() if isinstance(info, dict) else ""
    if not parsing_error:
        parsing_error = info.get("parsing_error") if isinstance(info, dict) else None

    # Валидация года
    year_int: Optional[int] = None
    if year_str:
        try:
            year_candidate = int(year_str)
            if YEAR_MIN <= year_candidate <= YEAR_MAX:
                year_int = year_candidate
            else:
                # Некорректный год по диапазону
                parsing_error = parsing_error or (
                    f"year_out_of_range: {year_candidate} not in [{YEAR_MIN}, {YEAR_MAX}]"
                )
        except ValueError:
            parsing_error = parsing_error or f"invalid_year_string: {year_str!r}"

    # Условия для отправки в Manual review:
    # - есть parsing_error
    # - не удалось определить год или название
    if parsing_error or not title or year_int is None:
        final_path = _ensure_unique_path(manual_dir / pdf_path.name)
        shutil.move(str(pdf_path), final_path)
        return NewPdfResult(
            source_path=pdf_path,
            final_path=final_path,
            classification="manual_review",
            title=title or None,
            year=year_int,
            parsing_error=parsing_error or "Missing title or valid year.",
        )

    # 3. Формируем новое имя и вычисляем hash
    new_filename = _build_new_filename(year_int, title)
    file_hash = _compute_file_hash(pdf_path)

    # 4. Проверяем наличие статьи в БД
    is_duplicate = _check_article_exists_by_hash(file_hash)

    target_dir = already_dir if is_duplicate else renamed_dir
    final_path = _ensure_unique_path(target_dir / new_filename)

    shutil.move(str(pdf_path), final_path)

    classification: Classification = "duplicate" if is_duplicate else "unique"

    return NewPdfResult(
        source_path=pdf_path,
        final_path=final_path,
        classification=classification,
        file_hash=file_hash,
        year=year_int,
        title=title,
        parsing_error=parsing_error,
    )


def iter_new_pdf_files() -> list[Path]:
    """
    Возвращает список PDF-файлов, лежащих напрямую в !New (без подкаталогов).

    Папки Renamed / Already in database / Manual review игнорируются.
    """
    dirs = _get_new_dirs()
    new_dir = dirs["new"]

    if not new_dir.exists():
        return []

    pdf_files: list[Path] = []
    for p in new_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf":
            pdf_files.append(p)

    return sorted(pdf_files)


def process_all_new_pdfs() -> list[NewPdfResult]:
    """
    Обрабатывает все PDF-файлы, лежащие непосредственно в !New.

    Возвращает список результатов для каждого файла.
    """
    results: list[NewPdfResult] = []
    for pdf_path in iter_new_pdf_files():
        result = process_new_pdf_file(pdf_path)
        results.append(result)
    return results
