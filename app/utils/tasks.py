"""后台任务工具。"""

import asyncio
from typing import (
    Any,
    Coroutine,
)

# 持有任务强引用，防止被 GC 提前回收
_background_tasks: set[asyncio.Task] = set()


def create_background_task(coro: Coroutine[Any, Any, Any]) -> None:
    """把协程作为「发射后不管」的任务跑起来，并持有引用直到它结束。

    事件循环对任务只持有弱引用，如果没有任何地方引用它，
    任务可能在跑完之前就被垃圾回收掉——这正是这里要保存引用的原因。

    Args:
        coro: 要在后台运行的协程
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    # 任务结束（无论成功失败）后自动从集合里移除，避免引用泄漏
    task.add_done_callback(_background_tasks.discard)
