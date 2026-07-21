#!/usr/bin/env python3
"""使用 Python 标准库把基础 Markdown 转换为 DOCX。"""

from __future__ import annotations

import argparse
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


def run_xml(text: str, *, bold: bool = False) -> str:
    font = '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>'
    props = f"<w:rPr>{font}{'<w:b/>' if bold else ''}</w:rPr>"
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph_xml(text: str, style: str | None = None, numbering: int | None = None) -> str:
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if numbering is not None:
        props.append(f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{numbering}"/></w:numPr>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{ppr}{run_xml(text)}</w:p>"


def table_xml(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    width = max(1000, 9000 // columns)
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(columns))
    output = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>',
              '<w:top w:val="single" w:sz="4" w:color="B7C3D0"/>',
              '<w:left w:val="single" w:sz="4" w:color="B7C3D0"/>',
              '<w:bottom w:val="single" w:sz="4" w:color="B7C3D0"/>',
              '<w:right w:val="single" w:sz="4" w:color="B7C3D0"/>',
              '<w:insideH w:val="single" w:sz="4" w:color="D7DEE5"/>',
              '<w:insideV w:val="single" w:sz="4" w:color="D7DEE5"/>',
              f'</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>']
    for row_index, row in enumerate(rows):
        output.append("<w:tr>")
        for value in row + [""] * (columns - len(row)):
            shading = '<w:shd w:fill="EAF0F5"/>' if row_index == 0 else ""
            output.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}</w:tcPr>'
                f'<w:p>{run_xml(value, bold=row_index == 0)}</w:p></w:tc>'
            )
        output.append("</w:tr>")
    output.append("</w:tbl>")
    return "".join(output)


def parse_markdown(markdown: str) -> list[str]:
    lines = markdown.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[index + 1]):
            rows = []
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            blocks.append(table_xml(rows))
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append(paragraph_xml(heading.group(2), "Title" if level == 1 and not blocks else f"Heading{level}"))
        elif bullet:
            blocks.append(paragraph_xml(bullet.group(1), numbering=1))
        elif numbered:
            blocks.append(paragraph_xml(numbered.group(1), numbering=2))
        elif line.strip():
            blocks.append(paragraph_xml(line.strip()))
        else:
            blocks.append("<w:p/>")
        index += 1
    return blocks


def create_docx(markdown: str, output: Path) -> None:
    body = "".join(parse_markdown(markdown))
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="280"/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="48"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:outlineLvl w:val="0"/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:outlineLvl w:val="1"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/></w:pPr><w:rPr><w:b/><w:color w:val="235789"/><w:sz w:val="27"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:outlineLvl w:val="2"/><w:pPr><w:keepNext/><w:spacing w:before="180" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>'''
    numbering = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>'''
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Agent Workstation</dc:creator><cp:lastModifiedBy>Agent Workstation</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/numbering.xml", numbering)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("docProps/core.xml", core)


def main() -> int:
    parser = argparse.ArgumentParser(description="把基础 Markdown 转换为 DOCX")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_docx(args.input.read_text(encoding="utf-8"), args.output)
    print(f"output={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
