from __future__ import annotations

import argparse
from collections import Counter
from typing import List, Optional

from dbmanager.db_core import init_db_schema
from dbmanager.new_manager import process_all_new_pdfs, NewPdfResult
from dbmanager.db_maintenance import (
    sync_article_database,
    extract_contents_for_new_articles,
    delete_article_from_db,
    cleanup_missing_files,
)


# ---------- Вспомогательные функции вывода ----------


def _print_new_pdfs_summary(results: List[NewPdfResult]) -> None:
    if not results:
        print("Нет новых PDF-файлов в папке !New.")
        return

    counter = Counter(r.classification for r in results)
    print("Результаты обработки новых PDF в !New:")
    for cls in ("unique", "duplicate", "manual_review"):
        if cls in counter:
            print(f"  {cls:>13}: {counter[cls]}")

    print("\nПодробности:")
    for r in results:
        print(
            f"- {r.source_path.name} -> {r.final_path} "
            f"[{r.classification}]"
        )
        if r.parsing_error:
            print(f"    parsing_error: {r.parsing_error}")


def _print_sync_summary(new_article_ids: List[int]) -> None:
    print("Синхронизация Article Database с БД завершена.")
    if not new_article_ids:
        print("  Новых уникальных статей не найдено.")
    else:
        print(f"  Добавлено новых уникальных статей: {len(new_article_ids)}")


def _print_extract_summary(processed_ids: List[int]) -> None:
    print("Экстракция содержимого статей (JSON) завершена.")
    if not processed_ids:
        print("  Нет статей без JSON или все такие статьи уже были обработаны.")
    else:
        print(f"  JSON сгенерирован/обновлён для статей: {len(processed_ids)}")


# ---------- Команды CLI (Этапы 1–3) ----------


def cmd_process_new(args: argparse.Namespace) -> None:
    """
    Команда: обработка новых PDF в !New (Этап 1).
    """
    init_db_schema()
    results = process_all_new_pdfs()
    _print_new_pdfs_summary(results)


def cmd_sync_db(args: argparse.Namespace) -> None:
    """
    Команда: синхронизация Article Database с БД (Этап 2).
    """
    init_db_schema()
    new_article_ids = sync_article_database()
    _print_sync_summary(new_article_ids)


def cmd_extract_json(args: argparse.Namespace) -> None:
    """
    Команда: экстракция содержимого статей в JSON (Этап 3).
    """
    init_db_schema()
    limit: Optional[int] = args.limit
    processed_ids = extract_contents_for_new_articles(limit=limit)
    _print_extract_summary(processed_ids)


def cmd_run_all(args: argparse.Namespace) -> None:
    """
    Команда: полный пайплайн: Этап 1 -> Этап 2 -> Этап 3.
    """
    init_db_schema()

    print("=== Этап 1: обработка новых PDF в !New ===")
    new_results = process_all_new_pdfs()
    _print_new_pdfs_summary(new_results)

    print("\n=== Этап 2: синхронизация Article Database с БД ===")
    new_article_ids = sync_article_database()
    _print_sync_summary(new_article_ids)

    print("\n=== Этап 3: экстракция содержимого в JSON ===")
    if new_article_ids:
        processed_ids = extract_contents_for_new_articles(
            article_ids=new_article_ids
        )
    else:
        processed_ids = []
    _print_extract_summary(processed_ids)


# ---------- Команды CLI (Этап 4: удаление и чистка) ----------


