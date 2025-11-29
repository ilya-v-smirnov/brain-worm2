#!/usr/bin/env python3
"""
Извлечение названия статьи и года публикации из PDF.

Основной интерфейс:
    from pdfparser.pdf_extract_title_year import extract_title_and_year

CLI:
    python -m pdfparser.pdf_extract_title_year path/to/file_or_dir

Результат:
    {
        "file_name": "sample.pdf",
        "title": "Engineered IgG1-Fc Molecules...",
        "year": "2017",          # всегда строка (или "" если не найден)
        "method": "scipdf" | "llm" | "hybrid" | "unknown",
        "parsing_error": None | "<описание ошибки>",
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

import warnings
try:
    # Глушим предупреждение BeautifulSoup про HTML-парсер на XML
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    # Если bs4 нет или структура другая — просто не глушим, но не падаем
    pass

from scipdf import parse_pdf_to_dict


# ---------- Конфиг ----------

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "config" / "settings.json"
YEAR_MIN = 1980
YEAR_MAX = 2050
LLM_TEXT_WORD_LIMIT = 150


@dataclass
class ExtractResult:
    file_name: str
    title: str
    year: str
    method: str  # "scipdf" | "llm" | "hybrid" | "unknown"
    parsing_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "title": self.title,
            "year": self.year,
            "method": self.method,
            "parsing_error": self.parsing_error,
        }


# ---------- Утилиты ----------

def _load_settings() -> dict:
    """
    Загружает конфиг из config/settings.json.
    Ошибки не фатальные: при проблемах возвращает пустой словарь.
    """
    try:
        if SETTINGS_PATH.is_file():
            with SETTINGS_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        # Не роняем парсер, просто идём без LLM
        return {}
    return {}


def _extract_year_from_pub_date(pub_date) -> Optional[str]:
    """
    Пытается извлечь год публикации из поля pub_date (строка/словарь/число),
    ограничиваясь диапазоном YEAR_MIN–YEAR_MAX.
    Возвращает строку с годом или None.
    """

    # Случай: год уже числом
    if isinstance(pub_date, int):
        if YEAR_MIN <= pub_date <= YEAR_MAX:
            return str(pub_date)
        return None

    # Случай: словарь
    if isinstance(pub_date, dict):
        for key in ("year", "pub_year", "publication_year"):
            val = pub_date.get(key)
            if isinstance(val, int) and YEAR_MIN <= val <= YEAR_MAX:
                return str(val)
            if isinstance(val, str):
                y = _extract_year_from_pub_date(val)
                if y is not None:
                    return y
        # если не нашли, пробуем по всем строковым значениям
        for val in pub_date.values():
            if isinstance(val, str):
                y = _extract_year_from_pub_date(val)
                if y is not None:
                    return y
        return None

    # Случай: строка
    if isinstance(pub_date, str):
        # Ищем все 4-значные числа, потом фильтруем по диапазону
        candidates = re.findall(r"\b(\d{4})\b", pub_date)
        for c in candidates:
            year_int = int(c)
            if YEAR_MIN <= year_int <= YEAR_MAX:
                return str(year_int)
        return None

    # Остальные типы нас не интересуют
    return None


def _collect_initial_text(article_dict: dict, word_limit: int = LLM_TEXT_WORD_LIMIT) -> str:
    """
    Собирает первые ~word_limit слов из текста статьи для передачи в LLM.
    Пытаемся использовать article_dict["sections"], где каждый элемент имеет поле "text" или подобное.
    Если структурированный текст не найден, fallback на title + abstract.
    """
    words: list[str] = []

    sections = article_dict.get("sections") or []
    for sec in sections:
        # В разных версиях scipdf поле может называться "text" или "paragraph"
        txt = sec.get("text") or sec.get("paragraph") or ""
        if not isinstance(txt, str):
            continue
        words.extend(txt.split())
        if len(words) >= word_limit:
            break

    if not words:
        # Fallback: title + abstract
        title = article_dict.get("title") or ""
        abstract = article_dict.get("abstract") or ""
        combined = f"{title}\n\n{abstract}"
        words = combined.split()

    truncated_words = words[:word_limit]
    return " ".join(truncated_words)


# ---------- LLM интеграция ----------

def _infer_title_year_with_llm(pdf_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Вызывает LLM (по умолчанию gpt-4.1-mini) для извлечения
    названия и года публикации из текстового фрагмента статьи.

    Ожидаемый ответ модели — ЧИСТЫЙ JSON:
        {"title": "...", "year": "YYYY"}

    При любой ошибке возвращает (None, None).
    """
    settings = _load_settings()
    api_key = settings.get("openai_api_key") or ""
    model = settings.get("default_model") or "gpt-4.1-mini"

    if not api_key:
        # Нет ключа — тихо выходим
        return None, None

    try:
        # Импортируем только если реально используем LLM
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=api_key)

        system_prompt = (
            "You are an assistant that extracts bibliographic metadata from scientific articles. "
            "Given the beginning of a scientific article, you must infer the exact article title "
            "and its publication year. "
            "If you are unsure about the year, leave it as an empty string.\n\n"
            "Return STRICTLY a JSON object with exactly two fields: 'title' and 'year', e.g.:\n"
            "{\"title\": \"...\", \"year\": \"2017\"}"
        )

        user_prompt = (
            "Here is the beginning of a scientific article in plain text. "
            "Extract the article's title and the publication year.\n\n"
            f"TEXT START:\n{pdf_text}\nTEXT END."
        )

        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        # Вытаскиваем текстовый ответ
        # Структура может немного отличаться в разных версиях клиента,
        # поэтому делаем максимально осторожно.
        content_items = getattr(response, "output", None) or getattr(response, "choices", None)
        if not content_items:
            return None, None

        # Пытаемся вытащить текст (конкретная структура зависит от версии SDK)
        text = None
        # Вариант через response.output[0].content[0].text (Responses API)
        try:
            text = content_items[0].content[0].text  # type: ignore
        except Exception:
            # Вариант через responses-like/legacy API
            try:
                text = content_items[0].message["content"]  # type: ignore
            except Exception:
                pass

        if not isinstance(text, str) or not text.strip():
            return None, None

        # Ожидаем, что text — JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Иногда модель может обернуть JSON в текст, попробуем вытащить фигурные скобки
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None, None
            try:
                data = json.loads(match.group(0))
            except Exception:
                return None, None

        title = data.get("title")
        year = data.get("year")

        if title is not None:
            title = str(title).strip()

        if year is not None:
            year = str(year).strip()
            # фильтруем по диапазону, если год выглядит как 4 цифры
            if re.fullmatch(r"\d{4}", year):
                year_int = int(year)
                if not (YEAR_MIN <= year_int <= YEAR_MAX):
                    year = ""
        return title or None, year or None

    except Exception:
        # Любые ошибки LLM не должны ломать общий парсер
        return None, None


