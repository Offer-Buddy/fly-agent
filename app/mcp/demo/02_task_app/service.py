"""④ 业务层：任务清单。

对应课件第四节。规矩只有一条 —— 这里不认识任何 Web 框架：
没有 import fastapi，也没有 import fastmcp，所以 REST 和 MCP 都能用它。
"""

from itertools import count

_TASKS: dict[int, dict] = {}  # 内存里的表，演示用；真项目换成数据库
_SEQ = count(1)  # 下一个任务的 id，从 1 开始


async def create_task(title: str, owner: str) -> dict:
    """新建任务。owner 由调用方传进来，业务自己不猜。

    Args:
        title: 任务标题。
        owner: 任务归属人，由网关解出后传进来。

    Returns:
        刚建好的任务记录。
    """
    task = {"id": next(_SEQ), "title": title, "owner": owner, "done": False}
    _TASKS[task["id"]] = task  # 落库（这里是落内存）
    return task


async def list_tasks(owner: str) -> list[dict]:
    """只返回自己的任务 —— 数据隔离在业务层做一次，两条通道都生效。

    Args:
        owner: 任务归属人。

    Returns:
        该用户的全部任务。
    """
    return [t for t in _TASKS.values() if t["owner"] == owner]


async def complete_task(task_id: int, owner: str) -> dict:
    """标记完成。不是自己的任务，一律当作不存在。

    Args:
        task_id: 任务 id。
        owner: 任务归属人。

    Returns:
        更新后的任务记录。

    Raises:
        LookupError: 任务不存在，或不属于这个用户。
    """
    task = _TASKS.get(task_id)
    if task is None or task["owner"] != owner:  # 别人的和不存在，对外表现一致
        raise LookupError(f"任务 {task_id} 不存在")
    task["done"] = True
    return task
