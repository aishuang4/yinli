"""按租户分桶的进程内缓存工具。

hosted 下模块级状态必须按 ``tenant_id`` 分桶，删除租户时能整体驱逐；
``current_tenant()`` 在 hosted 下没有上下文会抛错，因此 ``get()`` 天然 fail closed。
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from threading import RLock
from typing import Generic, TypeVar

from src.tenancy.context import current_tenant

T = TypeVar("T")

# 已注册的 TenantScoped 实例，供 evict_tenant / evict_all 统一驱逐。
_scoped_instances: weakref.WeakSet["TenantScoped"] = weakref.WeakSet()
# dict 形态缓存的清理函数：fn(tenant_id) 驱逐单租户，fn(None) 清空全部。
_cleanup_callbacks: dict[str, Callable[[str | None], None]] = {}
_registry_lock = RLock()


class TenantDictProxy:
    """把按租户分桶的 dict 暴露成普通映射。

    测试可以用 monkeypatch 换成普通 dict；未替换时，读写都进当前租户的桶，
    第一次访问才调用 loader。
    """

    def __init__(self, scoped: TenantScoped[dict], loader: Callable[[], None] | None = None) -> None:
        self._scoped = scoped
        self._loader = loader
        self._loaded: dict[str, dict] = {}

    def _bucket(self, *, load: bool) -> dict:
        from src.tenancy.context import current_tenant

        tenant_id = current_tenant().tenant_id
        bucket = self._scoped.get()
        if load and self._loaded.get(tenant_id) is not bucket:
            self._loaded[tenant_id] = bucket
            if self._loader is not None:
                self._loader()
        return bucket

    def __getitem__(self, key: object) -> object:
        return self._bucket(load=True)[key]

    def __setitem__(self, key: object, value: object) -> None:
        # 写入不触发整表加载，避免把刚写入 sqlite 的行立刻迁走。
        self._bucket(load=False)[key] = value

    def __delitem__(self, key: object) -> None:
        del self._bucket(load=True)[key]

    def __contains__(self, key: object) -> bool:
        return key in self._bucket(load=True)

    def __iter__(self):
        return iter(self._bucket(load=True))

    def __len__(self) -> int:
        return len(self._bucket(load=True))

    def get(self, key: object, default: object = None) -> object:
        return self._bucket(load=True).get(key, default)

    def items(self):
        return self._bucket(load=True).items()

    def values(self):
        return self._bucket(load=True).values()

    def keys(self):
        return self._bucket(load=True).keys()

    def clear(self) -> None:
        self._bucket(load=False).clear()

    def mark_loaded(self) -> None:
        """这次写入已经是完整快照，接下来的读取不要再跑 loader。"""

        from src.tenancy.context import current_tenant

        tenant_id = current_tenant().tenant_id
        self._loaded[tenant_id] = self._scoped.get()

    def update(self, other: object) -> None:
        self._bucket(load=False).update(other)  # type: ignore[arg-type]

    def pop(self, key: object, *args: object) -> object:
        return self._bucket(load=True).pop(key, *args)

    def setdefault(self, key: object, default: object = None) -> object:
        return self._bucket(load=True).setdefault(key, default)


class TenantScoped(Generic[T]):
    """按 tenant_id 懒加载、可整体驱逐的进程内状态。"""

    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._instances: dict[str, T] = {}
        self._lock = RLock()
        with _registry_lock:
            _scoped_instances.add(self)

    def get(self) -> T:
        """当前租户的实例；首次访问在当前租户上下文内调用 factory。"""

        tenant_id = current_tenant().tenant_id
        with self._lock:
            existing = self._instances.get(tenant_id)
        if existing is not None:
            return existing
        value = self._factory()
        with self._lock:
            return self._instances.setdefault(tenant_id, value)

    def peek(self, tenant_id: str) -> T | None:
        with self._lock:
            return self._instances.get(tenant_id)

    def replace(self, value: T) -> None:
        """整体替换当前租户的实例（如注册表重新加载）。"""

        tenant_id = current_tenant().tenant_id
        with self._lock:
            self._instances[tenant_id] = value

    def evict(self, tenant_id: str) -> None:
        with self._lock:
            self._instances.pop(tenant_id, None)

    def clear(self) -> None:
        with self._lock:
            self._instances.clear()


def register_tenant_cleanup(name: str, cleanup: Callable[[str | None], None]) -> None:
    """登记 dict 形态缓存的清理函数；重复登记同名会覆盖。"""

    with _registry_lock:
        _cleanup_callbacks[name] = cleanup


def evict_tenant(tenant_id: str) -> None:
    """驱逐该租户的全部 TenantScoped 实例与登记的 dict 缓存。"""

    with _registry_lock:
        scoped = list(_scoped_instances)
        callbacks = list(_cleanup_callbacks.values())
    for instance in scoped:
        instance.evict(tenant_id)
    for cleanup in callbacks:
        cleanup(tenant_id)


def evict_all() -> None:
    """清空全部租户缓存；测试 teardown 用。"""

    with _registry_lock:
        scoped = list(_scoped_instances)
        callbacks = list(_cleanup_callbacks.values())
    for instance in scoped:
        instance.clear()
    for cleanup in callbacks:
        cleanup(None)
