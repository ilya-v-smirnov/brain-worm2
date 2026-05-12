from __future__ import annotations

import argparse
from collections import Counter
from typing import List, Optional

from dbmanager.db_core import init_db_schema
from dbmanager.new_manager import process_all_new_pdfs, NewPdfResult
from dbmanager.db_maintenance import sync_article_database


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


# ---------- Команды CLI ----------


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


def cmd_run_all(args: argparse.Namespace) -> None:
    """
    Команда: полный пайплайн: Этап 1 -> Этап 2.

    Экстракция JSON в новой версии выполняется вручную из GUI
    (окно "Extracted Text"), поэтому в CLI её больше нет.
    """
    init_db_schema()

    print("=== Этап 1: обработка новых PDF в !New ===")
    new_results = process_all_new_pdfs()
    _print_new_pdfs_summary(new_results)

    print("\n=== Этап 2: синхронизация Article Database с БД ===")
    new_article_ids = sync_article_database()
    _print_sync_summary(new_article_ids)


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

    # run-all
    p_all = subparsers.add_parser(
        "run-all",
        help="Запустить все этапы по очереди: Этап 1 -> Этап 2.",
    )
    p_all.set_defaults(func=cmd_run_all)

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
