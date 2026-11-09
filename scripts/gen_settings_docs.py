#!/usr/bin/env python
"""配置一致性自查：代码字段 ↔ .env.example ↔ 文档表格。

背景：一个配置项要在三个地方出现——``app/core/config.py`` 的字段、
``.env.example`` 的模板行、``docs/configuration.md`` 第 7 节的表格。
三处靠手抄同步，历史上已经漂移过（模板里缺过 ``MAX_TOKENS``、
文档里留着已删除的字段）。本脚本只做**比对与报告，不改任何文件**。

用法::

    uv run python scripts/gen_settings_docs.py          # 检查，有差异时退出码 1
    uv run python scripts/gen_settings_docs.py --emit   # 打印全部字段的表格骨架

退出码：
    0 = 三处一致；1 = 存在差异；2 = 找不到文件
"""

import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
DOC = ROOT / "docs" / "configuration.md"

# 环境变量名（注意 ENVIRONMENT 字段对应的是 APP_ENV，属于改名，需要显式映射）
FIELD_TO_ENV = {"ENVIRONMENT": "APP_ENV"}

# 有字段但**不该**出现在 .env.example 里的：其值由代码推导，手写无效
SKIP_IN_ENV = {"RATE_LIMIT_ENDPOINTS"}


def load_settings_class() -> type:
    """导入 ``Settings`` 类，尽量不产生副作用干扰输出。

    导入该模块本身会加载 env 文件并构造一次全局 ``settings``（含 JWT 校验），
    所以这里先把 ``APP_ENV`` 兜底成 ``test``（测试环境豁免密钥校验），
    再把导入期间的提示输出吞掉——本脚本只关心字段定义，不关心运行时配置。

    Returns:
        type: ``Settings`` 类
    """
    os.environ.setdefault("APP_ENV", "test")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    with redirect_stdout(io.StringIO()):
        from app.core import config as config_module

    return config_module.Settings


def expected_env_keys(settings_cls: type) -> set[str]:
    """算出「应该在 .env.example 里出现」的全部变量名。

    除了字段本身，还要加上动态生成的限流键 ``RATE_LIMIT_<ENDPOINT>``
    （它们在代码里不是字段，但确实可以配置）。

    Args:
        settings_cls: 配置类

    Returns:
        set[str]: 环境变量名集合
    """
    keys: set[str] = set()
    for name in settings_cls.model_fields:
        if name in SKIP_IN_ENV:
            continue
        keys.add(FIELD_TO_ENV.get(name, name))

    # 动态限流键来自模块里的默认表，不重复硬编码
    from app.core import config as config_module

    for endpoint in config_module._RATE_LIMIT_ENDPOINT_DEFAULTS:
        keys.add(f"RATE_LIMIT_{endpoint.upper()}")
    return keys


def parse_env_example(path: Path) -> set[str]:
    """取出模板文件里所有 ``KEY=`` 形式的变量名（含被注释掉的）。

    模板里可选配置常常写成 ``# KEY=value``，让使用者按需取消注释，
    因此这里允许行首有一个 ``#``，否则会把「有示范但未启用」误判成缺失。

    Args:
        path: 模板文件路径

    Returns:
        set[str]: 变量名集合
    """
    pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)\s*=")
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := pattern.match(line.strip()):
            keys.add(match.group(1))
    return keys


def parse_doc_table(path: Path) -> set[str]:
    """取出文档里表格首列的反引号变量名（形如 ``| `MAX_TOKENS` | ... |``）。

    Args:
        path: 文档路径

    Returns:
        set[str]: 变量名集合
    """
    pattern = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := pattern.match(line.strip()):
            keys.add(match.group(1))
    return keys


def describe_type(annotation: Any) -> str:
    """把字段类型压成文档里常用的几个词。

    Args:
        annotation: 字段注解

    Returns:
        str: 类型名
    """
    origin = getattr(annotation, "__origin__", None)
    if origin is dict or annotation is dict:
        return "dict"
    if origin is list or annotation is list:
        return "list"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    name = getattr(annotation, "__name__", str(annotation))
    if name == "Path":
        return "path"
    if "Literal" in str(annotation):
        return "enum"
    if getattr(annotation, "__mro__", None) and any("Enum" in str(base) for base in annotation.__mro__):
        return "enum"
    return name


def describe_default(field: Any) -> str:
    """把字段默认值渲染成表格里的一格。

    Args:
        field: pydantic 的 FieldInfo

    Returns:
        str: 默认值描述
    """
    if field.default_factory is not None:  # type: ignore[comparison-overlap]
        return "—"
    default = field.default
    if default is None or default.__class__.__name__ == "PydanticUndefinedType":
        return "—"
    if isinstance(default, list):
        return ", ".join(str(item) for item in default) or "空"
    if isinstance(default, Path):
        return str(default)
    if default == "":
        return "空"
    return f"`{default}`"


def emit_table(settings_cls: type) -> None:
    """打印一份可直接粘进文档的 Markdown 表格骨架。

    Args:
        settings_cls: 配置类
    """
    print("| 变量 | 类型 | 默认值 | 说明 |")
    print("| --- | --- | --- | --- |")
    for name, field in settings_cls.model_fields.items():
        if name in SKIP_IN_ENV:
            continue
        env_name = FIELD_TO_ENV.get(name, name)
        print(f"| `{env_name}` | {describe_type(field.annotation)} | {describe_default(field)} | TODO |")


def main() -> int:
    """执行比对并输出结论。

    Returns:
        int: 进程退出码
    """
    if not ENV_EXAMPLE.is_file() or not DOC.is_file():
        print(f"找不到文件：{ENV_EXAMPLE} 或 {DOC}", file=sys.stderr)
        return 2

    settings_cls = load_settings_class()

    if "--emit" in sys.argv:
        emit_table(settings_cls)
        return 0

    expected = expected_env_keys(settings_cls)
    in_env = parse_env_example(ENV_EXAMPLE)
    in_doc = parse_doc_table(DOC)

    problems = 0

    def report(title: str, items: set[str]) -> None:
        """打印一组差异（为空时跳过）。

        Args:
            title: 差异说明
            items: 变量名集合
        """
        nonlocal problems
        if not items:
            return
        problems += len(items)
        print(f"\n{title}（{len(items)} 项）")
        for name in sorted(items):
            print(f"  - {name}")

    print(f"代码字段 {len(settings_cls.model_fields)} 个，预期环境变量 {len(expected)} 个")
    print(f".env.example 解析到 {len(in_env)} 个，文档表格解析到 {len(in_doc)} 个")

    report("① 代码里有，但 .env.example 缺", expected - in_env)
    report("② 代码里有，但文档表格缺", expected - in_doc)
    report("③ .env.example 有，但代码里没有（拼错或已删除）", in_env - expected)
    report("④ 文档表格有，但代码里没有（多半是删字段时漏删）", in_doc - expected)

    if problems == 0:
        print("\n三处一致 ✅")
        return 0

    print(f"\n共 {problems} 处不一致。补完再跑一次；--emit 可打印表格骨架。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
