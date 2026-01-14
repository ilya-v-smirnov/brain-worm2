from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dbmanager.db_core import get_project_home_dir, init_db_schema as _init_db_schema
from dbmanager.db_maintenance import (
    sync_article_database as _sync_article_database,
    extract_contents_for_new_articles as _extract_contents_for_new_articles,
    list_article_pdf_paths as _list_article_pdf_paths,
    get_article_paths as _get_article_paths,
    set_article_summary_path as _set_article_summary_path,
    delete_single_pdf_path as _delete_single_pdf_path,
    delete_article_everywhere as _delete_article_everywhere,
    parse_pdf_for_article as _parse_pdf_for_article,
    reconcile_article_paths as _reconcile_article_paths,
    set_article_json_path as _set_article_json_path,
)


@dataclass(frozen=True)
class FileRow:
    article_id: int
    pdf_path: str
    summary_path: str | None
    lecture_text_path: str | None
    lecture_audio_path: str | None


class DbGateway:
    """Тонкая прослойка GUI -> backend (по требованиям ТЗ)."""

    def __init__(self) -> None:
        self.project_home: Path = get_project_home_dir()
        self.db_path: Path = self.project_home / "article_index.db"

    # ---- Pipeline wrappers ----

    def init_db_schema(self) -> None:
        _init_db_schema()

    def sync_article_database(self) -> None:
        _sync_article_database()

    def reconcile_article_paths(self) -> dict[str, int]:
        return _reconcile_article_paths()

    def extract_contents_for_new_articles(self) -> None:
        _extract_contents_for_new_articles()

    # ---- Read operations ----

    def fetch_file_rows(self) -> list[FileRow]:
        if not self.db_path.exists():
            return []

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                '''
                SELECT
                    af.article_id,
                    af.pdf_path,
                    a.summary_path,
                    a.lecture_text_path,
                    a.lecture_audio_path
                FROM ArticleFile af
                JOIN Article a ON a.id = af.article_id
                ORDER BY af.pdf_path ASC;
                '''
            )
            out: list[FileRow] = []
            for r in cur.fetchall():
                out.append(
                    FileRow(
                        article_id=int(r[0]),
                        pdf_path=str(r[1]),
                        summary_path=r[2],
                        lecture_text_path=r[3],
                        lecture_audio_path=r[4],
                    )
                )
            return out
        finally:
            conn.close()

    def fetch_json_path_for_article(self, article_id: int) -> str | None:
        """Достаёт Article.json_path по id статьи."""
        if not self.db_path.exists():
            return None

        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT json_path FROM Article WHERE id = ?;", (article_id,))
            row = cur.fetchone()
            return None if not row else row[0]
        finally:
            conn.close()


    def set_summary_path_for_article(self, article_id: int, docx_abs_path: Path) -> str:
        """
        Сохраняет путь к summary docx в БД (Article.summary_path).

        В БД пишется путь ОТНОСИТЕЛЬНО project_home, если файл лежит внутри него,
        иначе пишется абсолютный путь.
        Возвращает строку, записанную в БД.
        """
        docx_abs_path = Path(docx_abs_path).resolve()
        try:
            rel = docx_abs_path.relative_to(self.project_home)
            rel_str = str(rel)
        except Exception:
            rel_str = str(docx_abs_path)

        _set_article_summary_path(article_id, rel_str)
        return rel_str
    
    
    def set_json_path_for_article(self, article_id: int, json_abs_path: Path) -> str:
        """
        Сохраняет путь к extracted JSON в БД (Article.json_path).

        В БД пишется путь ОТНОСИТЕЛЬНО project_home, если файл лежит внутри него,
        иначе пишется абсолютный путь.
        Возвращает строку, записанную в БД.
        """
        json_abs_path = Path(json_abs_path).resolve()
        try:
            rel = json_abs_path.relative_to(self.project_home)
            rel_str = str(rel)
        except Exception:
            rel_str = str(json_abs_path)

        _set_article_json_path(article_id, rel_str)
        return rel_str


    # ---- Delete / re-extract helpers for GUI ----

    def list_article_pdf_paths(self, article_id: int) -> list[str]:
        return _list_article_pdf_paths(article_id)

    def get_article_paths(self, article_id: int) -> dict[str, str | None]:
        return _get_article_paths(article_id)

    def delete_single_pdf_path(
        self,
        *,
        article_id: int,
        pdf_path: str,
        delete_physical_pdf: bool,
    ) -> DeleteReport:
        return _delete_single_pdf_path(
            article_id=article_id,
            pdf_path=pdf_path,
            delete_physical_pdf=delete_physical_pdf,
        )

    def delete_article_everywhere(
        self,
        *,
        article_id: int,
        delete_physical_pdfs: bool,
        delete_ai_files: bool,
    ) -> DeleteReport:
        return _delete_article_everywhere(
            article_id=article_id,
            delete_physical_pdfs=delete_physical_pdfs,
            delete_ai_files=delete_ai_files,
        )

    def parse_pdf_for_article(self, pdf_rel_or_abs: str) -> dict:
        pdf_abs = self.resolve_path(pdf_rel_or_abs)
        return _parse_pdf_for_article(pdf_abs)

    def resolve_path(self, rel_or_abs: str) -> Path:
        """
        Преобразует относительный путь (относительно корня проекта) в абсолютный.
        Если путь уже абсолютный — возвращает его как есть.
        """
        p = Path(rel_or_abs)
        return p if p.is_absolute() else (self.project_home / p)


# Backwards-compatible alias in case older code imports DBGateway
DBGateway = DbGateway
