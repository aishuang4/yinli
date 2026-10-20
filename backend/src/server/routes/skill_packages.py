"""Skill 标准包的文件系统、ZIP 安全与兼容诊断。"""

from __future__ import annotations

import os
import re
import shutil
import stat
import zipfile
from io import BytesIO
from typing import Any

import yaml
from fastapi import HTTPException

from src.config.runtime_paths import skill_packages_root


MAX_SKILL_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_SKILL_PACKAGE_FILES = 200


def _filename_slug(value: str) -> str:
    """生成适合下载文件名的 Skill 名称。"""

    slug = re.sub(r'[\\/:*?"<>|\r\n]+', "-", value.strip()).strip(" .-")
    return slug or "skill"


def _skill_package_dir(
    skill_name: str,
    *,
    packages_dir: str | None = None,
) -> str:
    """返回 Skill 标准包目录。"""

    return os.path.join(packages_dir or str(skill_packages_root()), _filename_slug(skill_name))


def _write_skill_package(
    skill_name: str,
    markdown: str,
    *,
    packages_dir: str | None = None,
) -> tuple[str, list[str]]:
    """写入标准 Skill 包目录，并返回 resourcePath/resources。"""

    package_dir = _skill_package_dir(skill_name, packages_dir=packages_dir)
    if os.path.exists(package_dir):
        shutil.rmtree(package_dir)
    os.makedirs(package_dir, exist_ok=True)
    skill_md = os.path.join(package_dir, "SKILL.md")
    with open(skill_md, "w", encoding="utf-8") as file:
        file.write(markdown.strip() + "\n")
    return package_dir, ["SKILL.md"]


def _skill_export_archive(skill: Any) -> bytes:
    """把一个 Skill 标准包打包为 zip。"""

    buffer = BytesIO()
    package_root = skill.resource_path
    archive_root = _filename_slug(skill.name)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if package_root and os.path.isdir(package_root):
            for root, _dirs, files in os.walk(package_root):
                for filename in sorted(files):
                    path = os.path.join(root, filename)
                    relative = os.path.relpath(path, package_root)
                    if relative.startswith("../"):
                        continue
                    archive.write(path, arcname=os.path.join(archive_root, relative))
        else:
            archive.writestr(os.path.join(archive_root, "SKILL.md"), skill.content.strip() + "\n")
    buffer.seek(0)
    return buffer.getvalue()


def _skill_export_filename(skill: Any) -> str:
    """返回 Skill 导出 zip 文件名。"""

    return f"{_filename_slug(skill.name)}.zip"


def _split_frontmatter(markdown: str) -> tuple[dict, str]:
    if not markdown.startswith("---"):
        return {}, markdown.strip()

    parts = markdown.split("---", 2)
    if len(parts) < 3:
        return {}, markdown.strip()

    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid SKILL.md frontmatter: {exc}") from exc

    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="SKILL.md frontmatter must be a YAML object")

    return metadata, parts[2].strip()


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        if info.filename.startswith("__MACOSX/"):
            continue
        normalized = os.path.normpath(info.filename)
        if normalized.startswith("../") or os.path.isabs(normalized):
            raise HTTPException(status_code=422, detail="Zip archive contains unsafe paths")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type == stat.S_IFLNK:
            raise HTTPException(status_code=422, detail="Zip archive must not contain symbolic links")
        members.append(info)

    if len(members) > MAX_SKILL_PACKAGE_FILES:
        raise HTTPException(status_code=413, detail="Skill archive contains too many files")
    return members


def _read_skill_md_from_zip(raw: bytes) -> tuple[str, str, zipfile.ZipFile, list[zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
        try:
            members = _safe_archive_members(archive)
            skill_paths = [
                info.filename
                for info in members
                if os.path.basename(info.filename) == "SKILL.md"
                and "artifacts" not in _path_parts(info.filename)
            ]
            if not skill_paths:
                raise HTTPException(
                    status_code=422,
                    detail="Zip archive must contain a SKILL.md file outside artifacts/",
                )

            skill_path = sorted(skill_paths, key=lambda item: item.count("/"))[0]
            info = archive.getinfo(skill_path)
            if info.file_size > MAX_SKILL_ARCHIVE_BYTES:
                raise HTTPException(status_code=413, detail="SKILL.md is too large")

            return skill_path, archive.read(skill_path).decode("utf-8"), archive, members
        except Exception:
            archive.close()
            raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="Invalid zip archive") from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="SKILL.md must be UTF-8 encoded") from exc