# ---------- Основная функция ----------

def extract_title_and_year(
    pdf_path: Union[str, Path],
    use_llm_fallback: bool = True,
    grobid_url: str = "http://localhost:8070",
    print_result: bool = False,
    force_llm: bool = False,
) -> dict:
    """
    Извлекает название статьи и год публикации из PDF-файла.

    :param pdf_path: путь к PDF
    :param use_llm_fallback: использовать ли LLM, если scipdf не дал результата
    :param grobid_url: URL сервиса GROBID (по умолчанию локальный Docker)
    :param print_result: печатать ли результат в консоль
    :return: словарь с ключами:
             file_name, title, year (string), method, parsing_error
    """
    path = Path(pdf_path)
    result = ExtractResult(
        file_name=path.name,
        title="",
        year="",
        method="unknown",
        parsing_error=None,
    )

    try:
        article = parse_pdf_to_dict(str(path), grobid_url=grobid_url)
    except Exception as e:
        result.parsing_error = f"scipdf_error: {type(e).__name__}: {e}"
        if print_result:
            _print_result(result)
        return result.to_dict()

    # 1. Пытаемся получить title и year из scipdf
    title_scipdf = article.get("title") if isinstance(article, dict) else None
    pub_date = article.get("pub_date") if isinstance(article, dict) else None
    year_scipdf = _extract_year_from_pub_date(pub_date)

    title = (title_scipdf or "").strip()
    year = (year_scipdf or "").strip()
    method = "scipdf" if (title or year) else "unknown"

    # 2. При необходимости — LLM fallback
    # Ветка LLM сработает, если:
    #   - force_llm == True (всегда), ИЛИ
    #   - не хватает title или year
    if use_llm_fallback and (force_llm or not title or not year):
        text_for_llm = _collect_initial_text(article)
        llm_title, llm_year = _infer_title_year_with_llm(text_for_llm)

        # Если scipdf ничего не дал, а LLM смог — метод = "llm"
        # Если scipdf что-то дал, а LLM что-то улучшил — метод = "hybrid"
        if llm_title and not title:
            title = llm_title
            method = "llm" if method == "unknown" else "hybrid"

        if llm_year and not year:
            year = llm_year
            method = "llm" if method == "unknown" else "hybrid"

    # 3. Финализируем результат
    result.title = title
    result.year = year
    result.method = method

    if not title and not year and result.parsing_error is None:
        result.parsing_error = "Could not infer title or year from scipdf or LLM."

    if print_result:
        _print_result(result)

    return result.to_dict()


def _print_result(result: ExtractResult) -> None:
    """
    Короткий принт в консоль (для человека).
    Title усечён до 50 символов.
    """
    title_short = result.title if result.title else "<none>"
    if len(title_short) > 50:
        title_short = title_short[:47] + "..."

    year_display = result.year if result.year else "<unknown>"

    line = (
        f"{result.file_name} | "
        f"Title: {title_short} | "
        f"Year: {year_display} | "
        f"method={result.method}"
    )

    print(line)
    if result.parsing_error:
        print(f"  [parsing_error] {result.parsing_error}", file=sys.stderr)


# ---------- CLI ----------

def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract article title and publication year from PDF using scipdf (+ optional LLM fallback)."
    )
    parser.add_argument(
        "path",
        help="Path to a PDF file or a directory containing PDF files.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM fallback (use only scipdf).",
    )
    parser.add_argument(
        "--grobid-url",
        default="http://localhost:8070",
        help="GROBID service URL (default: http://localhost:8070).",
    )
    parser.add_argument(
        "--force-llm",
        action="store_true",
        help="Force LLM usage even if scipdf already found both title and year.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    path = Path(args.path)

    if not path.exists():
        print(f"[ERROR] Path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    use_llm = not args.no_llm

    pdf_files: list[Path] = []
    if path.is_file():
        pdf_files = [path]
    elif path.is_dir():
        pdf_files = sorted(p for p in path.glob("*.pdf") if p.is_file())
        if not pdf_files:
            print(f"[WARN] No PDF files found in directory: {path}", file=sys.stderr)
    else:
        print(f"[ERROR] Path is neither file nor directory: {path}", file=sys.stderr)
        sys.exit(1)

    use_llm = not args.no_llm
    force_llm = args.force_llm

    for pdf in pdf_files:
        print(f"[INFO] Processing: {pdf}")
        extract_title_and_year(
            pdf_path=pdf,
            use_llm_fallback=use_llm,
            grobid_url=args.grobid_url,
            print_result=True,
            force_llm=force_llm,
        )



if __name__ == "__main__":
    main()
