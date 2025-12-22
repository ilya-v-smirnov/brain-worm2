from __future__ import annotations

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK

# --- Базовые помощники -------------------------------------------------------

def _p(doc: Document, text: str = "", *, bold: bool = False, size: int = 11, font: str | None = None):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = bool(bold)
    if size:
        run.font.size = Pt(size)
    if font:
        run.font.name = font
    # === ВЫРАВНИВАНИЕ ПО ШИРИНЕ ===
    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    para.paragraph_format.space_after = Pt(0)
    return run

def _blank(doc: Document, n: int = 1):
    # максимум одна визуальная пустая строка
    n = 1 if n and n > 0 else 0
    for _ in range(n):
        p = doc.add_paragraph("")
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_after = Pt(0)

def _heading(doc: Document, text: str):
    # Совместимость со старыми вызовами: заголовок уровня 2
    h = doc.add_heading(text, level=2)
    h.paragraph_format.space_after = Pt(0)

def _heading_h2(doc: Document, text: str):
    # настоящий заголовок Word уровня 2
    h = doc.add_heading(text, level=2)
    # уберём лишние интервалы после
    h.paragraph_format.space_after = Pt(0)

def _bullet_list(doc: Document, items):
    for it in items or []:
        p = doc.add_paragraph(str(it), style="List Bullet")
        # === ВЫРАВНИВАНИЕ ПО ШИРИНЕ ДЛЯ СПИСКОВ ===
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_after = Pt(0)

def _sections_block(doc: Document, sections: List[Dict[str, str]]):
    """
    Рендерит список секций.
    Элемент: {"title": str, "text": Optional[str], "level": int, "suppress_empty_dash": bool=False}
      - text=None  -> вывести только заголовок (без абзаца)
      - text==""   -> вывести "—" (кроме suppress_empty_dash=True)
    """
    if not sections:
        return
    for sec in sections:
        title = (sec.get("title") or "Section").strip()
        text = sec.get("text", "")
        level = int(sec.get("level") or 2)
        suppress_empty = bool(sec.get("suppress_empty_dash", False))

        # Заголовок
        h = doc.add_heading(title, level=level)
        if level == 3:
            for r in h.runs:
                r.font.size = Pt(12)
        h.paragraph_format.space_after = Pt(0)

        # Тело
        printed_body = False
        if text is None:
            # только заголовок (например, Results, когда есть подпункты) — тела нет
            printed_body = False
        else:
            body = text.strip()
            if not body and suppress_empty:
                # ничего не печатаем
                printed_body = False
            else:
                if not body:
                    body = "—"
                for chunk in body.split("\n\n"):
                    p = doc.add_paragraph(chunk.strip())
                    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                    p.paragraph_format.space_after = Pt(0)
                printed_body = True

        # Пустую строку добавляем только если реально печатали тело
        if printed_body:
            _blank(doc, 1)

def _abbrev_simple_table(doc: Document, pairs: List[tuple[str, str]]):
    """
    Рисует ТОЛЬКО таблицу (без заголовка). Интервалы после строк = 0 pt.
    """
    if not pairs:
        return
    table = doc.add_table(rows=1, cols=2)
    hdr = table.rows[0].cells
    hdr[0].text = "Abbreviation"
    hdr[1].text = "Expanded"
    # Убираем интервалы после абзацев в ячейках заголовка
    for cell in hdr:
        for para in cell.paragraphs:
            para.paragraph_format.space_after = Pt(0)

    for abbr, expanded in pairs:
        row = table.add_row().cells
        row[0].text = abbr or ""
        row[1].text = expanded or ""
        # Убираем интервалы после абзацев в каждой ячейке
        for cell in row:
            for para in cell.paragraphs:
                para.paragraph_format.space_after = Pt(0)

def _add_page_break_if_needed(doc: Document) -> None:
    if len(doc.paragraphs) > 0 or len(doc.tables) > 0:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

def _write_figure_summaries(doc: Document, figure_summaries):
    """
    Печатает раздел 'Figure summaries' перед 'Abbreviations'.
    Ожидает список объектов вида: [{"figure": "Figure 1", "summary": "..."}]
    """
    if not figure_summaries:
        return
    _heading_h2(doc, "Figure summaries")
    for item in figure_summaries:
        fig = str(item.get("figure") or "").strip()
        summ = str(item.get("summary") or "").strip()
        if not fig or not summ:
            continue
        p = doc.add_paragraph()
        r = p.add_run(f"{fig}. ")
        r.bold = True
        p.add_run(summ)


def _loc(label_en: str, lang: str) -> str:
    """
    Простая локализация заголовков для RU.
    Мы трогаем только то, что требуется задачей: Results -> Результаты.
    Остальные заголовки оставляем как есть (минимальная правка).
    """
    lang = (lang or "").upper()
    if lang == "RU":
        mapping = {
            "Results": "Результаты",
        }
        return mapping.get(label_en, label_en)
    return label_en


def append_ai_summary_to_docx(
    *,
    docx_path: Path,
    summary: dict,
):
    """
    Добавляет одну версию AI-summary в docx.
    Если файл существует — page break.
    """

    if docx_path.exists():
        doc = Document(str(docx_path))
        _add_page_break_if_needed(doc)
    else:
        docx_path.parent.mkdir(parents=True, exist_ok=True)
        doc = Document()

    header = summary.get("header") or {}

    # === HEADER ===
    h1 = doc.add_heading(f'{header.get("year","")} {header.get("title","")}', level=1)
    h1.paragraph_format.space_before = Pt(0)
    h1.paragraph_format.space_after = Pt(0)

    def meta_line(label, value):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r1 = p.add_run(f"{label}: "); r1.bold = True
        p.add_run(str(value))

    meta_line("Source path", header.get("source_path",""))
    meta_line("Model", header.get("model",""))
    meta_line("Language", header.get("language",""))
    _blank(doc, 1)

    # === KEY POINTS ===
    _heading_h2(doc, "Key points")
    _bullet_list(doc, summary.get("key_points"))
    _blank(doc, 1)

    # === SECTIONS ===
    sections_out = []

    sections_out.append({
        "title": "Introduction",
        "text": summary.get("introduction") or "—",
        "level": 2,
    })

    # Results: строго по подразделам
    results = summary.get("results") or []
    if results:
        sections_out.append({
            "title": "Results",
            "text": None,
            "level": 2,
            "suppress_empty_dash": True,
        })
        for r in results:
            sections_out.append({
                "title": r["section_title"],   # оригинальное название
                "text": r["mini_summary"],
                "level": 3,
            })

    sections_out.append({
        "title": "Discussion",
        "text": summary.get("discussion") or "—",
        "level": 2,
    })

    _sections_block(doc, sections_out)

    # === FIGURES ===
    _write_figure_summaries(doc, summary.get("figures", {}).get("items"))

    # === ABBREVIATIONS (если появятся позже) ===
    abbr = summary.get("abbreviations") or []
    if abbr:
        _heading_h2(doc, "Abbreviations")
        pairs = [(a["abbr"], a["expanded"]) for a in abbr]
        _abbrev_simple_table(doc, pairs)

    doc.save(str(docx_path))
