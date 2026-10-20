"""Docker Skill 投影：稳定 ID 命名，每次 Run 同步选中版本，不依赖 SDK basename 去重。"""
from __future__ import annotations

import io
import tarfile
import uuid

from agentscope.workspace import DockerWorkspace

from src.skills.catalog import _resources
from src.skills.reader import read_skill
from src.skills.sdk import resolved_path

_EXTRACT = """
import os,sys,tarfile,tempfile,shutil
archive,root,identity=sys.argv[1:]
os.makedirs(root,exist_ok=True)
stage=tempfile.mkdtemp(prefix='.skill-',dir=root)
try:
    with tarfile.open(archive) as stream:
        stream.extractall(stage,filter='data')
    target=os.path.join(root,identity)
    previous=target+'.old'
    if os.path.exists(previous): shutil.rmtree(previous)
    if os.path.exists(target): os.replace(target,previous)
    os.replace(stage,target)
    if os.path.exists(previous): shutil.rmtree(previous)
finally:
    if os.path.exists(stage): shutil.rmtree(stage)
    if os.path.exists(archive): os.unlink(archive)
"""


class FileSkillProjectionMixin:
    """仅负责应用已授权包的同步，不扩大容器工具权限。"""

    async def _copy_local_skills(self):
        paths = getattr(self, "_aw_skill_paths", None) or []
        backend = self._backend
        if not paths or backend is None:
            return
        for path in paths:
            selected = resolved_path(path)
            read_skill(selected)
            candidate = selected.candidate
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w", dereference=True) as archive:
                for relative in _resources(candidate.package_path):
                    archive.add(candidate.package_path / relative, arcname=relative, recursive=False)
            temp = f"/tmp/yinli-skill-{uuid.uuid4().hex}.tar"
            await backend.write_file(temp, buffer.getvalue())
            result = await backend.exec_shell(["python3", "-c", _EXTRACT, temp, self._skills_dir, candidate.id])
            if not result.ok():
                raise RuntimeError(f"Skill 容器投影失败：{candidate.id}")


class FileSkillDockerWorkspace(FileSkillProjectionMixin, DockerWorkspace):
    """保留 SDK Docker Workspace，仅替换 Skill 初始投影策略。"""
