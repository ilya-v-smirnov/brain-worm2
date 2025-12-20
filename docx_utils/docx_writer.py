from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt


def write_article_json_to_docx(data: dict[str, Any], out_path: Path, *, title: str | None = None) -> Path:
    """
    Write parsed article JSON into a .docx file.

    Expected keys (best-effort):
      - "Introduction"
      - "Methods"
      - "Results" (str or dict of subsections)
      - "Discussion"
      - "Figures" (list[str] or dict/list of captions)

    This function is tolerant to missing keys and different shapes.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    if title:
        doc.add_heading(title, level=1)

    def add_section(name: str, content: Any) -> None:
        if content is None:
            return

        # normalize "empty"
        if isinstance(content, str) and not content.strip():
            return
        if isinstance(content, (list, dict)) and not content:
            return

        doc.add_heading(name, level=2)

        if isinstance(content, str):
            _add_paragraph(doc, content)
            return

        if isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    _add_bullet(doc, item)
                else:
                    _add_paragraph(doc, str(item))
            return

        if isinstance(content, dict):
            # subsections
            for sub, txt in content.items():
                if sub:
                    doc.add_heading(str(sub), level=3)
                if isinstance(txt, str):
                    _add_paragraph(doc, txt)
                elif isinstance(txt, list):
                    for li in txt:
                        _add_bullet(doc, str(li))
                else:
                    _add_paragraph(doc, str(txt))
            return

        _add_paragraph(doc, str(content))

    add_section("Introduction", data.get("Introduction"))
    add_section("Methods", data.get("Methods"))
    add_section("Results", data.get("Results"))
    add_section("Discussion", data.get("Discussion"))

    # Figures: could be list or dict
    figs = data.get("Figures")
    if figs:
        doc.add_heading("Figures", level=2)
        if isinstance(figs, list):
            for i, cap in enumerate(figs, start=1):
                doc.add_heading(f"Figure {i}", level=3)
                _add_paragraph(doc, str(cap))
        elif isinstance(figs, dict):
            # e.g. {"Figure 1": "...", "Figure 2": "..."}
            for k, v in figs.items():
                doc.add_heading(str(k), level=3)
                _add_paragraph(doc, str(v))
        else:
            _add_paragraph(doc, str(figs))

    doc.save(str(out_path))
    return out_path


def _add_paragraph(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(11)


def _add_bullet(doc: Document, text: str) -> None:
    p = doc.add_paragraph(text, style="List Bullet")
    for r in p.runs:
        r.font.size = Pt(11)
