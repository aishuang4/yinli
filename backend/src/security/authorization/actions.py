"""规范实际工具行动；文件解析复用工具路径边界，敏感参数只在 Run 现场驻留。"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import difflib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.tools.configured_http_tool import prepare_configured_http_request
from src.tools.http_request import prepare_http_request
from src.security.egress import assert_public_url

from src.tenancy.context import current_tenant
from src.tools.scoped_paths import resolve_existing_workspace_path, resolve_workspace_path

from .effects import ToolIdentity, classify_effect, is_simple_readonly_shell
from .models import ActionSnapshot, ResourceRevision, RunAuthorization, TargetVersion
from .payloads import ActionInputVault, public_input


_FILE_FIELDS = {
    ("src.tools.scoped_read", "ScopedRead"): ("file_path", True),
    ("src.tools.scoped_write", "ScopedWrite"): ("file_path", False),
    ("src.tools.scoped_edit", "ScopedEdit"): ("file_path", False),
    ("src.tools.scoped_grep", "ScopedGrep"): ("path", True),
    ("src.tools.scoped_glob", "ScopedGlob"): ("path", True),
}


def file_target(path: str) -> TargetVersion:
    """绑定真实路径、存在性与原内容；异常文件不生成可执行快照。"""
    resolved = Path(path)
    if not resolved.exists():
        return TargetVersion(target=path, version="absent")
    stat = resolved.stat()
    digest = ""
    if resolved.is_file():
        hasher = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    elif not resolved.is_dir():
        raise ValueError("行动目标不是普通文件或目录")
    return TargetVersion(target=path, version=f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}",
                         contentDigest=digest)


def _public_url(value: str) -> str:
    """query/fragment 与 URL 内凭据不进审计目标，完整身份由受限摘要绑定。"""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class RuntimeActionBuilder:
    """由服务端工具实例建立双表示快照，审核/普通审计不获取原始敏感正文。"""

    def __init__(self, owner: RunAuthorization, *, known_urls: frozenset[str] = frozenset(),
                 filesystem_scope: str = "", extra_filesystem_scopes: tuple[str, ...] = (),
                 mcp_connections: dict[str, dict[str, Any]] | None = None,
                 resource_revisions: tuple[ResourceRevision, ...] = ()):
        self.owner = owner
        self.vault = ActionInputVault(owner)
        self.known_urls = known_urls
        self.filesystem_scope = filesystem_scope
        self.extra_filesystem_scopes = extra_filesystem_scopes
        self._tools: dict[str, Any] = {}
        self.mcp_connections = mcp_connections or {}
        self.resource_revisions = resource_revisions
        self._identity_readers: dict[int, Any] = {}
        self._original_inputs: dict[str, str] = {}
        self._tool_catalog = None

    def attach_tool_catalog(self, reader) -> None:
        """仅由集中 SDK adapter 提供实际可见工具，不能靠构造动作登记名字扩权。"""
        self._tool_catalog = reader

    async def available_tool_names(self) -> frozenset[str]:
        """没有真实 Toolkit 现场时一律没有可执行工具。"""
        return await self._tool_catalog() if self._tool_catalog is not None else frozenset()

    def clear(self) -> None:
        """Run 收口后销毁原参数、凭据连接和工具现场，保留数据库审计。"""
        self.vault.clear()
        self._original_inputs.clear()
        self._tools.clear()
        self._identity_readers.clear()
        self.mcp_connections.clear()
        self._tool_catalog = None

    def display_action(self, action: ActionSnapshot) -> dict:
        """可信本地/HTTP 工具提供脱敏有限摘要；未知 MCP 参数仍只展示结构。"""
        from src.config.model_catalog import read_catalog
        from .review_contract import ReviewEvidence, review_prompt

        tool = self.tool_for(action)
        if tool is None or bool(tool.is_mcp):
            return {}
        allowed = {"src.tools.scoped_write", "src.tools.scoped_edit", "src.agents.guarded_bash",
                   "src.tools.http_request", "src.tools.configured_http_tool", "src.tools.knowledge_base_search"}
        if type(tool).__module__ not in allowed:
            return {}
        raw = self.execution_input(action)
        catalog, _ = read_catalog()
        secrets = tuple(provider.api_key for provider in catalog.providers if provider.api_key)
        prompt, _ = review_prompt(action, ReviewEvidence("展示精确操作", raw), secrets=secrets)
        if not prompt:
            return {"summary": "行动正文超过展示上限，需要检查受限内容后判断"}
        inputs = json.loads(prompt)["action"]["input"]
        result = {"summary": json.dumps(inputs, ensure_ascii=False)[:6000]}
        if type(tool).__module__ == "src.tools.scoped_write":
            import difflib
            path = inputs.get("file_path", "")
            content = str(inputs.get("content", ""))
            before = ""
            if action.targets and action.targets[0].version != "absent":
                with Path(action.targets[0].target).open("r", encoding="utf-8", errors="replace") as source:
                    before = source.read(32_001)
            if len(before) > 32_000 or len(content) > 32_000:
                result["diffPreview"] = "文件变更超出预览上限，摘要不能作为完整内容"
            else:
                diff = "".join(difflib.unified_diff(before.splitlines(True), content.splitlines(True),
                                                    fromfile=str(path), tofile=str(path)))
                preview, _ = review_prompt(action, ReviewEvidence("展示差异", "{}", untrusted_material=(diff,)), secrets=secrets)
                result["diffPreview"] = json.loads(preview)["thirdPartyMaterial"]["items"][0][:6000] if preview else "差异超出预览上限"
        if action.effect == "outbound":
            result["outboundPreview"] = result["summary"]
        return result

    def register_tool_identity(self, tool: Any, reader: Any) -> None:
        """集中 SDK adapter 提供原生身份读取；领域层不探入 SDK 私有字段。"""
        self._identity_readers[id(tool)] = reader

    def tool_for(self, action: ActionSnapshot) -> Any | None:
        """只取相同授权现场已构造快照的工具，不从名称重新找不可信实例。"""
        if action.authorization != self.owner:
            return None
        return self._tools.get(action.fingerprint)

    def execution_input(self, action: ActionSnapshot) -> str:
        """执行只能取相同 Run/actor 的原始参数，禁止从客户端补全引用。"""
        if action.authorization != self.owner:
            raise ValueError("敏感行动参数归属不匹配")
        canonical = self.vault.get(action.restricted_input_ref)
        if self.vault.digest(canonical) != action.execution_input_digest:
            raise ValueError("敏感行动参数摘要不匹配")
        return canonical

    async def __call__(self, tool: Any, call_id: str, canonical: str) -> ActionSnapshot:
        if current_tenant().tenant_id != self.owner.tenant_id:
            raise ValueError("行动租户不匹配")
        inputs = json.loads(ActionSnapshot.canonical_json(canonical))
        identity = ToolIdentity(type(tool).__module__, type(tool).__name__, tool.name, bool(tool.is_mcp))
        key = (identity.module, identity.class_name)
        targets = []
        shell_proven = False
        execution_environment = None
        if key in _FILE_FIELDS:
            field, existing = _FILE_FIELDS[key]
            resolver = resolve_existing_workspace_path if existing else resolve_workspace_path
            resolved = resolver(inputs.get(field) or ".", base_dir=tool.base_dir,
                                scope_dir=tool.scope_dir, extra_scopes=tool.extra_scopes)
            inputs[field] = resolved
            targets.append(file_target(resolved))
        if key == ("src.agents.guarded_bash", "GuardedBash"):
            tool.validate_execution(str(inputs.get("command") or ""))
            inputs["timeout"] = tool.normalize_timeout(inputs.get("timeout", 120000))
            command = str(inputs.get("command") or "")
            # 未证明只读的 Shell 也绑定 cwd 和后端，防审批后切执行位置。
            cwd = str(Path(tool._cwd or ".").resolve())
            if self.filesystem_scope:
                cwd = resolve_workspace_path(cwd, scope_dir=self.filesystem_scope,
                                             extra_scopes=list(self.extra_filesystem_scopes))
            execution_environment = {"cwd": cwd, "backend": type(tool._backend).__name__}
            # 容器和 Windows 的 shell 语义不同，首版不推测其只读证明。
            if (os.name != "nt" and tool._backend.__class__.__name__ == "LocalBackend"
                    and self.filesystem_scope):
                parts = shlex.split(command) if is_simple_readonly_shell(command) else []
                # 重建时接受平台已经绑定的绝对可执行文件。
                if not parts:
                    try:
                        bound = shlex.split(command)
                        aliases = {"/bin/pwd": "pwd", "/bin/cat": "cat", "/usr/bin/wc": "wc"}
                        candidate = [aliases.get(bound[0], ""), *bound[1:]] if bound else []
                        if candidate and is_simple_readonly_shell(shlex.join(candidate)):
                            parts = candidate
                    except ValueError:
                        pass
                if parts:
                    cwd = resolve_workspace_path(tool._cwd or ".", scope_dir=self.filesystem_scope)
                    executable = {"pwd": "/bin/pwd", "cat": "/bin/cat", "wc": "/usr/bin/wc"}[parts[0]]
                    if Path(executable).is_file():
                        if parts[0] == "pwd":
                            targets.append(TargetVersion(target=cwd, version="cwd"))
                            parts = [executable]
                        else:
                            resolved = resolve_existing_workspace_path(
                                parts[-1], base_dir=cwd, scope_dir=self.filesystem_scope,
                                extra_scopes=list(self.extra_filesystem_scopes),
                            )
                            if not Path(resolved).is_file():
                                raise ValueError("只读 Shell 仅支持普通文件")
                            targets.append(file_target(resolved))
                            parts = [executable, *parts[1:-1], resolved]
                        # 系统二进制属于执行身份，不是 Agent 可访问的文件目标。
                        # 仍绑定内容版本，复核时二进制替换会使工具版本失效。
                        execution_environment["executable"] = file_target(executable).model_dump(mode="json", by_alias=True)
                        inputs["command"] = shlex.join(parts)
                        shell_proven = True
        if key == ("src.tools.web_fetch", "WebFetch"):
            inputs["url"] = str(inputs.get("url") or "").strip()
            await tool.validate_execution(inputs["url"])
            targets.append(TargetVersion(target=_public_url(inputs["url"])))
        request_data = None
        prepared = None
        if key == ("src.tools.configured_http_tool", "ConfiguredHttpTool"):
            parameters = dict(inputs.get("parameters") or inputs)
            prepared = prepare_configured_http_request(tool.config, parameters, timeout_seconds=tool.timeout_seconds)
        elif key == ("src.tools.http_request", "HttpRequest"):
            prepared = prepare_http_request(**inputs)
        elif key == ("src.tools.knowledge_base_search", "KnowledgeBaseSearch"):
            prepared = tool.prepare_execution(**inputs)
        if prepared is not None:
            request = httpx.Request(**{key: value for key, value in prepared.items() if key not in {"timeout", "auth"}})
            await assert_public_url(str(request.url), purpose="tool")
            targets.append(TargetVersion(target=_public_url(str(request.url))))
            request_data = {"method": request.method, "url": str(request.url),
                            "headers": dict(request.headers), "body": request.content.hex()}
            if prepared.get("auth") is not None:
                auth_request = next(prepared["auth"].auth_flow(request))
                request_data["headers"] = dict(auth_request.headers)
        mcp_identity = None
        if identity.is_mcp:
            reader = self._identity_readers.get(id(tool))
            if reader is None:
                raise ValueError("MCP 工具缺少平台连接证据")
            mcp_identity = reader()
            connection = self.mcp_connections.get(mcp_identity["server"])
            if connection is None:
                raise ValueError("MCP 连接已撤销或不在当前资源范围")
            mcp_identity = {**mcp_identity, "connection": connection}
            url = str(connection.get("url") or "")
            if url:
                await assert_public_url(url, purpose="mcp")
                targets.append(TargetVersion(target=_public_url(url)))
            else:
                targets.append(TargetVersion(target=f"mcp:{mcp_identity['server']}"))
        raw = ActionSnapshot.canonical_json(json.dumps(inputs, ensure_ascii=False))
        ref, digest = self.vault.put(raw)
        effect = classify_effect(identity, inputs, known_urls=self.known_urls,
                                shell_readonly_proven=shell_proven)
        control_plane_diff = ""
        action_resources = self.resource_revisions
        if effect == "control_plane_mutation" and targets:
            target = targets[0]
            resource_id = target.target
            revision = target.content_digest or target.version or "absent"
            action_resources = (*self.resource_revisions,
                                ResourceRevision(resourceType="file", resourceId=resource_id, revision=revision))
            before = ""
            path = Path(target.target)
            if path.is_file():
                try:
                    before = path.read_text(encoding="utf-8", errors="replace")[:6000]
                except OSError:
                    before = ""
            if identity.class_name == "ScopedEdit":
                old = str(inputs.get("old_string") or "")
                new = str(inputs.get("new_string") or "")
                after = before.replace(old, new, 1) if old and old in before else new
            else:
                after = str(inputs.get("content") or "")
            control_plane_diff = "".join(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                fromfile=resource_id, tofile=resource_id,
            ))[:12000]
        version_data = {"identity": [identity.module, identity.class_name], "schema": tool.input_schema,
                        "config": getattr(tool, "config", {}), "url": getattr(tool, "url", ""),
                        "headers": getattr(tool, "headers", {}), "authorization": getattr(tool, "authorization", ""),
                        "request": request_data, "mcp": mcp_identity, "executionEnvironment": execution_environment}
        version = self.vault.digest(json.dumps(version_data, ensure_ascii=False, sort_keys=True))
        action = ActionSnapshot(authorization=self.owner, callId=call_id, toolName=tool.name,
                                toolVersion=version, normalizedInputJson=json.dumps(public_input(inputs), ensure_ascii=False),
                                targets=tuple(targets), resourceRevisions=action_resources,
                                controlPlaneDiff=control_plane_diff, effect=effect,
                                restrictedInputRef=ref, executionInputDigest=digest)
        self._tools[action.fingerprint] = tool
        # 复核原始路径表达式，避免批准后 symlink 改指向却只检查旧 realpath。
        self._original_inputs.setdefault(action.fingerprint, canonical)
        return action

    async def revalidate(self, action: ActionSnapshot) -> str | None:
        """目标内容、符号链接、工具配置或凭据变化使原指纹失效。"""
        tool = self._tools.get(action.fingerprint)
        if tool is None:
            return "行动执行现场不存在"
        try:
            current = await self(tool, action.call_id, self._original_inputs[action.fingerprint])
        except (ValueError, TypeError, OSError, KeyError):
            return "行动目标或执行边界已变化"
        return None if current.fingerprint == action.fingerprint else "行动内容、目标或工具版本已变化"
