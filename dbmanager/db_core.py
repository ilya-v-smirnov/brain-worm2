
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


# Путь к корню проекта (каталог, где лежит dbmanager, config и т.п.)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"


def load_settings() -> dict:
    """
    Загружает JSON-настройки проекта из config/settings.json.

    Ожидается, что в настройках есть ключ PROJECT_HOME_DIR.
    """
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_project_home_dir() -> Path:
    """
    Возвращает Path к PROJECT_HOME_DIR из настроек.

    Пути вроде "~/..." будут развёрнуты через expanduser().
    """
    settings = load_settings()
    project_home = settings["PROJECT_HOME_DIR"]
    return Path(project_home).expanduser().resolve()


def get_db_path() -> Path:
    """
    Путь к файлу SQLite-базы данных: <PROJECT_HOME_DIR>/article_index.db.
    """
    return get_project_home_dir() / "article_index.db"


def get_connection() -> sqlite3.Connection:
    """
    Открывает соединение с SQLite-базой.

    Всегда включает поддержку внешних ключей (PRAGMA foreign_keys = ON).
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    """
    Создаёт таблицы Article и ArticleFile, если они ещё не существуют.

    Если conn не передан, функция сама открывает и закрывает соединение.
    """
    own_conn = False
    if conn is None:
        conn = get_connection()
        own_conn = True

    try:
        cursor = conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS Article (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash          TEXT NOT NULL UNIQUE,
                year               INTEGER NOT NULL,
                title              TEXT NOT NULL,
                pdf_master_path    TEXT NOT NULL,
                json_path          TEXT,
                summary_path       TEXT,
                lecture_text_path  TEXT,
                lecture_audio_path TEXT
            );

            CREATE TABLE IF NOT EXISTS ArticleFile (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                pdf_path   TEXT NOT NULL,
                FOREIGN KEY(article_id)
                    REFERENCES Article(id)
                    ON DELETE CASCADE,
                UNIQUE(article_id, pdf_path)
            );

            CREATE INDEX IF NOT EXISTS idx_articlefile_pdf_path
                ON ArticleFile(pdf_path);

            CREATE INDEX IF NOT EXISTS idx_articlefile_article_id
                ON ArticleFile(article_id);
            """
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()
