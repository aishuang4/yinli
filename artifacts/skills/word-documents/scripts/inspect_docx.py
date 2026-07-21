#!/usr/bin/env python3
"""使用标准库检查 DOCX 的结构和可见文本。"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{W}t"))


def inspect(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("文件不是有效的 DOCX/ZIP 容器")

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX 缺少 word/document.xml")
        root = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = []
        headings = []
        for paragraph in root.iter(f"{W}p"):
            text = paragraph_text(paragraph).strip()
            if not text:
                continue
            paragraphs.append(text)
            style = paragraph.find(f"./{W}pPr/{W}pStyle")
            style_name = style.get(f"{W}val", "") if style is not None else ""
            if style_name.lower().startswith("heading") or style_name.startswith("标题"):
                headings.append({"style": style_name, "text": text})

        tables = []
        for table in root.iter(f"{W}tbl"):
            rows = []
            for row in table.findall(f"./{W}tr"):
                rows.append([
                    "".join(paragraph_text(p) for p in cell.iter(f"{W}p")).strip()
                    for cell in row.findall(f"./{W}tc")
                ])
            tables.append(rows)

        properties: dict[str, str] = {}
        if "docProps/core.xml" in names:
            core = ET.fromstring(archive.read("docProps/core.xml"))
            for key, tag in (("title", f"{DC}title"), ("creator", f"{DC}creator"), ("keywords", f"{CP}keywords")):
                node = core.find(tag)
                if node is not None and node.text:
                    properties[key] = node.text

        return {
            "file": str(path.resolve()),
            "sizeBytes": path.stat().st_size,
            "paragraphCount": len(paragraphs),
            "tableCount": len(tables),
            "headings": headings,
            "paragraphs": paragraphs,
            "tables": tables,
            "properties": properties,
            "hasHeaders": any(name.startswith("word/header") and name.endswith(".xml") for name in names),
            "hasFooters": any(name.startswith("word/footer") and name.endswith(".xml") for name in names),
            "hasComments": "word/comments.xml" in names,
            "hasNumbering": "word/numbering.xml" in names,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 DOCX 结构并输出 JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="可选 JSON 输出文件")
    args = parser.parse_args()
    try:
        result = inspect(args.input)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
