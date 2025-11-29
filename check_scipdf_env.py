#!/usr/bin/env python3
"""
Проверка окружения для scipdf + GROBID.

Запуск:
    source venv/bin/activate
    python check_scipdf_env.py example_pdfs/sample.pdf

Если аргумент не указан, используется example_pdfs/sample.pdf по умолчанию.
"""

import sys
from pathlib import Path

try:
    from scipdf import parse_pdf_to_dict
except ImportError as e:
    print("[ERROR] Не удалось импортировать scipdf. Установите пакет в активном venv:")
    print("        pip install scipdf-parser")
    sys.exit(1)


def main():
    # 1. Определяем путь к PDF
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1])
    else:
        pdf_path = Path("example_pdfs/sample.pdf")

    if not pdf_path.is_file():
        print(f"[ERROR] Файл PDF не найден: {pdf_path.resolve()}")
        print("Создайте папку example_pdfs и положите туда хотя бы один PDF,")
        print("например: example_pdfs/sample.pdf")
        sys.exit(1)

    print(f"[INFO] Используем PDF: {pdf_path.resolve()}")

    # 2. Парсим PDF через scipdf + GROBID
    try:
        print("[INFO] Парсим PDF c помощью scipdf.parse_pdf_to_dict(...)")
        article_dict = parse_pdf_to_dict(
            str(pdf_path),
            grobid_url="http://localhost:8070"  # стандартный порт GROBID
        )
    except Exception as e:
        print("[ERROR] Ошибка при разборе PDF через scipdf:")
        print(f"        {type(e).__name__}: {e}")
        print("Проверьте, что GROBID запущен (docker ps) и доступен на порту 8070.")
        sys.exit(1)

    # 3. Выводим основные поля, чтобы убедиться, что структура корректна
    print("\n[OK] PDF успешно разобран. Ключи верхнего уровня в article_dict:")
    print("     ", list(article_dict.keys()))

    title = article_dict.get("title") or "<нет title>"
    print(f"[INFO] Заголовок статьи (title): {title[:80]!r}")

    # Многие статьи содержат год либо в metadata, либо в refs; здесь только проверяем наличие
    year = None
    metadata = article_dict.get("metadata") or {}
    if isinstance(metadata, dict):
        year = metadata.get("year") or metadata.get("publication_year")

    print(f"[INFO] Предполагаемый год публикации из metadata: {year!r}")

    # 4. Мини-проверка наличия разделов (introduction, body_text, references и т.п.)
    sections = article_dict.get("sections") or article_dict.get("body_text")
    if sections:
        print("[INFO] Найдено количество секций/абзацев:", len(sections))
    else:
        print("[WARN] Не удалось найти разделы текста (sections/body_text пусты или отсутствуют).")


if __name__ == "__main__":
    main()
