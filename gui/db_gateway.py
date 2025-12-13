from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dbmanager.db_core import get_project_home_dir, init_db_schema as _init_db_schema
from dbmanager.db_maintenance import (
    sync_article_database as _sync_article_database,
    extract_contents_for_new_articles as _extract_contents_for_new_articles,
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

    def resolve_path(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        return p if p.is_absolute() else (self.project_home / p)


# Backwards-compatible alias in case older code imports DBGateway
DBGateway = DbGateway
