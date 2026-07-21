#!/usr/bin/env python3
"""通过 LibreOffice 和 Poppler 渲染 DOCX，供视觉检查。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def find_command(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def render(source: Path, output_dir: Path) -> dict[str, object]:
    office = find_command("soffice", "libreoffice")
    if not office:
        raise RuntimeError("找不到 LibreOffice/soffice，无法进行视觉渲染验证")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="word-render-") as temp_name:
        temp = Path(temp_name)
        profile = temp / "profile"
        env = os.environ.copy()
        env["HOME"] = str(temp / "home")
        Path(env["HOME"]).mkdir()
        font_dirs = [
            path
            for path in ("/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts")
            if Path(path).is_dir()
        ]
        if font_dirs and "SAL_FONTPATH" not in env:
            env["SAL_FONTPATH"] = os.pathsep.join(font_dirs)
        command = [
            office,
            "--headless",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(temp),
            str(source.resolve()),
        ]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        pdf = temp / f"{source.stem}.pdf"
        if completed.returncode != 0 or not pdf.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"LibreOffice 转换失败: {detail}")
        final_pdf = output_dir / pdf.name
        shutil.copy2(pdf, final_pdf)

    pages: list[str] = []
    pdftoppm = find_command("pdftoppm")
    if pdftoppm:
        prefix = output_dir / "page"
        completed = subprocess.run(
            [pdftoppm, "-png", "-r", "150", str(final_pdf), str(prefix)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"PDF 转 PNG 失败: {(completed.stderr or completed.stdout).strip()}")
        pages = [str(path.resolve()) for path in sorted(output_dir.glob("page-*.png"))]
    return {"pdf": str(final_pdf.resolve()), "pages": pages, "pngAvailable": bool(pdftoppm)}


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 DOCX 为 PDF 和逐页 PNG")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = render(args.input, args.output_dir)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