def cmd_delete(args: argparse.Namespace) -> None:
    """
    Команда: удаление статьи/файла из БД (и опционально с диска).
    """
    init_db_schema()

    article_id = args.article_id
    file_hash = args.file_hash
    pdf_path = args.pdf_path
    mode = args.mode

    identifiers = [article_id is not None, file_hash is not None, pdf_path is not None]
    if sum(identifiers) != 1:
        print("ОШИБКА: нужно указать ровно один идентификатор: "
              "--article-id ИЛИ --file-hash ИЛИ --pdf-path.")
        return

    if mode == "db_and_files" and not args.yes_i_know:
        print(
            "ОШИБКА: режим db_and_files удаляет файлы с диска.\n"
            "Для подтверждения добавьте флаг: --yes-i-know"
        )
        return

    affected = delete_article_from_db(
        article_id=article_id,
        file_hash=file_hash,
        pdf_path=pdf_path,
        mode=mode,
    )

    if pdf_path is not None:
        print(f"Удалено записей ArticleFile: {affected}")
    else:
        print(f"Удалено статей Article: {affected}")


def cmd_cleanup(args: argparse.Namespace) -> None:
    """
    Команда: чистка ссылок на отсутствующие файлы.
    """
    init_db_schema()

    dry_run = not args.apply
    report = cleanup_missing_files(dry_run=dry_run)

    if dry_run:
        print("РЕЖИМ ПРОСМОТРА (dry run): ничего не удалено.")
    else:
        print("ЧИСТКА ВЫПОЛНЕНА.")

    print(f"  Устаревших строк ArticleFile (pdf не существует): "
          f"{report['stale_articlefile_rows']}")
    print(f"  Статей с битой ссылкой на json/summary/lecture: "
          f"{report['articles_with_missing_assets']}")


# ---------- Разбор аргументов и точка входа ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dbmanager.cli",
        description="Утилита управления базой статей (Article Database).",
    )

    subparsers = parser.add_subparsers(
        title="Команды",
        dest="command",
        required=True,
    )

    # process-new
    p_new = subparsers.add_parser(
        "process-new",
        help="Обработать новые PDF-файлы в папке !New (Этап 1).",
    )
    p_new.set_defaults(func=cmd_process_new)

    # sync-db
    p_sync = subparsers.add_parser(
        "sync-db",
        help="Синхронизировать Article Database с БД (Этап 2).",
    )
    p_sync.set_defaults(func=cmd_sync_db)

    # extract-json
    p_extract = subparsers.add_parser(
        "extract-json",
        help="Сгенерировать JSON для статей без json_path (Этап 3).",
    )
    p_extract.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить максимум статей для обработки за один запуск.",
    )
    p_extract.set_defaults(func=cmd_extract_json)

    # run-all
    p_all = subparsers.add_parser(
        "run-all",
        help="Запустить все этапы по очереди: Этап 1 -> Этап 2 -> Этап 3.",
    )
    p_all.set_defaults(func=cmd_run_all)

    # delete
    p_delete = subparsers.add_parser(
        "delete",
        help="Удалить статью или отдельный PDF (Этап 4.2).",
    )
    p_delete.add_argument(
        "--article-id",
        type=int,
        help="ID статьи в таблице Article, которую нужно удалить.",
    )
    p_delete.add_argument(
        "--file-hash",
        type=str,
        help="file_hash статьи, которую нужно удалить.",
    )
    p_delete.add_argument(
        "--pdf-path",
        type=str,
        help="Путь к PDF (как в БД или абсолютный), для удаления только этой копии.",
    )
    p_delete.add_argument(
        "--mode",
        choices=["db_only", "db_and_files"],
        default="db_only",
        help="Режим удаления: только из БД или из БД и с диска.",
    )
    p_delete.add_argument(
        "--yes-i-know",
        action="store_true",
        help="Подтверждение для режима db_and_files (удаление файлов с диска).",
    )
    p_delete.set_defaults(func=cmd_delete)

    # cleanup
    p_cleanup = subparsers.add_parser(
        "cleanup",
        help="Найти и (опционально) удалить устаревшие ссылки на отсутствующие файлы.",
    )
    p_cleanup.add_argument(
        "--apply",
        action="store_true",
        help="По умолчанию выполняется только dry run. "
             "Добавьте --apply, чтобы реально удалить устаревшие строки ArticleFile.",
    )
    p_cleanup.set_defaults(func=cmd_cleanup)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    func(args)


if __name__ == "__main__":
    main()
