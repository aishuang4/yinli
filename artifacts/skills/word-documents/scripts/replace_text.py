#!/usr/bin/env python3
"""在 DOCX 段落内执行确定性文字替换。"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def replace_in_xml(raw: bytes, old: str, new: str) -> tuple[bytes, int]:
    root = ET.fromstring(raw)
    replacements = 0
    for paragraph in root.iter(f"{W}p"):
        nodes = list(paragraph.iter(f"{W}t"))
        if not nodes:
            continue
        original = "".join(node.text or "" for node in nodes)
        count = original.count(old)
        if not count:
            continue
        nodes[0].text = original.replace(old, new)
        for node in nodes[1:]:
            node.text = ""
        replacements += count
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), replacements


def replace_docx(source: Path, target: Path, old: str, new: str) -> int:
    if source.resolve() == target.resolve():
        raise ValueError("输出文件不能覆盖输入文件")
    if not zipfile.is_zipfile(source):
        raise ValueError("输入文件不是有效的 DOCX/ZIP 容器")

    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as reader, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as writer:
        for info in reader.infolist():
            data = reader.read(info.filename)
            name = info.filename
            if name == "word/document.xml" or (
                name.startswith("word/header") or name.startswith("word/footer")
            ) and name.endswith(".xml"):
                data, count = replace_in_xml(data, old, new)
                total += count
            writer.writestr(info, data)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="替换 DOCX 中的文字")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    args = parser.parse_args()
    if not args.old:
        parser.error("--old 不能为空")
    try:
        count = replace_docx(args.input, args.output, args.old, args.new)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"replacements={count}")
    print(f"output={args.output.resolve()}")
    return 0 if count else 2


if __name__ == "__main__":
    raise SystemExit(main())
