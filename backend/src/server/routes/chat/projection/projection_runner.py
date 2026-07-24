"""按 stream_ordinal 串行消费 Domain Event 的单 runner。"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock, RLock
from uuid import uuid4

from .block_lifecycle import BlockLifecycleCoordinator
from .domain_events import DomainEventRepository
from .projection_registry import ProjectionRegistry
from .projection_writer import ProjectionStreamStore

_STREAM_LOCKS_GUARD = RLock()
_STREAM_LOCKS: dict[str, Lock] = defaultdict(Lock)


class ProjectionRunner:
    """一个 stream 同时只允许一个持有有效 fence 的串行投影执行者。"""

    def __init__(
        self,
        *,
        events: DomainEventRepository,
        store: ProjectionStreamStore,
        registry: ProjectionRegistry,
        coordinator: BlockLifecycleCoordinator,
        owner: str | None = None,
    ) -> None:
        self.events = events
        self.store = store
        self.registry = registry
        self.coordinator = coordinator
        self.owner = owner or f"projection-runner-{uuid4().hex}"

    def run_available(self, stream_key: str, run_id: str, *, limit: int | None = None) -> int:
        """消费当前连续可用的领域事实，失败时不越过缺口。"""

        self.store.ensure_stream(stream_key, run_id)
        with _STREAM_LOCKS_GUARD:
            stream_lock = _STREAM_LOCKS[stream_key]
        with stream_lock:
            lease = self.store.acquire_lease(stream_key, self.owner)
            consumed = 0
            try:
                state = self.store.load_materialized(stream_key, self.coordinator)
                while limit is None or consumed < limit:
                    event = self.events.get_next(stream_key, state.last_domain_ordinal)
                    if event is None:
                        break
                    mutations = self.registry.project(event, state.snapshot)
                    snapshot, frames = self.coordinator.apply_mutations(state.snapshot, mutations)
                    state = self.store.commit_event(lease, event, frames, snapshot)
                    consumed += 1
                return consumed
            finally:
                self.store.release_lease(lease)
