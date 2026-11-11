"""路由鉴权审计：把「哪些接口加了、哪些没加」变成一条命令。

忘了挂 ``Depends`` 不会报错、不会告警，接口安静地 200。靠人眼 review
总有一天会漏，所以让脚本去数。可直接接进 CI：有漏挂就以退出码 1 结束。

用法（仓库根目录）：

    python -m app.api.demo.jwt.audit          # 只打印清单
    python -m app.api.demo.jwt.audit --strict # 有漏挂时退出码 1
"""

import sys
from typing import Iterator

from fastapi.routing import APIRoute

from app.api.demo.jwt.app import app

# 依赖函数名 → 人话标签。新增守卫时在这里补一行，否则审计会把受保护的接口
# 误报成「漏挂」。
GUARD_LABELS: dict[str, str] = {
    "require_user": "用户令牌",
    "require_session": "会话令牌",
    "optional_user": "可选",
}

# 白名单：明确「不需要鉴权」的接口路径。空元组表示一个都不豁免。
# 把这份清单写进代码的意义是——新接口没出现在这里、也没挂依赖，就会被判为漏挂。
PUBLIC_ALLOWLIST: tuple[str, ...] = (
    "/demo/jwt/ping",
    "/demo/jwt/login",
    "/demo/jwt/feed",
    "/demo/jwt/cannot-cancel",
)


def _iter_api_routes(routes: list[object]) -> Iterator[APIRoute]:
    """递归展开路由树，只产出真正的接口。

    FastAPI 0.162 起 ``include_router`` 挂进来的是 ``_IncludedRouter`` 包装，
    不再是扁平展开的 ``APIRoute``，所以必须往下钻一层；旧版本则会直接产出
    ``APIRoute``，两种情况都要兼容。

    Args:
        routes: 某一层的路由列表

    Yields:
        APIRoute: 叶子节点上的接口
    """
    for route in routes:
        inner = getattr(route, "router", None)
        if inner is not None and hasattr(inner, "routes"):
            yield from _iter_api_routes(inner.routes)
        elif isinstance(route, APIRoute):
            yield route


def _collect_guards(dependant: object) -> set[str]:
    """收集一条路由上挂着的全部守卫依赖。

    路由级、挂载级、端点级的依赖最终都会合进同一棵 dependant 树，
    所以从根递归一遍就能把三种位置一网打尽。

    Args:
        dependant: FastAPI 的 Dependant 节点

    Returns:
        set[str]: 命中的守卫函数名
    """
    found: set[str] = set()
    for sub in getattr(dependant, "dependencies", []):
        name = getattr(getattr(sub, "call", None), "__name__", "")
        if name in GUARD_LABELS:
            found.add(name)
        found |= _collect_guards(sub)
    return found


def _width(text: str) -> int:
    """按东亚字符宽度估算显示宽度（中文算 2）。

    Args:
        text: 待测量文本

    Returns:
        int: 显示宽度
    """
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度右侧补空格。

    Args:
        text: 待补齐文本
        width: 目标显示宽度

    Returns:
        str: 补齐后的文本
    """
    return text + " " * max(0, width - _width(text))


def audit() -> list[tuple[str, str, str]]:
    """扫描整个应用，给每个接口判一个鉴权档位。

    Returns:
        list[tuple[str, str, str]]: ``(方法, 路径, 档位)`` 三元组
    """
    rows: list[tuple[str, str, str]] = []

    for route in _iter_api_routes(app.routes):
        guards = _collect_guards(route.dependant)

        if "require_session" in guards:
            label = GUARD_LABELS["require_session"]
        elif "require_user" in guards:
            label = GUARD_LABELS["require_user"]
        elif "optional_user" in guards:
            label = GUARD_LABELS["optional_user"]
        elif route.path in PUBLIC_ALLOWLIST:
            label = "公开（白名单）"
        else:
            label = "!! 漏挂"

        for method in sorted(route.methods or {"GET"}):
            rows.append((method, route.path, label))

    return rows


def main() -> int:
    """打印审计表并按需返回退出码。

    Returns:
        int: 0 表示全部符合预期，1 表示存在漏挂
    """
    rows = audit()
    leaked = [row for row in rows if row[2].startswith("!!")]

    print(f"共 {len(rows)} 个接口，其中未加保护的 {len(leaked)} 个\n")
    header = f"{_pad('方法', 8)}{_pad('路径', 34)}{_pad('档位', 18)}说明"
    print(header)
    print("-" * (_width(header) + 24))

    for method, path, label in rows:
        note = ""
        if label.startswith("!!"):
            note = "没有任何守卫，也没进白名单"
        elif label == "公开（白名单）":
            note = "按设计不需要身份"
        print(f"{_pad(method, 8)}{_pad(path, 34)}{_pad(label, 18)}{note}")

    if leaked and "--strict" in sys.argv:
        print("\n发现漏挂，退出码 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
