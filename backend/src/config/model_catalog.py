"""models.json 唯一配置源、原子保存及一次性旧配置迁移。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock, local
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.config.runtime_paths import tenant_root


PLATFORM_PROVIDER_ID = "yinli-platform"
BUILTIN_PROVIDER_NAME = "系统内置模型"


def models_path() -> Path:
    """当前租户的自定义模型目录文件。"""

    return tenant_root() / "models.json"


_lock = RLock()
_lock_state = local()
_secured_windows_files: dict[Path, tuple[int, int, int]] = {}


@contextmanager
def _locked():
    """同进程可重入；跨进程用独立锁文件避免 replace 使锁失效。"""
    with _lock:
        if getattr(_lock_state, "held", False):
            yield
            return
        models_path().parent.mkdir(parents=True, exist_ok=True)
        with open(models_path().with_suffix(".lock"), "a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            _lock_state.held = True
            try:
                yield
            finally:
                _lock_state.held = False
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class CatalogError(ValueError):
    """可向用户展示的配置错误，不能包含原始配置或 Key。"""


class CatalogConflict(CatalogError):
    """页面持有的配置已过期。"""


class ConfigValue(BaseModel):
    """文件字段使用 camelCase，内部允许字段名称。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class ProviderConfig(ConfigValue):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    adapter: Literal[
        "openai", "deepseek", "dashscope", "moonshot", "gemini", "anthropic", "ollama"
    ]
    base_url: str = Field(default="", alias="baseUrl")
    api_key: str = Field(default="", alias="apiKey", repr=False)
    requires_api_key: bool = Field(default=True, alias="requiresApiKey")
    enabled: bool = True


class ModelParameters(ConfigValue):
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=0.8, alias="topP", ge=0, le=1)
    max_tokens: int = Field(default=4096, alias="maxTokens", gt=0)


class Capabilities(ConfigValue):
    images: bool = False


class ModelEntry(ConfigValue):
    id: str = Field(min_length=1)
    provider_id: str = Field(alias="providerId", min_length=1)
    model: str = Field(min_length=1)
    label: str = Field(min_length=1)
    enabled: bool = True
    fallback_model_ids: tuple[str, ...] = Field(default=(), alias="fallbackModelIds")
    # 模型上下文窗口（tokens）。0 表示按供应商 adapter 的默认值；AgentScope 用它决定何时压缩上下文。
    context_size: int = Field(default=0, alias="contextSize", ge=0)
    parameters: ModelParameters = Field(default_factory=ModelParameters)
    capabilities: Capabilities = Field(default_factory=Capabilities)


class ModelCatalog(ConfigValue):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    revision: int = Field(default=1, ge=1)
    default_model_id: str = Field(default="", alias="defaultModelId")
    providers: tuple[ProviderConfig, ...] = ()
    models: tuple[ModelEntry, ...] = ()

    @model_validator(mode="after")
    def validate_references(self):
        for provider in self.providers:
            if any(char in provider.api_key for char in ("\r", "\n", "\0")):
                raise ValueError("API Key 不能包含换行或控制字符")
            if provider.base_url:
                url = urlsplit(provider.base_url)
                if (
                    url.scheme not in {"http", "https"}
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.fragment
                ):
                    raise ValueError("供应商地址必须是无内嵌凭据的 HTTP URL")
                if any(
                    key.lower() in {"key", "api_key", "apikey", "token", "access_token"}
                    for key, _ in parse_qsl(url.query)
                ):
                    raise ValueError("API Key 必须填写在独立字段")
        providers = {item.id for item in self.providers}
        models = {item.id: item for item in self.models}
        if len(providers) != len(self.providers) or len(models) != len(self.models):
            raise ValueError("配置 ID 不能重复")
        if self.default_model_id and self.default_model_id not in models:
            raise ValueError("默认模型不存在")
        for item in self.models:
            if item.provider_id not in providers:
                raise ValueError("模型供应商不存在")
            if len(set(item.fallback_model_ids)) != len(item.fallback_model_ids):
                raise ValueError("降级模型重复")
            if any(key not in models for key in item.fallback_model_ids):
                raise ValueError("降级模型不存在")
        visiting, visited = set(), set()

        def visit(key):
            if key in visiting:
                raise ValueError("降级链不能循环")
            if key in visited:
                return
            visiting.add(key)
            for child in models[key].fallback_model_ids:
                visit(child)
            visiting.remove(key)
            visited.add(key)

        for key in models:
            visit(key)
        return self


