"""会话产物预览：按后缀分类，文本与 Office 用标准库抽取，二进制不解码。"""

from __future__ import annotations

import csv
import io
import mimetypes
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Literal

PreviewKind = Literal["markdown", "text", "csv", "image", "pdf", "docx", "xlsx", "binary"]

MAX_PREVIEW_BYTES = 200 * 1024
MAX_OFFICE_ROWS = 80
MAX_OFFICE_COLS = 16

_MARKDOWN = frozenset({".md", ".markdown"})
_CSV = frozenset({".csv", ".tsv"})
_TEXT = frozenset({
    ".txt", ".json", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".tsx",
    ".jsx", ".py", ".yml", ".yaml", ".toml", ".log", ".ini", ".sh", ".sql",
})
_IMAGE = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def classify_preview_kind(name: str) -> PreviewKind:
    """按文件名后缀决定预览类型。"""

    suffix = Path(name).suffix.lower()
    if suffix in _MARKDOWN:
        return "markdown"
    if suffix in _CSV:
        return "csv"
    if suffix in _TEXT:
        return "text"
    if suffix in _IMAGE:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".xlsx":
        return "xlsx"
    return "binary"


def guess_media_type(name: str) -> str:
    """猜测产物的展示用 MIME。"""

    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def build_preview_payload(file_path: Path) -> dict[str, object]:
    """构造预览 JSON；图片 / PDF / 未知二进制不带正文。"""

    kind = classify_preview_kind(file_path.name)
    size = file_path.stat().st_size
    media_type = guess_media_type(file_path.name)
    if kind in {"image", "pdf", "binary"}:
        return {
            "path": str(file_path),
            "name": file_path.name,
            "size": size,
            "kind": kind,
            "mediaType": media_type,
            "content": "",
            "truncated": False,
        }
    if kind == "docx":
        content, truncated = _extract_docx(file_path)
    elif kind == "xlsx":
        content, truncated = _extract_xlsx(file_path)
    else:
        content, truncated = _read_text(file_path)
    return {
        "path": str(file_path),
        "name": file_path.name,
        "size": size,
        "kind": kind,
        "mediaType": media_type,
        "content": content,
        "truncated": truncated,
    }


def _read_text(file_path: Path) -> tuple[str, bool]:
    raw = file_path.read_bytes()[: MAX_PREVIEW_BYTES + 1]
    truncated = len(raw) > MAX_PREVIEW_BYTES
    if truncated:
        raw = raw[:MAX_PREVIEW_BYTES]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
    return content, truncated or file_path.stat().st_size > MAX_PREVIEW_BYTES


def _extract_docx(file_path: Path) -> tuple[str, bool]:
    try:
        with zipfile.ZipFile(file_path) as archive:
            xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return "无法解析该 Word 文件。", False
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W_NS}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{_W_NS}t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    content = "\n\n".join(paragraphs)
    truncated = len(content.encode("utf-8")) > MAX_PREVIEW_BYTES
    if truncated:
        content = content.encode("utf-8")[:MAX_PREVIEW_BYTES].decode("utf-8", errors="ignore")
    return content or "（空文档）", truncated


def _extract_xlsx(file_path: Path) -> tuple[str, bool]:
    try:
        with zipfile.ZipFile(file_path) as archive:
            strings = _xlsx_shared_strings(archive)
            sheet_name = _first_sheet_path(archive)
            xml = archive.read(sheet_name)
    except (KeyError, zipfile.BadZipFile, OSError, ElementTree.ParseError):
        return "无法解析该 Excel 文件。", False
    root = ElementTree.fromstring(xml)
    rows: list[list[str]] = []
    truncated = False
    for row in root.iter(f"{_S_NS}row"):
        if len(rows) >= MAX_OFFICE_ROWS:
            truncated = True
            break
        cells: dict[int, str] = {}
        max_col = -1
        for cell in row.findall(f"{_S_NS}c"):
            ref = cell.get("r") or ""
            col = _column_index(ref)
            if col < 0 or col >= MAX_OFFICE_COLS:
                if col >= MAX_OFFICE_COLS:
                    truncated = True
                continue
            cells[col] = _xlsx_cell_value(cell, strings)
            max_col = max(max_col, col)
        if max_col < 0:
            continue
        rows.append([cells.get(index, "") for index in range(max_col + 1)])
    if not rows:
        return "（空表格）", False
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().strip(), truncated


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall(f"{_S_NS}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{_S_NS}t")))
    return values


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    names = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
    if not names:
        raise KeyError("sheet")
    names.sort()
    return names[0]


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    if not letters:
        return -1
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - 64)
    return index - 1


def _xlsx_cell_value(cell: ElementTree.Element, strings: list[str]) -> str:
    cell_type = cell.get("t")
    value = cell.find(f"{_S_NS}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return strings[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    return value.text
