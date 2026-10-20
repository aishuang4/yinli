"""平台唯一授权编排：同一行动复用决定，执行前复核后领取一次许可。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4
from weakref import WeakKeyDictionary, WeakValueDictionary

from src.storage.permission_records import PermissionRecordStore

from .models import ActionSnapshot, AuthorizationDecision, ExecutionRecord, RunAuthorization
from .policy import BoundaryCheck, evaluate_policy

Reviewer = Callable[[ActionSnapshot], Awaitable[AuthorizationDecision]]

# 同一事件循环的多个 adapter/service 也复用同动作锁；弱引用不积累历史 Run。
_ACTION_LOCKS: WeakKeyDictionary = WeakKeyDictionary()


def _action_lock(fingerprint: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _ACTION_LOCKS.setdefault(loop, WeakValueDictionary())
    lock = locks.get(fingerprint)
    if lock is None:
        lock = asyncio.Lock()
        locks[fingerprint] = lock
    return lock


class PlatformAuthorizationService:
    """实例属于一份冻结 Run/actor；锁仅串行同动作，不持 SQLite 锁等待模型。"""

    def __init__(self, *, boundary_check: BoundaryCheck, revalidate: BoundaryCheck,
                 store: PermissionRecordStore | None = None, reviewer: Reviewer | None = None):
        self.boundary_check = boundary_check
        self.revalidate = revalidate
        self.store = store or PermissionRecordStore()
        self.reviewer = reviewer
        self._actor_denials: dict[tuple[str, str], int] = {}
        self._run_denials: dict[str, int] = {}
        self._boundary_denials: dict[tuple[str, str, str], int] = {}
        self.actor_denial_limit = 3
        self.run_denial_limit = 6

    async def authorize(self, action: ActionSnapshot) -> AuthorizationDecision:
        """边界每次检查，审批/model 结果按精确快照复用。"""

        lock = _action_lock(action.fingerprint)
        async with lock:
            result = await evaluate_policy(action, self.boundary_check)
            if result.source == "boundary":
                key = (action.authorization.run_id, action.authorization.actor_id, result.reason_code)
                count = self._boundary_denials.get(key, 0) + 1
                self._boundary_denials[key] = count
                if count >= self.actor_denial_limit:
                    return self._decision(action, "deny", "boundary", "boundary_denial_breaker",
                                          "同一硬边界已连续拒绝，停止继续尝试并向用户报告")
                return self._decision(action, result.verdict, result.source, result.reason_code, result.reason)
            try:
                saved = self.store.find(action)
            except ValueError:
                return self._decision(action, "deny", "boundary", "run_invalidated", "运行已失效或归属不匹配")
            if saved is None:
                candidate = self._decision(action, result.verdict, result.source, result.reason_code, result.reason)
                actor_key = (action.authorization.run_id, action.authorization.actor_id)
                if result.review_required and self._denial_limit_reached(actor_key):
                    candidate = self._decision(action, "deny", "boundary", "review_denial_breaker",
                                               "连续自动审核拒绝，已停止继续尝试，请向用户报告")
                elif result.review_required and self.reviewer is not None:
                    try:
                        candidate = await self.reviewer(action)
                        if candidate.action_fingerprint != action.fingerprint:
                            raise ValueError("审核结果不属于此行动")
                    except Exception:
                        candidate = self._decision(action, "needs_human", "model_error",
                                                   "review_unavailable", "自动审核失败，请人工判断")
                    if candidate.source == "model" and candidate.verdict == "deny":
                        self._record_denial(actor_key)
                # 模型等待期间边界可能收紧；迟到的 allow 不成为可消费许可。
                error = await self.boundary_check(action) or await self.revalidate(action)
                if error:
                    candidate = self._decision(action, "deny", "boundary", "hard_boundary", error)
                try:
                    if (candidate.source == "model" and candidate.verdict == "deny"
                            and action.authorization.surface != "job"):
                        saved = self.store.save_model_deny_with_review(action, candidate)
                    else:
                        saved = self.store.save(action, candidate)
                except ValueError:
                    return self._decision(action, "deny", "boundary", "run_invalidated", "运行已失效或归属不匹配")
            approval = self.store.get_approval(saved.id, action.authorization)
            if approval is not None:
                if approval.status == "approved":
                    return saved.model_copy(update={"verdict": "allow"})
                if approval.status in {"rejected", "cancelled", "expired", "interrupted"}:
                    return saved.model_copy(update={"verdict": "deny"})
            return saved

    async def reserve_execution(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """等待结束后读当前边界与目标版本，检查失败不占位、不执行。"""

        error = await self.boundary_check(action) or await self.revalidate(action)
        if error:
            raise ValueError(error)
        return self.store.reserve(action, decision_id)

    async def start_execution(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """占位与真正分发分开，分发前最后检查撤销和当前目标。"""

        error = await self.boundary_check(action) or await self.revalidate(action)
        if error:
            raise ValueError(error)
        return self.store.start(action, decision_id)

    async def finish_execution(self, action: ActionSnapshot, decision_id: str, *, status: str) -> bool:
        """执行终态不反推批准；取消或不完整结果由调用方保守收口为 unknown。"""

        return self.store.finish(decision_id, action.authorization, status=status)

    def _denial_limit_reached(self, actor_key: tuple[str, str]) -> bool:
        run_id, _actor_id = actor_key
        return (
            self._actor_denials.get(actor_key, 0) >= self.actor_denial_limit
            or self._run_denials.get(run_id, 0) >= self.run_denial_limit
        )

    def _record_denial(self, actor_key: tuple[str, str]) -> None:
        run_id, _actor_id = actor_key
        self._actor_denials[actor_key] = self._actor_denials.get(actor_key, 0) + 1
        self._run_denials[run_id] = self._run_denials.get(run_id, 0) + 1

    def reset_denials(self, owner: RunAuthorization) -> None:
        """只有人工决定清零对应 actor。只读、低风险和模型 allow 不清零。"""

        key = (owner.run_id, owner.actor_id)
        removed = self._actor_denials.pop(key, 0)
        if removed:
            self._run_denials[owner.run_id] = max(0, self._run_denials.get(owner.run_id, 0) - removed)
        self._boundary_denials = {
            item: count for item, count in self._boundary_denials.items() if item[:2] != key
        }

    @staticmethod
    def _decision(action, verdict, source, reason_code, reason):
        return AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
                                     verdict=verdict, source=source, reasonCode=reason_code, reason=reason,
                                     doNotRetry=reason_code in {"hard_boundary", "run_invalidated",
                                                                "review_denial_breaker", "boundary_denial_breaker"})