def _parse(payload: bytes | dict) -> ModelCatalog:
    try:
        if isinstance(payload, bytes):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            # 网页端展示/导出的脱敏目录可能包含这些非配置字段；读取时忽略它们。
            payload = dict(payload)
            payload.pop("etag", None)
            payload.pop("modelsPath", None)
            payload["providers"] = [
                {
                    key: value
                    for key, value in provider.items()
                    if key != "apiKeyConfigured"
                }
                for provider in payload.get("providers", [])
            ]
        return (
            ModelCatalog.model_validate(payload)
        )
    except ValidationError as exc:
        parts: list[str] = []
        for item in exc.errors()[:4]:
            loc = ".".join(str(part) for part in item.get("loc", ()) if part not in {"body"})
            msg = str(item.get("msg") or "无效")
            parts.append(f"{loc}: {msg}" if loc else msg)
        raise CatalogError(
            "models.json 格式或引用无效" + (f"：{'；'.join(parts)}" if parts else "，请检查配置后重试。")
        ) from None
    except (ValueError, TypeError) as exc:
        message = str(exc).strip() or "格式或引用无效"
        raise CatalogError(message) from None


def _secure_file(path: Path) -> None:
    if os.name == "nt":
        # SID 不依赖用户名编码；替换前给临时文件设置受保护 DACL。
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        import csv

        sid = next(csv.reader([result.stdout.strip()]))[1]
        # 外部复制的文件可能带有显式授权，先重置再收紧，避免保留其他用户访问权。
        subprocess.run(
            ["icacls", str(path), "/reset"],
            capture_output=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
            capture_output=True,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        path.chmod(0o600)


def _write(catalog: ModelCatalog) -> None:
    serialized = catalog.model_dump_json(by_alias=True, indent=2)
    if len(serialized.encode("utf-8")) > 4 * 1024 * 1024:
        raise CatalogError("models.json 超过 4 MB 限制。")
    models_path().parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(
            prefix=".models-", suffix=".tmp", dir=models_path().parent
        )
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            _secure_file(temporary)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, models_path())
    except (OSError, subprocess.SubprocessError):
        raise CatalogError(
            "models.json 保存失败，请检查文件权限或文件是否被占用。"
        ) from None
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _migrate() -> ModelCatalog:
    from src.config.legacy_model_settings import (
        LEGACY_PROVIDER_ADAPTERS,
        MODEL_PROVIDERS,
        ModelProvider,
    )
    from src.config.secret_store import SecretStoreError, get_model_api_key
    from src.config.settings import settings
    from src.storage.database import get_json_record

    old = get_json_record("system_settings", "model") or {}
    selected = str(old.get("provider") or "deepseek")
    configs = old.get("providerConfigs") or {selected: old}
    # 模板之外的旧供应商实例也要保留，否则迁移会静默丢配置。
    templates = dict(MODEL_PROVIDERS)
    for ident in configs:
        if ident in templates:
            continue
        config = configs.get(ident) or {}
        templates[ident] = ModelProvider(
            id=ident,
            name=str(config.get("providerName") or ident),
            default_base_url="",
            default_model=str(config.get("defaultModel") or ""),
            models=(),
            adapter=LEGACY_PROVIDER_ADAPTERS.get(ident, "openai"),
        )
    providers, models = [], []
    default_id = ""
    for ident, template in templates.items():
        config = configs.get(ident) or {}
        key = ""
        if not config.get("apiKeySuppressed"):
            try:
                key = str(config.get("apiKey") or get_model_api_key(ident) or "")
            except SecretStoreError:
                pass
            if not key and not config.get("apiKeyConfigured"):
                key = {
                    "deepseek": settings.deepseek_api_key,
                    "dashscope": settings.dashscope_api_key,
                }.get(ident, "")
        providers.append(
            ProviderConfig(
                id=ident,
                name=config.get("providerName") or template.name,
                adapter=template.adapter,
                baseUrl=config.get("baseUrl") or template.default_base_url,
                apiKey=key,
                requiresApiKey=template.requires_api_key,
            )
        )
        primary = config.get("defaultModel") or template.default_model
        names = list(
            dict.fromkeys(
                filter(
                    None,
                    [
                        primary,
                        *config.get("customModels", []),
                        *config.get("fallbackModels", []),
                        *template.models,
                    ],
                )
            )
        )
        ids = {name: f"{ident}:{name}" for name in names}
        for name in names:
            models.append(
                ModelEntry(
                    id=ids[name],
                    providerId=ident,
                    model=name,
                    label=name,
                    capabilities={"images": name == "qwen3.7-plus"},
                    fallbackModelIds=tuple(
                        dict.fromkeys(
                            ids[n]
                            for n in config.get("fallbackModels", [])
                            if n != name
                        )
                    )
                    if name == primary
                    else (),
                )
            )
        if ident == selected:
            default_id = ids.get(primary, "")
    return ModelCatalog(
        providers=tuple(providers), models=tuple(models), defaultModelId=default_id
    )


