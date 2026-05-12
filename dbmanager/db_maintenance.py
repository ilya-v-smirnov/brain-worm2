
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass, field

from dbmanager.db_core import get_project_home_dir, get_connection


# ---------- Общие утилиты для Article Database ----------


def _get_article_database_root() -> Path:
    """
    Корневой каталог 'Article Database' внутри PROJECT_HOME_DIR.
    """
    project_home = get_project_home_dir()
    return project_home / "Article Database"


def _compute_file_hash(pdf_path: Path, chunk_size: int = 1 << 20) -> str:
    """
    Вычисляет SHA256-хеш содержимого PDF-файла.
    """
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _parse_year_and_title_from_filename(filename: str) -> Tuple[int, str]:
    """
    Парсит имя файла вида '<Year> <Title>.pdf' и возвращает (year, title).

    Предполагается, что файлы, уже попавшие в основную базу, были
    предварительно переименованы на Этапе 1.

    Примеры:
        '2015 Plasmonic Sensors.pdf' -> (2015, 'Plasmonic Sensors')
    """
    name = filename
    if name.lower().endswith(".pdf"):
        name = name[:-4]

    # Ожидаем, что первые 4 символа — это год, затем пробел, затем название.
    try:
        year_str, title = name.split(" ", 1)
    except ValueError as e:
        raise ValueError(
            f"Filename does not match '<Year> <Title>.pdf' format: {filename!r}"
        ) from e

    try:
        year = int(year_str)
    except ValueError as e:
        raise ValueError(
            f"Year part of filename is not an integer: {year_str!r} in {filename!r}"
        ) from e

    title = title.strip()
    if not title:
        raise ValueError(f"Empty title part in filename: {filename!r}")

    return year, title


def _articlefile_exists(cur, pdf_rel_path: str) -> bool:
    """
    Проверяет, существует ли запись в ArticleFile с данным pdf_path.
    """
    cur.execute(
        "SELECT 1 FROM ArticleFile WHERE pdf_path = ? LIMIT 1;",
        (pdf_rel_path,),
    )
    return cur.fetchone() is not None


def _get_article_id_by_hash(cur, file_hash: str) -> Optional[int]:
    """
    Возвращает id статьи по file_hash или None, если статьи нет.
    """
    cur.execute(
        "SELECT id FROM Article WHERE file_hash = ? LIMIT 1;",
        (file_hash,),
    )
    row = cur.fetchone()
    return int(row[0]) if row is not None else None


def _insert_new_article(
    cur,
    file_hash: str,
    year: int,
    title: str,
    pdf_master_path: str,
) -> int:
    """
    Создаёт новую запись в Article и возвращает её id.

    json_path, summary_path, lecture_text_path, lecture_audio_path оставляем NULL.
    (Столбцы lecture_* сохранены в схеме для обратной совместимости со старыми БД,
    но новая версия приложения их не использует.)
    """
    cur.execute(
        """
        INSERT INTO Article (
            file_hash,
            year,
            title,
            pdf_master_path,
            json_path,
            summary_path,
            lecture_text_path,
            lecture_audio_path
        )
        VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL);
        """,
        (file_hash, year, title, pdf_master_path),
    )
    return int(cur.lastrowid)


def _insert_article_file(cur, article_id: int, pdf_rel_path: str) -> None:
    """
    Создаёт запись в ArticleFile для конкретного файла.
    """
    cur.execute(
        """
        INSERT OR IGNORE INTO ArticleFile (article_id, pdf_path)
        VALUES (?, ?);
        """,
        (article_id, pdf_rel_path),
    )


# ---------- Этап 2: синхронизация Article Database ↔ БД ----------


