"""会话产物预览分类与 Office 抽取。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.generated_file_preview import build_preview_payload, classify_preview_kind


def test_classify_preview_kind_by_suffix() -> None:
    assert classify_preview_kind("note.md") == "markdown"
    assert classify_preview_kind("data.csv") == "csv"
    assert classify_preview_kind("log.txt") == "text"
    assert classify_preview_kind("shot.png") == "image"
    assert classify_preview_kind("report.pdf") == "pdf"
    assert classify_preview_kind("brief.docx") == "docx"
    assert classify_preview_kind("sheet.xlsx") == "xlsx"
    assert classify_preview_kind("legacy.xls") == "binary"


def test_build_preview_payload_reads_markdown(tmp_path: Path) -> None:
    path = tmp_path / "readme.md"
    path.write_text("# 标题\n\n正文", encoding="utf-8")
    payload = build_preview_payload(path)
    assert payload["kind"] == "markdown"
    assert "标题" in str(payload["content"])
    assert payload["truncated"] is False


def test_build_preview_payload_skips_binary_body(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\r\n")
    payload = build_preview_payload(path)
    assert payload["kind"] == "image"
    assert payload["content"] == ""


def test_extract_docx_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "brief.docx"
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("[Content_Types].xml", "<Types></Types>")
    payload = build_preview_payload(path)
    assert payload["kind"] == "docx"
    assert "第一段" in str(payload["content"])
    assert "第二段" in str(payload["content"])


def test_extract_xlsx_first_sheet(tmp_path: Path) -> None:
    path = tmp_path / "sheet.xlsx"
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>姓名</t></si><si><t>Alice</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    payload = build_preview_payload(path)
    assert payload["kind"] == "xlsx"
    content = str(payload["content"])
    assert "姓名" in content
    assert "Alice" in content