def read_catalog() -> tuple[ModelCatalog, str]:
    """每次读取磁盘；迁移标记防止文件删除后复活旧 Key。"""
    with _locked():
        from src.storage.database import get_json_record, upsert_json_record

        if not models_path().exists():
            marker = get_json_record("system_settings", "models_file_migrated")
            if marker:
                raise CatalogError("models.json 不存在，请恢复配置文件。")
            try:
                migrated = _migrate()
            except (ValueError, TypeError):
                raise CatalogError("旧模型配置格式无效，迁移未完成。") from None
            _write(migrated)
        try:
            if models_path().stat().st_size > 4 * 1024 * 1024:
                raise CatalogError("models.json 超过 4 MB 限制。")
            payload = models_path().read_bytes()
        except OSError:
            raise CatalogError("models.json 无法读取，请检查权限。") from None
        catalog = _parse(payload)
        try:
            stat = models_path().stat()
            signature = (stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns)
            if os.name == "nt":
                if _secured_windows_files.get(models_path()) != signature:
                    _secure_file(models_path())
                    secured = models_path().stat()
                    _secured_windows_files[models_path()] = (
                        secured.st_ino,
                        secured.st_ctime_ns,
                        secured.st_mtime_ns,
                    )
            elif stat.st_mode & 0o077:
                _secure_file(models_path())
        except (OSError, subprocess.SubprocessError):
            raise CatalogError("models.json 无法设置仅当前用户访问权限。") from None
        if not get_json_record("system_settings", "models_file_migrated"):
            upsert_json_record(
                "system_settings", "models_file_migrated", {"version": 1}
            )
        return catalog, hashlib.sha256(payload).hexdigest()


_UPSTREAM_MODEL_LIMIT = 40