def sync_article_database() -> List[int]:
    """
    Этап 2: обновление списка статей и файлов в БД.

    Логика:
      1. Рекурсивно обходим все PDF-файлы в
         '<PROJECT_HOME_DIR>/Article Database', исключая папку '!New'
         и все её подкаталоги.
      2. Для каждого PDF:
         - строим относительный путь от PROJECT_HOME_DIR;
         - если такой pdf_path уже есть в ArticleFile, пропускаем;
         - иначе вычисляем file_hash и ищем Article по file_hash.
      3. Если Article с таким file_hash есть:
         - добавляем новую запись в ArticleFile (новый pdf_path).
      4. Если Article нет:
         - извлекаем year и title из имени файла '<Year> <Title>.pdf';
         - создаём новую запись в Article;
         - создаём запись в ArticleFile;
         - article_id новой статьи добавляем в список для последующей обработки.

    Возвращает список article_id статей, которые были добавлены как новые
    уникальные на этом этапе.
    """
    project_home = get_project_home_dir()
    article_root = _get_article_database_root()

    new_article_ids: List[int] = []

    with get_connection() as conn:
        cur = conn.cursor()

        for pdf_path in article_root.rglob("*.pdf"):
            # Пропускаем всё, что внутри !New
            if "!New" in pdf_path.parts:
                continue

            # Относительный путь от PROJECT_HOME_DIR:
            # 'Article Database/SPR/2015 Plasmonic Sensors.pdf'
            pdf_rel_path = pdf_path.relative_to(project_home).as_posix()

            # Проверяем, не зарегистрирован ли файл уже в ArticleFile
            if _articlefile_exists(cur, pdf_rel_path):
                continue

            # Новый путь: вычисляем file_hash и ищем статью
            file_hash = _compute_file_hash(pdf_path)
            article_id = _get_article_id_by_hash(cur, file_hash)

            if article_id is None:
                # Случай B — новой уникальной статьи в Article ещё нет
                year, title = _parse_year_and_title_from_filename(pdf_path.name)

                article_id = _insert_new_article(
                    cur=cur,
                    file_hash=file_hash,
                    year=year,
                    title=title,
                    pdf_master_path=pdf_rel_path,
                )
                new_article_ids.append(article_id)

            # В обоих случаях добавляем запись в ArticleFile
            _insert_article_file(cur, article_id, pdf_rel_path)

        conn.commit()

    return new_article_ids


# ---------- Reconciliation ссылок JSON / DOCX ----------


def reconcile_article_paths() -> dict[str, int]:
    """
    Восстанавливает отсутствующие ссылки на JSON/DOCX в БД, если файлы физически существуют.

    Логика:
      - Если Article.json_path пуст: ищем PROJECT_HOME_DIR/Contents/<pdf_basename>.json
      - Если Article.summary_path пуст: ищем PROJECT_HOME_DIR/PDF_summaries/<pdf_rel_without_article_db>.docx
        где pdf_rel_without_article_db = pdf_path без ведущего "Article Database/"

    Возвращает счётчики обновлений.
    """
    project_home = get_project_home_dir()

    updated_json = 0
    updated_summary = 0

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                a.id,
                a.json_path,
                a.summary_path,
                af.pdf_path
            FROM Article a
            JOIN ArticleFile af ON af.article_id = a.id
            ORDER BY a.id;
            """
        )
        rows = cur.fetchall()

        for article_id, json_path, summary_path, pdf_path in rows:
            # --- JSON ---
            if not json_path:
                pdf_basename = Path(str(pdf_path)).name  # "... .pdf"
                json_name = Path(pdf_basename).with_suffix(".json").name
                json_rel = str(Path("Contents") / json_name)
                json_abs = project_home / json_rel
                if json_abs.exists():
                    cur.execute(
                        "UPDATE Article SET json_path = ? WHERE id = ?;",
                        (json_rel, article_id),
                    )
                    updated_json += 1

            # --- DOCX summary ---
            if not summary_path:
                pdf_rel = Path(str(pdf_path))

                # ожидаем, что в БД pdf_path обычно начинается с "Article Database/..."
                parts = list(pdf_rel.parts)
                if len(parts) >= 2 and parts[0] == "Article Database":
                    stripped = Path(*parts[1:])  # без "Article Database"
                else:
                    # fallback: если вдруг pdf_path уже без префикса
                    stripped = pdf_rel

                docx_rel = Path("PDF_summaries") / stripped.with_suffix(".docx")
                docx_abs = project_home / docx_rel

                if docx_abs.exists():
                    cur.execute(
                        "UPDATE Article SET summary_path = ? WHERE id = ?;",
                        (str(docx_rel), article_id),
                    )
                    updated_summary += 1

        conn.commit()

    return {"updated_json": updated_json, "updated_summary": updated_summary}


# ---------- Удаление статей/файлов (для GUI) ----------

@dataclass
class DeleteReport:
    article_id: int
    mode: str  # "single_path" | "article_everywhere"
    removed_article_row: bool = False
    removed_articlefile_rows: int = 0
    updated_master_path_to: str | None = None
    deleted_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def list_article_pdf_paths(article_id: int) -> List[str]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT pdf_path FROM ArticleFile WHERE article_id = ? ORDER BY id;",
            (article_id,),
        )
        return [r[0] for r in cur.fetchall()]


def get_article_paths(article_id: int) -> Dict[str, Optional[str]]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pdf_master_path, json_path, summary_path, lecture_text_path, lecture_audio_path
            FROM Article
            WHERE id = ?;
            """,
            (article_id,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return {
            "pdf_master_path": row[0],
            "json_path": row[1],
            "summary_path": row[2],
            "lecture_text_path": row[3],
            "lecture_audio_path": row[4],
        }


def set_article_summary_path(article_id: int, summary_path: Optional[str]) -> None:
    """
    Записывает путь к docx с summary в Article.summary_path.

    summary_path рекомендуется хранить относительным к PROJECT_HOME_DIR.
    Можно передать None, чтобы очистить поле.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE Article SET summary_path = ? WHERE id = ?;",
            (summary_path, article_id),
        )
        conn.commit()


def set_article_json_path(article_id: int, json_path: Optional[str]) -> None:
    """
    Записывает путь к extracted JSON в Article.json_path.

    json_path рекомендуется хранить относительным к PROJECT_HOME_DIR.
    Можно передать None, чтобы очистить поле.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE Article SET json_path = ? WHERE id = ?;",
            (json_path, article_id),
        )
        conn.commit()