def _path_parts(path: str) -> tuple[str, ...]:
    """将压缩包路径规范化为便于校验的片段。"""

    return tuple(part for part in path.replace("\\", "/").split("/") if part and part != ".")


def _package_relative_path(skill_path: str, filename: str) -> str | None:
    """返回包根下的相对资源路径，包根外的文件不参与安装。"""

    root_parts = _path_parts(os.path.dirname(skill_path))
    file_parts = _path_parts(filename)
    if root_parts:
        if file_parts[:len(root_parts)] != root_parts:
            return None
        file_parts = file_parts[len(root_parts):]
    return "/".join(file_parts)


def _is_package_resource_path(path: str) -> bool:
    """判断路径是否属于可安装的 Skill 源文件，而非系统或构建残留。"""

    parts = _path_parts(path)
    return bool(parts) and "artifacts" not in parts and ".DS_Store" not in parts and "__MACOSX" not in parts


def _cleanup_skill_package_artifacts(package_dir: str) -> list[str]:
    """清理旧版本误导入的 artifacts，并重建资源清单。"""

    artifacts_dir = os.path.join(package_dir, "artifacts")
    if os.path.isdir(artifacts_dir):
        shutil.rmtree(artifacts_dir)

    resources: list[str] = []
    for root, _dirs, files in os.walk(package_dir):
        for filename in files:
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, package_dir).replace(os.sep, "/")
            if _is_package_resource_path(relative):
                resources.append(relative)
    return sorted(resources)


def _extract_skill_package(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    skill_path: str,
    skill_name: str,
    *,
    packages_dir: str | None = None,
) -> tuple[str, list[str]]:
    package_dir = _skill_package_dir(skill_name, packages_dir=packages_dir)
    if os.path.exists(package_dir):
        shutil.rmtree(package_dir)
    os.makedirs(package_dir, exist_ok=True)

    resources: list[str] = []
    for info in members:
        relative_path = _package_relative_path(skill_path, info.filename)
        if not relative_path or not _is_package_resource_path(relative_path):
            continue

        target_path = os.path.join(package_dir, relative_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with archive.open(info) as source, open(target_path, "wb") as target:
            shutil.copyfileobj(source, target)
        resources.append(relative_path)

    return package_dir, sorted(resources)


def _skill_package_warnings(*, name: str, skill_path: str, resources: list[str]) -> list[str]:
    """返回不阻断安装的 Agent Skills 兼容诊断。"""

    warnings: list[str] = []
    directory_name = os.path.basename(os.path.dirname(skill_path)).strip()
    if directory_name and directory_name != name:
        warnings.append(f"Skill name '{name}' 与包目录名 '{directory_name}' 不一致。")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        warnings.append("Skill name 建议使用小写字母、数字和连字符。")
    known_script_suffixes = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".ps1", ".rb"}
    unknown_scripts = [
        path for path in resources
        if path.replace("\\", "/").startswith("scripts/")
        and os.path.splitext(path)[1].lower() not in known_script_suffixes
    ]
    if unknown_scripts:
        warnings.append(
            "scripts/ 包含不会自动执行的未知文件类型: " + ", ".join(sorted(unknown_scripts)[:8]),
        )
    if any(os.path.splitext(path)[1].lower() in {".sh", ".bash"} for path in resources):
        warnings.append(
            "包含 POSIX Shell 脚本（.sh/.bash）：macOS/Linux 可直接执行；Windows 需要 Git Bash、WSL 或等价环境。",
        )
    if any(os.path.splitext(path)[1].lower() == ".ps1" for path in resources):
        warnings.append(
            "包含 PowerShell 脚本（.ps1）：Windows 原生支持；macOS/Linux 需要安装 PowerShell 7+。",
        )
    return warnings