def list_provider_upstream_models(provider_id: str) -> list[str]:
    """用已保存的供应商 Key 读取上游 /v1/models。不把 Key 带回调用方。"""

    import ipaddress

    import httpx

    catalog, _etag = read_catalog()
    provider = next((item for item in catalog.providers if item.id == provider_id), None)
    if provider is None:
        raise CatalogError("供应商不存在，请先保存连接。")
    if provider.requires_api_key and not provider.api_key.strip():
        raise CatalogError("请先保存该供应商的 API Key，再拉取上游模型。")
    base = provider.base_url.strip().rstrip("/")
    if not base:
        raise CatalogError("请先填写并保存 Base URL。")
    host = (urlsplit(base).hostname or "").lower()
    if host in {"localhost", "metadata.google.internal"}:
        raise CatalogError("不能从本机或元数据地址拉取模型。")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise CatalogError("不能从内网地址拉取模型。")
    endpoint = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key.strip() else {}
    try:
        response = httpx.get(endpoint, headers=headers, timeout=12.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise CatalogError("上游模型列表请求失败。") from exc
    if response.status_code >= 400:
        raise CatalogError(f"上游拒绝列出模型（{response.status_code}）。")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CatalogError("上游模型列表不是 JSON。") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CatalogError("上游模型列表格式无法识别。")
    names: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= _UPSTREAM_MODEL_LIMIT:
            break
    return names


def ensure_hosted_upstream_catalog() -> None:
    """托管且能拉到上游时，把模型名写入当前租户的 models.json。Key 仍留在控制面。"""

    from src.tenancy.context import current_tenant, is_hosted_mode

    if not is_hosted_mode():
        return
    user_id = current_tenant().user_id
    if not user_id:
        return
    from src.server.auth.key2api import Key2ApiError, base_url, key2api_configured, list_platform_models

    if not key2api_configured():
        return
    try:
        remote = list_platform_models(user_id)
    except Key2ApiError:
        return
    names = []
    seen: set[str] = set()
    for item in remote:
        name = str(item.get("label") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= _UPSTREAM_MODEL_LIMIT:
            break
    if not names:
        return
    catalog, _etag = read_catalog()
    providers = [item for item in catalog.providers if item.id != PLATFORM_PROVIDER_ID]
    providers.append(ProviderConfig(
        id=PLATFORM_PROVIDER_ID,
        name=BUILTIN_PROVIDER_NAME,
        adapter="openai",
        baseUrl=f"{base_url()}/v1",
        apiKey="",
        requiresApiKey=False,
        enabled=True,
    ))
    kept = [item for item in catalog.models if item.provider_id != PLATFORM_PROVIDER_ID]
    upstream = []
    for name in names:
        upstream.append(ModelEntry(
            id=f"upstream-{name}",
            providerId=PLATFORM_PROVIDER_ID,
            model=name,
            label=name,
        ))
    models = [*kept, *upstream]
    model_ids = {item.id for item in models}
    default_id = catalog.default_model_id if catalog.default_model_id in model_ids else upstream[0].id
    previous = {
        (item.id, item.model, item.label)
        for item in catalog.models
        if item.provider_id == PLATFORM_PROVIDER_ID
    }
    current = {(item.id, item.model, item.label) for item in upstream}
    existing_provider = next((item for item in catalog.providers if item.id == PLATFORM_PROVIDER_ID), None)
    if (
        previous == current
        and catalog.default_model_id == default_id
        and existing_provider is not None
        and existing_provider.name == BUILTIN_PROVIDER_NAME
    ):
        return
    _write(ModelCatalog(
        revision=catalog.revision,
        defaultModelId=default_id,
        providers=tuple(providers),
        models=tuple(models),
    ))


def public_catalog() -> dict:
    catalog, etag = read_catalog()
    result = catalog.model_dump(mode="json", by_alias=True)
    for provider in result["providers"]:
        provider["apiKeyConfigured"] = bool(provider.pop("apiKey"))
    result["etag"] = etag
    result["modelsPath"] = str(models_path())
    return result


def save_catalog(payload: dict, expected_etag: str) -> dict:
    """脱敏表单未提供 apiKey 时保留原值；空字符串明确清除。"""
    with _locked():
        current, etag = read_catalog()
        if etag != expected_etag:
            raise CatalogConflict("模型配置已改变，请刷新后重试。")
        keys = {provider.id: provider.api_key for provider in current.providers}
        data = dict(payload)
        data.pop("modelsPath", None)
        data.pop("etag", None)
        try:
            data["providers"] = [
                {
                    **{
                        key: value
                        for key, value in item.items()
                        if key != "apiKeyConfigured"
                    },
                    "apiKey": item.get("apiKey", keys.get(item.get("id"), "")),
                }
                for item in data.get("providers", [])
            ]
            data["models"] = [
                item
                for item in data.get("models") or []
                if str((item or {}).get("model") or "").strip()
                and str((item or {}).get("label") or "").strip()
            ]
        except (AttributeError, TypeError):
            raise CatalogError("供应商配置格式无效。") from None
        data["revision"] = current.revision + 1
        parsed = _parse(data)
        # 再次检查外部编辑；页面和本进程写操作已由锁串行化。
        if hashlib.sha256(models_path().read_bytes()).hexdigest() != etag:
            raise CatalogConflict("模型配置已改变，请刷新后重试。")
        _write(parsed)
        return public_catalog()