def _safe_unlink(path: Path, report: DeleteReport) -> None:
    try:
        if path.exists():
            path.unlink()
            report.deleted_files.append(str(path))
        else:
            report.missing_files.append(str(path))
    except Exception as e:
        report.errors.append(f"unlink_error: {path}: {type(e).__name__}: {e}")


def delete_single_pdf_path(
    *,
    article_id: int,
    pdf_path: str,
    delete_physical_pdf: bool,
) -> DeleteReport:
    report = DeleteReport(article_id=article_id, mode="single_path")
    project_home = get_project_home_dir()

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute("SELECT pdf_master_path FROM Article WHERE id = ?;", (article_id,))
        row = cur.fetchone()
        if not row:
            report.errors.append("article_not_found")
            return report
        master_path = row[0]

        cur.execute(
            "DELETE FROM ArticleFile WHERE article_id = ? AND pdf_path = ?;",
            (article_id, pdf_path),
        )
        report.removed_articlefile_rows = cur.rowcount

        # Если удалили master — переназначаем на оставшийся
        if master_path == pdf_path:
            cur.execute(
                "SELECT pdf_path FROM ArticleFile WHERE article_id = ? ORDER BY id LIMIT 1;",
                (article_id,),
            )
            r2 = cur.fetchone()
            if r2:
                new_master = r2[0]
                cur.execute(
                    "UPDATE Article SET pdf_master_path = ? WHERE id = ?;",
                    (new_master, article_id),
                )
                report.updated_master_path_to = new_master

        conn.commit()

    if delete_physical_pdf:
        p = Path(pdf_path)
        abs_path = p if p.is_absolute() else (project_home / p)
        _safe_unlink(abs_path, report)

    return report


def delete_article_everywhere(
    *,
    article_id: int,
    delete_physical_pdfs: bool,
    delete_ai_files: bool,
) -> DeleteReport:
    report = DeleteReport(article_id=article_id, mode="article_everywhere")
    project_home = get_project_home_dir()

    pdf_paths = list_article_pdf_paths(article_id)
    paths = get_article_paths(article_id)
    if not paths:
        report.errors.append("article_not_found")
        return report

    ai_candidates: list[str] = []
    if delete_ai_files:
        # lecture_* поля сохраняются в схеме БД, но в новой версии не используются;
        # если в старой БД они заполнены — удаляем заодно.
        for k in ("json_path", "summary_path", "lecture_text_path", "lecture_audio_path"):
            v = paths.get(k)
            if v:
                ai_candidates.append(v)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM Article WHERE id = ?;", (article_id,))
        report.removed_article_row = (cur.rowcount > 0)
        conn.commit()

    if delete_physical_pdfs:
        for pdf_path in pdf_paths:
            p = Path(pdf_path)
            abs_path = p if p.is_absolute() else (project_home / p)
            _safe_unlink(abs_path, report)

    if delete_ai_files:
        for rel_or_abs in ai_candidates:
            p = Path(rel_or_abs)
            abs_path = p if p.is_absolute() else (project_home / p)
            _safe_unlink(abs_path, report)

    return report
