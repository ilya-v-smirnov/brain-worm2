
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from dbmanager.db_core import get_project_home_dir, get_connection
from pdfparser.pdf_extract_content import parse_pdf_content


# ---------- Общие утилиты для Article Database ----------


def _get_article_database_root() -> Path:
    """
    Корневой каталог 'Article Database' внутри PROJECT_HOME_DIR.
    """
    project_home = get_project_home_dir()
    return project_home / "Article Database"


def _get_contents_dir() -> Path:
    """
    Каталог для JSON-файлов с содержимым статей: '<PROJECT_HOME_DIR>/Contents'.
    """
    project_home = get_project_home_dir()
    return project_home / "Contents"


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
    уникальные на этом этапе (для дальнейшей экстракции содержимого на Этапе 3).
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


# ---------- Этап 3: экстракция содержимого новых статей в JSON ----------


def _save_json_file(data: Dict[str, Any], out_path: Path) -> None:
    """
    Сохраняет словарь в JSON с ensure_ascii=False и отступами.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_contents_for_new_articles(
    article_ids: Optional[List[int]] = None,
    limit: Optional[int] = None,
) -> List[int]:
    """
    Этап 3: экстракция содержимого новых статей.

    Берёт статьи из Article, у которых json_path пустой, и для каждой:
      - читает pdf_master_path (относительно PROJECT_HOME_DIR),
      - вызывает parse_pdf_content(pdf_path),
      - сохраняет результат в JSON-файл в '<PROJECT_HOME_DIR>/Contents',
        имя файла совпадает с именем исходного PDF, но с расширением .json,
      - обновляет поле json_path в Article.

    Параметры:
        article_ids:
            если передан список, обрабатываются только статьи с этими id
            (и json_path IS NULL/пустой).
        limit:
            максимальное количество статей за один вызов (опционально).

    Возвращает список id статей, для которых JSON был создан/обновлён.
    """
    project_home = get_project_home_dir()
    contents_dir = _get_contents_dir()
    contents_dir.mkdir(parents=True, exist_ok=True)

    processed_ids: List[int] = []

    # Строим запрос к Article
    where_clauses = ["(json_path IS NULL OR json_path = '')"]
    params: List[Any] = []

    if article_ids:
        # Если список пуст, просто ничего не делаем
        placeholders = ", ".join("?" for _ in article_ids)
        where_clauses.append(f"id IN ({placeholders})")
        params.extend(article_ids)

    where_sql = " AND ".join(where_clauses)
    sql = f"SELECT id, pdf_master_path FROM Article WHERE {where_sql}"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        for article_id, pdf_rel_path in rows:
            # pdf_rel_path хранится относительно PROJECT_HOME_DIR
            pdf_abs_path = project_home / pdf_rel_path

            if not pdf_abs_path.is_file():
                # Файл физически отсутствует, оставляем json_path пустым.
                # Это может быть обработано на этапе чистки БД.
                continue

            # Пытаемся распарсить PDF
            try:
                parsed = parse_pdf_content(pdf_abs_path)
                # Гарантируем, что в результате есть ключ parsing_error
                parsed.setdefault("parsing_error", None)
            except Exception as e:
                # При ошибке парсинга всё равно сохраняем JSON с описанием ошибки
                parsed = {
                    "title": "",
                    "year": "",
                    "introduction": "",
                    "methods": "",
                    "results": [],
                    "discussion": "",
                    "figures": [],
                    "parsing_error": f"parse_pdf_content_error: {type(e).__name__}: {e}",
                }

            pdf_name = Path(pdf_rel_path).name
            json_name = Path(pdf_name).with_suffix(".json").name
            json_rel_path = (contents_dir.name + "/" + json_name)
            json_abs_path = project_home / json_rel_path

            _save_json_file(parsed, json_abs_path)

            # Обновляем Article.json_path относительным путём
            cur.execute(
                "UPDATE Article SET json_path = ? WHERE id = ?;",
                (json_rel_path, article_id),
            )

            processed_ids.append(article_id)

        conn.commit()

    return processed_ids
