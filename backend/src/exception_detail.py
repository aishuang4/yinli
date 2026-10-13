"""把异常组成可调试的文本。

各运行时的失败记录共用。ExceptionGroup 的 str() 只剩 sub-exception 计数，
这里展开子异常并附上堆栈。
"""

from __future__ import annotations

import traceback

_EXCEPTION_DETAIL_LIMIT = 8000


def format_agent_exception(exc: BaseException, *, prefix: str) -> str:
    """保留异常组里的子异常和堆栈，失败记录要能直接用来调试。"""

    summary = exception_summary(exc)
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
    text = f"{prefix}: {summary}"
    if detail:
        text = f"{text}\n{detail}"
    if len(text) <= _EXCEPTION_DETAIL_LIMIT:
        return text
    return text[:_EXCEPTION_DETAIL_LIMIT].rstrip() + "\n…(异常详情已截断)"


def exception_summary(exc: BaseException) -> str:
    """把 ExceptionGroup 展开成类型和消息，避免只剩下 sub-exception 计数。"""

    if isinstance(exc, BaseExceptionGroup):
        nested = "; ".join(exception_summary(item) for item in exc.exceptions)
        outer = str(exc).strip() or exc.__class__.__name__
        return f"{outer} | {nested}" if nested else outer
    message = str(exc).strip() or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"
