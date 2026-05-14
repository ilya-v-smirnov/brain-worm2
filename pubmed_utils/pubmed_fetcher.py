"""
PubMed metadata fetcher.

Использует Biopython (Bio.Entrez) для получения метаданных статьи по PMID.

NCBI требует указывать email при обращении к Entrez. Дефолт можно
переопределить, добавив ключ "pubmed_email" в settings.json.

Публичная функция: fetch_metadata_by_pmid(pmid) -> dict.
"""

from __future__ import annotations

import re
from http.client import HTTPException
from urllib.error import URLError

from Bio import Entrez


DEFAULT_EMAIL = "brain-worm-app@example.com"
DEFAULT_TOOL = "brain-worm"


def _get_email() -> str:
    """
    Email обязателен для NCBI. По умолчанию используем dummy-значение,
    но пользователь может переопределить через settings.json (ключ "pubmed_email").
    """
    try:
        # Импорт внутри функции — чтобы конфигурация загружалась лениво
        # и не падала, если settings нет (например, при модульных тестах).
        from config.settings import load_settings
        settings = load_settings()
        email = str(settings.get("pubmed_email") or "").strip()
        if email:
            return email
    except Exception:
        pass
    return DEFAULT_EMAIL


def _extract_doi(article: dict, pubmed_article: dict) -> str:
    """
    Достаёт DOI. Сначала ищет в ELocationID статьи, потом — в PubmedData/ArticleIdList.
    """
    elocs = article.get("ELocationID") or []
    for el in elocs:
        attrs = getattr(el, "attributes", {}) or {}
        if str(attrs.get("EIdType") or "").lower() == "doi":
            v = str(el).strip()
            if v:
                return v

    article_ids = (pubmed_article.get("PubmedData") or {}).get("ArticleIdList") or []
    for aid in article_ids:
        attrs = getattr(aid, "attributes", {}) or {}
        if str(attrs.get("IdType") or "").lower() == "doi":
            v = str(aid).strip()
            if v:
                return v

    return ""


def _extract_year(article: dict) -> str:
    """
    Достаёт год публикации. Сначала пробует Journal.JournalIssue.PubDate.Year,
    потом MedlineDate (где год может быть встроен в строку типа "2019 Mar").
    """
    journal = article.get("Journal") or {}
    pub_date = (journal.get("JournalIssue") or {}).get("PubDate") or {}

    year = str(pub_date.get("Year") or "").strip()
    if year:
        return year

    medline_date = str(pub_date.get("MedlineDate") or "").strip()
    m = re.search(r"\b(\d{4})\b", medline_date)
    if m:
        return m.group(1)

    return ""


def _format_authors(raw_authors: list) -> tuple[str, str]:
    """
    Возвращает (first_author, authors_all) в формате "Lastname Initials"
    (PubMed-style: "Smith JA", "Doe MK").

    Коллективных авторов (CollectiveName) тоже учитывает.
    """
    formatted: list[str] = []
    for a in raw_authors:
        if "LastName" in a:
            last = str(a.get("LastName") or "").strip()
            initials = str(a.get("Initials") or "").strip()
            if last and initials:
                formatted.append(f"{last} {initials}")
            elif last:
                formatted.append(last)
        elif "CollectiveName" in a:
            cn = str(a.get("CollectiveName") or "").strip()
            if cn:
                formatted.append(cn)

    first = formatted[0] if formatted else ""
    full = ", ".join(formatted)
    return first, full


def fetch_metadata_by_pmid(pmid: str) -> dict[str, str]:
    """
    Запрашивает метаданные статьи у NCBI PubMed по идентификатору PMID.

    Возвращает словарь с ключами:
        pmid, first_author, authors, journal, doi, year
    Все значения — строки (могут быть пустыми, если NCBI не вернул соответствующее поле).

    Бросает:
        ValueError   — PMID пустой, не число, или запись не найдена.
        RuntimeError — сетевая/HTTP ошибка при обращении к NCBI.
    """
    pmid_clean = str(pmid).strip()
    if not pmid_clean:
        raise ValueError("PMID is empty.")
    if not pmid_clean.isdigit():
        raise ValueError(f"PMID must be a number, got {pmid_clean!r}.")

    Entrez.email = _get_email()
    Entrez.tool = DEFAULT_TOOL

    try:
        handle = Entrez.efetch(db="pubmed", id=pmid_clean, retmode="xml")
    except URLError as e:
        raise RuntimeError(f"Network error while contacting PubMed:\n{e}") from e
    except HTTPException as e:
        raise RuntimeError(f"HTTP error while contacting PubMed:\n{e}") from e

    try:
        try:
            records = Entrez.read(handle)
        except Exception as e:
            raise ValueError(f"Failed to parse PubMed response: {e}") from e
    finally:
        try:
            handle.close()
        except Exception:
            pass

    pubmed_articles = records.get("PubmedArticle") or []
    if not pubmed_articles:
        raise ValueError(f"PMID {pmid_clean} not found in PubMed.")

    pa = pubmed_articles[0]
    medline = pa.get("MedlineCitation") or {}
    article = medline.get("Article") or {}

    first_author, authors_all = _format_authors(article.get("AuthorList") or [])

    journal = article.get("Journal") or {}
    journal_abbrev = str(
        journal.get("ISOAbbreviation")
        or journal.get("Title")
        or ""
    ).strip()

    doi = _extract_doi(article, pa)
    year = _extract_year(article)

    return {
        "pmid": pmid_clean,
        "first_author": first_author,
        "authors": authors_all,
        "journal": journal_abbrev,
        "doi": doi,
        "year": year,
    }
