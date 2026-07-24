"""Chat Event Stream V3 离线投影核心。"""

from .block_lifecycle import BlockLifecycleCoordinator
from .domain_events import DomainEvent, DomainEventDraft, DomainEventRepository
from .frame_schema import DisplaySnapshot, RuntimeFrame
from .projection_registry import ProjectionRegistry, default_projection_registry
from .projection_runner import ProjectionRunner
from .projection_writer import ProjectionStreamStore
from .stream_service import ProjectionStreamBatch, ProjectionStreamService

__all__ = [
    "BlockLifecycleCoordinator",
    "DisplaySnapshot",
    "DomainEvent",
    "DomainEventDraft",
    "DomainEventRepository",
    "ProjectionRegistry",
    "ProjectionRunner",
    "ProjectionStreamStore",
    "ProjectionStreamBatch",
    "ProjectionStreamService",
    "RuntimeFrame",
    "default_projection_registry",
]
