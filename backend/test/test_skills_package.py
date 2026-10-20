"""标准包注册、ZIP 安全、导出与显式迁移，使用文件事实源。"""
import asyncio
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from src.skills import SkillError, build_skill_catalog, current_skill_context
from src.skills.parser import parse_manual
from src.skills.packages import _read_skill_md_from_zip, _extract_skill_package, _skill_package_warnings, _skill_export_archive
from src.skills.registry import register_user_skill, SkillRegisterRequest, get_installed_skill
from src.skills.migration import migrate_skill_files
from src.storage import database


def test_missing_name_and_description_use_unified_defaults():
    parsed = parse_manual(b"# Header\n\nInstructions.", "directory")
    assert parsed.name == "directory" and parsed.description == "Instructions."
    with pytest.raises(SkillError):
        parse_manual(b"---\nname: [broken\n---\nInstructions", "directory")


def test_package_diagnostics_are_non_blocking():
    warnings = _skill_package_warnings(name="Workbook Inspector", skill_path="Different Folder/SKILL.md", resources=["SKILL.md", "scripts/helper.bin"])
    assert len(warnings) == 3
    assert any("目录名" in warning for warning in warnings)
    assert any("helper.bin" in warning for warning in warnings)


def test_platform_script_warnings():
    warnings = _skill_package_warnings(name="portable-skill", skill_path="portable-skill/SKILL.md", resources=["scripts/run.sh", "scripts/setup.ps1"])
    assert any("Windows" in warning for warning in warnings)
    assert any("PowerShell 7+" in warning for warning in warnings)


def test_zip_rejects_symlink():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("safe/SKILL.md", "---\ndescription: safe\n---\nInstructions")
        link = zipfile.ZipInfo("safe/scripts/link.py")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "../../outside.py")
    with pytest.raises(HTTPException) as error:
        _read_skill_md_from_zip(buffer.getvalue())
    assert error.value.status_code == 422


def test_import_rejects_oversized_archive():
    from src.server.routes.skills import import_skill_archive, MAX_SKILL_ARCHIVE_BYTES
    file = UploadFile(filename="skill.zip", file=BytesIO(b"x" * (MAX_SKILL_ARCHIVE_BYTES + 1)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(import_skill_archive(file))
    assert error.value.status_code == 413


def test_extract_ignores_build_artifacts_and_macos_metadata(tmp_path):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("report/SKILL.md", "---\ndescription: Report\n---\nInstructions")
        archive.writestr("report/scripts/report.py", "print('ok')")
        archive.writestr("report/.DS_Store", "metadata")
        archive.writestr("artifacts/skills/report/SKILL.md", "duplicate")
    skill_path, _, archive, members = _read_skill_md_from_zip(buffer.getvalue())
    try:
        path, resources = _extract_skill_package(archive, members, skill_path, "report", packages_dir=str(tmp_path))
    finally:
        archive.close()
    assert resources == ["SKILL.md", "scripts/report.py"]
    assert (Path(path) / "scripts/report.py").read_text() == "print('ok')"
    assert not (Path(path) / "artifacts").exists()


def test_register_atomic_file_package_without_database():
    item = register_user_skill(SkillRegisterRequest(name="API 文档检查", description="检查文档", content="检查参数和错误码。"))
    assert item.resource_path.endswith("/API 文档检查")
    assert "name: API 文档检查" in (Path(item.resource_path) / "SKILL.md").read_text()
    assert "检查参数" in get_installed_skill(item.id).content
    assert database.list_skills() == []


def test_export_contains_current_manual_and_support_files():
    item = register_user_skill(SkillRegisterRequest(name="变更摘要", description="总结变更", content="按新增和修复输出。"))
    (Path(item.resource_path) / "script.py").write_text("print('summary')")
    raw = _skill_export_archive(item)
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert "变更摘要/SKILL.md" in archive.namelist()
        assert archive.read("变更摘要/script.py") == b"print('summary')"


def test_query_does_not_materialize_database_only_record():
    context = current_skill_context()
    record = {"id": "custom-old", "name": "旧技能", "description": "历史", "content": "历史流程。", "category": "custom", "workspaceId": "default", "status": "installed", "enabled": True}
    database.replace_skills({record["id"]: record})
    assert not build_skill_catalog(context).candidates
    assert get_installed_skill("custom-old") is None
    report = migrate_skill_files([context], [record])
    assert report["records"][0]["result"] == "mapped"
    loaded = get_installed_skill("custom-old")
    assert loaded and "历史流程" in loaded.content
    assert database.list_skills()[0]["content"] == "历史流程。"


def test_register_rejects_normalized_duplicate():
    register_user_skill(SkillRegisterRequest(name="重复 Skill", description="第一次"))
    with pytest.raises(HTTPException) as error:
        register_user_skill(SkillRegisterRequest(name="重复-Skill", description="第二次"))
    assert error.value.status_code == 409
    assert len(build_skill_catalog(current_skill_context()).candidates) == 1


def test_export_rejects_outside_symlink(tmp_path):
    item = register_user_skill(SkillRegisterRequest(name="safe-export", description="safe"))
    secret = tmp_path / "outside.txt"
    secret.write_text("outside")
    (Path(item.resource_path) / "link").symlink_to(secret)
    with pytest.raises(HTTPException) as error:
        _skill_export_archive(item)
    assert error.value.status_code == 403
