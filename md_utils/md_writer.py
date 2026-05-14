"""
Export extracted article text (JSON content) to a Markdown (.md) file.

Markdown produced here mirrors the structure of export_extracted_text_to_docx:

    # <Year> <Title>

    **Source:** <pdf path>

    ## Introduction
    ...

    ## Methods
    ...

    ## Results

    ### <subsection title>
    ...

    ## Discussion
    ...

    ## Figures

    **<num>.** <caption>
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------- Helpers ----------


def _normalize_breaks(text: str) -> str:
    """
    Нормализует переносы строк, как в docx_writer:
      - CRLF / CR -> LF
      - VT, U+2028, U+2029, '^l' -> twin newline (новый абзац)
      - одиночные \\n превращаются в \\n\\n (новый абзац),
        но уже существующие \\n\\n сохраняются.
    """
    if not text:
        return ""
    s = str(text)

    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("^l", "\n\n")
    s = s.replace("\x0b", "\n\n")
    s = s.replace("\u2028", "\n\n")
    s = s.replace("\u2029", "\n\n")

    sentinel = "\uFFFF"
    s = s.replace("\n\n", sentinel)
    s = s.replace("\n", "\n\n")
    s = s.replace(sentinel, "\n\n")

    # Сжимаем кратные пустые строки до одной
    s = re.sub(r"\n{3,}", "\n\n", s)

    return s.strip()


def _section(title: str, body: str, *, level: int = 2) -> str:
    """Render a section with header + body. Empty body becomes em dash."""
    header = "#" * level
    body = _normalize_breaks(body)
    if not body:
        body = "—"
    return f"{header} {title}\n\n{body}\n"


def _metadata_block(metadata: dict[str, Any] | None) -> str:
    """
    Render metadata as a bullet list. Skipped entirely if all fields are empty.

    Returns a Markdown string ending with a trailing newline, or "" if there is
    nothing to render.
    """
    if not metadata or not isinstance(metadata, dict):
        return ""

    fields = [
        ("First author", str(metadata.get("first_author") or "").strip()),
        ("Authors",      str(metadata.get("authors") or "").strip()),
        ("Journal",      str(metadata.get("journal") or "").strip()),
        ("DOI",          str(metadata.get("doi") or "").strip()),
        ("PMID",         str(metadata.get("pmid") or "").strip()),
    ]

    nonempty = [(label, value) for label, value in fields if value]
    if not nonempty:
        return ""

    lines: list[str] = []
    for label, value in nonempty:
        lines.append(f"- **{label}:** {value}")

    return "\n".join(lines) + "\n"


def _metadata_block(metadata: dict[str, Any] | None) -> str:
    """
    Render Metadata section as a list of bold-prefixed entries.

    Returns empty string if metadata is missing or all fields are empty
    (we do not write an empty "## Metadata" header in this case).
    """
    if not metadata or not isinstance(metadata, dict):
        return ""

    fields = [
        ("PMID", metadata.get("pmid")),
        ("First author", metadata.get("first_author")),
        ("Authors", metadata.get("authors")),
        ("Journal", metadata.get("journal")),
        ("DOI", metadata.get("doi")),
    ]

    rendered = [(label, str(value or "").strip()) for label, value in fields]
    rendered = [(label, value) for label, value in rendered if value]
    if not rendered:
        return ""

    lines: list[str] = ["## Metadata", ""]
    for label, value in rendered:
        # Заменяем переносы строк в значениях (особенно в Authors, если их много)
        # на простой пробел, чтобы не разрывать bullet.
        value_one_line = " ".join(value.split())
        lines.append(f"- **{label}:** {value_one_line}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _figures_block(figures: list[dict[str, Any]] | None) -> str:
    """Render figures as bold-prefixed lines."""
    if not figures:
        return "## Figures\n\n—\n"

    lines: list[str] = ["## Figures", ""]
    any_figure = False
    for fig in figures:
        if not isinstance(fig, dict):
            continue
        num = str(fig.get("figure_number") or "").strip()
        # В JSON ключ называется "caption" (см. extracted_text_dialog.py:
        # figures.append({"figure_number": ..., "caption": ...})).
        cap = str(fig.get("caption") or "").strip()
        if not (num or cap):
            continue
        any_figure = True

        cap_norm = _normalize_breaks(cap)
        if num and cap_norm:
            lines.append(f"**{num}.** {cap_norm}")
        elif num:
            lines.append(f"**{num}.**")
        else:
            lines.append(cap_norm)
        lines.append("")

    if not any_figure:
        return "## Figures\n\n—\n"

    return "\n".join(lines).rstrip() + "\n"


def _results_block(results: list[dict[str, Any]] | None) -> str:
    """Render Results section with H3 subsections in JSON order."""
    lines: list[str] = ["## Results", ""]

    if not results or not isinstance(results, list):
        lines.append("—")
        lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    any_section = False
    for item in results:
        if not isinstance(item, dict):
            continue
        sec_title = str(item.get("section_title") or "").strip()
        sec_text = str(item.get("section_text") or "").strip()

        if not (sec_title or sec_text):
            continue
        any_section = True

        if sec_title:
            lines.append(f"### {sec_title}")
            lines.append("")

        body = _normalize_breaks(sec_text) or "—"
        lines.append(body)
        lines.append("")

    if not any_section:
        lines.append("—")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- Public API ----------


def export_extracted_text_to_md(
    *,
    md_path: Path | str,
    article: dict[str, Any],
    source_path: str = "",
) -> None:
    """
    Writes extracted article text to a Markdown file.

    `article` is the parsed JSON dictionary with keys:
        title, year, introduction, methods, results, discussion, figures

    If `source_path` is non-empty, it is rendered as a 'Source:' line below the title.
    """
    md_path = Path(str(md_path))
    md_path.parent.mkdir(parents=True, exist_ok=True)

    title = str(article.get("title") or "").strip()
    year_raw = article.get("year")
    year = str(year_raw).strip() if year_raw is not None else ""

    header_title = f"{year} {title}".strip() or "Article"

    chunks: list[str] = [f"# {header_title}", ""]

    if source_path:
        chunks.append(f"**Source:** {source_path}")
        chunks.append("")

    meta_md = _metadata_block(article.get("metadata"))
    if meta_md:
        chunks.append(meta_md)
        chunks.append("")

    chunks.append(_section("Introduction", str(article.get("introduction") or "")))
    chunks.append("")

    chunks.append(_section("Methods", str(article.get("methods") or "")))
    chunks.append("")

    chunks.append(_results_block(article.get("results")))
    chunks.append("")

    chunks.append(_section("Discussion", str(article.get("discussion") or "")))
    chunks.append("")

    chunks.append(_figures_block(article.get("figures")))

    text = "\n".join(chunks).rstrip() + "\n"
    md_path.write_text(text, encoding="utf-8")
